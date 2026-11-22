"""Public F1 calendars, outcome labels and separately sourced pre-race documents."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
import time
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError
import requests

_JOLPICA = "https://api.jolpi.ca/ergast/f1/"
_FIA = "https://www.fia.com"
_UA = "f1forecast/1.0 (Formula 1 historical research)"
_REQUESTS: deque[float] = deque()
_RATE_LOCK = Lock()
_SESSIONS = {"FirstPractice": "Practice 1", "Qualifying": "Qualifying", "Sprint": "Sprint"}
_QATAR_SCHEDULE = "https://www.lcsc.qa/formula1-2026-event-schedule"
_FILES = f"{_FIA}/sites/default/files/"
_OLD_DOCS = _FILES + "decision-document/"
_DOCS = f"{_FIA}/system/files/decision-document/"
_VEGAS_QUALI = _FILES + "2026_21_usa_f1_q0_timing_qualifyingsessionprovisionalclassification_v01.pdf"
_VEGAS_REPORT = "https://www.formula1.com/en/latest/article/what-the-teams-said-qualifying-in-las-vegas-2026.3nVBux5Arj5K1MNogvg9j9"
_HAAS_UPDATE = "https://www.haasf1team.com/news/team-statement-driver-update-0"
_QATAR_ARCHIVE = f"{_FIA}/documents/championships/fia-formula-one-world-championship-14/season/season-2026-2072/event/Qatar%20Grand%20Prix"
_QATAR_2025 = _DOCS + "2025_qatar_grand_prix_-_"
_QATAR_2026 = _DOCS + "2026_qatar_grand_prix_-_"
_EVENT_DOCS = {
    "2022-01": (
        ("entries", _FILES + "doc_7_-_2022_bahrain_grand_prix_-_entry_list.pdf"),
        ("grid", _FILES + "doc_34_-_2022_bahrain_grand_prix_-_provisional_starting_grid_corrected.pdf"),
        ("grid", _FILES + "doc_38_-_2022_bahrain_grand_prix_-_final_starting_grid.pdf"),
    ),
    "2023-01": (
        ("entries", _FILES + "2023_bahrain_grand_prix_-_entry_list.pdf"),
        ("grid", _FILES + "doc_27_-_2023_bahrain_grand_prix_-_provisional_starting_grid.pdf"),
        ("grid", _FILES + "doc_29_-_2023_bahrain_grand_prix_-_final_starting_grid.pdf"),
    ),
    "2023-17": (
        ("entries", _OLD_DOCS + "2023%20Qatar%20Grand%20Prix%20-%20Entry%20List.pdf"),
    ),
    "2024-01": (
        ("entries", _OLD_DOCS + "2024%20Bahrain%20Grand%20Prix%20-%20Entry%20List.pdf"),
    ),
    "2024-23": (
        ("grid", _OLD_DOCS + "2024%20Qatar%20Grand%20Prix%20-%20Final%20Starting%20Grid.pdf"),
    ),
    "2025-23": (
        ("entries", _QATAR_2025 + "entry_list.pdf"),
        ("grid", _QATAR_2025 + "provisional_starting_grid.pdf"),
        ("grid", _QATAR_2025 + "final_starting_grid.pdf"),
    ),
}
_FINAL_CLASSIFICATION = {
    "2022-01": ("Asia/Bahrain", _FILES + "doc_44_-_2022_bahrain_grand_prix_-_final_race_classification.pdf"),
    "2022-22": ("Asia/Dubai", _OLD_DOCS + "2022%20Abu%20Dhabi%20Grand%20Prix%20-%20Final%20Race%20Classification.pdf"),
    "2023-01": ("Asia/Bahrain", _FILES + "doc_45_-_2023_bahrain_grand_prix_-_final_race_classification.pdf"),
    "2023-17": ("Asia/Qatar", _OLD_DOCS + "2023%20Qatar%20Grand%20Prix%20-%20Final%20Race%20Classification.pdf"),
    "2023-22": ("Asia/Dubai", _FILES + "doc_63_-_2023_abu_dhabi_grand_prix_-_final_race_classification.pdf"),
    "2024-01": ("Asia/Bahrain", _FILES + "doc_32_-_2024_bahrain_grand_prix_-_final_race_classification.pdf"),
    "2025-04": ("Asia/Bahrain", _DOCS + "2025_bahrain_grand_prix_-_final_race_classification.pdf"),
    "2025-23": ("Asia/Qatar", _QATAR_2025 + "final_race_classification.pdf"),
}
_DOCUMENT_TZ = {"bahrain": "Asia/Bahrain", "losail": "Asia/Qatar"}
_DOCUMENT_ERRORS = (requests.RequestException, PdfReadError, ValueError)


def _fetch(url: str, cache: Path, *, refresh: bool = False, timeout: float = 15) -> tuple[bytes, dict]:
    provider = "jolpica" if url.startswith(_JOLPICA) else "public_documents"
    path = cache / provider / sha256(url.encode()).hexdigest()
    if not path.exists() or refresh:
        if provider == "jolpica":
            with _RATE_LOCK:
                while True:
                    now = time.monotonic()
                    while _REQUESTS and now - _REQUESTS[0] >= 3600:
                        _REQUESTS.popleft()
                    wait = 0.0
                    if len(_REQUESTS) >= 500:
                        wait = _REQUESTS[0] + 3600 - now
                    if len(_REQUESTS) >= 4:
                        wait = max(wait, _REQUESTS[-4] + 1 - now)
                    if wait <= 0:
                        _REQUESTS.append(now)
                        break
                    time.sleep(wait)
        response = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        response.raise_for_status()
        data = response.content
        if provider == "jolpica":
            json.loads(data)["MRData"]
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            tmp.write(data)
        Path(tmp.name).replace(path)
    else:
        data = path.read_bytes()
    return data, {"url": url, "sha256": sha256(data).hexdigest(), "status": "downloaded"}


def _jolpica(endpoint: str, cache: Path, refresh: bool, *, result: bool = False):
    offset = 0
    total = None
    while total is None or offset < total:
        url = f"{_JOLPICA}{endpoint}/?limit=100&offset={offset}"
        data, evidence = _fetch(url, cache, refresh=refresh)
        root = json.loads(data)["MRData"]
        races = root["RaceTable"]["Races"]
        size = sum(len(race["Results"]) for race in races) if result else len(races)
        reported_total = int(root["total"])
        if int(root["offset"]) != offset or (total is not None and reported_total != total):
            raise ValueError(f"Jolpica pagination changed while reading {url}")
        total = reported_total
        if offset + size > total or (not size and offset < total):
            raise ValueError(f"Incomplete Jolpica page: {url}")
        yield races, evidence
        offset += size


def _utc(item: dict) -> float:
    stamp = datetime.fromisoformat(f"{item['date']}T{item['time']}")
    if stamp.utcoffset() != timedelta(0):
        raise ValueError(f"Expected explicit UTC session time: {item!r}")
    return stamp.timestamp()


def _id(race: dict) -> str:
    return f"{int(race['season']):04d}-{int(race['round']):02d}"


def get_schedule(years: Iterable[int], cache: Path, refresh: bool = False) -> list[dict]:
    """Read Jolpica's calendar snapshot; independently check the target event."""
    result = []
    for year in sorted(set(years)):
        season = []
        for races, evidence in _jolpica(str(year), cache, refresh):
            for row in races:
                sessions = {"Race": _utc(row)}
                for key, name in _SESSIONS.items():
                    if row.get(key, {}).get("time"):
                        sessions[name] = _utc(row[key])
                if "Practice 1" not in sessions:
                    raise ValueError(f"Missing UTC FP1 in Jolpica schedule: {_id(row)}")
                circuit = row["Circuit"]
                location = circuit["Location"]
                gp = {
                    "id": _id(row), "year": int(row["season"]), "round": int(row["round"]),
                    "name": row["raceName"], "circuit": circuit["circuitId"],
                    "fp1": sessions["Practice 1"], "race": sessions["Race"], "sessions": sessions,
                    "latitude": float(location["lat"]), "longitude": float(location["long"]),
                    "source": evidence["url"], "sources": [evidence],
                    "status": "jolpica_snapshot_unversioned",
                }
                if gp["year"] != year:
                    raise ValueError(f"Jolpica returned the wrong season: {gp['id']}")
                if gp["id"] == "2026-22":
                    _verify_qatar(gp, cache, refresh)
                season.append(gp)
        if not season or [gp["round"] for gp in season] != list(range(1, len(season) + 1)):
            raise ValueError(f"Empty or non-contiguous Jolpica schedule: {year}")
        result.extend(season)
    return result


