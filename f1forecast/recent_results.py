"""Refresh the last two classified main races; never promote live timing to a result."""

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
import requests

from .archive import _ALIASES
from .dataset import FINAL_YEAR, document_events, load_corpus, records_for
from .engine import asof, epoch, utc
from .publication import prediction, read_file, write_file
from .results import (_FIA, _QATAR_ARCHIVE, _VEGAS_QUALI, _driver_codes,
                      _fetch, _jolpica, _normalized, _pdf, _vegas_roster, get_schedule)
from .training import sealed_model

F1 = 'https://www.formula1.com'
VEGAS_GRID = F1 + '/en/latest/article/official-grid-who-starts-where-in-las-vegas-after-multiple-penalties.1PRdR9E7qYRjHoRh1NlDR4'
VEGAS_GRID_WITNESS = 'https://www.pitpass.com/84196/Las-Vegas-Grand-Prix-Starting-Grid'
FIA_SEASON = _QATAR_ARCHIVE.rsplit('/event/', 1)[0]
PUBLIC_FEED = 'site/data/upcoming.json'
REPOSITORY = 'https://github.com/Hibra999/F1-AwesomeMachinePredictor'


def html(url, work, refresh=False, as_of=None):
    if as_of is not None:
        cached = work / 'public_documents' / sha256(url.encode()).hexdigest()
        if not cached.exists() or cached.stat().st_mtime > as_of:
            raise ValueError(f'No saved page at or before the requested observation: {url}')
    return BeautifulSoup(_fetch(url, work, refresh=refresh)[0], 'html.parser')


def fia_documents(page, as_of):
    """Use publication on the FIA site, not PDF creation or an assumed GP timezone."""
    docs = {}
    for link in page.select('.document-row a[href]'):
        title = re.sub(r'^Doc\s+\d+\s*[-–]\s*', '', link.select_one('.title').get_text(strip=True)).casefold()
        published = link.select_one('.published').get_text(' ', strip=True)
        match = re.search(r'(\d\d\.\d\d\.\d\d \d\d:\d\d) (CEST|CET)', published)
        if not match:
            raise ValueError(f'Unknown FIA publication clock: {published}')
        stamp = datetime.strptime(match[1], '%d.%m.%y %H:%M').replace(tzinfo=UTC).timestamp()
        stamp -= 3600 * (2 if match[2] == 'CEST' else 1)
        if stamp <= as_of and (title not in docs or stamp > docs[title][0]):
            docs[title] = (stamp, urljoin(_FIA, link['href']))
    return docs


def f1_rows(page):
    result = {}
    for row in page.select('table tbody tr'):
        cells = row.select('td')
        if len(cells) < 3 or not cells[0].get_text(strip=True).isdigit():
            continue
        rank = int(cells[0].get_text(strip=True))
        parts = list(cells[2].stripped_strings)
        if rank in result or len(parts) < 2 or not re.fullmatch('[A-Z]{3}', parts[-1]):
            raise ValueError('Invalid driver or duplicate position in Formula 1 classification')
        result[rank] = (parts[-1], ' '.join(parts[:-1]))
    if len({code for code, _ in result.values()}) != len(result):
        raise ValueError('Duplicate driver in Formula 1 classification')
    return result


def classification(docs, page, result_url, event_url, work, codes):
    f1 = f1_rows(page)
    for status in ('final', 'provisional'):
        candidates = [(t, u) for title, (t, u) in docs.items()
                      if re.fullmatch(status + r' race classification(?:\s*[-(].*)?', title)]
        if not candidates:
            continue
        _, url = max(candidates)
        try:
            pdf, _ = _pdf(url, work, False)
        except requests.RequestException:
            # FIA confirms the decision exists; Formula 1 supplies the classified order.
            if all(i in f1 for i in (1, 2, 3)):
                return status, url, [f1[i] for i in (1, 2, 3)]
            return 'pending', url, []
        if 'Race Classification' not in ' '.join(pdf.pages[0].extract_text().split()):
            raise ValueError(f'Not a race classification: {url}')
        positions = {}
        for line in pdf.pages[-1].extract_text(extraction_mode='layout').splitlines():
            m = re.match(r'^\s*([123])\s+\d{1,2}\s+(.+?)\s{2,}', line)
            if m and (code := codes.get(_normalized(m[2]))):
                positions[int(m[1])] = (code, m[2].strip().title())
        if positions.keys() != {1, 2, 3} or len({v[0] for v in positions.values()}) != 3:
            raise ValueError(f'Unreadable FIA podium: {url}')
        return status, url, [positions[i] for i in (1, 2, 3)]
    if all(i in f1 for i in (1, 2, 3)):
        return 'provisional', result_url, [f1[i] for i in (1, 2, 3)]
    if not page.find(string=re.compile('No results available', re.I)):
        raise ValueError(f'Unrecognized Formula 1 race-result page: {result_url}')
    return 'pending', event_url, []


