#!/usr/bin/env python3
"""Resumable, concurrent World Athletics calendar crawler.

Enumerates the full public competition calendar and stores every flat-distance
running result that carries an exact date of birth into a local SQLite spool.

Data policy (see README.md): the raw corpus is research input only. It is never
committed to version control and never published; only derived aggregate models
are. An API key must be supplied through the environment:

    export WORLD_ATHLETICS_GRAPHQL_KEY=...

Usage:
    # 1. enumerate the calendar into the spool (cheap, idempotent)
    python3 scripts/fetch_world_athletics.py --enumerate

    # 2. crawl every not-yet-fetched competition (resumable; re-run to continue)
    python3 scripts/fetch_world_athletics.py --crawl --workers 10

    # 3. inspect what landed
    python3 scripts/fetch_world_athletics.py --report
"""

import argparse
import csv
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "raw" / "wa.sqlite"

ENDPOINT = os.environ.get(
    "WORLD_ATHLETICS_GRAPHQL_ENDPOINT",
    "https://graphql-prod-4895.edge.aws.worldathletics.org/graphql",
)

SOURCE_LABEL = "World Athletics public competition results"

# The model only covers ages 10-19. Storing two years of slack either side keeps
# the door open for a wider model later without letting senior elite results
# (Diamond League, senior national championships) dominate the spool.
MIN_AGE_YEARS = int(os.environ.get("WA_MIN_AGE_YEARS", "8"))
MAX_AGE_YEARS = int(os.environ.get("WA_MAX_AGE_YEARS", "22"))

# Flat-distance running events only. Hurdles, steeplechase, race walks and field
# events are deliberately excluded: "100m" must mean 100m flat, never 100m
# hurdles, or the event_id would pool two different physiological tasks.
EVENT_MAP = {
    "60 Metres": ("60m", 60),
    "100 Metres": ("100m", 100),
    "200 Metres": ("200m", 200),
    "300 Metres": ("300m", 300),
    "400 Metres": ("400m", 400),
    "600 Metres": ("600m", 600),
    "800 Metres": ("800m", 800),
    "1000 Metres": ("1000m", 1000),
    "1500 Metres": ("1500m", 1500),
    "Mile": ("mile", 1609.344),
    "1 Mile": ("mile", 1609.344),
    "3000 Metres": ("3000m", 3000),
    "5 Kilometres": ("5k", 5000),
    "10 Kilometres": ("10k", 10000),
    "5,000 Metres": ("5k", 5000),
    "10,000 Metres": ("10k", 10000),
    "5 Kilometres Road Race": ("5k", 5000),
    "10 Kilometres Road Race": ("10k", 10000),
    "5 Kilometres Cross Country": ("5k", 5000),
    "10 Kilometres Cross Country": ("10k", 10000),
    "3000 Metres Cross Country": ("3000m", 3000),
}

EVENT_LOOKUP = {name.lower(): value for name, value in EVENT_MAP.items()}

# Events that exist as a road/mass-participation variant only. If the source name
# says "Metres" it is a track event on a road-legal venue, not a road race.
ROAD_EVENT_IDS = {"mile", "5k", "10k"}

OBSTACLE_TOKENS = ("hurdles", "steeplechase", "relay", "race walk", "walking")
CROSS_COUNTRY_TOKENS = ("cross country", "cross-country", "crosscountry", "trail", "mountain")
INDOOR_TOKENS = ("indoor", "short track", "year round", "winter")

# Marks that mean "did not finish / no valid performance".
INVALID_MARK_TOKENS = ("DNS", "DNF", "DQ", "NM", "NR", "PB", "RET")