def _entry(row: dict) -> dict:
    driver = row["Driver"]
    code = driver["code"]
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ValueError(f"Missing Jolpica driver code: {driver['driverId']}")
    return {
        "id": code, "name": f"{driver['givenName']} {driver['familyName']}",
        "team": row["Constructor"]["constructorId"], "number": int(row["number"]),
    }


def get_results(year: int, cache: Path, refresh: bool = False) -> dict[str, dict]:
    """Read final-snapshot labels, never a pre-race source for entries or grid."""
    result: dict[str, dict] = {}
    for races, evidence in _jolpica(f"{year}/results", cache, refresh, result=True):
        for race in races:
            gp_id = _id(race)
            if int(race["season"]) != year:
                raise ValueError(f"Jolpica returned the wrong results season: {gp_id}")
            event = result.setdefault(gp_id, {
                "labels": {}, "entries": [], "source": evidence["url"], "sources": [],
                "available_at": None, "versioning": "unversioned_jolpica_snapshot",
            })
            event["sources"].append(evidence)
            for row in race["Results"]:
                entry = _entry(row)
                if entry["id"] in event["labels"]:
                    raise ValueError(f"Duplicate driver {entry['id']} in {gp_id}")
                event["labels"][entry["id"]] = int(row["position"])
                event["entries"].append(entry)
    if not result:
        raise ValueError(f"No Jolpica race outcomes reported for {year}")
    for gp_id, event in result.items():
        if gp_id in _FINAL_CLASSIFICATION:
            _classification_evidence(gp_id, event, cache, refresh)
    return result


