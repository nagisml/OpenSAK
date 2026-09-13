#!/usr/bin/env python3
"""
scripts/migrate_gsak_filters.py — convert saved GSAK filters into OpenSAK
filter profiles (.json).

GSAK stores every saved filter as one row in ``gsak.db3``::

    TranslateFilters(Type, Description, Data)
        Type        'FI' for a filter
        Description the filter's name as shown in GSAK
        Data        the whole filter dialog, serialised (see _parse_blob)

OpenSAK stores one JSON file per filter profile (see
``opensak.filters.engine.FilterProfile``)::

    {"name": ..., "filterset": {"mode": "AND", "filters": [...]}, "sort": {...}}

OpenSAK's filter GUI covers far less than GSAK's filter dialog, but its
``where_clause`` filter runs arbitrary SQL against the whole OpenSAK schema
(``SELECT id FROM caches WHERE (<sql>)``, so sub-selects on ``logs``,
``waypoints``, ``attributes``, ``trackables`` and ``user_notes`` are all
legal). So the rule here is: **anything the GUI cannot express becomes a
where_clause**, and a criterion is only reported as unsupported when OpenSAK
genuinely has nowhere to store the data.

What that covers, none of which any GUI filter can do: the Logs tab (types,
finder, dates, counts), the Unterwegspunkte tab (waypoint count, type, name,
code, comments, date, created-by-user), polygon area selections (translated
into a real point-in-polygon test, not just a bounding box), the watch list,
elevation / user-sort / bearing / compass quadrants, hidden / created /
changed / last-GPX / last-user / last-found dates including GSAK's rolling
"Während" windows, TB-and-coin names, source, my-found count, and every text
comparison beyond plain "contains".

What is still reported instead of translated, and why:
  * user-defined GSAK columns (the ``Custom`` table) — OpenSAK stores none,
    so there is no column for SQL to test;
  * regular-expression criteria — OpenSAK registers no REGEXP operator on its
    SQLite connection;
  * GSAK's Waymark / L&F Celebration cache types — the importer files them
    under "Unknown Cache", where nothing can tell them apart;
  * GSAK's "reverse filter", watch-list-only fields such as the GPX symbol
    name, ``FavPerc`` and ``LabId`` — no OpenSAK column exists.

Everything that could not be translated is preserved in an extra ``_gsak``
block inside each file (JSON has no comments); OpenSAK ignores both unknown
top-level keys and unknown ``filter_type`` values on load, so the written
files stay fully loadable.

Usage:
    python scripts/migrate_gsak_filters.py \
        ~/AppData/Roaming/gsak/gsak.db3 ~/AppData/Roaming/opensak/filters

    # preview without writing anything
    python scripts/migrate_gsak_filters.py gsak.db3 out/ --dry-run

    # only some filters, overwrite existing files, keep the raw GSAK blob
    python scripts/migrate_gsak_filters.py gsak.db3 out/ \
        --only NotFound --only "Export SearchCH" --overwrite --embed-raw

    # let GSAK's isOwner in a saved where clause translate to an owner test
    python scripts/migrate_gsak_filters.py gsak.db3 out/ --me Vyrembi

Confidence: every decoding marked "ASSUMED" below was inferred from real
filter data, not from GSAK documentation. Each assumption that actually
affected a written file is listed in that file's ``_gsak.assumptions``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# ── GSAK → OpenSAK vocabularies ──────────────────────────────────────────────

# GSAK filter-dialog cache-type labels → OpenSAK cache_type string, routed
# through GSAK's one-letter codes so this stays consistent with
# GSAK_CACHE_TYPE_MAP in opensak/importer/gsak_importer.py (the authoritative
# mapping). None = GSAK has the type but OpenSAK has no equivalent.
GSAK_TYPE_LABEL_TO_OSAK: dict[str, Optional[str]] = {
    "Traditional":     "Traditional Cache",
    "Multi":           "Multi-cache",
    "Mystery":         "Unknown Cache",
    "Letterbox":       "Letterbox Hybrid",
    "Wherigo":         "Wherigo Cache",
    "Earth":           "Earthcache",
    "Virtual":         "Virtual Cache",
    "Webcam":          "Webcam Cache",
    "Event":           "Event Cache",
    "CITO":            "Cache In Trash Out Event",
    "Mega Event":      "Mega-Event Cache",
    "Giga Event":      "Giga-Event Cache",
    "L&F Event":       "Community Celebration Event",
    "Groundspeak HQ":  "Geocaching HQ Cache",
    "Block Party":     "Geocaching HQ Block Party",
    "Maze Exhibit":    "GPS Adventures Maze",
    "Project APE":     "Project A.P.E. Cache",
    "Lab Cache":       "Lab Cache",
    "Locationless":    "Locationless (Reverse) Cache",
    "Benchmark":       "Benchmark",   # not in CACHE_TYPES, but the GSAK importer
    "Other":           "Other",       # writes both of these verbatim
    "Waymark":         None,          # no OpenSAK equivalent
    "L&F Celebration": None,          # GSAK code D — deliberately unmapped
}

# GSAK container checkbox key → OpenSAK CONTAINER_SIZES value.
# Virtual/Unknown are GSAK placeholders for "no physical container" and both
# collapse to "Not chosen" on import (GSAK_CONTAINER_MAP in gsak_importer.py),
# so selecting either in GSAK cannot be reproduced exactly.
GSAK_CONTAINER_KEYS: dict[str, str] = {
    "cbxMicro":     "Micro",
    "cbxSmall":     "Small",
    "cbxRegular":   "Regular",
    "cbxLarge":     "Large",
    "cbxOther":     "Other",
    "cbxNotChosen": "Not chosen",
    "cbxVirtual":   "Not chosen",   # collapses — see above
    "chkUnknown":   "Not chosen",   # collapses — see above
}
_COLLAPSED_CONTAINERS = {"cbxVirtual", "chkUnknown"}

# Text-comparison combo (cbxDesc/Geocache-Name, cbxOwnerName/Owner-Name,
# cbxCountry/Land, …). Item order read off the GSAK dialog (German UI):
# Enthält, Enthält nicht, Gleich, Ungleich, Leer, Nicht leer, In Liste,
# RegEx, Nicht(RegExp).
#
# Note there is no "off" entry: index 0 ("contains") is the default, and a
# text criterion counts as active whenever its edit box is non-empty — or,
# for empty/not-empty, on the operator alone.
TEXT_OP: dict[int, str] = {
    0: "contains",
    1: "not_contains",
    2: "equals",
    3: "not_equals",
    4: "empty",
    5: "not_empty",
    6: "in_list",
    7: "regex",
    8: "not_regex",
}
# Operators that need no value in the edit box.
TEXT_OP_VALUELESS = {"empty", "not_empty"}
# GSAK's "Quelle"/"Symbolname" combos use a different item list (they open on
# "Gleich"), but neither field has an OpenSAK column, so they are only ever
# reported — never decoded through this table.

# Numeric-comparison combo (cbxFavorite/Favoritenpunkte, cbxLogCount, …):
# Beliebig, Kleiner gleich, Größer gleich, Gleich, Zwischen (Inklusive).
NUM_OP: dict[int, Optional[str]] = {
    0: None,            # "Beliebig" = criterion off
    1: "at_most",
    2: "at_least",
    3: "equals",
    4: "between",
}

# Difficulty/Terrain use the same operators minus "Beliebig" — the dialog
# opens on "Kleiner gleich" with the value 5.0, which is how "no D/T
# restriction" is stored. ASSUMED order (the dialog only ever shows the
# collapsed combo); a criterion is therefore only written out when it
# actually narrows the 1.0–5.0 range.
DT_OP: dict[int, str] = {
    0: "at_most",
    1: "at_least",
    2: "equals",
    3: "between",
}

# Date combos (cbxFound/Letztes Funddatum, cbxDNFDate/DNF-Datum, …):
# Am oder vor, Am oder nach, Gleich, Zwischen (Inklusive), Während,
# Nicht während, Verglichen mit, Beliebig.
DATE_OP: dict[int, Optional[str]] = {
    0: "on_or_before",
    1: "on_or_after",
    2: "equals",
    3: "between",
    4: "during",          # rolling window — no OpenSAK equivalent
    5: "not_during",      # rolling window — no OpenSAK equivalent
    6: "compared_with",   # compares two date columns — no OpenSAK equivalent
    7: None,              # "Beliebig" = criterion off
}

# The "Logs" and "Unterwegspunkte" tabs have their own date combos (cbxLogDate,
# cbxcDate) whose stored value is 6 in every saved filter — including filters
# whose date boxes clearly are not meant to restrict anything ("Noch nie
# gefunden", "Letzte 2 DNF"), and including ones that do use the rest of the
# tab. There is nothing for a log/waypoint date to be "compared with", so
# ASSUMED: these two combos simply lack the "Verglichen mit" entry, which puts
# their "Beliebig" at index 6 instead of 7.
DATE_OP_NO_COMPARE: dict[int, Optional[str]] = {
    0: "on_or_before",
    1: "on_or_after",
    2: "equals",
    3: "between",
    4: "during",
    5: "not_during",
    6: None,          # "Beliebig" = criterion off
}

# "Mein Funddatum" (cbxUserFound) does NOT use the list above: its stored value
# is 0 in every saved filter while every other date combo in the same blob
# holds 7 ("Beliebig"), and filters that store 0 also tick both "Gefunden" and
# "Nicht gefunden" — which would contradict any my-found-date restriction. So 0
# is this combo's "off" state and the date boxes beside it are stale UI values.
# The remaining indices are unknown and are reported rather than guessed.
DATE_OP_MY_FOUND: dict[int, Optional[str]] = {0: None}


def _dt_value(index: int) -> float:
    """GSAK D/T value combo index → rating: index 0 = 1.0 … index 8 = 5.0."""
    return 1.0 + 0.5 * index


# ── GSAK where-clause → OpenSAK SQL ──────────────────────────────────────────

# Column renames, applied case-insensitively on word boundaries. OpenSAK's
# WhereClauseFilter runs "SELECT id FROM caches WHERE (<sql>)", so scalar
# subqueries against the related tables are legal.
WHERE_COLUMN_MAP: dict[str, str] = {
    # ── Caches table ────────────────────────────────────────────────────────
    "code":          "gc_code",
    "cachetype":     "cache_type",
    "placedby":      "placed_by",
    "ownername":     "owner_name",
    "ownerid":       "owner_id",
    "placeddate":    "hidden_date",
    "favpoints":     "favorite_points",
    "userflag":      "user_flag",
    "usersort":      "user_sort",
    "userdata":      "user_data_1",
    "user2":         "user_data_2",
    "user3":         "user_data_3",
    "user4":         "user_data_4",
    "userdate":      "found_date",
    "founddate":     "found_date",
    "foundbymedate": "found_date",
    "lastfounddate": "last_found_date",
    "dnfdate":       "dnf_date",
    "ftf":           "first_to_find",
    "lock":          "locked",
    "ispremium":     "premium_only",
    "pmonly":        "premium_only",
    "cacheid":       "gc_cache_id",
    "gcnote":        "gc_note",
    "lastgpxdate":   "last_gpx_update",
    "changed":       "last_updated",
    "created":       "imported_at",
    "lastlogdate":   "last_log_date",
    "longdescription":  "long_description",
    "shortdescription": "short_description",
    "hints":         "encoded_hints",
    "tbcount":       "trackable_count",
    "numlogs":       "log_count",
    "foundcount":    "found_log_count",
    # GSAK keeps these on the cache row itself (empty string / 0 when unset);
    # OpenSAK keeps them in user_notes and simply has no row — hence coalesce,
    # so "= ''" style GSAK tests keep working.
    "hascorrected":  "coalesce((SELECT is_corrected FROM user_notes WHERE user_notes.cache_id = caches.id), 0)",
    "usernote":      "coalesce((SELECT note FROM user_notes WHERE user_notes.cache_id = caches.id), '')",
    "userlat":       "(SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = caches.id)",
    "userlon":       "(SELECT corrected_lon FROM user_notes WHERE user_notes.cache_id = caches.id)",
    "lastuserdate":  "(SELECT updated_at FROM user_notes WHERE user_notes.cache_id = caches.id)",
    # Identical names, listed so they are not reported as unknown identifiers.
    "name":          "name",
    "container":     "container",
    "difficulty":    "difficulty",
    "terrain":       "terrain",
    "latitude":      "latitude",
    "longitude":     "longitude",
    "latoriginal":   "latitude",
    "lonoriginal":   "longitude",
    "elevation":     "elevation",
    "country":       "country",
    "state":         "state",
    "county":        "county",
    "found":         "found",
    "dnf":           "dnf",
    "archived":      "archived",
    "watch":         "watch",
    "color":         "color",
    "guid":          "guid",
    "bearing":       "bearing",
    "distance":      "distance",   # OpenSAK rewrites this to its haversine UDF
    "url":           "url",
}

# GSAK sub-table columns → their OpenSAK counterparts.
#
# GSAK links its child tables to the parent cache by GC CODE (Logs.lParent,
# Waypoints.cParent, Attributes.aCode, Corrected.kCode); OpenSAK links them by
# the numeric caches.id. A rename on its own would therefore produce SQL that
# runs but matches nothing, so every rename below is followed by
# _relink_code_joins(), which rewrites GSAK's code-based joins into id-based
# ones.
WHERE_SUBTABLE_MAP: dict[str, str] = {
    # LOGS
    "lparent":   "cache_id",
    "llogid":    "log_id",
    "ltype":     "log_type",
    "ldate":     "log_date",
    "lby":       "finder",
    "lownerid":  "finder_id",
    "ltext":     "text",
    "lencoded":  "text_encoded",
    # WAYPOINTS
    "cparent":   "cache_id",
    "ccode":     "wp_code",
    "cprefix":   "prefix",
    "cname":     "name",
    "ctype":     "wp_type",
    "cdate":     "wp_date",
    "clat":      "latitude",
    "clon":      "longitude",
    "ccomment":  "comment",
    "cbyuser":   "created_by_user",
    "cflag":     "wp_flag",
    "curl":      "url",
    # ATTRIBUTES
    "acode":     "cache_id",
    "aid":       "attribute_id",
    "ainc":      "is_on",
    # CORRECTED → user_notes (see WHERE_TABLE_MAP)
    "kcode":     "cache_id",
    "kafterlat": "corrected_lat",
    "kafterlon": "corrected_lon",
}

# GSAK table names → OpenSAK table names. Logs/Waypoints/Attributes keep their
# names; only Corrected moves.
WHERE_TABLE_MAP: dict[str, str] = {
    "corrected": "user_notes",
}

# GSAK columns whose OpenSAK counterpart carries the opposite meaning.
WHERE_INVERTED: dict[str, str] = {
    "tempdisabled": "(NOT available)",
}

# Mappings that are close but not exact. They still translate (the clause stays
# "verified"), but the difference is recorded in the file's _gsak.assumptions.
WHERE_SOFT_NOTES: dict[str, str] = {
    "lastgpxdate": "GSAK's LastGPXDate (last GPX/API refresh) became OpenSAK's last_gpx_update — "
                   "the timestamp of the last import pass that touched the cache, which is the "
                   "same idea but is stamped locally, not taken from the source file",
    "changed": "GSAK's Changed (the GC.com-side listing change date) became OpenSAK's "
               "last_updated, which the GSAK importer fills from exactly that column",
    "created": "GSAK's Created (record-creation date) became OpenSAK's imported_at, which is "
               "when THIS OpenSAK database first saw the cache — not when GSAK first saw it",
    "foundbymedate": "GSAK's FoundByMeDate became OpenSAK's found_date",
    "lastuserdate": "GSAK's LastUserDate became a sub-select on OpenSAK's user_notes.updated_at "
                    "(NULL for caches that have no personal note or corrected coordinates)",
    "userdata": "GSAK's UserData became OpenSAK's user_data_1",
    "tbcount": "GSAK's tbCount became OpenSAK's trackable_count (filled in on import)",
    "numlogs": "GSAK's NumLogs became OpenSAK's cached log_count column",
    "foundcount": "GSAK's FoundCount is a 0/1 \"found by me\" flag; it became OpenSAK's "
                  "found_log_count (how many of the user's own found-type logs the cache has), "
                  "which is >= 1 for every cache GSAK would have counted",
    "hascorrected": "GSAK's HasCorrected became a sub-select on OpenSAK's user_notes.is_corrected",
    "usernote": "GSAK's UserNote became a sub-select on OpenSAK's user_notes.note",
    "latitude": "GSAK's Latitude/Longitude hold the CORRECTED position for a solved cache; "
                "OpenSAK's caches.latitude/longitude always hold the posted position and keep "
                "the correction in user_notes.corrected_lat/corrected_lon. Wrap the column in "
                "coalesce((SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = "
                "caches.id), latitude) if the clause meant the corrected position",
    "kafterlat": "GSAK's Corrected table became OpenSAK's user_notes (kCode → cache_id, "
                 "kAfterLat/kAfterLon → corrected_lat/corrected_lon)",
    "clat": "GSAK's child-waypoint coordinates (cLat/cLon) became OpenSAK's "
            "waypoints.latitude/longitude, which are NULL — not 0.0 — when unknown",
}
WHERE_SOFT_NOTES["latoriginal"] = (
    "GSAK's LatOriginal/LonOriginal (the posted position of a solved cache) became OpenSAK's "
    "caches.latitude/longitude, which always hold the posted position — the correction lives "
    "in user_notes.corrected_lat/corrected_lon"
)
WHERE_SOFT_NOTES["lonoriginal"] = WHERE_SOFT_NOTES["latoriginal"]
WHERE_SOFT_NOTES["longitude"] = WHERE_SOFT_NOTES["latitude"]
WHERE_SOFT_NOTES["kafterlon"] = WHERE_SOFT_NOTES["kafterlat"]
WHERE_SOFT_NOTES["kcode"] = WHERE_SOFT_NOTES["kafterlat"]
WHERE_SOFT_NOTES["clon"] = WHERE_SOFT_NOTES["clat"]

# GSAK identifiers with no mechanical translation — each one makes the
# translated clause "unverified" and is named in the file's _gsak block.
WHERE_UNMAPPABLE: dict[str, str] = {
    "isowner":   "GSAK's isOwner has no OpenSAK column — pass --me '<your account>' to have it "
                 "translated to owner_name = '<your account>'",
    "ltime":     "GSAK's logs.lTime is a separate time-of-day column; OpenSAK folds date and "
                 "time into logs.log_date, so compare against that instead",
    "kbeforelat": "GSAK's Corrected.kBeforeLat/kBeforeLon (the posted position of a solved "
                  "cache) became OpenSAK's caches.latitude/longitude — they are not columns of "
                  "user_notes, so this sub-select needs a join back to caches",
    "cachememo": "GSAK's CacheMemo table is split across OpenSAK's caches / user_notes columns",
    "cachesall": "GSAK's CachesAll view (all databases at once) has no OpenSAK counterpart",
    "custom":    "GSAK's Custom/CustomLocal tables (user-defined columns) have no OpenSAK "
                 "counterpart — only user_data_1–4 exist",
    "customlocal": "GSAK's Custom/CustomLocal tables (user-defined columns) have no OpenSAK "
                   "counterpart — only user_data_1–4 exist",
    "travelbugs": "GSAK's CacheMemo.TravelBugs text maps to OpenSAK's trackables table / trackable_count",
    "favperc":   "GSAK's FavPerc (favourite percentage) is not stored by OpenSAK",
    "labid":     "GSAK's LabId is not stored by OpenSAK",
    "symbol":    "GSAK's Symbol (GPX symbol name) is not stored by OpenSAK",
}
WHERE_UNMAPPABLE["kbeforelon"] = WHERE_UNMAPPABLE["kbeforelat"]

# GSAK Status ('A' active, 'T' temporarily disabled, 'X' archived) → the
# equivalent test on OpenSAK's archived / available booleans. Verified 1:1
# against the importer (gsak_importer.py: _STATUS_ARCHIVED / _STATUS_AVAILABLE).
_STATUS_SQL: dict[str, str] = {
    "A": "(available AND NOT archived)",
    "T": "(NOT available AND NOT archived)",
    "X": "archived",
}
_STATUS_CMP_RE = re.compile(r"\bstatus\b\s*(=|<>|!=)\s*'([ATX])'", re.IGNORECASE)

# GSAK macro globals and macro functions (%g_… / $g_… / g_Distance(…)) that can
# appear in a saved where clause and only resolve while a GSAK macro is running.
# GSAK's kBeforeLat/kAfterLon and friends are NOT listed here: inside a where
# clause they are columns of the Corrected table, and are mapped as such.
_MACRO_GLOBAL_RE = re.compile(r"^g_", re.IGNORECASE)

# GSAK one-letter cache-type codes as used inside where clauses.
GSAK_CODE_TO_OSAK: dict[str, Optional[str]] = {
    "T": "Traditional Cache",   "M": "Multi-cache",
    "U": "Unknown Cache",       "B": "Letterbox Hybrid",
    "W": "Webcam Cache",        "V": "Virtual Cache",
    "E": "Event Cache",         "C": "Cache In Trash Out Event",
    "R": "Earthcache",          "I": "Wherigo Cache",
    "L": "Locationless (Reverse) Cache",
    "O": "Other",               "G": "Benchmark",
    "Q": "Lab Cache",           "A": "Project A.P.E. Cache",
    "H": "Geocaching HQ Cache", "J": "Giga-Event Cache",
    "P": "Geocaching HQ Block Party",
    "X": "GPS Adventures Maze", "Z": "Mega-Event Cache",
    "F": "Community Celebration Event",
    "D": None, "Y": None,
}

# OpenSAK columns (and sub-selects) that hold numbers. GSAK's saved SQL often
# quotes boolean/integer literals ("UserFlag = '1'"); against a plain column
# SQLite's INTEGER affinity still coerces that, but against an expression —
# such as the coalesce() sub-selects above — it does not: "SELECT 0 = '0'"
# is false. So quoted numeric literals are unquoted after translation.
_NUMERIC_COLUMNS = {
    "found", "archived", "available", "premium_only", "user_flag", "locked",
    "dnf", "first_to_find", "trackable_count", "favorite_points", "user_sort",
    "difficulty", "terrain", "latitude", "longitude", "elevation",
    "is_corrected", "corrected_lat", "corrected_lon", "attribute_id", "is_on",
    "watch", "log_count", "found_log_count", "waypoint_count", "find_count",
    "bearing", "distance", "created_by_user", "wp_flag", "text_encoded",
}
_NUM_EXPR = r"(?:coalesce\((?:[^()']|\([^()]*\))*\)|[A-Za-z_][A-Za-z_0-9]*)"
_QUOTED_NUM_RE = re.compile(
    rf"(?P<expr>{_NUM_EXPR})\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*'(?P<val>-?\d+(?:\.\d+)?)'"
)
_QUOTED_NUM_REV_RE = re.compile(
    rf"'(?P<val>-?\d+(?:\.\d+)?)'\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*(?P<expr>{_NUM_EXPR})"
)


def _unquote_numeric_literals(sql: str, notes: list[str]) -> str:
    """Drop the quotes around numeric literals compared against numeric columns."""

    def _fix(m: re.Match, reverse: bool = False) -> str:
        expr = m.group("expr")
        if not any(col in expr.lower() for col in _NUMERIC_COLUMNS):
            return m.group(0)
        notes.append(
            "where: GSAK compared a numeric column against a quoted literal "
            "(e.g. \"= '1'\"); the quotes were removed so SQLite compares numbers"
        )
        val, op = m.group("val"), m.group("op")
        return f"{val} {op} {expr}" if reverse else f"{expr} {op} {val}"

    sql = _QUOTED_NUM_RE.sub(_fix, sql)
    return _QUOTED_NUM_REV_RE.sub(lambda m: _fix(m, reverse=True), sql)


_STRING_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
# Identifiers are matched together with any table qualifier ("c.hascorrected",
# not "c" and "hascorrected" separately): several GSAK columns map to a
# correlated sub-select rather than a plain column name, and pasting one of
# those in after a "c." would produce invalid SQL.
_IDENT_RE = re.compile(
    r"\b(?:(?P<qual>[A-Za-z_][A-Za-z_0-9]*)\s*\.\s*)?(?P<word>[A-Za-z_][A-Za-z_0-9]*)\b")
_CACHETYPE_IN_RE = re.compile(r"\bcachetype\b\s+(not\s+)?in\s*\(([^)]*)\)", re.IGNORECASE)
# GSAK's Latitude/Longitude hold the CORRECTED position of a solved cache and
# LatOriginal/LonOriginal the posted one; OpenSAK's caches.latitude/longitude
# are the posted position and the correction lives in user_notes. Both GSAK
# columns therefore translate to the same OpenSAK column, so a clause that
# compares the two collapses into a tautology and has to be flagged.
_GSAK_ORIGINAL_POS_RE = re.compile(r"\b(?:latoriginal|lonoriginal)\b", re.IGNORECASE)
_GSAK_CORRECTED_POS_RE = re.compile(r"\b(?:latitude|longitude)\b", re.IGNORECASE)
_CACHETYPE_CMP_RE = re.compile(r"\bcachetype\b\s*(=|<>|!=)\s*'([A-Za-z])'", re.IGNORECASE)

# SQL keywords, functions and OpenSAK table names that are never column names.
_SQL_WORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "like",
    "between", "exists", "as", "on", "join", "left", "right", "full", "cross",
    "natural", "using", "inner", "outer", "group",
    "by", "order", "having", "union", "intersect", "except", "with",
    "recursive", "values", "all", "distinct", "case", "when", "then",
    "else", "end", "asc", "desc", "limit", "offset", "date", "datetime", "now",
    "localtime", "utc", "day", "days", "month", "months", "year", "years",
    "hour", "minute", "second", "weekday", "start", "of",
    "julianday", "unixepoch", "strftime", "count", "sum", "total", "min",
    "max", "avg", "length", "instr", "iif", "printf", "format", "typeof",
    "group_concat", "glob", "escape", "random", "hex", "ltrim", "rtrim",
    "lower", "upper", "substr", "substring", "trim", "replace", "cast",
    "int", "integer", "real", "text", "decimal", "numeric", "float", "double",
    "boolean", "char", "varchar", "blob", "collate", "nocase", "binary",
    "coalesce", "ifnull", "nullif", "abs", "round", "true",
    "false", "caches", "logs", "attributes", "user_notes", "waypoints",
    "trackables",
}

# Every OpenSAK column name (plus its table names), so an identifier that is
# ALREADY an OpenSAK column — because a rewrite earlier in the pipeline
# produced it, or because the GSAK clause happened to use the same word — is
# not reported as unrecognised.
_OSAK_COLUMNS: set[str] = (
    set(WHERE_COLUMN_MAP.values())
    | set(WHERE_SUBTABLE_MAP.values())
    | {
        "id", "cache_id", "gc_code", "cache_type", "available", "archived",
        "short_desc_html", "long_desc_html", "found_date", "dnf_date",
        "first_to_find", "user_data_1", "user_data_2", "user_data_3",
        "user_data_4", "favorite_points", "gc_note", "guid", "watch",
        "gc_cache_id", "find_count", "log_count", "trackable_count",
        "found_log_count", "last_log_date", "waypoint_count",
        "parent_gc_code", "locked", "location_source", "location_basis",
        "location_updated", "location_dataset", "imported_at", "source_file",
        "last_found_date", "last_gpx_update", "last_four_logs", "last_updated",
        "is_corrected", "corrected_lat", "corrected_lon", "updated_at", "note",
        "wp_type", "wp_code", "wp_date", "wp_flag", "prefix", "comment",
        "description", "created_by_user", "log_id", "log_type", "log_date",
        "finder", "finder_id", "text_encoded", "logged_by_owner",
        "tracking_code", "ref", "attribute_id", "is_on",
    }
) - {v for v in WHERE_COLUMN_MAP.values() if not v.isidentifier()}


def _split_sql_literals(sql: str) -> list[tuple[bool, str]]:
    """Split *sql* into (is_string_literal, chunk) parts so identifier
    rewriting never touches the inside of a quoted string."""
    parts: list[tuple[bool, str]] = []
    pos = 0
    for m in _STRING_LITERAL_RE.finditer(sql):
        if m.start() > pos:
            parts.append((False, sql[pos:m.start()]))
        parts.append((True, m.group(0)))
        pos = m.end()
    if pos < len(sql):
        parts.append((False, sql[pos:]))
    return parts


_UNMAPPED_TYPE_NOTE = (
    "where: GSAK cache-type code {codes} has no OpenSAK cache type — the GSAK importer files "
    "such caches under 'Unknown Cache', where they are indistinguishable from real mystery "
    "caches, so the code was dropped from the type list rather than left as raw GSAK SQL"
)


def _expand_cachetype_codes(sql: str, warnings: list[str], notes: list[str]) -> str:
    """Rewrite GSAK's single-letter cachetype comparisons to OpenSAK names.

    Codes with no OpenSAK cache type (GSAK's D / Y) are dropped from the list
    and recorded as a note rather than a warning: leaving the raw GSAK letter
    in place would produce SQL that silently matches nothing, and the importer
    collapses those types into "Unknown Cache" anyway, so no translation can
    single them out.
    """

    def _names(codes: str) -> Optional[str]:
        out: list[str] = []
        dropped: list[str] = []
        for raw in codes.split(","):
            code = raw.strip().strip("'").upper()
            if not code:
                continue
            name = GSAK_CODE_TO_OSAK.get(code)
            if name is None:
                dropped.append(code)
                continue
            quoted = "'" + name.replace("'", "''") + "'"
            if quoted not in out:
                out.append(quoted)
        if dropped:
            notes.append(_UNMAPPED_TYPE_NOTE.format(
                codes=", ".join(f"'{c}'" for c in sorted(set(dropped)))))
        return ", ".join(out) if out else None

    def _sub_in(m: re.Match) -> str:
        names = _names(m.group(2))
        if names is None:
            # Every listed code was unmappable: "in (…)" can never be true and
            # "not in (…)" is always true.
            warnings.append(
                "where: a cachetype list held only GSAK codes with no OpenSAK equivalent — "
                "the comparison was left unchanged and needs a manual decision"
            )
            return m.group(0)
        return f"cache_type {'not ' if m.group(1) else ''}in ({names})"

    def _sub_cmp(m: re.Match) -> str:
        code, op = m.group(2).upper(), m.group(1)
        name = GSAK_CODE_TO_OSAK.get(code)
        if name is None:
            # No OpenSAK cache type can stand for this code, so the equality can
            # never hold (and its negation always does).
            notes.append(_UNMAPPED_TYPE_NOTE.format(codes=f"'{code}'"))
            return "1 = 0" if op == "=" else "1 = 1"
        return f"cache_type {op} '{name}'"

    sql = _CACHETYPE_IN_RE.sub(_sub_in, sql)
    return _CACHETYPE_CMP_RE.sub(_sub_cmp, sql)


def _expand_status(sql: str, notes: list[str]) -> str:
    """Rewrite GSAK's Status = 'A'/'T'/'X' tests onto archived / available."""

    def _sub(m: re.Match) -> str:
        expr = _STATUS_SQL[m.group(2).upper()]
        notes.append(
            "where: GSAK's Status ('A' active / 'T' temporarily disabled / 'X' archived) was "
            "rewritten onto OpenSAK's archived / available booleans, which the GSAK importer "
            "derives from exactly that column"
        )
        return expr if m.group(1) == "=" else f"NOT {expr}"

    return _STATUS_CMP_RE.sub(_sub, sql)


_ALIAS_RE = re.compile(
    r"\b(?:from|join)\s+[A-Za-z_][A-Za-z_0-9]*\s+(?:as\s+)?([A-Za-z_][A-Za-z_0-9]*)",
    re.IGNORECASE,
)


# ── GSAK code-joins → OpenSAK id-joins ───────────────────────────────────────
#
# Applied after the identifier renames, so these patterns already talk about
# OpenSAK column names (gc_code / cache_id) rather than GSAK's.
_JOIN_CODE_TO_ID_RE = re.compile(
    r"\b(?P<a>[A-Za-z_][A-Za-z_0-9]*)\.gc_code\b\s*=\s*"
    r"(?P<b>[A-Za-z_][A-Za-z_0-9]*\.)?cache_id\b", re.IGNORECASE)
_JOIN_ID_TO_CODE_RE = re.compile(
    r"\b(?P<b>[A-Za-z_][A-Za-z_0-9]*\.)?cache_id\b\s*=\s*"
    r"(?P<a>[A-Za-z_][A-Za-z_0-9]*)\.gc_code\b", re.IGNORECASE)
_IN_SUBSELECT_RE = re.compile(
    r"(?P<qual>\b[A-Za-z_][A-Za-z_0-9]*\.)?\bgc_code\b\s+(?P<neg>not\s+)?in\s*\(",
    re.IGNORECASE)

_RELINK_NOTE = (
    "where: GSAK links its Logs/Waypoints/Attributes/Corrected rows to the parent cache by GC "
    "code (lParent/cParent/aCode/kCode); OpenSAK links them by the numeric caches.id, so the "
    "code-based joins and \"code in (select …)\" tests were rewritten onto id"
)


def _closing_paren(sql: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at *open_idx*, or -1.

    Skips over single-quoted string literals so a bracket inside one is not
    mistaken for structure.
    """
    depth = 0
    i = open_idx
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            i += 1
            while i < len(sql):
                if sql[i] == "'":
                    if i + 1 < len(sql) and sql[i + 1] == "'":
                        i += 2
                        continue
                    break
                i += 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _selects_cache_id(subselect: str) -> bool:
    """True when *subselect* is a SELECT whose result column is a cache_id."""
    body = subselect.strip()
    if not re.match(r"(?is)^select\b", body):
        return False
    # Only the select list matters — a cache_id in the WHERE part is a join
    # predicate, not the value the outer "in" is compared against.
    m = re.search(r"(?is)\bfrom\b", body)
    select_list = body[6:m.start()] if m else body[6:]
    return re.search(r"\bcache_id\b", select_list, re.IGNORECASE) is not None


def _relink_code_joins(sql: str, notes: list[str]) -> str:
    """Turn GSAK's GC-code joins into OpenSAK's numeric-id joins."""
    hit = False

    def _sub_join(m: re.Match) -> str:
        nonlocal hit
        hit = True
        return f"{m.group('a')}.id = {m.group('b') or ''}cache_id"

    def _sub_join_rev(m: re.Match) -> str:
        nonlocal hit
        hit = True
        return f"{m.group('b') or ''}cache_id = {m.group('a')}.id"

    sql = _JOIN_CODE_TO_ID_RE.sub(_sub_join, sql)
    sql = _JOIN_ID_TO_CODE_RE.sub(_sub_join_rev, sql)

    # "code [not] in (select lParent …)" → "id [not] in (select cache_id …)".
    out: list[str] = []
    pos = 0
    while True:
        m = _IN_SUBSELECT_RE.search(sql, pos)
        if not m:
            break
        open_idx = m.end() - 1
        close_idx = _closing_paren(sql, open_idx)
        if close_idx < 0 or not _selects_cache_id(sql[open_idx + 1:close_idx]):
            out.append(sql[pos:m.end()])
            pos = m.end()
            continue
        hit = True
        out.append(sql[pos:m.start()])
        out.append(f"{m.group('qual') or ''}id {m.group('neg') or ''}in (")
        pos = m.end()
    out.append(sql[pos:])
    sql = "".join(out)

    if hit:
        notes.append(_RELINK_NOTE)
    return sql


def _table_aliases(sql: str) -> set[str]:
    """Collect table aliases (``from caches c``, ``join logs l``) plus every
    identifier used as a qualifier (``c.name``) so they are not mistaken for
    column names."""
    aliases = {m.group(1).lower() for m in _ALIAS_RE.finditer(sql)}
    aliases -= _SQL_WORDS
    aliases |= {m.group(1).lower()
                for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\.", sql)}
    return aliases


def translate_where(sql: str, custom_columns: Optional[list[str]] = None,
                    me: Optional[str] = None
                    ) -> tuple[str, list[str], list[str], bool]:
    """Best-effort translation of a GSAK where clause into OpenSAK SQL.

    *custom_columns* are the user-defined GSAK column names of the same filter
    (from its ``*custom*`` section); they are reported by name rather than as
    anonymous unknown identifiers. *me* is the caller's geocaching.com account
    name (``--me``), used to translate GSAK's ``isOwner``.

    Returns (translated_sql, warnings, notes, verified). ``verified`` is False
    when anything was left untranslated; ``notes`` are inexact-but-usable
    mappings that do not block verification.
    """
    warnings: list[str] = []
    notes: list[str] = []
    sql = _expand_cachetype_codes(sql, warnings, notes)
    sql = _expand_status(sql, notes)
    aliases = _table_aliases(sql)
    custom = {name.lower() for name in (custom_columns or [])}

    out: list[str] = []
    for is_literal, chunk in _split_sql_literals(sql):
        if is_literal:
            out.append(chunk)
            continue

        def _sub_ident(m: re.Match) -> str:
            qual, word = m.group("qual"), m.group("word")
            prefix = f"{qual}." if qual else ""

            def _keep(mapped: str) -> str:
                """Re-attach the table qualifier — unless the mapping is an
                expression rather than a bare column name, in which case the
                qualifier cannot survive."""
                if mapped.isidentifier():
                    return prefix + mapped
                if qual:
                    notes.append(
                        f"where: '{prefix}{word}' maps to a sub-select on OpenSAK's user_notes, "
                        f"which cannot carry the '{qual}' table qualifier — the sub-select is "
                        f"correlated to the outer caches row instead, so check the logic if "
                        f"'{qual}' was not that outer caches table"
                    )
                return mapped

            low = word.lower()
            if low in _SQL_WORDS or low in aliases:
                return prefix + word
            if low in custom:
                warnings.append(
                    f"where: '{word}' is a user-defined GSAK column — OpenSAK has no custom "
                    f"columns (only user_data_1–4)"
                )
                return prefix + word
            if _MACRO_GLOBAL_RE.match(word):
                warnings.append(
                    f"where: '{word}' is a GSAK macro variable resolved at run time — "
                    f"substitute a literal value"
                )
                return prefix + word
            if low == "isowner":
                if me:
                    notes.append(
                        f"where: GSAK's isOwner was translated to an owner_name test against "
                        f"--me {me!r}"
                    )
                    return f"lower(coalesce(owner_name, '')) = '{_escape_sql(me.lower())}'"
                warnings.append(f"where: {WHERE_UNMAPPABLE['isowner']}")
                return prefix + word
            if low in WHERE_INVERTED:
                warnings.append(
                    f"where: '{word}' → {WHERE_INVERTED[low]} — inverted meaning, verify the logic"
                )
                return WHERE_INVERTED[low]
            if low in WHERE_UNMAPPABLE:
                warnings.append(f"where: {WHERE_UNMAPPABLE[low]}")
                return prefix + word
            mapped = (WHERE_COLUMN_MAP.get(low)
                      or WHERE_SUBTABLE_MAP.get(low)
                      or WHERE_TABLE_MAP.get(low))
            if mapped is not None:
                if low in WHERE_SOFT_NOTES:
                    notes.append("where: " + WHERE_SOFT_NOTES[low])
                return _keep(mapped)
            if low in _OSAK_COLUMNS:
                return prefix + word   # already an OpenSAK column
            # Anything still unrecognised is left alone and reported.
            warnings.append(f"where: unrecognised identifier '{word}' left unchanged")
            return prefix + word

        out.append(_IDENT_RE.sub(_sub_ident, chunk))

    if _GSAK_ORIGINAL_POS_RE.search(sql) and _GSAK_CORRECTED_POS_RE.search(
            _GSAK_ORIGINAL_POS_RE.sub("", sql)):
        warnings.append(
            "where: the clause compares GSAK's Latitude/Longitude (the CORRECTED position of a "
            "solved cache) against LatOriginal/LonOriginal (the posted one). OpenSAK's "
            "caches.latitude/longitude ARE the posted position and keep the correction in "
            "user_notes, so both GSAK columns translate to the same OpenSAK column and the "
            "comparison collapses into a tautology — rewrite the corrected side as "
            "coalesce((SELECT corrected_lat FROM user_notes WHERE user_notes.cache_id = "
            "caches.id), latitude)"
        )

    sql = _relink_code_joins("".join(out), notes)
    sql = _unquote_numeric_literals(sql, notes)
    return sql, _dedupe(warnings), _dedupe(notes), not warnings


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# ── GSAK blob parsing ────────────────────────────────────────────────────────

class GsakFilter:
    """One parsed GSAK filter blob."""

    def __init__(self, name: str, raw: str):
        self.name = name
        self.raw = raw
        self.kv: dict[str, str] = {}
        self.custom: list[str] = []   # *custom* section, one line per criterion
        self.where: str = ""          # *where* section, verbatim GSAK SQL

    def text(self, key: str) -> str:
        return self.kv.get(key, "").strip()

    def flag(self, key: str, default: Optional[bool] = None) -> Optional[bool]:
        v = self.kv.get(key)
        if v is None:
            return default
        return v.strip().lower() == "true"

    def num(self, key: str) -> Optional[int]:
        v = self.text(key)
        if not v:
            return None
        try:
            return int(float(v))
        except ValueError:
            return None


def _parse_blob(name: str, data: str) -> GsakFilter:
    """Parse a TranslateFilters.Data blob.

    Layout (CRLF separated):
        key=value lines, one per dialog control
        [ArcFilter=~lat,lon~lat,lon…]        polygon / arc selection
        [<Cache type label>=True|False]      one line per cache type
        [*custom*]                           user-defined column criteria follow
        [*where*=<SQL>]                      raw SQL, runs to the end of the blob
    """
    f = GsakFilter(name, data)
    body, sep, where = data.partition("*where*=")
    if sep:
        f.where = where.strip()

    in_custom = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.strip() == "*custom*":
            in_custom = True
            continue
        if not line.strip():
            continue
        if in_custom:
            f.custom.append(line.strip())
            continue
        key, eq, value = line.partition("=")
        if eq:
            f.kv[key.strip()] = value
    return f


def delphi_date(serial: str) -> Optional[datetime]:
    """Convert a Delphi TDateTime serial (days since 1899-12-30) to a datetime."""
    try:
        value = float(serial.strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return datetime(1899, 12, 30) + timedelta(days=value)


# ── Conversion ───────────────────────────────────────────────────────────────

class Conversion:
    """Result of converting one GSAK filter."""

    def __init__(self, name: str):
        self.name = name
        self.filters: list[dict[str, Any]] = []
        self.assumptions: list[str] = []
        self.unsupported: list[str] = []
        self.open_points: list[str] = []
        self.where_note: Optional[dict[str, Any]] = None

    def add(self, f: dict[str, Any]) -> None:
        self.filters.append(f)

    def add_where(self, sql: str, note: str) -> None:
        """Add a where_clause filter for a criterion OpenSAK's filter GUI
        cannot express, and record how it was derived."""
        self.filters.append({"filter_type": "where_clause", "sql": sql})
        self.assumptions.append(note)


class Options:
    """Everything convert() needs beyond the filter itself."""

    def __init__(self, center: Optional[tuple[float, float]] = None,
                 miles: bool = False, emit_where: str = "verified",
                 me: Optional[str] = None):
        self.center = center
        self.miles = miles
        self.emit_where = emit_where
        self.me = me


def _escape_sql(value: str) -> str:
    return value.replace("'", "''")


def convert(gf: GsakFilter, opts: Options) -> Conversion:
    """Translate one parsed GSAK filter into OpenSAK filter dicts + notes."""
    c = Conversion(gf.name)

    _convert_cache_types(gf, c)
    _convert_containers(gf, c)
    _convert_dt(gf, c)
    _convert_found(gf, c)
    _convert_availability(gf, c)
    _convert_text_fields(gf, c)
    _convert_favorites(gf, c)
    _convert_distance(gf, c, opts)
    _convert_flags(gf, c)
    _convert_text_search(gf, c)
    _convert_dates(gf, c)
    _convert_attributes(gf, c)
    _convert_polygon(gf, c, opts)
    _convert_custom(gf, c)
    _convert_log_tab(gf, c)
    _convert_child_wp_tab(gf, c)
    _convert_misc(gf, c)
    _convert_where(gf, c, opts)
    return c


def _convert_cache_types(gf: GsakFilter, c: Conversion) -> None:
    present = [k for k in GSAK_TYPE_LABEL_TO_OSAK if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present):
        return   # everything ticked = no restriction
    osak: list[str] = []
    for label in selected:
        mapped = GSAK_TYPE_LABEL_TO_OSAK[label]
        if mapped is None:
            c.unsupported.append(
                f"cache type '{label}' was selected but has no OpenSAK equivalent"
            )
        elif mapped not in osak:
            osak.append(mapped)
    if osak:
        c.add({"filter_type": "cache_type", "types": osak})
    elif selected:
        c.unsupported.append(
            "cache-type criterion dropped — none of the selected types exist in OpenSAK"
        )


def _convert_containers(gf: GsakFilter, c: Conversion) -> None:
    present = [k for k in GSAK_CONTAINER_KEYS if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present):
        return
    sizes: list[str] = []
    for key in selected:
        size = GSAK_CONTAINER_KEYS[key]
        if size not in sizes:
            sizes.append(size)
        if key in _COLLAPSED_CONTAINERS:
            c.assumptions.append(
                f"container: GSAK's '{key}' has no OpenSAK equivalent and was mapped to "
                f"'Not chosen' (same as the GSAK importer)"
            )
    if sizes:
        c.add({"filter_type": "container", "sizes": sizes})


def _convert_dt(gf: GsakFilter, c: Conversion) -> None:
    for label, op_key, v1_key, v2_key, ftype, lo_key, hi_key in (
        ("difficulty", "cbxDifficulty", "cbxDif", "cbxDif2",
         "difficulty", "min_difficulty", "max_difficulty"),
        ("terrain", "cbxTerrain", "cbxTer", "cbxTer2",
         "terrain", "min_terrain", "max_terrain"),
    ):
        raw_op = gf.num(op_key)
        i1 = gf.num(v1_key)
        if raw_op is None or i1 is None:
            continue
        op = DT_OP.get(raw_op)
        if op is None:
            c.unsupported.append(f"{label}: unknown GSAK comparison index {raw_op}")
            continue
        v1 = _dt_value(i1)
        i2 = gf.num(v2_key)
        v2 = _dt_value(i2) if i2 is not None else v1
        if op == "at_most":
            lo, hi = 1.0, v1
        elif op == "at_least":
            lo, hi = v1, 5.0
        elif op == "equals":
            lo, hi = v1, v1
        else:   # between (inclusive)
            lo, hi = min(v1, v2), max(v1, v2)
        if (lo, hi) == (1.0, 5.0):
            continue   # the dialog's reset state ("Kleiner gleich 5.0") = no restriction
        c.add({"filter_type": ftype, lo_key: lo, hi_key: hi})
        c.assumptions.append(
            f"{label}: GSAK's operator index {raw_op} was read as '{op}' → range {lo}–{hi} "
            f"(the D/T operator list is 0 = Kleiner gleich, 1 = Größer gleich, 2 = Gleich, "
            f"3 = Zwischen)"
        )


def _convert_found(gf: GsakFilter, c: Conversion) -> None:
    found, notfound = gf.flag("chkFound"), gf.flag("chkNotFound")
    if found is None or notfound is None:
        return
    if found and not notfound:
        c.add({"filter_type": "found"})
    elif notfound and not found:
        c.add({"filter_type": "not_found"})
    elif not found and not notfound:
        c.add_where(
            "1 = 0",
            "found/not found: GSAK had BOTH boxes unticked, which matches nothing. OpenSAK's "
            "GUI cannot express an always-false criterion, so it became a where_clause on the "
            "constant 1 = 0 — delete that filter if the GSAK filter was simply misconfigured"
        )


def _convert_availability(gf: GsakFilter, c: Conversion) -> None:
    avail = gf.flag("chkAvailable")
    if avail is None:
        return
    c.add({
        "filter_type": "availability",
        "show_avail": bool(avail),
        "show_unavail": bool(gf.flag("chkTempUnavailable", False)),
        "show_archived": bool(gf.flag("chkArchivedOnly", False)),
    })


def _text_criterion(gf: GsakFilter, c: Conversion, op_key: str, val_key: str,
                    ftype: Optional[str], column: str, label: str) -> None:
    """Translate one GSAK text criterion.

    *ftype* is the native OpenSAK filter to use for plain "contains"/"equals"
    matches, or None when OpenSAK has no such filter and every operator has to
    go through a where_clause on *column*.
    """
    raw_op = gf.num(op_key) or 0
    op = TEXT_OP.get(raw_op)
    value = gf.text(val_key)
    if op is None:
        if value:
            c.unsupported.append(
                f"{label}: unknown GSAK comparison index {raw_op} for value {value!r}"
            )
        return
    if not value and op not in TEXT_OP_VALUELESS:
        return   # empty edit box = criterion not set

    col = f"lower(coalesce({column}, ''))"
    needle = _escape_sql(value.lower())

    if op in ("contains", "equals") and ftype:
        # OpenSAK's native text filters are case-insensitive substring matches.
        c.add({"filter_type": ftype, "text": value})
        if op == "equals":
            c.assumptions.append(
                f"{label}: GSAK matched {value!r} exactly ('Gleich'); OpenSAK's {ftype} filter "
                f"is a substring match, so it is slightly wider"
            )
        return

    sql = {
        "contains":     f"{col} like '%{needle}%'",
        "not_contains": f"{col} not like '%{needle}%'",
        "equals":       f"{col} = '{needle}'",
        "not_equals":   f"{col} <> '{needle}'",
        "empty":        f"{col} = ''",
        "not_empty":    f"{col} <> ''",
        "in_list":      f"{col} in ("
                        + ", ".join(f"'{_escape_sql(v.strip().lower())}'"
                                    for v in value.split(";") if v.strip())
                        + ")",
    }.get(op)

    if sql is None:   # regex / not_regex
        c.unsupported.append(
            f"{label}: GSAK used a regular expression ({value!r}) — OpenSAK has no regex filter "
            f"and registers no REGEXP operator on its SQLite connection, so no where_clause "
            f"could be generated either"
        )
        return

    c.add_where(
        sql,
        f"{label}: GSAK's '{op}' comparison has no OpenSAK GUI filter, so it became a "
        f"where_clause on {column}"
    )


def _convert_text_fields(gf: GsakFilter, c: Conversion) -> None:
    _text_criterion(gf, c, "cbxDesc", "edtDesc", "name", "name", "geocache name")
    _text_criterion(gf, c, "cbxCode", "edtCode", "gc_code", "gc_code", "GC code")
    _text_criterion(gf, c, "cbxOwnerName", "edtOwnerName", "owner_name", "owner_name", "owner")
    _text_criterion(gf, c, "cbxPlacedBy", "edtPlacedBy", "placed_by", "placed_by", "placed by")
    _text_criterion(gf, c, "cbxCountry", "edtCountry", "country", "country", "country")
    _text_criterion(gf, c, "cbxState", "edtState", "state", "state", "state")
    _text_criterion(gf, c, "cbxCounty", "edtCounty", "county", "county", "county")
    # GSAK's four free-text user fields map 1:1 onto OpenSAK's user_data_1–4
    # columns, but OpenSAK has no filter for them — hence where_clause only.
    for n, (op_key, val_key) in enumerate(
        (("cbxUserData", "edtUserData"), ("cbxUser2", "EdtUser2"),
         ("cbxUser3", "edtUser3"), ("cbxUser4", "edtUser4")), start=1
    ):
        _text_criterion(gf, c, op_key, val_key, None, f"user_data_{n}", f"user data {n}")


def _convert_favorites(gf: GsakFilter, c: Conversion) -> None:
    raw_op = gf.num("cbxFavorite") or 0
    v1, v2 = gf.num("edtFavorite"), gf.num("edtFavorite2")
    if raw_op == 0 or v1 is None:
        return
    op = NUM_OP.get(raw_op)
    if op is None:
        c.unsupported.append(f"favourite points: unknown GSAK comparison index {raw_op}")
        return
    if op == "at_most":
        lo, hi = 0, v1
    elif op == "at_least":
        lo, hi = v1, 9999
    elif op == "equals":
        lo, hi = v1, v1
    else:   # between (inclusive)
        hi = v2 if v2 is not None else 9999
        lo, hi = min(v1, hi), max(v1, hi)
    c.add({"filter_type": "favorite_points", "min_pts": lo, "max_pts": hi})


def _convert_distance(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    raw_op = gf.num("cbxDistance") or 0
    if raw_op == 0:
        return
    op = NUM_OP.get(raw_op)
    if op is None:
        c.unsupported.append(f"distance: unknown GSAK comparison index {raw_op}")
        return
    v1, v2 = gf.text("edtDistance"), gf.text("edtDistance2")
    if not v1:
        return
    unit = "miles" if opts.miles else "km"
    factor = 1.609344 if opts.miles else 1.0
    try:
        d1 = float(v1) * factor
        d2 = float(v2) * factor if v2 else None
    except ValueError:
        c.unsupported.append(f"distance: could not read radius {v1!r}/{v2!r}")
        return

    if op == "at_most":
        lo_km, hi_km = 0.0, d1
    elif op == "at_least":
        lo_km, hi_km = d1, None
    elif op == "equals":
        lo_km, hi_km = d1, d1
    else:   # between (inclusive)
        if d2 is None:
            c.unsupported.append("distance: 'Zwischen' without a second value — not migrated")
            return
        lo_km, hi_km = min(d1, d2), max(d1, d2)
    # The `distance` pseudo-column is served in the OpenSAK user's OWN unit
    # (see _distance_sql), so SQL keeps GSAK's raw numbers while the native
    # filter below takes real kilometres.
    lo_raw, hi_raw = lo_km / factor, (hi_km / factor if hi_km is not None else None)
    unit_note = (
        f" The numbers are GSAK's ({unit}), and OpenSAK serves `distance` in whatever unit its "
        f"own settings use — scale them if the two do not agree."
    )

    if opts.center is not None and hi_km is not None:
        lat, lon = opts.center
        c.add({
            "filter_type": "distance",
            "lat": round(lat, 6), "lon": round(lon, 6),
            "max_km": round(hi_km, 3), "min_km": round(lo_km, 3),
            "center_state": None,
        })
        c.open_points.append(
            f"distance: GSAK stores only the radius ({v1} {unit}) and evaluates it against "
            f"whatever centre point is active at the time. OpenSAK's distance filter freezes a "
            f"lat/lon snapshot ({lat}, {lon}) instead, so this filter no longer follows the "
            f"centre point. If you want the GSAK behaviour back, replace this filter with "
            f"{{\"filter_type\": \"where_clause\", \"sql\": \"{_distance_sql(lo_raw, hi_raw)}\"}} — "
            f"OpenSAK rewrites a bare `distance` in a where_clause to a haversine call against "
            f"the ACTIVE centre point.{unit_note} OPEN POINT for OpenSAK: let a saved distance "
            f"filter reference a named centre point (DistanceFilter.center_state already exists "
            f"for the picker) so this needs no hand-written SQL."
        )
        return

    # No centre point (or an open-ended "at least" range, which the GUI's
    # distance filter cannot express) — fall back to SQL on the `distance`
    # column, which OpenSAK rewrites to a haversine call against the active
    # centre point. That is in fact closer to GSAK than the snapshot above.
    c.add_where(
        _distance_sql(lo_raw, hi_raw),
        f"distance: GSAK filtered on {v1}{'–' + v2 if v2 else ''} {unit} from its centre point"
        + ("" if opts.center is not None else " and no centre coordinate was available")
        + ". It became a where_clause on `distance`, which OpenSAK rewrites to a haversine "
          "call against the ACTIVE centre point — so, like GSAK, it follows the centre point "
          "instead of freezing a snapshot of it." + unit_note
    )


def _distance_sql(lo: float, hi: Optional[float]) -> str:
    """WHERE fragment for a distance range on OpenSAK's `distance` column.

    OpenSAK's _prepare_where_clause_filters() replaces a bare `distance` with
    `_opensak_dist(latitude, longitude)`, a haversine UDF measuring from the
    active centre point and returning the OpenSAK user's own unit — so *lo*
    and *hi* stay in the unit the GSAK filter was written in.
    """
    if hi is None:
        return f"distance >= {round(lo, 3)}"
    if lo <= 0:
        return f"distance <= {round(hi, 3)}"
    return f"distance between {round(lo, 3)} and {round(hi, 3)}"


def _tri_flag(gf: GsakFilter, yes_key: str, no_key: str) -> Optional[bool]:
    """GSAK yes/no checkbox pairs: both ticked = no restriction (None),
    only yes = True, only no = False."""
    yes, no = gf.flag(yes_key), gf.flag(no_key)
    if yes is None or no is None:
        return None
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None


def _convert_flags(gf: GsakFilter, c: Conversion) -> None:
    corrected = _tri_flag(gf, "chkCorrectYes", "chkCorrectNo")
    if corrected is True:
        c.add({"filter_type": "has_corrected"})
    elif corrected is False:
        c.add({"filter_type": "no_corrected"})

    dnf = _tri_flag(gf, "chkDNFYes", "chkDNFNo")
    if dnf is not None:
        c.add({"filter_type": "dnf", "has_dnf": dnf})

    ftf = _tri_flag(gf, "chkFtfyes", "chkFtfNo")
    if ftf is not None:
        c.add({"filter_type": "ftf", "has_ftf": ftf})

    premium = _tri_flag(gf, "chkPoYes", "chkPoNo")
    if premium is True:
        c.add({"filter_type": "premium"})
    elif premium is False:
        c.add({"filter_type": "non_premium"})

    locked = _tri_flag(gf, "chkLockYes", "chkLockNo")
    if locked is not None:
        c.add({"filter_type": "locked", "locked": locked})

    # "User-Flag: Gesetzt / Nicht gesetzt"
    flagged = _tri_flag(gf, "chkUserFlag1", "chkUserFlag2")
    if flagged is not None:
        c.add({"filter_type": "user_flag", "flagged": flagged})

    # "Enthält TB/Coin: Ja / Nein"
    trackables = _tri_flag(gf, "cbxBugs", "chkBugNo")
    if trackables is True:
        c.add({"filter_type": "has_trackable"})
    elif trackables is False:
        c.add({"filter_type": "where_clause", "sql": "coalesce(trackable_count, 0) = 0"})
        c.assumptions.append(
            "trackables: GSAK filtered to caches WITHOUT trackables; OpenSAK only has "
            "has_trackable, so this became a where_clause on trackable_count"
        )

    # "Hat Benutzernotiz: Ja / Nein"
    note = _tri_flag(gf, "chkNoteYes", "chkNoteNo")
    if note is not None:
        op = "<>" if note else "="
        c.add({
            "filter_type": "where_clause",
            "sql": (f"coalesce((SELECT note FROM user_notes "
                    f"WHERE user_notes.cache_id = caches.id), '') {op} ''"),
        })
        c.assumptions.append(
            f"user note {'present' if note else 'absent'}: OpenSAK has no note-presence filter, "
            f"so this became a where_clause on user_notes.note"
        )

    # "Geocache auf der Beobachtungsliste: Ja / Nein". OpenSAK does store this
    # (caches.watch, filled from GSAK's own Watch column by gsak_importer.py)
    # but has no GUI filter for it.
    watch = _tri_flag(gf, "chkWatchYes", "chkWatchNo")
    if watch is not None:
        c.add_where(
            f"coalesce(watch, 0) = {1 if watch else 0}",
            f"watch list: GSAK filtered to caches {'on' if watch else 'not on'} your watch "
            f"list; OpenSAK stores that in caches.watch (imported from GSAK's Watch column) "
            f"but has no GUI filter for it, so it became a where_clause"
        )

    # "Unterwegspunkte: Ja / Nein" (child waypoints present)
    child = _tri_flag(gf, "chkChildYes", "chkChildNo")
    if child is not None:
        c.add({
            "filter_type": "where_clause",
            "sql": (("EXISTS" if child else "NOT EXISTS")
                    + " (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id)"),
        })
        c.assumptions.append(
            f"child waypoints {'present' if child else 'absent'}: OpenSAK has no waypoint "
            f"filter, so this became a where_clause on the waypoints table"
        )


def _convert_text_search(gf: GsakFilter, c: Conversion) -> None:
    text = gf.text("edtFull")
    if not text:
        return
    if gf.flag("chkRegEx", False):
        c.unsupported.append(
            f"text search {text!r} was a regular expression in GSAK; OpenSAK's text_search is a "
            f"plain substring match"
        )
    # "Wo suchen": Überall (RbtFullAll) searches everything, in which case the
    # individual Logs / Notizen / Beschreibung boxes are ignored by GSAK — they
    # are only read for "Nur ausgewählte" (rbtFullSelect).
    search_all = bool(gf.flag("RbtFullAll", False))
    c.add({
        "filter_type": "text_search",
        "text": text,
        "search_description": search_all or bool(gf.flag("chkFullDes", False)),
        "search_logs": search_all or bool(gf.flag("chkFullLogs", False)),
        "search_notes": search_all or bool(gf.flag("chkFullNotes", False)),
        "search_hint": False,
    })
    if search_all:
        c.assumptions.append(
            "text search: GSAK searched 'Überall', mapped to description + logs + notes "
            "(OpenSAK's hint search stays off)"
        )
    if gf.flag("chkFullHighlight", False):
        c.unsupported.append(
            "text search: GSAK's 'highlight matches' option has no OpenSAK equivalent"
        )


def _end_of_day(value: datetime) -> datetime:
    return value.replace(hour=23, minute=59, second=59)


class DateCriterion:
    """A decoded GSAK date criterion: an operator plus its operands."""

    def __init__(self, op: str, start: Optional[datetime], end: Optional[datetime],
                 during: str = ""):
        self.op = op
        self.start = start
        self.end = end
        self.during = during

    def native_range(self) -> Optional[tuple[Optional[datetime], Optional[datetime]]]:
        """The (from, to) pair OpenSAK's native date filters take, or None for
        the rolling windows, which have no absolute bounds."""
        if self.op in ("during", "not_during"):
            return None
        if self.op == "on_or_before":
            return None, (_end_of_day(self.start) if self.start else None)
        if self.op == "on_or_after":
            return self.start, None
        if self.op == "equals":
            return self.start, (_end_of_day(self.start) if self.start else None)
        return self.start, (_end_of_day(self.end) if self.end else None)   # between


# GSAK's rolling-window edit box ("Während den letzten …"). Only ever seen
# empty in real saved filters, so the unit handling below is ASSUMED: a bare
# number means days, and an explicit unit word is honoured.
_DURING_RE = re.compile(
    r"^\s*(\d+)\s*"
    r"(tag|tage|tagen|day|days|woche|wochen|week|weeks|monat|monate|monaten|"
    r"month|months|jahr|jahre|jahren|year|years)?\s*$",
    re.IGNORECASE,
)
_DURING_UNIT = {
    "tag": "day", "tage": "day", "tagen": "day", "day": "day", "days": "day",
    "woche": "day", "wochen": "day", "week": "day", "weeks": "day",
    "monat": "month", "monate": "month", "monaten": "month",
    "month": "month", "months": "month",
    "jahr": "year", "jahre": "year", "jahren": "year", "year": "year", "years": "year",
}


def _during_modifier(text: str) -> Optional[str]:
    """GSAK 'during' value → a SQLite date() modifier such as "-30 day"."""
    m = _DURING_RE.match(text or "")
    if not m:
        return None
    n, unit = int(m.group(1)), (m.group(2) or "day").lower()
    if unit in ("woche", "wochen", "week", "weeks"):
        n *= 7
    return f"-{n} {_DURING_UNIT[unit]}"


def _date_criterion(gf: GsakFilter, c: Conversion, op_key: str, from_key: str,
                    to_key: str, during_key: str, label: str,
                    ops: Optional[dict[int, Optional[str]]] = None
                    ) -> Optional[DateCriterion]:
    """Decode one GSAK date criterion.

    Returns None when the criterion is off, when its operator index is not
    decoded for this field, or for "Verglichen mit" (which compares two date
    columns — GSAK does not record WHICH two, so nothing can be reconstructed).
    """
    ops = ops or DATE_OP
    raw_op = gf.num(op_key)
    if raw_op is None:
        return None
    if raw_op not in ops:
        start, end = delphi_date(gf.text(from_key)), delphi_date(gf.text(to_key))
        c.unsupported.append(
            f"{label}: GSAK date operator index {raw_op} is not decoded for this field — "
            f"criterion not migrated (dates in the dialog: "
            f"{start.date().isoformat() if start else None} … "
            f"{end.date().isoformat() if end else None})"
        )
        return None
    op = ops[raw_op]
    if op is None:
        return None   # "Beliebig"
    if op == "compared_with":
        c.unsupported.append(
            f"{label}: GSAK compared this date against another date column "
            f"('Verglichen mit'), and the blob does not record which one — nothing to translate"
        )
        return None

    during = gf.text(during_key)
    if op in ("during", "not_during"):
        if _during_modifier(during) is None:
            c.unsupported.append(
                f"{label}: GSAK used a rolling window ('{op}') whose size {during!r} could not "
                f"be read, so no date range could be reconstructed"
            )
            return None
        return DateCriterion(op, None, None, during)

    start, end = delphi_date(gf.text(from_key)), delphi_date(gf.text(to_key))
    if start is None and (op != "between" or end is None):
        return None   # operator set but no usable date in the dialog
    return DateCriterion(op, start, end)


def _date_sql(column: str, crit: DateCriterion) -> str:
    """WHERE fragment for a decoded date criterion on *column*.

    Compared with date() on both sides so a stored timestamp's time-of-day
    never excludes a day the GSAK filter would have included.
    """
    col = f"date({column})"

    def _d(value: Optional[datetime]) -> str:
        return f"'{value.date().isoformat()}'" if value else "null"

    if crit.op in ("during", "not_during"):
        mod = _during_modifier(crit.during)
        window = f"{col} >= date('now', 'localtime', '{mod}')"
        return window if crit.op == "during" else f"({column} IS NULL OR NOT ({window}))"
    if crit.op == "on_or_before":
        return f"{col} <= {_d(crit.start)}"
    if crit.op == "on_or_after":
        return f"{col} >= {_d(crit.start)}"
    if crit.op == "equals":
        return f"{col} = {_d(crit.start)}"
    lo, hi = crit.start, crit.end
    if lo and hi and hi < lo:
        lo, hi = hi, lo
    if lo is None:
        return f"{col} <= {_d(hi)}"
    if hi is None:
        return f"{col} >= {_d(lo)}"
    return f"{col} between {_d(lo)} and {_d(hi)}"


# GSAK date criterion → (op key, from key, to key, during key, label, OpenSAK
# column, native filter_type or None, operator table, extra note or "").
#
# Every field has an OpenSAK column, so the ones with no native filter still
# migrate — as a where_clause on that column.
_DATE_FIELDS: tuple[tuple[str, str, str, str, str, str, Optional[str],
                          dict[int, Optional[str]], str], ...] = (
    ("cbxUserFound", "edtDateMeF", "edtDateMeT", "edtFbmDuring",
     "my found date", "found_date", "found_by_me_date", DATE_OP_MY_FOUND, ""),
    ("cbxDNFDate", "edtDNFDateF", "edtDNFDateT", "edtDNFDateDuring",
     "DNF date", "dnf_date", "dnf_date", DATE_OP, ""),
    ("cbxLastLog", "edtLastLogF", "edtLastLogT", "edtLastLogDuring",
     "last log date", "last_log_date", "last_log_date", DATE_OP, ""),
    ("cbxFound", "edtDateF", "edtDateF2", "edtFoundDuring",
     "last found date (by anyone)", "last_found_date", None, DATE_OP, ""),
    ("cbxPlaced", "edtDateP", "edtDateP2", "edtPlacedDuring",
     "hidden date", "hidden_date", None, DATE_OP, ""),
    ("cbxCreate", "edtCreateF", "edtCreateT", "edtCreatedDuring",
     "record created date", "imported_at", None, DATE_OP,
     " imported_at is when THIS OpenSAK database first saw the cache, not when GSAK did"),
    ("cbxLastUpdate", "edtLastUpdateF", "edtLastUpdateT", "edtLastUpdateDuring",
     "last GPX update", "last_gpx_update", None, DATE_OP,
     (" last_gpx_update is stamped locally on every import pass, where GSAK's LastGPXDate came"
      " from the source file")),
    ("cbxLastUser", "edtLastUserF", "edtLastUserT", "edtLastUserDuring",
     "last user update", "(SELECT updated_at FROM user_notes WHERE user_notes.cache_id = caches.id)",
     None, DATE_OP,
     (" a cache with no personal note and no corrected coordinates has no user_notes row at"
      " all, so it can never match")),
    ("cbxChange", "edtChangeF", "edtChangeT", "edtChangeDuring",
     "last changed date", "last_updated", None, DATE_OP, ""),
)


def _convert_dates(gf: GsakFilter, c: Conversion) -> None:
    for (op_key, from_key, to_key, during_key,
         label, column, ftype, ops, extra) in _DATE_FIELDS:
        crit = _date_criterion(gf, c, op_key, from_key, to_key, during_key, label, ops)
        if crit is None:
            continue

        rng = crit.native_range() if ftype else None
        if rng is not None and (rng[0] is not None or rng[1] is not None):
            start, end = rng
            c.add({
                "filter_type": ftype,
                "from_date": start.isoformat() if start else None,
                "to_date": end.isoformat() if end else None,
            })
            continue

        # No native filter for this column, or a rolling window the native
        # from/to filters cannot express — both become SQL.
        why = ("OpenSAK's date filters take absolute from/to dates, so GSAK's rolling "
               f"'{crit.op}' window became a date('now', …) comparison"
               if crit.op in ("during", "not_during")
               else f"OpenSAK has no GUI filter for this date, so it became a where_clause "
                    f"on {column.split('(')[0].strip() or column}")
        c.add_where(_date_sql(column, crit), f"{label}: {why}.{extra}")


def _convert_attributes(gf: GsakFilter, c: Conversion) -> None:
    wanted: list[dict[str, Any]] = []
    for key, value in gf.kv.items():
        m = re.fullmatch(r"chkAtt(\d+)_([012])", key)
        if not m or value.strip().lower() != "true":
            continue
        attr_id, state = int(m.group(1)), m.group(2)
        if state == "0":
            wanted.append({"filter_type": "attribute", "attribute_id": attr_id, "is_on": True})
        elif state == "1":
            wanted.append({"filter_type": "attribute", "attribute_id": attr_id, "is_on": False})
        else:   # "2" = GSAK's "Keines" — OpenSAK has no negated attribute filter
            wanted.append({
                "filter_type": "where_clause",
                "sql": (f"NOT EXISTS (SELECT 1 FROM attributes a WHERE a.cache_id = caches.id "
                        f"AND a.attribute_id = {attr_id})"),
            })
            c.assumptions.append(
                f"attribute {attr_id}: GSAK's 'Keines' column became a where_clause "
                f"(the cache must not carry the attribute in either state)"
            )
    if not wanted:
        return
    c.assumptions.append(
        "attributes: GSAK's three checkbox columns are Ja / Nein / Keines → is_on true, "
        "is_on false, and 'not present at all'"
    )
    if gf.flag("rbtAttAny", False) and len(wanted) > 1:
        c.add({"mode": "OR", "filters": wanted})   # nested OR filterset
    else:
        for f in wanted:
            c.add(f)


def _bbox_sql(coords: list[tuple[float, float]], pad_km: float = 0.0) -> str:
    """Bounding-box test around *coords*, optionally grown by *pad_km*."""
    lats = [p[0] for p in coords]
    lons = [p[1] for p in coords]
    dlat = pad_km / 111.32
    mid = math.radians(sum(lats) / len(lats))
    dlon = pad_km / max(111.32 * math.cos(mid), 1e-6)
    return (f"latitude between {min(lats) - dlat:.6f} and {max(lats) + dlat:.6f} "
            f"AND longitude between {min(lons) - dlon:.6f} and {max(lons) + dlon:.6f}")


def _polygon_sql(coords: list[tuple[float, float]]) -> Optional[str]:
    """Point-in-polygon test for *coords* as a SQLite WHERE fragment.

    Standard even-odd ray casting: count how many polygon edges a ray cast east
    from the cache crosses; an odd count means the cache is inside. Horizontal
    edges are skipped — a ray can never cross one, and skipping them also keeps
    the generated SQL free of division by zero. The bounding box is prepended
    as a cheap necessary condition so SQLite can reject most rows outright.
    """
    ring = list(coords)
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring.pop()                      # GSAK repeats the first vertex to close
    if len(ring) < 3:
        return None
    terms: list[str] = []
    for i, (lat_i, lon_i) in enumerate(ring):
        lat_j, lon_j = ring[i - 1]
        if lat_i == lat_j:
            continue
        terms.append(
            f"(CASE WHEN (({lat_i:.6f} > latitude) <> ({lat_j:.6f} > latitude)) "
            f"AND (longitude < ({lon_j:.6f} - {lon_i:.6f}) * (latitude - {lat_i:.6f}) "
            f"/ ({lat_j:.6f} - {lat_i:.6f}) + {lon_i:.6f}) THEN 1 ELSE 0 END)"
        )
    if not terms:
        return None
    return f"{_bbox_sql(ring)} AND ((" + " + ".join(terms) + ") % 2) = 1"


def _convert_polygon(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    arc = gf.text("ArcFilter")
    if not arc:
        return
    kind = ("polygon" if gf.flag("rbtPoly", False)
            else "arc" if gf.flag("rbtArc", False)
            else "point" if gf.flag("rbtPoint", False) else "area")
    coords: list[tuple[float, float]] = []
    for point in arc.split("~"):
        point = point.strip()
        if not point:
            continue
        try:
            lat_s, lon_s = point.split(",")
            coords.append((float(lat_s), float(lon_s)))
        except ValueError:
            continue
    exclude = bool(gf.flag("chkArcExclude", False))
    if not coords:
        c.unsupported.append(
            f"{kind} area selection: no usable coordinates in the GSAK definition ({arc})"
        )
        return

    sql = _polygon_sql(coords) if kind == "polygon" else None
    if sql is not None:
        note = (f"{kind}: GSAK's {len(coords)}-point area became a point-in-polygon "
                f"where_clause (even-odd ray casting over the polygon's edges, with the "
                f"bounding box in front of it so SQLite can reject most caches cheaply) — "
                f"OpenSAK has no polygon filter in its GUI")
    else:
        # Arc / point / degenerate polygon: fall back to the bounding box,
        # grown by the radius for a point-plus-radius selection.
        try:
            radius = float(gf.text("edtArcDistance") or 0) * (1.609344 if opts.miles else 1.0)
        except ValueError:
            radius = 0.0
        sql = _bbox_sql(coords, radius)
        note = (f"{kind}: GSAK's area selection ({len(coords)} point(s)"
                + (f", radius {gf.text('edtArcDistance')}" if radius else "")
                + ") was APPROXIMATED by its bounding box as a where_clause — the box is "
                  "wider than the shape GSAK used, so it can let extra caches through")
    if exclude:
        sql = f"NOT ({sql})"
        note += ". GSAK excluded rather than included this area, so the test is negated"
    c.add_where(sql, note + f". Raw GSAK definition: {arc}")


def _convert_custom(gf: GsakFilter, c: Conversion) -> None:
    """User-defined GSAK columns (the Custom / CustomLocal tables).

    The one criterion here that no where_clause can rescue: OpenSAK has no
    custom-column storage at all, so there is no expression to point the SQL
    at. Reported with the raw GSAK criterion so it can be rebuilt by hand on
    one of user_data_1-4 after the data is moved there.
    """
    for line in gf.custom:
        parts = line.split(";")
        name = parts[0] if parts else line
        c.unsupported.append(
            f"user-defined GSAK column '{name}': OpenSAK stores no custom columns (only "
            f"user_data_1–4, and the GSAK importer deliberately does not import Custom — see "
            f"gsak_importer.py), so there is no column for a where_clause to test. Raw GSAK "
            f"criterion: {line}"
        )


def _convert_log_tab(gf: GsakFilter, c: Conversion) -> None:
    """Logs tab — OpenSAK's GUI reaches log TEXT (via text_search) but never
    log types, finders, dates or counts. All of those live in OpenSAK's `logs`
    table, which a where_clause can query, so the whole tab is translated into
    one COUNT(*) sub-select over the logs that match the tab's criteria.

    GSAK's log-type names (``Lt<name>=True``, one key per ticked type) are the
    same strings OpenSAK stores in logs.log_type — the GSAK importer copies
    Logs.lType through verbatim — so they need no translation.
    """
    if "chkLogFound" not in gf.kv and "cbxLogCount" not in gf.kv:
        return

    conds: list[str] = []
    described: list[str] = []
    notes: list[str] = []

    types = sorted(k[2:] for k, v in gf.kv.items()
                   if k.startswith("Lt") and v.strip().lower() == "true")
    if types:
        conds.append("l.log_type in ("
                     + ", ".join(f"'{_escape_sql(t)}'" for t in types) + ")")
        described.append("log types " + ", ".join(types))

    finder = gf.text("edtGeoName")
    if finder:
        if gf.flag("rbtRegex", False):
            c.unsupported.append(
                f"log finder {finder!r} is a regular expression in GSAK — SQLite has no REGEXP "
                f"operator in OpenSAK, so the finder restriction was dropped from the log "
                f"criteria below"
            )
        elif gf.flag("rbtId", False):
            # "Id" mode: edtGeoName is the numeric geocaching.com user id, which
            # the importer stores verbatim in logs.finder_id.
            conds.append(f"coalesce(l.finder_id, '') = '{_escape_sql(finder)}'")
            described.append(f"logs by GC user id {finder}")
        elif gf.flag("rbtWild", False):
            like = _escape_sql(finder.lower()).replace("*", "%").replace("?", "_")
            conds.append(f"lower(coalesce(l.finder, '')) like '{like}'")
            described.append(f"logs by name matching {finder!r}")
            notes.append("GSAK's */? wildcards became SQL's %/_")
        else:   # rbtExact
            conds.append(f"lower(coalesce(l.finder, '')) = '{_escape_sql(finder.lower())}'")
            described.append(f"logs by {finder!r}")

    date_crit = _date_criterion(gf, c, "cbxLogDate", "edtLogDateF", "edtLogDateT",
                                "edtLogDateDuring", "log date", DATE_OP_NO_COMPARE)
    if date_crit is not None:
        conds.append(_date_sql("l.log_date", date_crit))
        described.append(f"log date {date_crit.op.replace('_', ' ')}")
        notes.append("the log-date combo's 'Beliebig' was read as index 6 — see "
                     "DATE_OP_NO_COMPARE in the migration script")

    # Options the blob records but whose meaning could not be pinned down from
    # real filters; reported rather than guessed at.
    if gf.num("cbxLogsToSearch"):
        c.open_points.append(
            f"logs: GSAK's 'Logs to search' selector is set to index "
            f"{gf.num('cbxLogsToSearch')} (something other than 'all logs' — the GSAK-standard "
            f"filter using it is named \"Letzte 2 DNF\", so it most likely restricts the count "
            f"to the newest few logs). That restriction is NOT in the generated SQL, which "
            f"counts every matching log. To add it, wrap the sub-select's FROM in "
            f"(SELECT … FROM logs WHERE cache_id = caches.id ORDER BY log_date DESC LIMIT n)."
        )
    if gf.num("cbxLogInclude"):
        c.open_points.append(
            f"logs: GSAK's 'Logs to include' selector is set to index {gf.num('cbxLogInclude')}, "
            f"whose meaning is not decoded — the generated SQL ignores it"
        )

    raw_count_op = gf.num("cbxLogCount") or 0
    count_op = NUM_OP.get(raw_count_op)
    if count_op is None and not conds:
        return          # nothing on the tab is actually set

    where = " AND ".join(["l.cache_id = caches.id"] + conds)
    count = f"(SELECT COUNT(*) FROM logs l WHERE {where})"

    if count_op is None:
        sql = f"{count} > 0"
        described.insert(0, "at least one log")
    else:
        v1, v2 = gf.num("edtLogFrom"), gf.num("edtLogTo")
        if v1 is None:
            c.unsupported.append(
                f"log count: GSAK comparison '{count_op}' with no number in the box — "
                f"not migrated"
            )
            return
        if count_op == "at_most":
            sql = f"{count} <= {v1}"
        elif count_op == "at_least":
            sql = f"{count} >= {v1}"
        elif count_op == "equals":
            sql = f"{count} = {v1}"
        else:   # between (inclusive)
            if v2 is None:
                c.unsupported.append("log count: 'Zwischen' without a second value — "
                                     "not migrated")
                return
            sql = f"{count} between {min(v1, v2)} and {max(v1, v2)}"
        described.insert(0, f"log count {count_op.replace('_', ' ')} "
                            f"{v1}{f'..{v2}' if count_op == 'between' else ''}")

    c.add_where(sql, "logs (" + "; ".join(described) + "): OpenSAK's GUI filters cannot query "
                     "logs at all (only text_search reaches log text), so the whole Logs tab "
                     "became one COUNT(*) where_clause on the logs table"
                     + "".join(f". {n}" for n in notes))


_WP_COUNT = "(SELECT COUNT(*) FROM waypoints w WHERE w.cache_id = caches.id)"


def _convert_child_wp_tab(gf: GsakFilter, c: Conversion) -> None:
    """Unterwegspunkte tab — OpenSAK has no waypoint filter, but its waypoints
    table is reachable from a where_clause, so most of the tab survives."""
    if "cbxcCount" not in gf.kv and "cbxctype" not in gf.kv:
        return

    # Number of child waypoints.
    _numeric_criterion(gf, c, "cbxcCount", "edtcCount1", "edtcCount2",
                       _WP_COUNT, "child waypoint count")

    # Waypoint type ("In Liste" holds a semicolon-separated list).
    raw_op, types = gf.num("cbxctype") or 0, gf.text("edtctype")
    if types:
        op = TEXT_OP.get(raw_op)
        values = [v.strip().lower() for v in types.split(";") if v.strip()] \
            if op == "in_list" else [types.lower()]
        if op in ("in_list", "equals", "contains"):
            in_list = ", ".join(f"'{_escape_sql(v)}'" for v in values)
            c.add({
                "filter_type": "where_clause",
                "sql": (f"EXISTS (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id "
                        f"AND lower(w.wp_type) in ({in_list}))"),
            })
            c.assumptions.append(
                "child waypoint type: became a where_clause on the waypoints table (GSAK's "
                "type names — Final Location, Parking Area, Virtual Stage, Physical Stage, "
                "Trailhead, Reference Point — match OpenSAK's wp_type values verbatim)"
            )
        else:
            c.unsupported.append(
                f"child waypoint type ({types!r}, GSAK comparison index {raw_op}) — not migrated"
            )

    # Waypoint description. GSAK's cName lands in OpenSAK's waypoints.name and
    # cComment in waypoints.comment (gsak_importer.py); description is left
    # empty by that importer, so all three are searched.
    desc = gf.text("edtcDescription")
    if desc:
        needle = _escape_sql(desc.lower())
        c.add({
            "filter_type": "where_clause",
            "sql": (f"EXISTS (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id "
                    f"AND (lower(coalesce(w.name, '')) like '%{needle}%' "
                    f"OR lower(coalesce(w.comment, '')) like '%{needle}%' "
                    f"OR lower(coalesce(w.description, '')) like '%{needle}%'))"),
        })
        c.assumptions.append(
            "child waypoint description: became a where_clause searching the waypoints "
            "table's name, comment and description columns (GSAK's cName imports into "
            "OpenSAK's waypoints.name)"
        )

    # Waypoint code and comments — plain text criteria on waypoints.wp_code /
    # waypoints.comment, so they go through the same text operators as the
    # cache-level fields.
    for op_key, val_key, column, label in (
        ("cbxcCode", "edtcCode", "wp_code", "waypoint code"),
        ("cbxcComments", "edtcComments", "comment", "waypoint comments"),
    ):
        _wp_text_criterion(gf, c, op_key, val_key, column, label)

    # Waypoint date.
    date_crit = _date_criterion(gf, c, "cbxcDate", "edtcDate1", "edtcDate2",
                                "edtcDateDuring", "child waypoint date",
                                DATE_OP_NO_COMPARE)
    if date_crit is not None:
        c.add_where(
            f"EXISTS (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id "
            f"AND {_date_sql('w.wp_date', date_crit)})",
            "child waypoint date: became a where_clause on waypoints.wp_date (the waypoint-date "
            "combo's 'Beliebig' was read as index 6 — see DATE_OP_NO_COMPARE)"
        )

    # "Von Benutzer erstellt: Ja / Nein" → waypoints.created_by_user.
    by_user = _tri_flag(gf, "chkcByUserYes", "chkcByUserNo")
    if by_user is not None:
        c.add_where(
            f"EXISTS (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id "
            f"AND coalesce(w.created_by_user, 0) = {1 if by_user else 0})",
            f"child waypoints {'created' if by_user else 'not created'} by the user: became a "
            f"where_clause on waypoints.created_by_user (GSAK's cByUser)"
        )

    if gf.flag("chkcSetFlag", False) or gf.flag("chkcClearFlag", False):
        c.unsupported.append(
            "the Unterwegspunkte tab's set/clear-flag actions are a GSAK side effect, "
            "not a filter — nothing to migrate"
        )


def _wp_text_criterion(gf: GsakFilter, c: Conversion, op_key: str, val_key: str,
                       column: str, label: str) -> None:
    """One text criterion from the Unterwegspunkte tab, as an EXISTS
    where_clause on the waypoints table."""
    raw_op = gf.num(op_key) or 0
    op = TEXT_OP.get(raw_op)
    value = gf.text(val_key)
    if op is None:
        if value:
            c.unsupported.append(
                f"child {label}: unknown GSAK comparison index {raw_op} for value {value!r}"
            )
        return
    if not value and op not in TEXT_OP_VALUELESS:
        return
    col = f"lower(coalesce(w.{column}, ''))"
    needle = _escape_sql(value.lower())
    test = {
        "contains":     f"{col} like '%{needle}%'",
        "not_contains": f"{col} not like '%{needle}%'",
        "equals":       f"{col} = '{needle}'",
        "not_equals":   f"{col} <> '{needle}'",
        "empty":        f"{col} = ''",
        "not_empty":    f"{col} <> ''",
        "in_list":      f"{col} in ("
                        + ", ".join(f"'{_escape_sql(v.strip().lower())}'"
                                    for v in value.split(";") if v.strip())
                        + ")",
    }.get(op)
    if test is None:   # regex / not_regex
        c.unsupported.append(
            f"child {label}: GSAK used a regular expression ({value!r}) — SQLite has no REGEXP "
            f"operator in OpenSAK, so no where_clause could be generated"
        )
        return
    c.add_where(
        f"EXISTS (SELECT 1 FROM waypoints w WHERE w.cache_id = caches.id AND {test})",
        f"child {label}: OpenSAK has no waypoint filter, so the '{op}' comparison became a "
        f"where_clause on waypoints.{column}"
    )


def _numeric_criterion(gf: GsakFilter, c: Conversion, op_key: str, v1_key: str,
                       v2_key: Optional[str], column: str, label: str,
                       extra: str = "") -> None:
    """Translate a GSAK numeric criterion into a where_clause on *column*
    (OpenSAK has no native filter for any of these columns)."""
    raw_op = gf.num(op_key) or 0
    v1 = gf.num(v1_key)
    op = NUM_OP.get(raw_op)
    # Index 0 is "Beliebig" — the criterion is off, whatever is left in the
    # edit box beside it.
    if op is None:
        if raw_op != 0:
            c.unsupported.append(f"{label}: unknown GSAK comparison index {raw_op}")
        return
    if v1 is None:
        return
    v2 = gf.num(v2_key) if v2_key else None
    if op == "at_most":
        sql = f"{column} <= {v1}"
    elif op == "at_least":
        sql = f"{column} >= {v1}"
    elif op == "equals":
        sql = f"{column} = {v1}"
    else:   # between (inclusive)
        if v2 is None:
            c.unsupported.append(f"{label}: 'Zwischen' without a second value — not migrated")
            return
        sql = f"{column} between {min(v1, v2)} and {max(v1, v2)}"
    c.add_where(
        sql,
        f"{label}: OpenSAK has no GUI filter for this field, so it became a where_clause "
        f"on {column}.{extra}"
    )


# GSAK compass-quadrant checkbox → the bearing range it covers, in degrees
# clockwise from north. The eight 45° sectors are centred on their compass
# point, so N spans 337.5°–22.5° and wraps around 0.
_QUADRANTS: dict[str, tuple[float, float]] = {
    "cbxN":  (337.5, 22.5),
    "cbxNe": (22.5, 67.5),   "cbxNE": (22.5, 67.5),
    "cbxE":  (67.5, 112.5),
    "cbxSE": (112.5, 157.5),
    "cbxS":  (157.5, 202.5),
    "cbxSW": (202.5, 247.5),
    "cbxW":  (247.5, 292.5),
    "cbxNW": (292.5, 337.5),
}


def _convert_misc(gf: GsakFilter, c: Conversion) -> None:
    # Numeric criteria that OpenSAK stores as a column but has no filter for.
    _numeric_criterion(gf, c, "cbxElevation", "edtElevation", "edtElevation2",
                       "elevation", "elevation")
    _numeric_criterion(gf, c, "cbxUsort", "edtUsort", "edtUsort2",
                       "user_sort", "user sort")
    # GSAK's FoundCount turned out to be a 0/1 "found by me" flag rather than a
    # community find count (verified by the GSAK importer against a real
    # 12,600-cache database), so OpenSAK's found_log_count — how many of the
    # user's own found-type logs the cache carries — is the honest counterpart.
    _numeric_criterion(gf, c, "cbxFoundCount", "EdtFoundCount", None,
                       "found_log_count", "my found count",
                       extra=" GSAK's FoundCount is really a 0/1 found-by-me flag; "
                             "found_log_count counts the user's own found-type logs, so it is "
                             ">= 1 for exactly the caches GSAK would have counted as 1")
    # OpenSAK persists a bearing per cache, recomputed whenever the centre
    # point changes (db/database.py::recalculate_distances).
    _numeric_criterion(gf, c, "cbxDegrees", "edtDegrees", None,
                       "bearing", "bearing/degrees",
                       extra=" bearing is measured from the ACTIVE centre point and is only "
                             "as fresh as the last distance recalculation")

    _text_criterion(gf, c, "cbxSource", "edtSource", None, "source_file", "source")

    # "TB/Coin-Name" — OpenSAK keeps trackables in their own table.
    _trackable_name_criterion(gf, c)

    # No OpenSAK column at all.
    if gf.text("edtSymbol"):
        c.unsupported.append(
            f"symbol name criterion ({gf.text('edtSymbol')!r}, GSAK operator index "
            f"{gf.num('cbxSymbol')}) — OpenSAK does not store a GPX symbol name"
        )

    _convert_quadrants(gf, c)

    if gf.flag("chkReverse", False):
        c.unsupported.append(
            "GSAK's 'reverse filter' (invert the whole result) was set — OpenSAK cannot negate "
            "a filter set, and the criteria above are not all where_clauses that could be "
            "folded into one negated expression; the written filters match the NON-inverted "
            "criteria"
        )


def _convert_quadrants(gf: GsakFilter, c: Conversion) -> None:
    """Compass-quadrant tick boxes → a bearing range where_clause."""
    present = [k for k in _QUADRANTS if k in gf.kv]
    if not present:
        return
    selected = [k for k in present if gf.flag(k)]
    if len(selected) == len(present) or not selected:
        return   # all ticked (or none recorded) = no restriction
    tests: list[str] = []
    for key in selected:
        lo, hi = _QUADRANTS[key]
        tests.append(f"(bearing >= {lo} OR bearing < {hi})" if lo > hi
                     else f"(bearing >= {lo} AND bearing < {hi})")
    c.add_where(
        "bearing IS NOT NULL AND (" + " OR ".join(tests) + ")",
        f"compass quadrants ({', '.join(k[3:] for k in selected)}): OpenSAK has no bearing "
        f"filter in its GUI, so the selection became a where_clause on caches.bearing — which "
        f"is measured from the ACTIVE centre point and is only as fresh as the last distance "
        f"recalculation. ASSUMED: GSAK's quadrants are the eight 45° sectors centred on their "
        f"compass point (N = 337.5°–22.5°)"
    )


def _trackable_name_criterion(gf: GsakFilter, c: Conversion) -> None:
    """GSAK's TB/coin name box → an EXISTS on OpenSAK's trackables table."""
    raw_op = gf.num("cbxTbugName") or 0
    op = TEXT_OP.get(raw_op)
    value = gf.text("edtTbugName")
    if op is None:
        if value:
            c.unsupported.append(
                f"TB/coin name: unknown GSAK comparison index {raw_op} for value {value!r}"
            )
        return
    if not value and op not in TEXT_OP_VALUELESS:
        return
    col = "lower(coalesce(t.name, ''))"
    needle = _escape_sql(value.lower())
    test = {
        "contains":     f"{col} like '%{needle}%'",
        "not_contains": f"{col} not like '%{needle}%'",
        "equals":       f"{col} = '{needle}'",
        "not_equals":   f"{col} <> '{needle}'",
        "empty":        f"{col} = ''",
        "not_empty":    f"{col} <> ''",
        "in_list":      f"{col} in ("
                        + ", ".join(f"'{_escape_sql(v.strip().lower())}'"
                                    for v in value.split(";") if v.strip())
                        + ")",
    }.get(op)
    if test is None:   # regex / not_regex
        c.unsupported.append(
            f"TB/coin name: GSAK used a regular expression ({value!r}) — SQLite has no REGEXP "
            f"operator in OpenSAK"
        )
        return
    c.add_where(
        f"EXISTS (SELECT 1 FROM trackables t WHERE t.cache_id = caches.id AND {test})",
        f"TB/coin name: GSAK matched the cache's travel-bug list as text; OpenSAK keeps "
        f"trackables in their own table, so the '{op}' comparison became a where_clause on "
        f"trackables.name"
    )


def _convert_where(gf: GsakFilter, c: Conversion, opts: Options) -> None:
    """Translate the GSAK where clause.

    *emit* is one of:
        "verified" — emit a where_clause filter only when nothing was left
                     untranslated (default). OpenSAK treats SQL that fails to
                     execute as "matches nothing", so an unverified clause in
                     an AND set would silently empty the whole filter.
        "always"   — emit it regardless (the SQL still needs a manual check).
        "never"    — never emit it; keep it in the _gsak block only.
    """
    if not gf.where:
        return
    emit = opts.emit_where
    custom_columns = [line.split(";")[0] for line in gf.custom if line.split(";")[0]]
    sql, warnings, notes, verified = translate_where(gf.where, custom_columns, opts.me)
    c.assumptions.extend(notes)
    emitted = emit == "always" or (emit == "verified" and verified)
    c.where_note = {
        "gsak_sql": gf.where,
        "translated_sql": sql,
        "verified": verified,
        "warnings": warnings,
        "emitted_as_filter": emitted,
    }
    if emitted:
        c.add({"filter_type": "where_clause", "sql": sql})
    if not verified:
        c.open_points.append(
            "where clause: the SQL could not be translated completely (see _gsak.where.warnings)"
            + (" and was emitted anyway — verify it before relying on this filter"
               if emitted else
               ". It was NOT added to the filter set, because OpenSAK treats a where_clause "
               "whose SQL fails as 'matches nothing', which would silently empty this filter. "
               "Fix _gsak.where.translated_sql by hand and add it as "
               "{\"filter_type\": \"where_clause\", \"sql\": \"…\"}")
        )


# ── Output ───────────────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """Mirror FilterProfile.save()'s sanitisation so a later save from inside
    OpenSAK lands on the same file."""
    return "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in name)


def build_profile(c: Conversion, source_db: Path, gf: GsakFilter,
                  embed_raw: bool) -> dict[str, Any]:
    gsak_block: dict[str, Any] = {
        "note": ("Migrated from GSAK by scripts/migrate_gsak_filters.py. OpenSAK ignores this "
                 "block; it records what could not be translated."),
        "source_database": str(source_db),
        "source_filter": c.name,
        "migrated_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if c.assumptions:
        gsak_block["assumptions"] = _dedupe(c.assumptions)
    if c.unsupported:
        gsak_block["unsupported"] = _dedupe(c.unsupported)
    if c.open_points:
        gsak_block["open_points"] = _dedupe(c.open_points)
    if c.where_note:
        gsak_block["where"] = c.where_note
    if embed_raw:
        gsak_block["raw"] = gf.raw

    return {
        "name": c.name,
        "filterset": {"mode": "AND", "filters": c.filters},
        "sort": {"field": "name", "ascending": True},
        "_gsak": gsak_block,
    }


def read_gsak_center(gsak_ini: Path) -> tuple[Optional[tuple[float, float]], bool]:
    """Return ((lat, lon) or None, use_miles) read from gsak.ini.

    The centre point lives in the [LastCenter] section; rbtMiles.Checked says
    whether GSAK's distances are miles rather than kilometres.
    """
    if not gsak_ini.is_file():
        return None, False
    lat = lon = None
    miles = False
    section = ""
    for line in gsak_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        key, eq, value = line.partition("=")
        if not eq:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "rbtmiles.checked":
            miles = value.lower() == "true"
        elif section == "lastcenter":
            try:
                if key == "lat":
                    lat = float(value)
                elif key == "lon":
                    lon = float(value)
            except ValueError:
                pass
    return ((lat, lon) if lat is not None and lon is not None else None), miles


def load_gsak_filters(db_path: Path) -> list[tuple[str, str]]:
    """Return [(name, data)] for every saved filter in gsak.db3 (read-only)."""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        rows = conn.execute(
            "SELECT Description, Data FROM TranslateFilters WHERE Type = 'FI'"
        ).fetchall()
    return [(str(name), str(data or "")) for name, data in rows if name]


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        description="Convert saved GSAK filters into OpenSAK filter profiles.",
    )
    p.add_argument("gsak_db", type=Path, help="path to gsak.db3")
    p.add_argument("out_dir", type=Path,
                   help="folder for the .json profiles (e.g. %%APPDATA%%/opensak/filters)")
    p.add_argument("--center", metavar="LAT,LON",
                   help="centre point for distance filters "
                        "(default: [LastCenter] in gsak.ini next to gsak.db3)")
    p.add_argument("--gsak-ini", type=Path,
                   help="path to gsak.ini (default: gsak.ini beside gsak.db3)")
    p.add_argument("--miles", action="store_true",
                   help="treat GSAK distances as miles (default: read rbtMiles from gsak.ini)")
    p.add_argument("--me", metavar="ACCOUNT",
                   help="your geocaching.com account name, used to translate GSAK's isOwner "
                        "in a where clause into owner_name = '<ACCOUNT>' (without it, isOwner "
                        "is reported and the clause stays unverified)")
    p.add_argument("--only", action="append", metavar="NAME", default=[],
                   help="migrate only this GSAK filter (repeatable)")
    p.add_argument("--where", choices=("verified", "always", "never"), default="verified",
                   help="when to add a translated GSAK where clause as a where_clause filter: "
                        "'verified' (default) only when it translated cleanly, 'always' even when "
                        "it needs a manual fix, 'never' to keep it in the _gsak block only")
    p.add_argument("--embed-raw", action="store_true",
                   help="also store the original GSAK blob in _gsak.raw")
    p.add_argument("--overwrite", action="store_true",
                   help="overwrite existing .json files (default: skip them)")
    p.add_argument("--dry-run", action="store_true", help="write nothing, just report")
    args = p.parse_args()

    if not args.gsak_db.is_file():
        print(f"Error: no such file: {args.gsak_db}", file=sys.stderr)
        return 1

    gsak_ini = args.gsak_ini or args.gsak_db.with_name("gsak.ini")
    ini_center, ini_miles = read_gsak_center(gsak_ini)
    miles = args.miles or ini_miles

    center = ini_center
    if args.center:
        try:
            lat_s, lon_s = args.center.split(",")
            center = (float(lat_s), float(lon_s))
        except ValueError:
            print(f"Error: --center expects LAT,LON, got {args.center!r}", file=sys.stderr)
            return 1

    if center:
        origin = "--center" if args.center else f"{gsak_ini.name} [LastCenter]"
        print(f"Centre point for distance filters: {center[0]}, {center[1]}  (from {origin})")
    else:
        print("No centre point available — distance filters will be reported as unsupported. "
              "Pass --center LAT,LON to migrate them.")
    print(f"GSAK distance unit: {'miles' if miles else 'km'}")
    if args.me:
        print(f"Owner account for isOwner: {args.me}")

    opts = Options(center=center, miles=miles, emit_where=args.where, me=args.me)

    try:
        rows = load_gsak_filters(args.gsak_db)
    except sqlite3.Error as e:
        print(f"Error reading {args.gsak_db}: {e}", file=sys.stderr)
        return 1

    wanted = {n.lower() for n in args.only}
    if wanted:
        found = {r[0].lower() for r in rows}
        rows = [r for r in rows if r[0].lower() in wanted]
        for name in sorted(wanted - found):
            print(f"Warning: no GSAK filter named {name!r}", file=sys.stderr)

    if not args.dry_run:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    used: dict[str, str] = {}
    written = skipped = 0
    total_unsupported = 0

    for name, data in sorted(rows, key=lambda r: r[0].lower()):
        gf = _parse_blob(name, data)
        conv = convert(gf, opts)
        profile = build_profile(conv, args.gsak_db, gf, args.embed_raw)

        stem = safe_filename(name)
        if stem in used:
            n = 2
            while f"{stem}_{n}" in used:
                n += 1
            stem = f"{stem}_{n}"
        used[stem] = name
        path = args.out_dir / f"{stem}.json"

        notes = profile["_gsak"]
        flags = []
        if notes.get("unsupported"):
            flags.append(f"{len(notes['unsupported'])} unsupported")
        if notes.get("assumptions"):
            flags.append(f"{len(notes['assumptions'])} assumed")
        if conv.where_note and not conv.where_note["verified"]:
            flags.append("where unverified"
                         + ("" if conv.where_note["emitted_as_filter"] else ", not applied"))
        if notes.get("open_points"):
            flags.append("open point")
        suffix = ("  [" + ", ".join(flags) + "]") if flags else ""

        if path.exists() and not args.overwrite and not args.dry_run:
            print(f"  skip  {path.name}  (exists — use --overwrite)")
            skipped += 1
            continue

        print(f"  {'would write' if args.dry_run else 'write'} {path.name}  "
              f"{len(conv.filters)} filter(s){suffix}")
        total_unsupported += len(notes.get("unsupported", []))
        if not args.dry_run:
            path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        written += 1

    print(f"\n{written} filter(s) {'would be ' if args.dry_run else ''}written, {skipped} skipped, "
          f"{total_unsupported} unsupported criteria recorded in _gsak blocks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
