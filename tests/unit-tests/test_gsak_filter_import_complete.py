"""
tests/unit-tests/test_gsak_filter_import_complete.py — GSAK filter conditions
that used to end up in comments, or as SQL that ran but meant something else.
"""

from __future__ import annotations

import sqlite3

import pytest

from opensak.db.sql_functions import register_geo_functions
from opensak.filters.engine import (
    FilterSet,
    TextSearchFilter,
    WhereClauseFilter,
    where_clause_error,
)
from opensak.importer import gsak_filter_importer as gfi
from opensak.importer.gsak_filter_importer import (
    COMMENT,
    Options,
    SQL,
    convert,
    parse_filter_blob,
    read_gsak_custom_fields,
    translate_where,
)
from opensak.utils.constants import CACHE_TYPES


@pytest.fixture
def conn():
    """The tables a translated clause touches, one corrected cache (id 1) and
    one that isn't (id 2)."""
    c = sqlite3.connect(":memory:")
    register_geo_functions(c)
    c.executescript("""
        CREATE TABLE caches (id INTEGER PRIMARY KEY, gc_code TEXT, name TEXT,
                             latitude REAL, longitude REAL, trackable_count INTEGER);
        CREATE TABLE user_notes (cache_id INTEGER, corrected_lat REAL, corrected_lon REAL);
        CREATE TABLE waypoints (cache_id INTEGER, name TEXT);
        CREATE TABLE logs (cache_id INTEGER, log_date TEXT, finder_id INTEGER);
        INSERT INTO caches VALUES (1, 'GC1', 'one', 47.0, 8.0, 6), (2, 'GC2', 'two', 46.0, 7.0, 1);
        INSERT INTO user_notes VALUES (1, 47.5, 8.5);
        INSERT INTO waypoints VALUES (2, 'x');
        INSERT INTO logs VALUES (1, '2020-05-06 23:30:00.000000', 7);
    """)
    yield c
    c.close()


def _ids(conn, gsak_sql: str) -> list[int]:
    sql, warnings, _notes, verified = translate_where(gsak_sql)
    assert verified, warnings
    return [r[0] for r in conn.execute(f"SELECT id FROM caches WHERE ({sql}) ORDER BY id")]


class TestPositions:
    def test_latitude_is_the_corrected_position(self, conn):
        assert _ids(conn, "latitude = 47.5") == [1]
        assert _ids(conn, "latitude = 46.0") == [2]

    def test_corrected_differs_from_posted(self, conn):
        # This comparison was a tautology, so it was left in a comment.
        assert _ids(conn, "latitude = latoriginal and longitude = lonoriginal") == [2]

    def test_alias_keeps_its_position(self, conn):
        sql = "code in (select c.code from caches c where c.latitude = 47.5)"
        assert _ids(conn, sql) == [1]

    def test_kbefore_is_the_posted_position(self, conn):
        sql = ("code in (select kcode from corrected "
               "where g_Distance(kbeforelat,kbeforelon,kafterlat,kafterlon,'K') > 10)")
        assert _ids(conn, sql) == [1]


class TestJoins:
    def test_unqualified_code_join(self, conn):
        # Became "cache_id = gc_code", comparing an id with a code: no matches.
        assert _ids(conn, "0 < (select count(*) from waypoints w where w.cparent = code)") == [2]

    def test_unqualified_join_reversed(self, conn):
        assert _ids(conn, "0 < (select count(*) from waypoints w where code = w.cparent)") == [2]


def test_travelbug_count(conn):
    sql = """code in (select code from (
select c.code,
((LENGTH(travelbugs) - LENGTH(REPLACE(travelbugs, 'ref =', ''))) / LENGTH('ref =') ) as tbCount
from caches c, cachememo cm where c.code = cm.code and tbCount > 5
))"""
    assert _ids(conn, sql) == [1]


def test_text_search_stays_a_regex():
    blob = "edtFull=checker[ :]*falsch\r\nchkRegEx=True\r\nRbtFullAll=True"
    c = convert(parse_filter_blob("t", blob), Options())
    assert c.comments == []
    f = c.filters[0]
    assert isinstance(f, TextSearchFilter) and f.op == "regex"


def test_g_gc_date_with_ltime_in_a_clause(conn):
    # Check_Different_DNF's shape.
    sql = ("code in (select lparent from logs l "
           "where g_GcDate(l.ldate,l.ltime) > date('2017-01-01'))")
    sql_ok, warnings, _notes, verified = translate_where(sql)
    assert verified, warnings
    assert "substr(l.log_date, 12, 8)" in sql_ok
    assert _ids(conn, sql) == [1]


class TestCustomFieldsOfOtherDatabases:
    def test_read_from_gsak_db3(self, tmp_path):
        path = tmp_path / "gsak.db3"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE CustomDefs (fname, ftype, fscope, fdb)")
        db.execute("INSERT INTO CustomDefs VALUES ('LabID', 'String', 'Local', 'AdventureLabs')")
        db.commit()
        db.close()
        assert read_gsak_custom_fields(path) == {"LabID": "string"}

    def test_no_such_table(self, tmp_path):
        path = tmp_path / "gsak.db3"
        sqlite3.connect(path).close()
        assert read_gsak_custom_fields(path) == {}

    def _where(self, opts: Options):
        return convert(parse_filter_blob("t", "*where*=labid <> ''"), opts)

    def test_field_of_another_database_migrates(self):
        c = self._where(Options(
            custom_fields={}, other_custom_fields={"LabID": "string"},
            validate_sql=lambda sql: "no such column: LabID",
        ))
        assert [x.status for x in c.criteria] == [SQL]
        assert any("LabID" in note for note in c.notes)

    def test_unknown_field_stays_a_comment(self):
        c = self._where(Options(custom_fields={}))
        assert [x.status for x in c.criteria] == [COMMENT]


def test_sql_that_cannot_run_is_not_migrated():
    c = convert(parse_filter_blob("t", "*where*=found = 1"), Options(
        validate_sql=lambda sql: "near \"x\": syntax error",
    ))
    assert [x.status for x in c.criteria] == [COMMENT]
    assert "syntax error" in c.where_text()


class TestCacheTypes:
    def test_types_exist(self):
        assert "Waymark" in CACHE_TYPES
        assert gfi.GSAK_TYPE_LABEL_TO_OSAK["L&F Celebration"] in CACHE_TYPES

    def test_label_is_native(self):
        types = {label: "False" for label in gfi.GSAK_TYPE_LABEL_TO_OSAK}
        types["Waymark"] = "True"
        types["L&F Celebration"] = "True"
        c = convert(parse_filter_blob(
            "t", "\r\n".join(f"{k}={v}" for k, v in types.items())), Options())
        assert c.comments == []
        assert sorted(c.filters[0].types) == ["Geocaching HQ Celebration", "Waymark"]

    def test_code_in_a_clause(self):
        sql = translate_where("cachetype = 'Y' or cachetype = 'D'")[0]
        assert "1 = 0" not in sql and "'Waymark'" in sql


def test_failing_where_clause_reports_its_error(db_session):
    from opensak.filters.engine import apply_filters
    bad = WhereClauseFilter("no_such_column = 1")
    fs = FilterSet()
    fs.add(bad)
    assert apply_filters(db_session, fs) == []
    assert "no_such_column" in (where_clause_error(fs) or "")
    good = FilterSet()
    good.add(WhereClauseFilter("1 = 1"))
    apply_filters(db_session, good)
    assert where_clause_error(good) is None