def _verify_qatar(gp: dict, cache: Path, refresh: bool) -> None:
    data, evidence = _fetch(_QATAR_SCHEDULE, cache, refresh=refresh)
    markers = {"fri-f1-fp1": "Practice 1", "sat-f1-quali": "Qualifying", "sun-f1-grand-prix": "Race"}
    expected = {}
    site = BeautifulSoup(data, "html.parser")
    if "GMT+3" not in site.get_text():
        raise ValueError("Lusail schedule no longer confirms Qatar local time (GMT+3)")
    for row in site.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td")]
        if cells and cells[0] in markers and "Confirmed" in cells:
            expected[markers[cells[0]]] = datetime.fromisoformat(f"{cells[1]}T{cells[2]}+03:00").timestamp()
    if len(expected) != len(markers) or any(gp["sessions"].get(k) != v for k, v in expected.items()):
        raise ValueError(f"Jolpica and Lusail disagree on the 2026 Qatar UTC schedule: {expected}")
    gp["sources"].append(evidence)
    gp["status"] = "cross_checked_jolpica_lusail"


def _pdf(url: str, cache: Path, refresh: bool) -> tuple[PdfReader, dict]:
    data, evidence = _fetch(url, cache, refresh=refresh, timeout=6)
    if not data.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    return PdfReader(BytesIO(data)), evidence


def _printed_time(head: str, zone: str) -> tuple[float, dict]:
    doc = re.search(r"\bDocument\s+(\d+)\s+Date\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})\s+Time\s+(\d{2}:\d{2})", head)
    if not doc:
        raise ValueError("no printed FIA document number, date and time")
    number, day, clock = doc.groups()
    local = datetime.strptime(f"{day} {clock}", "%d %B %Y %H:%M").replace(tzinfo=ZoneInfo(zone))
    return local.timestamp(), {"document": int(number), "printed_time": local.isoformat(), "timezone": zone}


