"""Refresh the last two classified main races; never promote live timing to a result."""

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from .archive import _ALIASES
from .dataset import FINAL_YEAR, document_events, load_corpus, records_for
from .engine import asof, epoch, utc
from .publication import pre_race_events, prediction, read_file, write_file
from .race_evaluation import distribution, race_evaluation, utc_time
from .results import (_FIA, _QATAR_ARCHIVE, _VEGAS_QUALI, _driver_codes,
                      _fetch, _jolpica, _normalized, _pdf, _vegas_roster, get_schedule)
from .training import sealed_model

F1 = 'https://www.formula1.com'
VEGAS_GRID = F1 + '/en/latest/article/official-grid-who-starts-where-in-las-vegas-after-multiple-penalties.1PRdR9E7qYRjHoRh1NlDR4'
VEGAS_GRID_WITNESS = 'https://www.pitpass.com/84196/Las-Vegas-Grand-Prix-Starting-Grid'
FIA_SEASON = _QATAR_ARCHIVE.rsplit('/event/', 1)[0]
PUBLIC_FEED = 'site/data/upcoming.json'
REPOSITORY = 'https://github.com/Hibra999/F1-AwesomeMachinePredictor'
REPORT = Path('reports/recent-podiums.json')
VEGAS_CAPTURE = Path('local-panel/las-vegas-2026.json')
LEGACY_FIELDS = {'classification_utc': None, 'race_evaluation': None}


def fia_urls(url):
    return (url.replace(_FIA + '/', 'https://api.fia.com/'), url)


def html(url, work, refresh=False, as_of=None):
    if as_of is not None:
        cached = work / 'public_documents' / sha256(url.encode()).hexdigest()
        if not cached.exists() or cached.stat().st_mtime > as_of:
            raise ValueError(f'No saved page at or before the requested observation: {url}')
    return BeautifulSoup(_fetch(url, work, refresh=refresh)[0], 'html.parser')


def fia_documents(page, as_of, name=None):
    """Use publication on the FIA site, not PDF creation or an assumed GP timezone."""
    docs = {}
    for link in page.select('.document-row a[href]'):
        if name:
            event = link.find_parent('ul', class_='document-type-wrapper')
            heading = event.parent.select_one('.event-title') if event else None
            if not heading or _normalized(heading.get_text(strip=True)) != _normalized(name):
                continue
        match = re.fullmatch(r'Doc\s+(\d+)\s*[-–]\s*(.+)', link.select_one('.title').get_text(strip=True), re.I)
        if not match:
            continue
        number, title = int(match[1]), match[2].casefold()
        published = link.select_one('.published').get_text(' ', strip=True)
        match = re.search(r'(\d\d\.\d\d\.\d\d \d\d:\d\d) (CEST|CET)', published)
        if not match:
            raise ValueError(f'Unknown FIA publication clock: {published}')
        stamp = datetime.strptime(match[1], '%d.%m.%y %H:%M').replace(tzinfo=UTC).timestamp()
        stamp -= 3600 * (2 if match[2] == 'CEST' else 1)
        if stamp <= as_of and (title not in docs or stamp > docs[title][0]):
            docs[title] = (stamp, urljoin(_FIA, link['href']), number)
    return docs


def result_rows(page):
    numbers, drivers, ranks = set(), set(), set()
    for row in page.select('table tbody tr'):
        cells = row.select('td')
        if len(cells) < 3:
            if cells and not row.find(string=re.compile('No results available', re.I)):
                raise ValueError('Unrecognized Formula 1 result row')
            continue
        raw = cells[0].get_text(strip=True)
        rank = int(raw) if raw.isdigit() else None
        number = cells[1].get_text(strip=True)
        parts = list(cells[2].stripped_strings)
        if (not number.isdigit() or number in numbers or len(parts) < 2 or not re.fullmatch('[A-Z]{3}', parts[-1])
                or parts[-1] in drivers or rank is not None and (rank < 1 or rank in ranks)):
            raise ValueError('Invalid or duplicate Formula 1 result row')
        drivers.add(parts[-1])
        numbers.add(number)
        ranks.add(rank)
        laps = cells[4].get_text(strip=True) if len(cells) > 4 else ''
        yield rank, number, parts[-1], ' '.join(parts[:-1]), int(laps) if laps.isdigit() else None


