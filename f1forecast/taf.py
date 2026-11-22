"""Issued NWS terminal forecasts, not observed or circuit weather.

Development pilot only (2022–25). The three circuit/airport pairs are fixed.
IEM describes its parsed archive at https://mesonet.agron.iastate.edu/request/taf.php
and its issue-time CSV at https://mesonet.agron.iastate.edu/cgi-bin/request/taf.py?help .
Use its raw NWS product archive instead of the CSV: validity, cancellations,
corrections and the WMO bulletin clock must survive ingestion.
https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?help
https://aviationweather.gov/help/data/#taf

Times are UTC. Before scheduled start minus 30 minutes, take the latest
product by max(TAF issue, WMO header, IEM product stamp), strictly before
the cut. These clocks do not prove local receipt. Never backfill an older
forecast after an ambiguous revision, cancellation, gap or invalid period.
The scheduled start must lie inside the raw bulletin's [start, end).
An independent published timetable must agree with the input UTC start.
Keep both times on disagreement; never substitute a repaired cut for the
frozen dataset's cut. Las Vegas local Saturday night is Sunday in UTC.

True means explicit RA/DZ (including mixed precipitation) at the airport in
the prevailing forecast or an active TEMPO/PROB group. An FM resets all
elements. BECMG changes prevailing weather at its end; during a change from
wet to dry or vice versa, return unknown rather than assign an onset minute.
All change periods are half-open. VCSH, VCTS, cloud and wind alone do not
say it will rain at the airport. False means no explicit rain in a decodable
forecast, not an observed dry race or even guaranteed dry airport weather.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urlencode
from zipfile import BadZipFile, ZipFile

import requests


_STATIONS = {"americas": "KAUS", "miami": "KFLL", "vegas": "KLAS"}
_ARCHIVE = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
_FIA_FILES = "https://www.fia.com/sites/default/files/"
_SCHEDULE_SOURCES = {
    2022: _FIA_FILES + "start_time_info_-_version_13_17.02.22.pdf",
    2023: _FIA_FILES + "start_time_info_-_version_3_08.12.22.pdf",
    2024: _FIA_FILES + "start_time_info_-_version_1_16.01.24_1.pdf",
    2025: "https://www.formula1.com/en/latest/article/"
          "f1-announces-race-start-times-for-2025-season.490KLLD7T1AAM7wQl28tn6",
}
_STARTS_UTC = {
    2022: {"KFLL": "05-08T19:30", "KAUS": "10-23T19:00"},
    2023: {"KFLL": "05-07T19:30", "KAUS": "10-22T19:00", "KLAS": "11-19T06:00"},
    2024: {"KFLL": "05-05T20:00", "KAUS": "10-20T19:00", "KLAS": "11-24T06:00"},
    2025: {"KFLL": "05-04T20:00", "KAUS": "10-19T19:00", "KLAS": "11-23T04:00"},
}


def _archive(cache, station, cutoff, offline):
    pil = "TAF" + station[1:]
    # AFOS uses an exclusive upper bound; the local check uses the exact cut.
    end = cutoff.replace(second=0, microsecond=0)
    if end < cutoff:
        end += timedelta(minutes=1)
    start = end - timedelta(hours=36)  # US NWS TAFs cover at most 30 hours.
    url = _ARCHIVE + "?" + urlencode({
        "pil": pil, "sdate": start.strftime("%Y-%m-%dT%H:%MZ"),
        "edate": end.strftime("%Y-%m-%dT%H:%MZ"), "fmt": "zip", "limit": 9999,
    })
    path = cache / "taf" / f"{pil}_{end:%Y%m%d%H%M}.zip"
    if path.exists():
        data = path.read_bytes()
    elif offline:
        raise FileNotFoundError(path.name)
    else:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.content
        with ZipFile(BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise BadZipFile("failed CRC")
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(path)
    return url, data


def _clock(code, anchor):
    """Resolve a day of month only inside the short bulletin window."""
    if not re.fullmatch(r"\d{4}(?:\d{2})?", code):
        raise ValueError("bad_clock")
    day, hour, minute = int(code[:2]), int(code[2:4]), int(code[4:] or 0)
    if hour > 24 or minute > 59 or hour == 24 and minute:
        raise ValueError("bad_clock")
    for offset in range(-2, 3):
        date = anchor.date() + timedelta(days=offset)
        if date.day == day:
            return datetime.combine(date, datetime.min.time(), UTC) + timedelta(hours=hour, minutes=minute)
    raise ValueError("clock_outside_window")


def _period(code, anchor):
    if not re.fullmatch(r"\d{4}/\d{4}", code):
        raise ValueError("bad_period")
    start, end = (_clock(part, anchor) for part in code.split("/"))
    if not start < end <= start + timedelta(hours=30):
        raise ValueError("bad_period")
    return start, end


_WIND = re.compile(r"(?:\d{3}|VRB)\d{2,3}(?:G\d{2,3})?KT")
_VIS = re.compile(r"[PM]?\d+(?:/\d+)?SM")
_SKY = re.compile(r"(?:FEW|SCT|BKN|OVC|VV)\d{3}(?:CB)?|SKC|CLR")
_WX = re.compile(r"[+-]?(?:VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
                 r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*")
_MARK = re.compile(r"\b(FM\d{6}|TEMPO|BECMG|PROB(?:30|40)(?: TEMPO)?)\b")


def _weather(words, full):
    """Partial groups without a weather element inherit the prevailing weather."""
    if not words or full and not all(any(p.fullmatch(w) for w in words) for p in (_WIND, _VIS, _SKY)):
        raise ValueError("incomplete_group")
    weather = []
    for word in words:
        if word == "NSW" or _WX.fullmatch(word) and word.strip("+-") not in ("", "VC"):
            weather.append(word)
        elif not (any(p.fullmatch(word) for p in (_WIND, _VIS, _SKY)) or
                  re.fullmatch(r"\d|WS\d{3}/\d{5,6}KT", word)):
            raise ValueError("unsupported_group_element")
    if not weather and not full:
        return None
    return any("VC" not in w and ("RA" in w or "DZ" in w) for w in weather)


def _forecast(body, issue, race, row):
    words = body.split()
    if "CNL" in words or "NIL" in words:
        raise ValueError("cancelled_or_nil")
    if not words:
        raise ValueError("missing_validity")
    start, end = _period(words[0], issue)
    row.update(valid_from_utc=start.isoformat(), valid_to_utc=end.isoformat())
    if not start <= race < end:
        raise ValueError("not_valid_at_start")
    base, *changes = _MARK.split(" ".join(words[1:]))
    wet, conditional = _weather(base.split(), True), False
    active = ["BASE"]
    previous = start
    for marker, text in zip(changes[::2], changes[1::2]):
        parts = text.split()
        if marker.startswith("FM"):
            when = _clock(marker[2:], issue)
            if not previous <= when < end:
                raise ValueError("bad_fm_order")
            previous = when
            change = _weather(parts, True)
            if when <= race:
                wet, conditional = change, False
                active = [marker]
            continue
        if not parts:
            raise ValueError("missing_change_period")
        begin, stop = _period(parts[0], issue)
        if not start <= begin < stop <= end or begin < previous:
            raise ValueError("bad_change_period")
        change = _weather(parts[1:], False)
        if marker == "BECMG" and begin <= race:
            if change is not None and change != wet:
                wet = change if stop <= race else None
            active.append(f"{marker} {parts[0]}")
        elif begin <= race < stop:
            conditional |= change is True
            active.append(f"{marker} {parts[0]}")
    row["active_groups"] = active
    row["wet"] = True if conditional else wet
    row["reason"] = "issued_forecast" if row["wet"] is not None else "becmg_transition"


_WMO = re.compile(r"(?m)^([A-Z]{4}\d{2}) ([A-Z]{4}) (\d{6})(?: ([A-Z]{3}))?[ \t]*$")


def _latest(data, station, cutoff, race, row):
    pil = "TAF" + station[1:]
    latest = None
    candidates = {}
    with ZipFile(BytesIO(data)) as archive:
        for item in archive.infolist():
            name = re.fullmatch(pil + r"_(\d{12})\.txt", item.filename)
            if not name:
                raise ValueError("unrecognized_product_name")
            product = datetime.strptime(name[1], "%Y%m%d%H%M").replace(tzinfo=UTC)
            if product >= cutoff:
                continue
            raw = archive.read(item)
            text = raw.decode("ascii").replace("\r", "")
            wmo = _WMO.search(text)
            header = re.search(r"\b" + pil + r"\s+TAF\s+(?:(AMD|COR)\s+)?" +
                               station + r"\s+(\d{6})Z\b", text)
            clocks = [product]
            issue = wmo_time = None
            if wmo:
                wmo_time = _clock(wmo[3], product)
                clocks.append(wmo_time)
            if header:
                issue = _clock(header[2], product)
                clocks.append(issue)
            available = max(clocks)
            if available >= cutoff or latest is not None and latest > available:
                continue
            if latest != available:
                candidates.clear()
                latest = available
            candidates[raw] = product, wmo, wmo_time, header, issue, text
    if not candidates:
        raise ValueError("no_pre_cut_product")
    row["available_utc"] = latest.isoformat()
    if len(candidates) != 1:
        raise ValueError("ambiguous_product_order")
    raw, (product, wmo, wmo_time, header, issue, text) = candidates.popitem()
    row.update(product_utc=product.isoformat(), bulletin_sha256=sha256(raw).hexdigest())
    if not (header and wmo):
        raise ValueError("unrecognized_bulletin")
    bbb = wmo[4]
    product_id = f"{product:%Y%m%d%H%M}-{wmo[2]}-{wmo[1]}-{pil}" + ("-" + bbb if bbb else "")
    row.update(issued_utc=issue.isoformat(), wmo_utc=wmo_time.isoformat(), issuer=wmo[2],
               product_id=product_id, modifier=header[1] or bbb or "routine",
               source=f"https://mesonet.agron.iastate.edu/api/1/nwstext/{product_id}")
    if (header[1] == "COR" or (bbb or "").startswith("CC")) and latest <= issue:
        raise ValueError("undated_correction")
    body = text[header.end():].strip()
    if not body.endswith("=") or "=" in body[:-1]:
        raise ValueError("incomplete_bulletin")
    _forecast(body[:-1], issue, race, row)


def load_pilot(cache: Path, gps, *, offline=False) -> dict[str, dict]:
    """Return one auditable row per calendar GP. Raw ZIPs live in ``cache/taf``."""
    rows = {}
    for gp in gps:
        station = _STATIONS.get(gp["circuit"])
        row = rows[gp["id"]] = dict(wet=None, issued_utc=None, station=station, source=None)
        year = gp["year"]
        scheduled = _STARTS_UTC.get(year, {}).get(station)
        if not scheduled:
            row["reason"] = "outside_pilot"
            continue
        race = datetime.fromtimestamp(gp["race"], UTC)
        cutoff = race - timedelta(minutes=30)
        row.update(source=_ARCHIVE, cutoff_utc=cutoff.isoformat(), target_utc=race.isoformat())
        official = datetime.fromisoformat(f"{year}-{scheduled}Z")
        row.update(scheduled_utc=official.isoformat(), schedule_source=_SCHEDULE_SOURCES[year])
        if race != official:
            row["reason"] = "published_schedule_mismatch"
            continue
        try:
            url, data = _archive(cache, station, cutoff, offline)
            row.update(archive_url=url, archive_sha256=sha256(data).hexdigest())
            _latest(data, station, cutoff, race, row)
        except FileNotFoundError:
            row["reason"] = "cache_miss"
        except (BadZipFile, requests.RequestException, OSError) as exc:
            row["reason"] = type(exc).__name__
        except ValueError as exc:
            row["reason"] = str(exc)
    return rows