CALENDAR_QUERY = """query getCalendarEvents($startDate: String, $endDate: String, $query: String, $competitionGroupId: Int, $limit: Int, $offset: Int, $hideCompetitionsWithNoResults: Boolean, $orderDirection: OrderDirectionEnum) { getCalendarEvents(startDate: $startDate, endDate: $endDate, query: $query, competitionGroupId: $competitionGroupId, limit: $limit, offset: $offset, hideCompetitionsWithNoResults: $hideCompetitionsWithNoResults, orderDirection: $orderDirection) { hits results { id name venue startDate endDate } options { competitionGroups { id name count } } } }"""
RESULTS_QUERY = """query getCalendarCompetitionResults($competitionId: Int, $day: Int, $eventId: Int) { getCalendarCompetitionResults(competitionId: $competitionId, day: $day, eventId: $eventId) { competition { name venue } eventTitles { events { event eventId gender isRelay races { date results { competitor { id birthDate } mark wind remark place } } } } } }"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS competition_groups (
    group_id   INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    hit_count  INTEGER
);
CREATE TABLE IF NOT EXISTS competitions (
    competition_id INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    venue          TEXT,
    country        TEXT,
    start_date     TEXT,
    end_date       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    rows_ingested  INTEGER NOT NULL DEFAULT 0,
    rows_seen      INTEGER NOT NULL DEFAULT 0,
    rows_with_dob  INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE TABLE IF NOT EXISTS competition_group_members (
    competition_id INTEGER NOT NULL,
    group_id       INTEGER NOT NULL,
    PRIMARY KEY (competition_id, group_id)
);
CREATE TABLE IF NOT EXISTS results (
    competition_id INTEGER NOT NULL,
    event_id       TEXT    NOT NULL,
    sex            TEXT    NOT NULL,
    birth_date     TEXT    NOT NULL,
    race_date      TEXT    NOT NULL,
    seconds        REAL    NOT NULL,
    surface        TEXT    NOT NULL,
    timing_method  TEXT    NOT NULL,
    athlete_id     TEXT    NOT NULL,
    wind           REAL,
    remark         TEXT,
    place          TEXT,
    country        TEXT    NOT NULL,
    group_id       INTEGER,
    level          TEXT    NOT NULL,
    season_year    INTEGER NOT NULL,
    source         TEXT    NOT NULL,
    source_url     TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS results_identity
    ON results (athlete_id, event_id, birth_date, race_date, seconds, competition_id);
CREATE INDEX IF NOT EXISTS results_cohort
    ON results (event_id, sex, surface, birth_date);
"""

LEVEL_ORDER = {
    "international": 0,
    "national": 1,
    "regional": 2,
    "permit": 3,
    "meet": 4,
}


class ApiError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def api_key():
    key = os.environ.get("WORLD_ATHLETICS_GRAPHQL_KEY")
    if not key:
        sys.exit(
            "WORLD_ATHLETICS_GRAPHQL_KEY is not set.\n"
            "Export the GraphQL key before running the crawler; it is never stored in this repo."
        )
    return key


_local = threading.local()