def f1_rows(page):
    return {rank: (code, name) for rank, _, code, name, _ in result_rows(page) if rank is not None}


def classification(docs, page, result_url, event_url, work, gp, day, refresh=False):
    rows = list(result_rows(page))
    ranked = {row[0]: row for row in rows if row[0] is not None}
    top = [ranked[i] for i in (1, 2, 3)] if {1, 2, 3} <= ranked.keys() else []
    podium = [(r[2], r[3]) for r in top]
    participants = [r[2] for r in rows]
    headers = [cell.get_text(strip=True).casefold() for cell in page.select('table thead th')]
    distance = top[0][4] if top and headers[:5] == ['pos.', 'no.', 'driver', 'team', 'laps'] else None
    for status in ('final', 'provisional'):
        candidates = [doc for title, doc in docs.items()
                      if re.fullmatch(status + r' race classification(?:\s*[-(].*)?', title)]
        if not candidates:
            continue
        stamp, url, number = max(candidates)
        name = _ALIASES.get(gp['name'], gp['name'])
        filename = _normalized(unquote(urlsplit(url).path.rsplit('/', 1)[-1]))
        if (urlsplit(url).netloc not in {'www.fia.com', 'api.fia.com'} or urlsplit(url).scheme != 'https'
                or _normalized(f"{gp['year']} {name}") not in filename
                or _normalized(status + ' race classification') not in filename or stamp <= gp['race']):
            raise ValueError(f'FIA listing does not identify the scheduled event: {url}')
        pdf = None
        for mirror in fia_urls(url):
            try:
                pdf, _ = _pdf(mirror, work, refresh)
                break
            except requests.RequestException:
                continue
        if not top:
            return 'pending', event_url, [], None, [], None
        if pdf is not None:
            cover = ' '.join(pdf.pages[0].extract_text().split())
            document = re.search(r'\bDocument (\d+) Date (\d{1,2} [A-Za-z]+ \d{4}) Time (\d\d:\d\d)', cover)
            weekend = re.search(r'[-–] (\d{1,2} [A-Za-z]+ \d{4})', cover)
            if (not document or int(document[1]) != number or not weekend
                    or datetime.strptime(weekend[1], '%d %B %Y').date() != day
                    or not _normalized(cover).startswith(_normalized(f"{gp['year']} {name}"))
                    or f'Title {status.title()} Race Classification' not in cover):
                raise ValueError(f'FIA cover disagrees with event, decision or result date: {url}')
            printed = datetime.strptime(f'{document[2]} {document[3]}', '%d %B %Y %H:%M').replace(tzinfo=UTC)
            if abs(printed.timestamp() - stamp) > 14 * 3600:
                raise ValueError(f'FIA printed date disagrees with publication date: {url}')
            table = pdf.pages[-1].extract_text(extraction_mode='layout')
            if not re.search(rf'\bDoc\s+{number}\s+Time\b', table):
                raise ValueError(f'FIA timing sheet disagrees with the decision number: {url}')
            for rank, car, _, driver, _ in top:
                line = re.search(rf'^\s*{rank}\s+{car}\s+(.+?)\s{{2,}}', table, re.M)
                if not line or _normalized(line[1]) != _normalized(driver):
                    raise ValueError(f'FIA and Formula 1 podiums disagree: {url}')
            frame = re.findall(r'^\s*(?:\d{1,2}\s+)?(\d{1,2})\s+(?=[^\W\d_])',
                               table.partition('FASTEST LAP')[0], re.M)
            if set(frame) != {r[1] for r in rows}:
                distance = None  # A truncated table cannot certify the denominator for the Brier score.
        else:
            # A listing proves a decision exists, not that F1 already reflects a penalty or DSQ.
            continue
        return status, url, podium, utc(stamp), participants, distance
    if podium:
        return 'provisional', result_url, podium, None, participants, distance
    if not page.find(string=re.compile('No results available', re.I)):
        raise ValueError(f'Unrecognized Formula 1 race-result page: {result_url}')
    return 'pending', event_url, [], None, [], None


