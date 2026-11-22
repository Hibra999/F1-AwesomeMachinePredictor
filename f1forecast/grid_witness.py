"""Testigo privado T−30: texto literal de una revisión de Wikipedia en inglés.

Atribución: colaboradores de Wikipedia, CC BY-SA 4.0
(https://creativecommons.org/licenses/by-sa/4.0/); revision_url enlaza la versión.
sha256 identifica el wikitext UTF-8, sin expandir plantillas actuales. «ordinary»
solo describe una fila numérica sin sanción explícita; no certifica su ausencia.
"""

import hashlib
import json
from math import ceil
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from .engine import epoch, utc
from .results import _normalized

_API = 'https://en.wikipedia.org/w/api.php'
_UA = 'PodioProbable/1.0 (historical research; https://github.com/Hibra999/F1-AwesomeMachinePredictor)'
_TITLES = {'Brazilian Grand Prix': 'São Paulo Grand Prix', 'Emilia Romagna Grand Prix': 'Emilia-Romagna Grand Prix'}
# Identidades estables, no hace falta consultar una clasificación posterior.
_CODES = {_normalized(name): code for code, *names in (
    ('ALB', 'Alexander Albon', 'Alex Albon'), ('ALO', 'Fernando Alonso'), ('ANT', 'Andrea Kimi Antonelli', 'Kimi Antonelli'),
    ('BEA', 'Oliver Bearman'), ('BOR', 'Gabriel Bortoleto'), ('BOT', 'Valtteri Bottas'), ('COL', 'Franco Colapinto'),
    ('DOO', 'Jack Doohan'), ('GAS', 'Pierre Gasly'), ('HAD', 'Isack Hadjar'), ('HAM', 'Lewis Hamilton'),
    ('HUL', 'Nico Hülkenberg'), ('LAW', 'Liam Lawson'), ('LEC', 'Charles Leclerc'), ('MAG', 'Kevin Magnussen'),
    ('NOR', 'Lando Norris'), ('OCO', 'Esteban Ocon'), ('PER', 'Sergio Pérez'), ('PIA', 'Oscar Piastri'),
    ('RIC', 'Daniel Ricciardo'), ('RUS', 'George Russell'), ('SAI', 'Carlos Sainz Jr.', 'Carlos Sainz'),
    ('SAR', 'Logan Sargeant'), ('STR', 'Lance Stroll'), ('TSU', 'Yuki Tsunoda'), ('VER', 'Max Verstappen'),
    ('ZHO', 'Zhou Guanyu')) for name in names}
_LINK = r'\[\[[^\[\]]+\]\]'
_SUBJECT = re.compile(rf'(?P<names>{_LINK}(?:(?:\s*,\s*(?:and\s+)?|\s+and\s+){_LINK})*)\s+(?P<body>.*)', re.I)
_REF = re.compile(r'\{\{\s*ref\s*\|([^{}]*)\}\}', re.I)
_NOTE = re.compile(r'^\*\s*\{\{\s*note\s*\|([^{}]*)\}\}\s*[–—:-]?\s*(.*)$', re.I | re.M)


def _codes(text):
    codes = set()
    for link in re.findall(_LINK, text):
        name = link[2:-2].split('|')[0].partition(' (')[0]
        if code := _CODES.get(_normalized(name)):
            codes.add(code)
    return codes


def _key(params):
    return next((p.strip() for p in params.split('|') if p.strip()), '')