def _classification_evidence(gp_id: str, event: dict, cache: Path, refresh: bool) -> None:
    zone, url = _FINAL_CLASSIFICATION[gp_id]
    try:
        pdf, evidence = _pdf(url, cache, refresh)
        head = " ".join(pdf.pages[0].extract_text().split())
        if "Final Race Classification" not in head:
            raise ValueError("document is not titled Final Race Classification")
        issued_at, printed = _printed_time(head, zone)
        numbers = {entry["number"]: entry["id"] for entry in event["entries"]}
        positions = {}
        for row in pdf.pages[-1].extract_text(extraction_mode="layout").splitlines():
            match = re.match(r"^\s*(\d{1,2})\s+(\d{1,2})\s+(?=[^\W\d_])", row)
            if match and int(match[2]) in numbers:
                positions[numbers[int(match[2])]] = int(match[1])
        if len(positions) < 3:
            raise ValueError("fewer than three readable numbered positions in FIA classification")
    except _DOCUMENT_ERRORS as exc:
        event["official_classification"] = {"url": url, "status": "unavailable", "reason": str(exc)}
        return
    matched = {code: issued_at for code, position in positions.items() if event["labels"].get(code) == position}
    event["official_classification"] = {
        **evidence, **printed, "issued_at": issued_at, "matching_positions": len(matched),
        "numbered_positions": len(positions),
    }
    if len(matched) == len(event["labels"]):
        event["available_at"] = issued_at
        event["versioning"] = "jolpica_snapshot_matched_to_dated_fia_final_classification"
    elif matched:
        event["available_at"] = matched
        event["versioning"] = "partial_fia_position_evidence_other_positions_unversioned"


def _normalized(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if c.isascii() and c.isalnum())


def _driver_codes(year: int, cache: Path, refresh: bool) -> dict[str, str]:
    url = f"{_JOLPICA}{year}/drivers/?limit=100&offset=0"
    data, _ = _fetch(url, cache, refresh=refresh)
    root = json.loads(data)["MRData"]
    drivers = root["DriverTable"]["Drivers"]
    if len(drivers) != int(root["total"]):
        raise ValueError(f"Incomplete Jolpica driver identity catalog: {url}")
    codes = {_normalized(d["givenName"] + d["familyName"]): d["code"] for d in drivers if "code" in d}
    for name, code in {"Kimi Antonelli": "ANT", "Alex Albon": "ALB", "Zhou Guanyu": "ZHO",
                       "Rafa Câmara": "CAM"}.items():
        if code in codes.values():
            codes[_normalized(name)] = code
    return codes


def _entry_from_document(number: str, name: str, team: str, year: int, codes: dict[str, str]) -> dict:
    code = codes.get(_normalized(name))
    if not code:
        raise ValueError(f"No stable Jolpica driver identity for {name!r}")
    name = " ".join(name.split())
    team_key = _normalized(team)
    patterns = {
        "astonmartin": "aston_martin", "redbullracing": "red_bull", "mercedesamg": "mercedes",
        "ferrari": "ferrari", "mclaren": "mclaren", "alpine": "alpine", "williams": "williams",
        "haas": "haas", "alfaromeo": "alfa", "alphatauri": "alphatauri", "sauber": "sauber",
        "cashapprb": "rb", "racingbulls": "rb", "audi": "audi", "cadillac": "cadillac",
    }
    # Team names precede constructor names on entry lists.
    matches = [(team_key.index(key), value) for key, value in patterns.items() if key in team_key]
    if not matches:
        raise ValueError(f"No season {year} constructor identity for {team!r}")
    return {"id": code, "name": name, "team": min(matches)[1], "number": int(number)}


def _checked_entries(entries: list[dict], size: int) -> list[dict]:
    if len(entries) != size or len({row["id"] for row in entries}) != size or len({row["number"] for row in entries}) != size:
        raise ValueError(f"Expected {size} unique driver identities and race numbers, parsed {len(entries)}")
    return sorted(entries, key=lambda row: row["id"])


def _entry_rows(table: str, gp: dict, codes: dict[str, str]) -> list[dict]:
    entries = []
    for row in table.splitlines():
        match = re.match(r"^\s*(\d{1,2})\s+(.+?)\s+[A-Z]{3}\s{2,}(.+)$", row)
        if match:
            entries.append(_entry_from_document(*match.groups(), gp["year"], codes))
    return _checked_entries(entries, 22 if gp["year"] >= 2026 else 20)