def history(path):
    # Accept only the remote tracking branch, not a local replay or unpublished commit.
    try:
        result = subprocess.run(['git', 'log', 'origin/main', '--diff-filter=AM', '--format=%H', '--', path],
                                check=False, capture_output=True, encoding='utf-8')
    except FileNotFoundError:
        return
    if result.returncode:
        return  # A source archive has no remote; the watcher and tracked report still work.
    for sha in result.stdout.split():
        raw = subprocess.check_output(['git', 'show', f'{sha}:{path}'], encoding='utf-8')
        yield json.loads(raw), f'{REPOSITORY}/blob/{sha}/{path}'


def published_cuts():
    cuts = {}
    for feed, url in history(PUBLIC_FEED):
        if feed['mode'] == 'forecast':
            for cut in feed['predictions']:
                if cut['horizon'] == 'pre_race' and cut['origin'] == 'published':
                    cuts[feed['event']['id']] = (cut, [url])  # Oldest publication wins.
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
        if (utc_time(cut['cutoff_utc']).timestamp() != cutoff
                or not cutoff <= utc_time(cut['published_utc']).timestamp() <= cutoff + 120):
            raise ValueError('Published forecast outside the T−30 window')
        return cut, urls
    if row and gp['year'] == FINAL_YEAR:
        events, urls = row['events'], [row['source']]
    elif gp['id'] == '2026-21':
        events, urls = vegas_events(gp, docs, work, codes, cutoff)
    else:
        # Training-period backtests would leak labels. Other rounds need a prepared causal history.
        return None, []
    snap = asof(gp, records, pre_race_events(events, gp['id'], cutoff), 'pre_race', cutoff)
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


def sources(gp, result_url, fia, work, refresh, clock, as_of):
    name = _ALIASES.get(gp['name'], gp['name'])
    event_url = fia + '/event/' + quote(name) if fia else result_url
    page = html(result_url, work, refresh, as_of)
    heading = page.select_one('h1')
    active = [urljoin(F1, a['href']) for a in page.select('a[href$="/race-result"]')
              if a.get_text(strip=True) == 'Race ResultActive']
    if (not result_url.startswith(f"{F1}/en/results/{gp['year']}/races/") or not heading
            or active != [result_url] or not heading.get_text(' ', strip=True).endswith(f" {gp['year']} - RACE RESULT")):
        raise ValueError(f'Not an official main-race result page: {result_url}')
    date = next((m[1] for p in page.select('p') if (m := re.fullmatch(
        r'.*?\b(\d{1,2} [A-Z][a-z]{2} \d{4})', p.get_text(strip=True)))), None)
    day = datetime.strptime(date, '%d %b %Y').date() if date else None
    scheduled = datetime.fromtimestamp(gp['race'], UTC).date()
    if not day or abs((day - scheduled).days) > 1:
        raise ValueError(f'Mismatched official result date: {result_url}')
    docs = {}
    if fia:
        for mirror in fia_urls(event_url):
            if as_of is not None and not (work / 'public_documents' / sha256(mirror.encode()).hexdigest()).exists():
                continue
            try:
                listing = html(mirror, work, refresh, as_of)
            except requests.RequestException:
                continue
            for title, doc in fia_documents(listing, clock, name).items():
                if title not in docs or doc[0] > docs[title][0]:
                    docs[title] = doc
    return day, docs, classification(docs, page, result_url, event_url, work, gp, day, refresh)