def published_cuts():
    # Pages publishes this one file. Read remote-tracking history, never a local replay or draft.
    def git(*args):
        return subprocess.check_output(['git', *args], encoding='utf-8')
    cuts = {}
    for sha in git('log', 'origin/main', '--diff-filter=AM', '--reverse', '--format=%H', '--', PUBLIC_FEED).split():
        feed = json.loads(git('show', f'{sha}:{PUBLIC_FEED}'))
        if feed['mode'] != 'forecast':
            continue
        for cut in feed['predictions']:
            if cut['horizon'] == 'pre_race' and cut['origin'] == 'published' and cut['published_utc']:
                cuts.setdefault(feed['event']['id'], (cut, [f'{REPOSITORY}/blob/{sha}/{PUBLIC_FEED}']))
    return cuts


def vegas_events(gp, docs, work, codes, cutoff):
    issued, entries, evidence = _vegas_roster(gp, codes, work, False)
    urls = [e['url'] for e in evidence[:2]]
    if issued > cutoff:
        raise ValueError('The qualifying roster postdates T−30')
    events = list(document_events(dict(entries=entries, entries_at=issued, entries_source=urls)))
    table = _pdf(_VEGAS_QUALI, work, False)[0].pages[0].extract_text(extraction_mode='layout')
    by_number = {e['number']: e['id'] for e in entries}
    for line in table.partition('POLE POSITION LAP')[0].splitlines():
        m = re.match(r'^\s*(?:\d{1,2}\s+)?(\d{1,2})\s+[A-Za-z]', line)
        if m and (times := re.findall(r'\b(\d):(\d\d\.\d{3})\b', line)):
            events.append(dict(kind='qualifying', at=issued, source=_VEGAS_QUALI,
                               driver=by_number[int(m[1])], seconds=min(int(a) * 60 + float(b) for a, b in times)))
    # F1's full grid table is unversioned. Require every place in a separately dated, pre-cut grid.
    article = html(VEGAS_GRID, work)
    issued = datetime.strptime(article.find('time').get_text(strip=True), '%b %d, %Y %I:%M%p UTC').replace(tzinfo=UTC).timestamp()
    grid_url = urljoin(F1, article.select_one('a[href$="/starting-grid"]')['href'])
    official = f1_rows(html(grid_url, work))
    witness = html(VEGAS_GRID_WITNESS, work)
    modified = epoch(witness.select_one('meta[property="article:modified_time"]')['content'])
    ranks = {}
    for row in witness.select('table.pparticletable tr'):
        cells = [td.get_text(strip=True) for td in row.select('td')]
        if cells and cells[0].isdigit():
            pos = int(cells[0])
            code, name = official[pos]
            if not _normalized(name).endswith(_normalized(cells[1])) or pos in ranks:
                raise ValueError('Pre-race grid and official starting grid disagree')
            ranks[pos] = code
    if ranks.keys() != set(range(1, len(entries) + 1)) or set(ranks.values()) != set(by_number.values()):
        raise ValueError('Incomplete pre-race starting grid')
    grid_decision = docs.get('final starting grid')
    stamp = max(issued, modified)
    if stamp > cutoff:
        raise ValueError('Grid evidence postdates T−30')
    sources = [VEGAS_GRID, grid_url, VEGAS_GRID_WITNESS]
    if grid_decision and grid_decision[0] <= cutoff:
        stamp = max(stamp, grid_decision[0])
        sources.insert(0, grid_decision[1])
    events.append(dict(kind='grid', at=stamp, source=sources, positions={code: pos for pos, code in ranks.items()}))
    urls.extend(sources)
    return events, urls


def forecast(gp, row, records, bundle, issued, docs, work, codes):
    cutoff = gp['race'] - 1800
    if issued:
        cut, urls = issued
        if epoch(cut['cutoff_utc']) != cutoff or not cutoff <= epoch(cut['published_utc']) <= cutoff + 120:
            raise ValueError('Published forecast outside the T−30 window')
        return cut, urls
    if row and gp['year'] == FINAL_YEAR:
        events, urls = row['events'], [row['source']]
    elif gp['id'] == '2026-21':
        events, urls = vegas_events(gp, docs, work, codes, cutoff)
    else:
        # Training-period backtests would leak labels. Other rounds need a prepared causal history.
        return None, []
    snap = asof(gp, records, events, 'pre_race', cutoff)
    return (prediction(bundle, snap) if snap else None), urls