def _note_kind(body):
    # Classify the subject, never the driver impeded or promoted in a later clause.
    first = body.lower().partition('. ')[0]
    if re.search(r'\b(?:rescinded|withdrawn penalty|no further action|no penalty)\b', body, re.I):
        return 'other'
    if re.search(r'\b(?:disqualified|withdrew|did not start)\b', first):
        return 'other'
    if re.search(r'(?:^|, but )(?:(?:he|they) )?(?:both )?(?:received|was given|were given|was handed|were handed) '
                 r'(?:a |an )?[\w-]+[- ]place (?:grid )?(?:penalt(?:y|ies)|drop)\b', first):
        return 'places'
    start = re.search(r'(?:^|, but )(?:(?:he|they) )?(?:was|were) required to start (?:the race )?from the '
                      r'(pit lane|back of the grid|rear of the grid)\b', first)
    if start:
        return 'pit' if start[1] == 'pit lane' else 'back'
    if first.startswith(('failed to set a time', 'did not take part')):
        return 'other'
    if first.startswith('set identical lap times'):
        return 'ordinary'
    return 'unknown'


def _cell(line):
    # Ignore sorting hints: non-numbered PL/DSQ cells may also sort numerically.
    value = re.sub(r'^[^{}\[\]|]*?=[^|]*\|', '', line[1:].strip())
    return value.replace("'", '').strip()


def _position(value):
    value = _REF.sub('', value).strip()
    if re.fullmatch(r'[0-9]+', value):
        pos = int(value)
        if not 1 <= pos <= 20:
            raise ValueError('posición fuera del campo')
        return pos
    return None


def _table(text):
    sections = re.findall(r'^==[ \t]*Qualifying[ \t]*==[ \t]*$(.*?)(?=^==[^=]|\Z)', text, re.I | re.M | re.S)
    if len(sections) != 1:
        raise ValueError('sección de clasificación GP ausente o ambigua')
    section, = sections
    section = re.sub(r'<ref\b[^>]*?/\s*>|<ref\b[^>]*>.*?</ref\s*>', '', section, flags=re.I | re.S)
    candidates = []
    for table in re.finditer(r'^\{\|(.*?)^\|}', section, re.M | re.S):
        rows = re.split(r'^\|-.*$', table[1], flags=re.M)
        header = [_cell(line).lower() for line in rows[0].splitlines() if line.startswith('!')]
        if (len(header) == 6 and 'qualifying position' in header[0] and 'car number' in header[1]
                and header[2:4] == ['driver', 'constructor'] and 'qualifying times' in header[4]
                and 'grid position' in header[5] and len(rows) >= 3
                and [_cell(line).upper() for line in rows[1].splitlines() if line.startswith('!')] == ['Q1', 'Q2', 'Q3']):
            candidates.append((rows[2:], section[table.end():]))
    if len(candidates) != 1:
        raise ValueError('tabla Q1/Q2/Q3 con parrilla ausente o ambigua')
    rows, notes = candidates[0]
    by_driver = {}
    for params, note in _NOTE.findall(notes):
        if subject := _SUBJECT.fullmatch(note.strip()):
            for code in _codes(subject['names']):
                by_driver.setdefault(code, []).append((_key(params), _note_kind(subject['body'])))
    drivers = {}
    for row in rows:
        cells = [_cell(line) for line in row.splitlines() if line.startswith(('!', '|'))]
        if not cells or re.search(r'^[!|]\s*colspan\s*=', row.strip(), re.I):
            continue  # Footer: 107% rule and sources, not driver rows.
        if len(cells) != 8 or len(codes := _codes(cells[2])) != 1:
            raise ValueError('fila de piloto incompleta o identidad ambigua')
        code, = codes
        if code in drivers:
            raise ValueError('piloto duplicado')
        q, g = cells[0], cells[-1]
        qual, grid = _position(q), _position(g)
        refs = {_key(p) for p in _REF.findall(q + g)}
        notes = by_driver.get(code, [])
        keys = {key for key, _ in notes}
        kinds = {kind for _, kind in notes}
        if re.search(r'\b(?:DSQ|DNS|disqualified|did not start)\b', q + ' ' + g, re.I):
            kind = 'other'
        elif re.search(r'\b(?:PL|pit lane)\b', g, re.I):
            kind = 'pit'
        elif (refs and refs != keys) or len(kinds) > 1:
            kind = 'unknown'
        elif kinds:
            kind = kinds.pop()
        else:
            kind = 'ordinary' if qual is not None and grid is not None else 'unknown'
        drivers[code] = {'qual': qual, 'grid': grid, 'kind': kind}
    for field in ('qual', 'grid'):
        positions = [item[field] for item in drivers.values() if item[field] is not None]
        if not positions or len(set(positions)) != len(positions):
            raise ValueError(f'columna {field} vacía o con posiciones duplicadas')
    return drivers