def produce(work, as_of=None, refresh=False, *, issued_only=False, live_path=None, previous=None):
    clock = as_of if as_of is not None else datetime.now(UTC).timestamp()
    if clock > datetime.now(UTC).timestamp():
        raise ValueError('The observation time cannot be in the future')
    rows, records, bundle = {}, [], None
    if not issued_only:
        corpora = [load_corpus(work / name) for name in ('validation-data.joblib', 'final-data.joblib')]
        rows = {r['gp']['id']: r for corpus in corpora for r in corpus}
        records = records_for(*corpora)
        bundle, _ = sealed_model(work)
    public = published_cuts()
    feeds = [read_file(p) for p in dict.fromkeys([live_path, work / 'live.json']) if p and p.exists()]
    prior = {r['gp']: LEGACY_FIELDS | r
             for r in [*previous['races'], *previous['pending']]} if previous else {}
    forecasts = dict(prior)
    if issued_only:
        for historical, _ in history(REPORT.as_posix()):
            if utc_time(historical['as_of_utc']).timestamp() <= clock:
                for row in [*historical['races'], *historical['pending']]:
                    if row['forecast'] and not forecasts.get(row['gp'], {}).get('forecast'):
                        forecasts[row['gp']] = row
    report = {'races': [], 'pending': []}
    for gp, result_url, fia in recent_calendar(clock, work, refresh):
        gp_id = gp['id']
        old = prior.get(gp_id)
        candidates = [f for f in feeds if f.get('mode') == 'live' and f['event']['id'] == gp_id]
        if gp_id == '2026-21' and VEGAS_CAPTURE.exists():
            candidates.append(read_file(VEGAS_CAPTURE))
        candidates = [f for f in candidates if f['event']['id'] == gp_id and f['mode'] == 'live'
                      and utc_time(f['updated_utc']).timestamp() <= clock
                      and utc_time(f['event']['race_utc']).timestamp() == gp['race']]
        live = next((f for f in candidates if f['complete'] is True), None)
        try:
            day, docs, outcome = sources(gp, result_url, fia, work, refresh, clock, as_of)
        except requests.RequestException:
            if not old:
                raise
            report['pending' if old['status'] == 'pending' else 'races'].append(old)
            if len(report['races']) == 2:
                break
            continue
        status, source, podium, classified, participants, distance = outcome
        if status == 'pending' and not live and not old:
            continue  # Scheduled start alone does not prove a finish.
        official = [d for d, _ in podium] or None
        name = _ALIASES.get(gp['name'], gp['name'])
        cutoff = utc(gp['race'] - 1800)
        row = dict(gp=gp_id, name=name, date=day.isoformat(), official=official, forecast=None,
                   names=dict(podium), status=status, source_url=source, result_url=result_url,
                   forecast_urls=[], cutoff_utc=cutoff, origin=None, model=None, published_utc=None,
                   classification_utc=classified, race_evaluation=None)
        if old and (old['date'] != row['date'] or old['result_url'] != result_url):
            raise ValueError(f'{gp_id}: existing report identifies a different race')
        strength = {'pending': 0, 'provisional': 1, 'final': 2}
        if old and (strength[old['status']] > strength[status] or old['status'] == status
                    and ((old['classification_utc'] and (not classified or
                          utc_time(old['classification_utc']) > utc_time(classified)))
                         or urlsplit(old['source_url']).hostname in {'www.fia.com', 'api.fia.com'}
                         and status != 'pending' and source == result_url)):
            row = old.copy()
        else:
            row['race_evaluation'] = race_evaluation(live, gp, status, official, participants, distance, classified)
            if (row['race_evaluation'] is None and old and old['official'] == official
                    and old['source_url'] == source and old['classification_utc'] == classified):
                row['race_evaluation'] = old['race_evaluation']
        issued = public.get(gp_id)
        if not issued:
            for feed in candidates:
                pre = [c for c in feed['predictions'] if c['horizon'] == 'pre_race' and c['origin'] == 'published']
                if len(pre) > 1:
                    raise ValueError(f'{gp_id}: duplicate issued T−30')
                if pre:
                    issued = pre[0], feed.get('evidence_urls', [])
                    break
        saved = forecasts.get(gp_id)
        if saved and saved['forecast'] and not issued and (issued_only or saved['origin'] == 'published'):
            if utc_time(saved['cutoff_utc']) != utc_time(cutoff):
                raise ValueError(f'{gp_id}: saved T−30 disagrees with the schedule')
            row.update({k: saved[k] for k in ('forecast', 'forecast_urls', 'origin', 'model', 'published_utc')})
            row['names'] = {**{k: saved['names'][k] for k in saved['forecast']},
                            **{k: row['names'][k] for k in row['official'] or []}}
        elif issued or not issued_only:
            codes = _driver_codes(gp['year'], work, False) if gp_id == '2026-21' and not issued else {}
            cut, urls = forecast(gp, rows.get(gp_id), records, bundle, issued, docs, work, codes)
            if cut:
                drivers = distribution(cut)
                if cut['horizon'] != 'pre_race' or cut['lap'] is not None:
                    raise ValueError(f'{gp_id}: expected a T−30 forecast')
                row.update(forecast=cut['podium']['drivers'], forecast_urls=urls, origin=cut['origin'],
                           model=cut['model_version'], published_utc=cut['published_utc'])
                if saved and saved['origin'] == 'published' and any(
                        row[k] != saved[k] for k in ('forecast', 'origin', 'model', 'published_utc')):
                    raise ValueError(f'{gp_id}: conflicting records for an issued T−30')
                row['names'] = {**{k: drivers[k]['name'] for k in row['forecast']},
                                **{k: row['names'][k] for k in row['official'] or []}}
        report['pending' if row['status'] == 'pending' else 'races'].append(row)
        if len(report['races']) == 2:
            break
    if len(report['races']) != 2:
        raise ValueError('Could not verify two recent classifications')
    unchanged = previous and all(report[k] == [prior[r['gp']] for r in previous[k]] for k in ('pending', 'races'))
    report['as_of_utc'] = previous['as_of_utc'] if unchanged else utc(clock)
    return report