def recent_calendar(clock, work, refresh):
    current_year = datetime.fromtimestamp(clock, UTC).year
    for year in (current_year, current_year - 1):
        try:
            gps = get_schedule([year], work, refresh=refresh)
        except ValueError:
            today = datetime.fromtimestamp(clock, UTC).date().isoformat()
            if any(race['date'] <= today for races, _ in _jolpica(str(year), work, False) for race in races):
                raise
            continue  # A new calendar can lack session times before the opening weekend.
        if not any(gp['race'] < clock for gp in gps):
            continue
        index = f'{F1}/en/results/{year}/races'
        routes = list(dict.fromkeys(urljoin(F1, a['href']) for a in html(index, work, refresh).select('a[href$="/race-result"]')))
        if len(routes) != len(gps):
            raise ValueError(f'Formula 1 and Jolpica calendars disagree for {year}')
        fia = FIA_SEASON if f'/season/season-{year}-' in FIA_SEASON else None
        if not fia:
            championship = FIA_SEASON.rsplit('/season/', 1)[0]
            try:
                options = html(championship, work, refresh).select('option[value]')
                fia = next((urljoin(_FIA, o['value']) for o in options
                            if re.search(fr'/season/season-{year}-\d+$', o['value'])), None)
            except requests.RequestException:
                pass  # A Formula 1 table without a verified FIA decision is provisional only.
        for gp, url in reversed(list(zip(gps, routes, strict=True))):
            if gp['race'] < clock:
                yield gp, url, fia


def produce(work, as_of=None, refresh=False):
    clock = as_of if as_of is not None else datetime.now(UTC).timestamp()
    if clock > datetime.now(UTC).timestamp():
        raise ValueError('The observation time cannot be in the future')
    corpora = [load_corpus(work / name) for name in ('validation-data.joblib', 'final-data.joblib')]
    rows = {r['gp']['id']: r for corpus in corpora for r in corpus}
    records = records_for(*corpora)
    bundle, _ = sealed_model(work)
    public = published_cuts()
    catalogs = {}
    live = read_file(work / 'live.json') if (work / 'live.json').exists() else {}
    report = {'races': [], 'pending': []}
    for gp, result_url, fia in recent_calendar(clock, work, refresh):
        if gp['year'] not in catalogs:
            catalogs[gp['year']] = _driver_codes(gp['year'], work, False)
        codes = catalogs[gp['year']]
        name = _ALIASES.get(gp['name'], gp['name'])
        event_url = fia + '/event/' + quote(name) if fia else result_url
        page = html(result_url, work, refresh, as_of)
        heading = page.select_one('h1')
        if not heading or not heading.get_text(' ', strip=True).endswith(' - RACE RESULT'):
            raise ValueError(f'Not an official main-race result page: {result_url}')
        date = next((m[1] for p in page.select('p') if (m := re.fullmatch(r'.*?\b(\d{1,2} [A-Z][a-z]{2} \d{4})', p.get_text(strip=True)))), None)
        day = datetime.strptime(date, '%d %b %Y').date() if date else None
        scheduled = datetime.fromtimestamp(gp['race'], UTC).date()
        if not day or abs((day - scheduled).days) > 1:
            raise ValueError(f'Mismatched official result date: {result_url}')
        docs = {}
        if fia:
            try:
                listing = html(event_url, work, refresh, as_of)
            except requests.RequestException:
                cached = work / 'public_documents' / sha256(event_url.encode()).hexdigest()
                listing = html(event_url, work, as_of=as_of) if cached.exists() else None
            if listing is not None:
                docs = fia_documents(listing, as_of if as_of is not None else datetime.now(UTC).timestamp())
        status, source, podium = classification(docs, page, result_url, event_url, work, codes)
        if status == 'pending' and source == event_url and not (live.get('mode') == 'live' and live.get('complete') and
                live['event']['id'] == gp['id'] and epoch(live['updated_utc']) <= clock):
            continue  # Scheduled start alone does not prove a finish.
        cut, urls = forecast(gp, rows.get(gp['id']), records, bundle, public.get(gp['id']), docs, work, codes)
        top = cut['podium']['drivers'] if cut else None
        names = {d['id']: d['name'] for d in cut['drivers'] if d['id'] in top} if cut else {}
        names.update(podium)
        report['pending' if status == 'pending' else 'races'].append(dict(
            gp=gp['id'], name=name, date=day.isoformat(), official=[a for a, _ in podium] or None,
            forecast=top, names=names, status=status, source_url=source, result_url=result_url,
            forecast_urls=urls, cutoff_utc=utc(gp['race'] - 1800), origin=cut['origin'] if cut else None,
            model=cut['model_version'] if cut else None, published_utc=cut['published_utc'] if cut else None))
        if len(report['races']) == 2:
            break
    report['as_of_utc'] = utc(as_of if as_of is not None else datetime.now(UTC).timestamp())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.local'))
    parser.add_argument('--out', type=Path, default=Path('reports/recent-podiums.json'))
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--refresh', action='store_true', help='Refresh mutable F1 and FIA event pages')
    source.add_argument('--as-of', type=epoch, help='UTC observation time; requires pages cached no later than this instant')
    args = parser.parse_args()
    report = produce(args.work, args.as_of, args.refresh)
    write_file(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