def _store(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(data)
    Path(tmp.name).replace(path)


def load_witness(gp, work, *, offline=False):
    """Read the last revision strictly before the corrected race time minus 30 minutes."""
    year, rnd = gp['year'], gp['round']
    if year not in (2024, 2025) or not 1 <= rnd <= 24 or gp['id'] != f'{year}-{rnd:02d}':
        raise ValueError('Solo se admiten las rondas de estudio 2024–25')
    cut = gp['race'] - 1800
    # MediaWiki timestamps are inclusive whole seconds. Never accept a cut-time edit.
    boundary = ceil(cut)
    title = f"{year} {_TITLES.get(gp['name'], gp['name'])}"
    path = Path(work) / 'grid_witness' / f"{gp['id']}-{boundary}.json"
    result = {'revision_url': None, 'available_utc': None, 'sha256': None, 'reason': 'cache_missing', 'drivers': {}}
    cached = path.is_file()
    if offline and not cached:
        return result
    if cached:
        data = path.read_bytes()
    else:
        params = dict(action='query', format='json', formatversion=2, prop='revisions', titles=title,
                      rvstart=utc(boundary - 1).replace('.000Z', 'Z'), rvlimit=1, rvdir='older',
                      rvprop='ids|timestamp|sha1|content', rvslots='main')
        try:
            response = requests.get(_API, params=params, headers={'User-Agent': _UA}, timeout=25)
            response.raise_for_status()
            data = response.content
        except requests.RequestException as exc:
            return {**result, 'reason': f'transport_error: {exc}'}
    try:
        doc = json.loads(data)
        if 'error' in doc:
            return {**result, 'reason': f"api_error: {doc['error'].get('code')}"}
        page, = doc['query']['pages']
        if page['title'] != title or page['ns'] != 0:
            return {**result, 'reason': 'invalid_revision: título incorrecto'}
        if not page.get('revisions'):
            if not cached:
                _store(path, data)
            return {**result, 'reason': 'no_revision'}
        rev, = page['revisions']
        stamp, revid, slot = rev['timestamp'], rev['revid'], rev['slots']['main']
        if (not isinstance(revid, int) or revid < 1 or slot['contentmodel'] != 'wikitext'
                or not stamp.endswith('Z') or epoch(stamp) >= cut):
            return {**result, 'reason': 'invalid_revision: versión no elegible'}
        text = slot['content']
        raw = text.encode('utf-8')
        if hashlib.sha1(raw).hexdigest() != rev['sha1']:
            return {**result, 'reason': 'cache_invalid: sha1 de MediaWiki no coincide'}
        text = re.sub(r'<!--.*?(?:-->|\Z)', '', text, flags=re.S)
        for field, expected in (('Year', year), ('Race_No', rnd)):
            if re.findall(rf'^\|[ \t]*{field}[ \t]*=[ \t]*(\d+)[ \t]*$', text, re.I | re.M) != [str(expected)]:
                return {**result, 'reason': f'invalid_revision: {field} no coincide'}
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return {**result, 'reason': f'cache_invalid: {exc}'}
    if not cached:
        _store(path, data)
    result.update(revision_url=f'https://en.wikipedia.org/w/index.php?oldid={revid}', available_utc=stamp,
                  sha256=hashlib.sha256(raw).hexdigest())
    try:
        return {**result, 'drivers': _table(text), 'reason': 'ok'}
    except ValueError as exc:
        return {**result, 'reason': f'unusable_table: {exc}'}