def _refresh(work, out, *, live_path=None, issued_only=False, refresh=True, as_of=None):
    saved = read_file(out) if out.exists() else None
    versions = [saved] if saved else []
    if REPORT.exists() and REPORT.resolve() != out.resolve():
        versions.append(read_file(REPORT))
    ceiling = datetime.now(UTC).timestamp() if as_of is None else as_of
    if saved and utc_time(saved['as_of_utc']).timestamp() > ceiling:
        raise ValueError('Do not overwrite a newer retrospective; use a separate --out for a historical observation')
    previous = max((r for r in versions if utc_time(r['as_of_utc']).timestamp() <= ceiling),
                   key=lambda r: utc_time(r['as_of_utc']), default=None)
    try:
        report = produce(work, as_of, refresh, issued_only=issued_only, live_path=live_path, previous=previous)
    except requests.RequestException:
        if not issued_only or previous is None:
            raise
        # A calendar outage need not hide the existing retrospective from a local operator.
        report = dict(previous, **{k: [LEGACY_FIELDS | r for r in previous[k]] for k in ('races', 'pending')})
    from .pages import verify_recent
    verify_recent(report)
    if saved != report:
        write_file(out, report)
    return report


def refresh_report(work, out, *, live_path=None):
    """Refresh a public or operator-local retrospective without loading a model or corpus."""
    return _refresh(work, out, live_path=live_path, issued_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.local'))
    parser.add_argument('--out', type=Path, default=REPORT)
    parser.add_argument('--issued-only', action='store_true', help='Keep existing backtests, never generate new forecasts')
    parser.add_argument('--live', type=Path, help='Operator-received race feed, including any seeded issued T−30')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--refresh', action='store_true', help='Refresh mutable F1 and FIA event pages')
    source.add_argument('--as-of', type=epoch, help='UTC observation time; requires pages cached no later than this instant')
    args = parser.parse_args()
    report = _refresh(args.work, args.out, as_of=args.as_of, refresh=args.refresh,
                      issued_only=args.issued_only, live_path=args.live)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