def _grid_rows(table: str, codes: dict[str, str], size: int) -> tuple[dict[str, int], list[str]]:
    result = {}
    pit_lane = set()
    numbered, separator, pit = table.partition("DRIVERS REQUIRED TO START FROM THE PIT LANE")
    if separator:
        for row in pit.splitlines():
            match = re.match(r"^\s*\d{1,2}\s+(.+?)(?:\s{2,}|$)", row)
            if match and (code := codes.get(_normalized(match[1].rstrip(" *")))):
                pit_lane.add(code)
    for start, stop in ((0, 82), (82, None)):
        pending = None
        for raw in numbered.splitlines():
            line = raw[start:stop].strip()
            match = re.match(r"(?:(\d{1,2})\s+)?(\d{1,2})\s+(.+?)(?:\s{2,}|$)", line)
            if match:
                position, _, name = match.groups()
                code = codes.get(_normalized(name.rstrip(" *")))
                if code:
                    if position is None:
                        pending = code
                    elif int(position) <= size:
                        if int(position) in result.values() or code in result:
                            raise ValueError(f"Duplicate FIA grid position or driver: {position}, {code}")
                        result[code] = int(position)
                    continue
            if pending and (match := re.match(r"^(\d{1,2})\s+(?=[^\W\d_])", line)):
                if int(match[1]) in result.values() or pending in result:
                    raise ValueError(f"Duplicate FIA grid position or driver: {match[1]}, {pending}")
                result[pending] = int(match[1])
            pending = None
    if len(result) + len(pit_lane) != size or result.keys() & pit_lane or any(not 1 <= rank <= size for rank in result.values()):
        raise ValueError(f"Expected {size} FIA grid entries, parsed {len(result)} positions and {len(pit_lane)} pit-lane starters")
    return result, sorted(pit_lane)


def _qatar_candidates(cache: Path) -> tuple[list[tuple[str, str]], list[str]]:
    candidates = [("entries", _QATAR_2026 + "entry_list.pdf")]
    candidates.extend(("grid", _QATAR_2026 + name) for name in ("provisional_starting_grid.pdf", "final_starting_grid.pdf"))
    notes = []
    try:
        # Recheck the changing listing; successful PDFs are cached separately.
        html, _ = _fetch(_QATAR_ARCHIVE, cache, refresh=True, timeout=6)
        for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
            url = urljoin(_QATAR_ARCHIVE, anchor["href"])
            path = urlsplit(url)
            title = anchor.get_text(" ", strip=True).casefold()
            qatar_pdf = path.path.casefold().endswith(".pdf") and "2026qatar" in _normalized(unquote(path.path))
            if path.scheme == "https" and path.netloc in {"www.fia.com", "api.fia.com"} and qatar_pdf:
                if "entry list" in title:
                    candidates.append(("entries", url))
                elif "starting grid" in title and "sprint" not in title:
                    candidates.append(("grid", url))
    except _DOCUMENT_ERRORS as exc:
        notes.append(f"FIA Qatar decision-document landing unavailable: {_QATAR_ARCHIVE}: {exc}")
    return list(dict.fromkeys(candidates)), notes


def _event_pdf(gp: dict, kind: str, url: str, codes: dict, cache: Path, refresh: bool):
    pdf, evidence = _pdf(url, cache, refresh)
    head = " ".join(pdf.pages[0].extract_text().split())
    zone = _DOCUMENT_TZ[gp["circuit"]]
    issued, printed = _printed_time(head, zone)
    if f"{gp['year']} {gp['name']}".casefold() not in head.casefold() or issued >= gp["race"]:
        raise ValueError("document does not match the pre-race event")
    table = pdf.pages[-1].extract_text(extraction_mode="layout")
    if kind == "entries" and "Entry List" in head:
        value = _entry_rows(table, gp, codes)
    elif kind == "grid" and "Starting Grid" in head and "Sprint Starting Grid" not in head:
        value, pit_lane = _grid_rows(table, codes, 22 if gp["year"] >= 2026 else 20)
        if pit_lane:
            evidence["pit_lane"] = pit_lane
    else:
        raise ValueError(f"FIA document header does not match requested {kind}")
    return issued, value, {**evidence, **printed}