def graphql(operation, variables, retries=5, timeout=90):
    key = api_key()
    body = json.dumps(
        {"operationName": operation, "query": CALENDAR_QUERY if operation == "getCalendarEvents" else RESULTS_QUERY,
         "variables": variables}
    ).encode()
    request = urllib.request.Request(
        ENDPOINT, data=body, headers={"Content-Type": "application/json", "X-Api-Key": key}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if payload.get("errors"):
                message = json.dumps(payload["errors"])[:200]
                # A GraphQL-level error will not fix itself; do not burn retries.
                raise ApiError(message)
            return payload["data"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as error:
            if attempt == retries - 1:
                raise ApiError(f"{type(error).__name__}: {error}") from error
            time.sleep(min(30, 2**attempt) + random.random())
    raise ApiError("unreachable")


# --------------------------------------------------------------------------- #
# SQLite
# --------------------------------------------------------------------------- #

def connect(db_path, tune=False):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=120, check_same_thread=False)
    if tune:
        # journal_mode is persistent; only the initialiser needs to set it.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def init_db(db_path):
    connection = connect(db_path, tune=True)
    connection.executescript(SCHEMA)
    connection.commit()
    return connection


# SQLite allows a single writer. Serialising the (cheap) per-competition commit
# is simpler and steadier than relying on busy-timeout backoff.
WRITE_LOCK = threading.Lock()


def thread_connection(db_path):
    if getattr(_local, "connection", None) is None:
        _local.connection = connect(db_path)
    return _local.connection


def is_excluded_competition(group_names):
    """Multi-event competitions publish their 800m and 1500m legs as ordinary
    events, but those marks are not comparable with standalone races - they carry
    points in some federations and are run with combined-event tactics. Pooling
    them flattens the age curve and doubles the spread, so the whole competition
    is dropped."""
    return any("combined event" in name.lower() for name in group_names)


def classify_level(group_names):
    """Map competition-group names to a selection tier.

    Only genuinely elite-selected events (World Athletics / Olympic / FISU) are
    treated as a separate tier. Continental (Area) and national championships are
    broad participation populations, which is exactly what the model needs.
    """
    level = None
    for name in group_names:
        lowered = name.lower()
        if lowered.startswith("world athletics") or lowered.startswith("olympic") or lowered.startswith("fisu"):
            candidate = "international"
        elif "permit" in lowered:
            candidate = "permit"
        elif lowered.startswith("national"):
            candidate = "national"
        elif lowered.startswith("area"):
            candidate = "regional"
        else:
            candidate = "meet"
        if level is None or LEVEL_ORDER[candidate] < LEVEL_ORDER[level]:
            level = candidate
    return level or "meet"


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

COUNTRY_RE = re.compile(r"\(([A-Z]{3})\)\s*$")


def country_from_venue(venue):
    if not venue:
        return "ZZZ"
    match = COUNTRY_RE.search(str(venue).strip())
    return match.group(1) if match else "ZZZ"


def normalize_date(value):
    """World Athletics emits ISO, `29 JUN 2006` and `29 June 2006`."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text[:10] if pattern == "%Y-%m-%d" else text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def parse_mark(value):
    """Return seconds for a running mark, or None if it is not a valid time."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if any(token in text for token in INVALID_MARK_TOKENS):
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            return None
        try:
            values = [float(part) for part in parts]
        except ValueError:
            return None
        if len(values) == 2:
            return values[0] * 60 + values[1]
        return values[0] * 3600 + values[1] * 60 + values[2]
    try:
        return float(text)
    except ValueError:
        return None


def normalize_event(name):
    """Map a World Athletics event title to a canonical flat-distance event."""
    cleaned = str(name or "")
    cleaned = cleaned.replace("Women's", " ").replace("Men's", " ").replace("Mixed", " ")
    # Drop implementation suffixes: "(5kg)", "(i)", "(U18 Men)", "(Final)".
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\b(Short Track|Indoor)\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(",").strip()
    if not cleaned:
        return None, None
    lowered = cleaned.lower()
    if any(token in lowered for token in OBSTACLE_TOKENS):
        return None, None
    mapped = EVENT_LOOKUP.get(lowered)
    if mapped is None:
        return None, None
    event_id, distance = mapped
    return event_id, distance


def infer_surface(event_name, competition_name, group_names, event_id):
    title = (event_name or "").lower()
    haystack = " ".join([title, (competition_name or "").lower(), " ".join(group_names).lower()])
    if any(token in haystack for token in CROSS_COUNTRY_TOKENS):
        return "cross-country"
    if "(i)" in title or any(token in haystack for token in INDOOR_TOKENS):
        return "indoor-track"
    # "Kilometres" contains "metres", so the distinction needs a word boundary.
    if event_id in ROAD_EVENT_IDS and not re.search(r"\bmetres\b", title):
        return "road"
    return "outdoor-track"


def infer_timing_method(remark, venue):
    text = f"{remark or ''}".lower()
    if "hand" in text or "handtim" in text:
        return "hand"
    # Hand timing is the norm at small low-level meets; WA flags it explicitly
    # when it is electronic, so absence of a flag at a minor meet is a hint only.
    if "electronic" in text:
        return "electronic"
    return "electronic"


def parse_wind(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("+", "").replace("m/s", "").strip())
    except ValueError:
        return None


def competition_url(competition_id, name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return f"https://worldathletics.org/competition/calendar-results/results/{competition_id}/{slug}"


# --------------------------------------------------------------------------- #
# Enumeration
# --------------------------------------------------------------------------- #

def competition_groups(start_date, end_date):
    data = graphql("getCalendarEvents", {
        "startDate": start_date, "endDate": end_date, "query": None, "competitionGroupId": None,
        "limit": 1, "offset": 0, "hideCompetitionsWithNoResults": True, "orderDirection": "Ascending",
    })["getCalendarEvents"]
    return data["options"]["competitionGroups"]


def paginate_group(start_date, end_date, group_id):
    offset = 0
    while True:
        data = graphql("getCalendarEvents", {
            "startDate": start_date, "endDate": end_date, "query": None,
            "competitionGroupId": group_id if group_id > 0 else None,
            "limit": 100, "offset": offset, "hideCompetitionsWithNoResults": True,
            "orderDirection": "Ascending",
        })["getCalendarEvents"]
        results = data["results"]
        for competition in results:
            yield competition
        offset += len(results)
        if not results or offset >= data["hits"]:
            return


def enumerate_calendar(connection, start_date, end_date):
    groups = competition_groups(start_date, end_date)
    connection.executemany(
        "INSERT INTO competition_groups (group_id, name, hit_count) VALUES (?,?,?) "
        "ON CONFLICT(group_id) DO UPDATE SET name=excluded.name, hit_count=excluded.hit_count",
        [(group["id"], group["name"], group.get("count")) for group in groups],
    )
    connection.commit()

    known = {
        row[0] for row in connection.execute(
            "SELECT competition_id FROM competitions WHERE competition_id NOT IN "
            "(SELECT competition_id FROM competition_group_members)"
        )
    }

    total = 0
    for group in sorted(groups, key=lambda item: item["id"]):
        group_id = group["id"]
        if group_id == 0:
            continue
        inserted = 0
        members = 0
        for competition in paginate_group(start_date, end_date, group_id):
            members += 1
            competition_id = competition["id"]
            venue = competition.get("venue")
            if competition_id in known:
                connection.execute(
                    "INSERT OR IGNORE INTO competition_group_members (competition_id, group_id) VALUES (?,?)",
                    (competition_id, group_id),
                )
                continue
            connection.execute(
                "INSERT OR IGNORE INTO competitions "
                "(competition_id, name, venue, country, start_date, end_date) VALUES (?,?,?,?,?,?)",
                (competition_id, competition.get("name") or "", venue, country_from_venue(venue),
                 competition.get("startDate"), competition.get("endDate")),
            )
            connection.execute(
                "INSERT OR IGNORE INTO competition_group_members (competition_id, group_id) VALUES (?,?)",
                (competition_id, group_id),
            )
            inserted += 1
        connection.commit()
        total += inserted
        print(f"  group {group_id:>5} {group['name'][:44]:44s} members={members:5d} new={inserted:5d}", flush=True)

    pending = connection.execute("SELECT COUNT(*) FROM competitions WHERE status='pending'").fetchone()[0]
    print(f"Enumeration complete: {total} new competitions; {pending} pending to crawl.")
    return total


def competition_context(connection, competition_id):
    groups = [row[0] for row in connection.execute(
        "SELECT g.name FROM competition_groups g "
        "JOIN competition_group_members m ON m.group_id = g.group_id "
        "WHERE m.competition_id = ?", (competition_id,))]
    return groups


# --------------------------------------------------------------------------- #
# Crawling
# --------------------------------------------------------------------------- #

def ingest_competition(connection, competition_id, retry_failed=True):
    context = connection.execute(
        "SELECT name, start_date, country FROM competitions WHERE competition_id=?", (competition_id,)
    ).fetchone()
    if context is None:
        return 0, "missing"
    competition_name, start_date, country = context
    group_names = competition_context(connection, competition_id)
    if is_excluded_competition(group_names):
        with WRITE_LOCK:
            connection.execute("DELETE FROM results WHERE competition_id=?", (competition_id,))
            connection.execute(
                "UPDATE competitions SET status='excluded', error='combined-events competition' WHERE competition_id=?",
                (competition_id,),
            )
            connection.commit()
        return 0, None
    level = classify_level(group_names)
    primary_group = None
    if group_names:
        row = connection.execute(
            "SELECT m.group_id FROM competition_group_members m JOIN competition_groups g "
            "ON g.group_id=m.group_id WHERE m.competition_id=? ORDER BY g.hit_count DESC LIMIT 1",
            (competition_id,),
        ).fetchone()
        primary_group = row[0] if row else None

    payload = graphql("getCalendarCompetitionResults", {"competitionId": competition_id, "day": None, "eventId": None})
    result = payload.get("getCalendarCompetitionResults") or {}
    fallback_date = normalize_date(start_date) or result.get("competition", {}).get("startDate")
    race_fallback = normalize_date(fallback_date)
    url = competition_url(competition_id, competition_name)

    rows = []
    seen_rows = set()
    rows_seen = 0
    rows_with_dob = 0
    rows_out_of_age = 0
    min_age_days = int(MIN_AGE_YEARS * 365.2425)
    max_age_days = int((MAX_AGE_YEARS + 1) * 365.2425)
    for group in result.get("eventTitles") or []:
        for event in group.get("events") or []:
            if event.get("isRelay"):
                continue
            event_id, _ = normalize_event(event.get("event", ""))
            if event_id is None:
                continue
            sex = "female" if event.get("gender") == "W" else ("male" if event.get("gender") == "M" else None)
            if sex is None:
                continue
            surface = infer_surface(event.get("event", ""), result.get("competition", {}).get("name") or competition_name,
                                    group_names, event_id)
            for race in event.get("races") or []:
                race_date = normalize_date(race.get("date")) or race_fallback
                if race_date is None:
                    continue
                season_year = int(race_date[:4])
                for row in race.get("results") or []:
                    competitor = row.get("competitor") or {}
                    rows_seen += 1
                    birth_date = normalize_date(competitor.get("birthDate"))
                    athlete_id = str(competitor.get("id") or "")
                    if not birth_date or not athlete_id:
                        continue
                    rows_with_dob += 1
                    age_days = (date.fromisoformat(race_date) - date.fromisoformat(birth_date)).days
                    if age_days < min_age_days or age_days >= max_age_days:
                        rows_out_of_age += 1
                        continue
                    seconds = parse_mark(row.get("mark"))
                    if seconds is None or seconds <= 0:
                        continue
                    identity = (athlete_id, event_id, birth_date, race_date, seconds)
                    if identity in seen_rows:
                        continue
                    seen_rows.add(identity)
                    rows.append((
                        competition_id, event_id, sex, birth_date, race_date, seconds, surface,
                        infer_timing_method(row.get("remark"), competition_name), athlete_id,
                        parse_wind(row.get("wind")), row.get("remark"), row.get("place"),
                        country, primary_group, level, season_year, SOURCE_LABEL, url,
                    ))

    with WRITE_LOCK:
        connection.executemany(
            "INSERT OR IGNORE INTO results (competition_id, event_id, sex, birth_date, race_date, seconds, surface,"
            " timing_method, athlete_id, wind, remark, place, country, group_id, level, season_year, source, source_url)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        status = "ok" if rows else "empty"
        connection.execute(
            "UPDATE competitions SET status=?, rows_ingested=?, rows_seen=?, rows_with_dob=?, error=NULL "
            "WHERE competition_id=?",
            (status, len(rows), rows_seen, rows_with_dob, competition_id),
        )
        connection.commit()
    return len(rows), None


def crawl(db_path, workers, limit, max_attempts, only_level, only_country, retry_failed):
    connection = init_db(db_path)
    clauses = ["status = 'pending'"]
    if retry_failed:
        clauses.append("attempts < ?")
    params = [max_attempts]
    if only_country:
        clauses.append("country IN (%s)" % ",".join("?" for _ in only_country))
        params.extend(only_country)
    query = "SELECT competition_id FROM competitions WHERE %s ORDER BY start_date, competition_id" % " AND ".join(clauses)
    if limit:
        query += f" LIMIT {int(limit)}"
    todo = [row[0] for row in connection.execute(query, params)]
    if only_level:
        wanted = set(only_level)
        filtered = []
        for competition_id in todo:
            if classify_level(competition_context(connection, competition_id)) in wanted:
                filtered.append(competition_id)
        todo = filtered
    print(f"Crawling {len(todo)} competitions with {workers} workers -> {db_path}", flush=True)
    if not todo:
        return

    state = {"done": 0, "rows": 0, "errors": 0}
    lock = threading.Lock()
    started = time.time()

    def worker(competition_id):
        local = thread_connection(db_path)
        try:
            rows, _ = ingest_competition(local, competition_id)
        except Exception as exc:  # noqa: BLE001 - one bad competition must not kill the run
            try:
                with WRITE_LOCK:
                    local.execute("UPDATE competitions SET attempts=attempts+1, error=? WHERE competition_id=?",
                                  (f"{type(exc).__name__}: {exc}"[:300], competition_id))
                    local.commit()
            except sqlite3.Error:
                pass
            with lock:
                state["errors"] += 1
                state["done"] += 1
            return
        with lock:
            state["done"] += 1
            state["rows"] += rows
            done = state["done"]
            if done % 250 == 0 or done == len(todo):
                elapsed = time.time() - started
                rate = done / elapsed if elapsed else 0
                eta = (len(todo) - done) / rate if rate else 0
                print(f"  {done}/{len(todo)} comps  rows={state['rows']}  errors={state['errors']}  "
                      f"{rate:.2f} comp/s  eta={eta/60:.1f} min", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, todo))
    elapsed = time.time() - started
    print(f"Crawl finished in {elapsed/60:.1f} min: {state['rows']} result rows, {state['errors']} errors.")
    connection.close()


# --------------------------------------------------------------------------- #
# Reporting / export
# --------------------------------------------------------------------------- #

def report(connection, empty_sample=0):
    total = connection.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    athletes = connection.execute("SELECT COUNT(DISTINCT athlete_id) FROM results").fetchone()[0]
    print(f"results rows        : {total:,}")
    print(f"distinct athletes   : {athletes:,}")
    for status, count in connection.execute(
        "SELECT status, COUNT(*) FROM competitions GROUP BY status ORDER BY 2 DESC"
    ):
        print(f"competitions {status:<8}: {count:,}")

    print("\nlevel tiers (rows are the population fit tier unless 'international'):")
    for level, rows, comps in connection.execute(
        "SELECT r.level, COUNT(*), COUNT(DISTINCT r.competition_id) FROM results r GROUP BY r.level ORDER BY 2 DESC"
    ):
        share = "fit" if level != "international" else "validation only"
        print(f"  {level:<14} comps={comps:6,}  results={rows:>9,}  ({share})")

    print("\nDOB fill rate per country (rows with exact DOB / flat-event rows seen), top 15:")
    for country, seen, dob, comps in connection.execute(
        "SELECT country, SUM(rows_seen), SUM(rows_with_dob), COUNT(*) FROM competitions "
        "WHERE status IN ('ok','empty') GROUP BY country HAVING SUM(rows_seen) > 0 ORDER BY SUM(rows_seen) DESC LIMIT 15"
    ):
        print(f"  {country}  comps={comps:5,}  rows_seen={seen:>8,}  dob={100.0*dob/seen:5.1f}%")

    print("\ncohort sizes (event/sex/surface), all cohorts:")
    for event_id, sex, surface, count in connection.execute(
        "SELECT event_id, sex, surface, COUNT(*) FROM results GROUP BY 1,2,3 ORDER BY 1,2,3"
    ):
        print(f"  {event_id:>6}  {sex:<6} {surface:<15} {count:>8,}")

    print("\nage distribution at race date (whole years):")
    rows = list(connection.execute(
        "SELECT CAST((julianday(race_date) - julianday(birth_date)) / 365.25 AS INTEGER) AS age, COUNT(*) "
        "FROM results GROUP BY age ORDER BY age"
    ))
    peak = max((count for _, count in rows), default=1)
    for age, count in rows:
        bar = "#" * min(60, int(60 * count / peak))
        print(f"  {age:>3}  {count:>8,}  {bar}")

    if empty_sample:
        print(f"\nsample of {empty_sample} 'empty' competitions (no usable flat-distance row):")
        for competition_id, name, country, rows_seen, rows_with_dob in connection.execute(
            "SELECT competition_id, name, country, rows_seen, rows_with_dob FROM competitions "
            "WHERE status='empty' ORDER BY rows_seen DESC LIMIT ?", (empty_sample,)
        ):
            print(f"  {competition_id:>7} {country} seen={rows_seen:>5} dob={rows_with_dob:>5}  {name[:60]}")


def export_csv(connection, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = ["event_id", "sex", "birth_date", "race_date", "seconds", "surface", "timing_method",
               "athlete_id", "wind", "remark", "place", "country", "group_id", "level", "season_year",
               "source", "source_url"]
    query = "SELECT %s FROM results ORDER BY event_id, sex, surface, birth_date" % ",".join(columns)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        while True:
            chunk = connection.execute(query).fetchmany(50000)
            if not chunk:
                break
            writer.writerows(chunk)
    print(f"Exported {output}")


def reset_failed(connection, limit=None):
    connection.execute("UPDATE competitions SET status='pending', error=NULL WHERE status='error'"
                       + (f" AND attempts < {int(limit)}" if limit else ""))
    connection.commit()
    print("Reset failed competitions to pending.")


def selftest():
    """Guard against the silent-failure mode where every event is rejected."""
    cases = [
        ("Men's 100 Metres", "100m", "outdoor-track"),
        ("Women's 200 Metres", "200m", "outdoor-track"),
        ("Women's 400 Metres Hurdles", None, None),
        ("Men's 3000 Metres Steeplechase", None, None),
        ("Men's 4x100 Metres Relay", None, None),
        ("Men's 60 Metres (i)", "60m", "indoor-track"),
        ("Women's 60 Metres Short Track", "60m", "indoor-track"),
        ("Women's Shot Put (5kg)", None, None),
        ("Men's 1500 Metres (Mixed)", "1500m", "outdoor-track"),
        ("Women's 10,000 Metres", "10k", "outdoor-track"),
        ("Women's 5 Kilometres Road Race", "5k", "road"),
        ("Men's Mile", "mile", "road"),
        ("Women's 800 Metres", "800m", "outdoor-track"),
        ("Women's 10 Kilometres Cross Country", "10k", "cross-country"),
    ]
    failures = 0
    for title, expected_id, expected_surface in cases:
        event_id, _ = normalize_event(title)
        surface = infer_surface(title, "", [], event_id) if event_id else None
        if event_id != expected_id or (expected_id and surface != expected_surface):
            failures += 1
            print(f"  FAIL event  {title!r}: id={event_id} surface={surface} (want {expected_id}/{expected_surface})")
    marks = [("10.63", 10.63), ("1:52.03", 112.03), ("9:58.72", 598.72), ("4:03.11", 243.11),
             ("DNS", None), ("DNF", None), ("DQ", None), ("NR", None), ("NM", None), ("PB", None), ("", None)]
    for text, expected in marks:
        actual = parse_mark(text)
        if expected is None:
            ok = actual is None
        else:
            ok = actual is not None and abs(actual - expected) < 1e-6
        if not ok:
            failures += 1
            print(f"  FAIL mark   {text!r}: got {actual} want {expected}")
    dates = [("2006-06-29", "2006-06-29"), ("29 JUN 2006", "2006-06-29"), ("29 June 2006", "2006-06-29"),
             ("garbage", None), (None, None), ("", None)]
    for text, expected in dates:
        actual = normalize_date(text)
        if actual != expected:
            failures += 1
            print(f"  FAIL date   {text!r}: got {actual} want {expected}")
    levels = [
        (["World Athletics U20 Championships"], "international"),
        (["National Senior Outdoor Championships"], "national"),
        (["Area U18 Championships"], "regional"),
        (["Area Permit Outdoor Meetings"], "permit"),
        (["Wanda Diamond League Meeting"], "meet"),
        (["World Athletics U20 Championships", "Area Regional Senior Championships"], "international"),
    ]
    for names, expected in levels:
        actual = classify_level(names)
        if actual != expected:
            failures += 1
            print(f"  FAIL level  {names}: got {actual} want {expected}")
    countries = [("Oslo (NOR)", "NOR"), ("São Paulo (BRA)", "BRA"), ("", "ZZZ"), ("Nowhere", "ZZZ")]
    for venue, expected in countries:
        actual = country_from_venue(venue)
        if actual != expected:
            failures += 1
            print(f"  FAIL venue  {venue!r}: got {actual} want {expected}")
    print(f"selftest: {'OK' if failures == 0 else str(failures) + ' FAILURES'}")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--max-attempts", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="crawl at most N pending competitions (smoke tests)")
    parser.add_argument("--levels", default="", help="restrict crawl to these level tiers")
    parser.add_argument("--countries", default="", help="restrict crawl to these country codes")
    parser.add_argument("--no-retry-failed", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--enumerate", action="store_true")
    parser.add_argument("--crawl", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--empty-sample", type=int, default=0)
    parser.add_argument("--export-csv", type=Path)
    parser.add_argument("--reset-failed", type=int, default=0, metavar="MAX_ATTEMPTS")
    arguments = parser.parse_args()

    if arguments.reset_failed:
        reset_failed(init_db(arguments.db), arguments.reset_failed or None)
        if not any([arguments.enumerate, arguments.crawl, arguments.report, arguments.export_csv]):
            return
    if arguments.selftest:
        sys.exit(1 if selftest() else 0)
    if not any([arguments.enumerate, arguments.crawl, arguments.report, arguments.export_csv]):
        parser.error("nothing to do: pass --selftest, --enumerate, --crawl, --report or --export-csv")

    connection = init_db(arguments.db)
    if arguments.enumerate:
        enumerate_calendar(connection, arguments.start_date, arguments.end_date)
    if arguments.crawl:
        crawl(arguments.db, arguments.workers, arguments.limit, arguments.max_attempts,
              [value for value in arguments.levels.split(",") if value],
              [value.strip().upper() for value in arguments.countries.split(",") if value],
              not arguments.no_retry_failed)
        connection = init_db(arguments.db)
    if arguments.report:
        report(connection, arguments.empty_sample)
    if arguments.export_csv:
        export_csv(connection, arguments.export_csv)
    connection.close()


if __name__ == "__main__":
    main()