def _vegas_roster(gp: dict, codes: dict, cache: Path, refresh: bool) -> tuple[float, list[dict], list[dict]]:
    pdf, timing = _pdf(_VEGAS_QUALI, cache, refresh)
    table = pdf.pages[0].extract_text(extraction_mode="layout")
    if "Qualifying Session Provisional Classification" not in table or "LAS VEGAS GRAND PRIX 2026" not in table:
        raise ValueError("FIA PDF is not the 2026 Las Vegas provisional qualifying classification")
    entries = []
    for row in table.split("POLE POSITION LAP", 1)[0].splitlines():
        match = re.match(r"^\s*(?:\d{1,2}\s+)?(\d{1,2})\s+(.+?)\s{2,}(.+?)\s{2,}\d:\d{2}\.\d{3}", row)
        if match:
            entries.append(_entry_from_document(*match.groups(), gp["year"], codes))
    entries = _checked_entries(entries, 22)
    report, editorial = _fetch(_VEGAS_REPORT, cache, refresh=refresh)
    article = BeautifulSoup(report, "html.parser")
    published = article.find("time")
    report_text = _normalized(article.get_text(" ", strip=True))
    if published is None or any(_normalized(entry["name"]) not in report_text for entry in entries):
        raise ValueError("Dated 2026 Las Vegas report does not corroborate all FIA roster identities")
    stamp = datetime.strptime(published.get_text(" ", strip=True), "%b %d, %Y %I:%M%p UTC").replace(tzinfo=timezone.utc)
    creation = pdf.metadata.creation_date
    if creation is None or creation.utcoffset() is None or creation > stamp:
        raise ValueError("FIA timing PDF creation and editorial publication time are inconsistent")
    timing["pdf_creation_time"] = creation.isoformat()
    editorial["published_at"] = stamp.isoformat()
    try:
        _, haas = _fetch(_HAAS_UPDATE, cache, refresh=refresh, timeout=6)
    except requests.RequestException as exc:
        haas = {"url": _HAAS_UPDATE, "status": "unavailable", "reason": str(exc)}
    return stamp.timestamp(), entries, [timing, editorial, haas]


def get_documents(gp: dict, cache: Path, *, refresh: bool = False, as_of: float | None = None) -> dict:
    """Read dated pre-race documents, using ``as_of`` as their issue-time cutoff."""
    cutoff = min(time.time() if as_of is None else as_of, gp["race"])
    qatar = gp["id"] == "2026-22"
    warnings = []
    candidates = list(_EVENT_DOCS.get(gp["id"], ()))
    if qatar and cutoff >= gp["fp1"] - 2 * 86400:
        candidates, warnings = _qatar_candidates(cache)
    if not (qatar or candidates):
        return {"source": [], "warnings": ["No dated FIA event documents indexed for this GP."]}
    codes = _driver_codes(gp["year"], cache, refresh)
    docs = {"source": [], "sources": [], "warnings": warnings}
    if candidates:
        with ThreadPoolExecutor(max_workers=4) as pool:
            submitted = [(kind, url, pool.submit(_event_pdf, gp, kind, url, codes, cache, refresh))
                         for kind, url in candidates]
            for kind, url, future in submitted:
                try:
                    issued, value, evidence = future.result()
                except _DOCUMENT_ERRORS as exc:
                    warnings.append(f"FIA {kind} unavailable: {url}: {exc}")
                    continue
                if issued > cutoff:
                    warnings.append(f"FIA {kind} issued after cutoff: {url}")
                    continue
                if issued >= docs.get(f"{kind}_at", float("-inf")):
                    docs[kind] = value
                    docs[f"{kind}_at"] = issued
                    docs[f"{kind}_status"] = "dated_fia_event_document"
                    docs[f"{kind}_source"] = evidence
    if qatar and "entries" not in docs:
        try:
            issued, roster, sources = _vegas_roster(gp, codes, cache, refresh)
            if issued <= cutoff:
                docs.update(entries=roster, entries_at=issued, entries_status="provisional_previous_gp_qualifying",
                            entries_source=sources)
                warnings.append("No dated Qatar FIA entry list by cutoff; Las Vegas qualifying roster is provisional for Qatar.")
            else:
                warnings.append("Las Vegas qualifying roster publication postdates cutoff; no roster inferred.")
        except _DOCUMENT_ERRORS as exc:
            warnings.append(f"Provisional Las Vegas qualifying roster unavailable: {exc}")
    if "entries" not in docs:
        warnings.append("No independently dated FIA event entry list by cutoff; no entries inferred.")
    if "grid" not in docs:
        warnings.append("No independently dated FIA race starting grid by cutoff; no grid inferred.")
    for kind in ("entries", "grid"):
        evidence = docs.get(f"{kind}_source")
        if evidence:
            docs["sources"].extend(evidence if isinstance(evidence, list) else [evidence])
    docs["source"] = [source["url"] for source in docs["sources"]]
    return docs
