"""tests/unit-tests/test_macro_statistics_report.py — what a statistics macro
needs: opensak.write_text() with its file-type allow-list and the autostart
deny (security item G), the SVG charts of opensak.chart.*, and the shipped
example find_statistics.lua against a real database."""

import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from opensak.db.database import session_for
from opensak.db.manager import get_db_manager
from opensak.db.models import Cache, Log
from opensak.macro import FolderApproval, MacroError, MacroRuntime
from opensak.macro.permissions import (
    FolderAccessDenied,
    FolderPermission,
    check_access,
    resolve_path,
    safe_write,
    temp_dir,
    write_denied_reason,
)
from opensak.export.file_export import active_database_name
from opensak.stats import charts, tables
from opensak.stats import html as report_html
from opensak.stats import maps as report_maps

EXAMPLE = (Path(__file__).resolve().parents[2] / "macros" / "examples"
           / "find_statistics.lua")


class FakeHost:
    """Only folder approval and the database name are used here."""

    def end_macro(self):
        pass

    def approve_folder(self, target, folder, write):
        return FolderApproval.DENY

    def database_name(self):
        return active_database_name()

    def __getattr__(self, name):
        # Bound into the opensak table at every run, never called here.
        return lambda *args: None


@pytest.fixture(autouse=True)
def own_temp_dir(tmp_path, monkeypatch):
    """opensak.temp_dir() inside the test's folder, so the reports these
    tests write never replace real ones in the system temp folder."""
    import tempfile

    folder = tmp_path / "systemp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


@pytest.fixture(autouse=True)
def macros_dir(tmp_path, monkeypatch):
    d = tmp_path / "install" / "macros"
    d.mkdir(parents=True)
    monkeypatch.setattr("opensak.config.get_macros_dir", lambda: d)
    return d


def _run(source, host=None, **kwargs):
    out: list[str] = []
    MacroRuntime(host or FakeHost(), output=out.append, **kwargs).run(source)
    return out


def _lua_path(path: Path) -> str:
    return str(path).replace("\\", "/")


# ── Security G: allowed file types, autostart locations ─────────────────────

def _rw(path):
    return [FolderPermission(str(path), read=True, write=True)]


@pytest.mark.parametrize("name", ["a.bat", "a.PS1", "a.lua", "a.exe", "a", "a.html.", "a.html:x"])
def test_suffix_outside_allow_list_is_refused(tmp_path, name):
    with pytest.raises(FolderAccessDenied, match="only these file types"):
        check_access(tmp_path / name, write=True, permissions=_rw(tmp_path),
                     allowed_suffixes=(".html", ".txt"))


def test_allowed_suffix_any_case_and_reads_are_not_limited(tmp_path):
    perms = _rw(tmp_path)
    assert check_access(tmp_path / "A.HTML", write=True, permissions=perms,
                        allowed_suffixes=(".html",)) == resolve_path(tmp_path / "A.HTML")
    # Reads and writes without an allow-list keep working as before.
    check_access(tmp_path / "a.lua", write=False, permissions=perms, allowed_suffixes=(".html",))
    check_access(tmp_path / "a.gpx", write=True, permissions=perms)


def test_safe_write_checks_allowed_suffixes(tmp_path):
    with pytest.raises(FolderAccessDenied, match="only these file types"):
        safe_write(tmp_path / "x.cmd", "x", permissions=_rw(tmp_path),
                   allowed_suffixes=iter([".txt"]))
    target = safe_write(tmp_path / "x.txt", "x", permissions=_rw(tmp_path),
                        allowed_suffixes=iter([".txt"]))    # iterated twice
    assert target.read_text() == "x"


@pytest.mark.parametrize("relative", [
    ".bashrc", ".zshrc", ".profile", ".config/autostart/x.txt",
    "Library/LaunchAgents/x.txt",
])
def test_autostart_locations_are_never_written(relative):
    home = Path.home()      # tmp folder (conftest)
    target = home / relative
    with pytest.raises(FolderAccessDenied, match="autostart"):
        check_access(target, write=True, permissions=_rw(home))
    # Reading them is not part of this rule.
    check_access(target, write=False, permissions=_rw(home))


def test_windows_startup_folder_is_never_written(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    startup = tmp_path / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert write_denied_reason(resolve_path(startup / "x.txt")) == "it is an autostart location"
    if os.name == "nt":     # case-insensitive file names
        assert write_denied_reason(resolve_path(str(startup / "x.txt").upper())) is not None


# ── opensak.write_text ──────────────────────────────────────────────────────

def test_write_text_writes_utf8_and_appends():
    out = _run("""
        local p = opensak.write_text(opensak.temp_dir() .. "/report.html", "<p>ä</p>")
        print(p)
        opensak.write_text(p, "\\n<p>2</p>", { append = true })
        print(opensak.write_text(opensak.temp_dir() .. "/n.txt", 42))
    """)
    target = temp_dir() / "report.html"
    assert out == [str(target), str(temp_dir() / "n.txt")]
    assert target.read_text(encoding="utf-8") == "<p>ä</p>\n<p>2</p>"
    assert (temp_dir() / "n.txt").read_text() == "42"


def test_write_text_relative_path_is_next_to_the_macro(macros_dir):
    host = FakeHost()
    host.approve_folder = lambda target, folder, write: FolderApproval.ONCE
    out: list[str] = []
    MacroRuntime(host, output=out.append).run(
        'print(opensak.write_text("out/r.txt", "x"))', macro_path=macros_dir / "m.lua")
    assert out == [str(resolve_path(macros_dir / "out" / "r.txt"))]


@pytest.mark.parametrize("name", ["evil.bat", "other.lua", "x.gpx", "noext"])
def test_write_text_refuses_other_file_types_without_asking(name):
    host = FakeHost()
    asked = []
    host.approve_folder = lambda *a: asked.append(a) or FolderApproval.ONCE
    with pytest.raises(MacroError, match="only these file types can be written"):
        _run(f'opensak.write_text(opensak.temp_dir() .. "/{name}", "x")', host)
    assert asked == []
    assert not (temp_dir() / name).exists()


def test_write_text_needs_write_permission(tmp_path):
    host = FakeHost()
    host.approve_folder = lambda *a: FolderApproval.DENY
    with pytest.raises(MacroError, match="denied by the user"):
        _run(f'opensak.write_text("{_lua_path(tmp_path)}/r.html", "x")', host)
    assert not (tmp_path / "r.html").exists()


@pytest.mark.parametrize("call, message", [
    ('opensak.write_text()', "expects a file path"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", {})', "text must be a string"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", "x", { mode = "a" })', "options must be"),
    ('opensak.write_text(opensak.temp_dir() .. "/a.txt", "x", { append = 1 })', "append must be"),
])
def test_write_text_argument_errors(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


def test_write_text_size_limit(monkeypatch):
    monkeypatch.setattr("opensak.macro.runtime.MAX_WRITE_BYTES", 10)
    with pytest.raises(MacroError, match="larger than"):
        _run('opensak.write_text(opensak.temp_dir() .. "/big.txt", string.rep("x", 11))')


# ── Charts ──────────────────────────────────────────────────────────────────

ROWS = [{"key": 2023, "count": 5}, {"key": 2024, "count": 12}, {"key": "<b>", "count": None}]


def _bars(svg: str) -> int:
    return len(re.findall(r'<path d="M', svg))


def test_bar_chart_is_svg_with_escaped_labels_and_tooltips():
    svg = charts.bar(ROWS, title='Finds & "more"')
    assert svg.startswith("<svg ") and svg.endswith("</svg>")
    assert 'aria-label="Finds &amp; &quot;more&quot;"' in svg
    assert "<b>" not in svg and "&lt;b&gt;" in svg
    assert "<title>2024: 12</title>" in svg and "<title>&lt;b&gt;: 0</title>" in svg
    assert _bars(svg) == 2      # the 0 bar draws nothing
    assert "var(--chart-color, #2a78d6)" in svg


def test_bar_chart_horizontal_grows_with_rows_and_truncates_labels():
    rows = [{"key": "x" * 40 + str(i), "count": i} for i in range(1, 21)]
    svg = charts.bar(rows, horizontal=True)
    height = int(re.search(r'height="(\d+)"', svg).group(1))
    assert height > charts.DEFAULT_HEIGHT
    assert "x" * 27 + "…" in svg and f"<title>{'x' * 40}20: 20</title>" in svg
    assert _bars(svg) == 20


def test_axis_ticks_are_round():
    assert charts._ticks(12) == [0, 2.5, 5.0, 7.5, 10.0, 12.5]
    assert charts._ticks(1000) == [0, 200, 400, 600, 800, 1000]
    assert charts._ticks(0) == [0.0, 1.0]


def test_line_chart_points_and_last_value():
    rows = [{"m": "2024-01", "t": 3}, {"m": "2024-02", "t": 3}, {"m": "2024-03", "t": 10}]
    svg = charts.line(rows, x="m", y="t", area=True)
    assert svg.count("<circle") == 3 and "<polygon" in svg and "<polyline" in svg
    assert '<title>2024-03: 10</title>' in svg
    assert re.search(r'font-weight="600">10</text>', svg)


def test_empty_rows_still_draw_an_axis():
    assert charts.bar([]).startswith("<svg")
    assert charts.line([]).startswith("<svg")


@pytest.mark.parametrize("kwargs, message", [
    ({"rows": [{"key": 1, "count": "x"}]}, "must be a number"),
    ({"rows": [{"key": 1, "count": float("nan")}]}, "must be a number"),
    ({"rows": [1]}, "not a table"),
    ({"rows": ROWS, "width": 10}, "width must be"),
    ({"rows": ROWS, "color": "red;x:url(a)"}, "color must be"),
])
def test_chart_errors(kwargs, message):
    with pytest.raises(charts.ChartError, match=message):
        charts.bar(**kwargs)


def test_chart_from_lua():
    out = _run("""
        local svg = opensak.chart.bar({ {year = "2024", finds = 3} }, { x = "year", y = "finds", title = "T" })
        print(svg:sub(1, 4), svg:find("<title>2024: 3</title>", 1, true) ~= nil)
        print(opensak.chart.line({}, { area = true }):sub(1, 4))
    """)
    assert out == ["<svg\ttrue", "<svg"]


@pytest.mark.parametrize("call, message", [
    ('opensak.chart.bar("x")', "expects a list of rows"),
    ('opensak.chart.bar({}, { area = true })', r"unknown option\(s\) \['area'\]"),
    ('opensak.chart.line({}, { horizontal = true })', "unknown option"),
    ('opensak.chart.bar({}, { x = 1 })', "x must be a string"),
    ('opensak.chart.bar({}, { labels = "yes" })', "labels must be true or false"),
    ('opensak.chart.bar({ {key = 1, count = "x"} })', "row 1: count must be a number"),
])
def test_chart_errors_from_lua(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


# ── Example macro find_statistics.lua ───────────────────────────────────────

def _cache(s, code, found_date, cache_type="Traditional Cache", d=1.5, t=2.0, logs=()):
    cache = Cache(gc_code=code, name=f"Cache {code}", cache_type=cache_type,
                  latitude=47.0, longitude=8.0, difficulty=d, terrain=t,
                  found=found_date is not None, found_date=found_date,
                  url=f"https://coord.info/{code}")
    s.add(cache)
    s.flush()
    for log_type, when in logs:
        s.add(Log(cache_id=cache.id, log_type=log_type, log_date=when, finder="me"))
    return cache


@pytest.fixture
def finds_db():
    manager = get_db_manager()
    db = manager.new_database("Finds")
    manager.switch_to(db)
    with session_for(db.path) as s:
        day = datetime(2024, 3, 4)
        # Same day: GCB's find log was imported first, so it is find no. 2
        # although GCA sorts first by code (and by found time).
        _cache(s, "GCB", day.replace(hour=12), logs=[("Found it", day)])
        _cache(s, "GCA", day.replace(hour=9), "Multi-cache", 3.0, 4.5,
               logs=[("Found it", day)])
        _cache(s, "GCZ", datetime(2023, 12, 31), logs=[("Found it", datetime(2023, 12, 31))])
        _cache(s, "GCN", datetime(2024, 5, 1), "Unknown Cache", 5.0, 5.0)
        _cache(s, "GCX", None)                          # not found
    return db


def _tile(page, label):
    m = re.search(r'<div class="value">([^<]*)</div><div class="label">' + re.escape(label), page)
    return m and m.group(1)


def test_find_statistics_example(finds_db, boundaries):
    _describe(finds_db, {
        "GCA": {"latitude": 47.0, "longitude": 5.0, "country": "Alpha", "state": "North A"},
        "GCB": {"latitude": 46.0, "longitude": 6.0, "country": "Alpha", "state": "North A"},
        "GCN": {"latitude": 42.0, "longitude": 5.0, "country": "Alpha", "state": "South A"},
        "GCZ": {"latitude": 45.0, "longitude": 15.0, "country": "Beta", "state": "Beta One"},
    })
    source = EXAMPLE.read_text(encoding="utf-8").replace(
        "local MILESTONE_STEP = nil", "local MILESTONE_STEP = 2")
    out = _run(source)
    target = temp_dir() / "find_statistics.html"
    assert out == [f"4 finds → {target}", "Open the file in your browser."]
    page = target.read_text(encoding="utf-8")

    assert _tile(page, "finds") == "4"
    assert _tile(page, "cache days") == "3"
    assert _tile(page, "finds per cache day") == "1.33"
    assert _tile(page, "D/T combinations") == "3 / 81"
    # 2023-12-31, 2024-03-04, 2024-05-01: no two days in a row; the longest
    # break is 1 January to 3 March 2024 (leap year).
    assert _tile(page, "longest streak (days)") == "1"
    assert _tile(page, "longest break (days)") == "63"
    assert _tile(page, "countries") == "2 / 2"
    assert _tile(page, "states in Alpha") == "2 / 2"
    assert "2023-12-31 to 2024-05-01" in page
    # Per year, stacked by cache type; the running total ends at 4 in May 2024.
    assert "<title>2024 · Traditional Cache: 1 (total 3)</title>" in page
    assert "<title>2024-04: 3</title>" in page and "<title>2024-05: 4</title>" in page
    for weekday in ("Mon: 2", "Tue: 0", "Wed: 1", "Sun: 1"):
        assert f"<title>{weekday}</title>" in page
    assert "<title>Traditional Cache: 2</title>" in page
    # Maps: countries and the states of the home country ("South A" is the
    # outline "South Ä").
    assert "<title>Alpha: 3</title>" in page and "<title>Beta: 1</title>" in page
    assert "<title>North A: 2</title>" in page and "<title>South Ä: 1</title>" in page
    assert page.count("<circle") >= 4                    # dots + rose rings
    # Milestones 1, 2 and the last one (4); 2 is GCB, found on the same day
    # as GCA but with the earlier imported find log.
    rows = re.findall(r'<tr><td class="num">(\d+)</td><td>([\d-]+)</td><td class="num">(\d*)</td>'
                      r'<td><a href="https://coord.info/(GC\w+)">GC\w+</a></td>', page)
    assert rows == [("1", "2023-12-31", "", "GCZ"), ("2", "2024-03-04", "64", "GCB"),
                    ("4", "2024-05-01", "58", "GCN")]


def test_find_statistics_example_without_boundary_data(finds_db):
    out = _run(EXAMPLE.read_text(encoding="utf-8"))
    assert out[0].startswith("4 finds → ")
    page = (temp_dir() / "find_statistics.html").read_text(encoding="utf-8")
    assert "Map not available: the boundary data is not installed." in page
    assert _tile(page, "countries") == "0"


def test_find_statistics_example_without_finds():
    manager = get_db_manager()
    manager.switch_to(manager.new_database("Empty"))
    out = _run(EXAMPLE.read_text(encoding="utf-8"))
    assert out == ["No found caches with a found date in Empty."]


# ── opensak.stats.* (Python side) ───────────────────────────────────────────

FINDS = [
    {"code": "GC1", "d": "2024-03-04", "type": "Traditional", "fav": 2, "dt": 1.5},
    {"code": "GC2", "d": "2024-03-04 10:00:00", "type": "Multi", "fav": 5, "dt": 2.0},
    {"code": "GC3", "d": "2024-05-01", "type": "Traditional", "fav": None, "dt": 1.5},
    {"code": "GC4", "d": "2023-12-31", "type": "Mystery", "fav": 1, "dt": None},
    {"code": "GC5", "d": None, "type": "Traditional"},
]


@pytest.mark.parametrize("period, keys", [
    ("year", ["2023", "2024"]),
    ("month", ["2023-12", "2024-03", "2024-05"]),
    ("week", ["2023-W52", "2024-W10", "2024-W18"]),
    ("day", ["2023-12-31", "2024-03-04", "2024-05-01"]),
    ("weekday", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
])
def test_group_by_period(period, keys):
    result = tables.group(FINDS, "d", period=period)
    assert [g["key"] for g in result] == keys
    assert sum(g["count"] for g in result) == 4        # nil date left out


def test_group_distinct_sum_sort_and_top():
    result = tables.group(FINDS, "type", count_distinct="d", sum="fav", sort="count")
    assert result == [
        {"key": "Traditional", "count": 3, "distinct": 2, "sum": 2},
        {"key": "Multi", "count": 1, "distinct": 1, "sum": 5},
        {"key": "Mystery", "count": 1, "distinct": 1, "sum": 1},
    ]
    top = tables.group(FINDS, "type", sort="count", top=1, other="Rest")
    assert top == [{"key": "Traditional", "count": 3}, {"key": "Rest", "count": 2}]
    assert [g["key"] for g in tables.group(FINDS, "fav", sort="key")] == [1, 2, 5]


def test_fill_running_and_every():
    months = tables.fill(tables.group(FINDS, "d", period="month"))
    assert [(m["key"], m["count"]) for m in months] == [
        ("2023-12", 1), ("2024-01", 0), ("2024-02", 0), ("2024-03", 2),
        ("2024-04", 0), ("2024-05", 1)]
    years = tables.fill([{"key": 2020, "n": 1}, {"key": "2022", "n": 2}], period="year",
                        end="2023")
    assert [(y["key"], y["n"]) for y in years] == [
        ("2020", 1), ("2021", 0), ("2022", 2), ("2023", 0)]
    total = tables.running(months)
    assert [m["total"] for m in total] == [1, 1, 1, 3, 3, 4]
    assert "total" not in months[0]                    # copies
    ordered = [FINDS[i] for i in tables.sort_order(FINDS, ["d", "code"])]
    picked = tables.every(ordered[:4], 2, date_field="d")
    assert [(p["n"], p["code"], p.get("days")) for p in picked] == [
        (1, "GC4", None), (2, "GC1", 64), (4, "GC3", 58)]
    assert [p["n"] for p in tables.every(ordered, 2, first=False, last=False)] == [2, 4]


def test_sort_order_descending_nil_last_and_stable():
    order = tables.sort_order(FINDS, ["-fav"])
    assert [FINDS[i]["code"] for i in order] == ["GC2", "GC1", "GC4", "GC3", "GC5"]
    order = tables.sort_order(FINDS, ["type", "-code"])
    assert [FINDS[i]["code"] for i in order] == ["GC2", "GC4", "GC5", "GC3", "GC1"]


def test_pivot_and_summary():
    m = tables.pivot(FINDS, "type", "dt")
    assert m["rows"] == ["Multi", "Traditional"] and m["cols"] == [1.5, 2.0]
    assert m["cells"] == [[0, 1], [2, 0]]
    assert (m["max"], m["total"], m["filled"]) == (2, 3, 2)
    fixed = tables.pivot(FINDS, "type", "dt", rows_order=["Traditional"], cols_order=[1, 1.5, 2])
    assert fixed["cells"] == [[0, 2, 0]]
    assert tables.summary(FINDS, "fav") == {
        "count": 3, "distinct": 3, "min": 1, "max": 5, "sum": 8, "mean": 8 / 3, "median": 2}
    dates = tables.summary(FINDS, "d")
    assert (dates["count"], dates["min"], dates["max"]) == (4, "2023-12-31", "2024-05-01")
    assert "sum" not in dates
    assert tables.summary([], "x") == {"count": 0, "distinct": 0}


def test_days_between_and_period_errors():
    assert tables.days_between("2024-02-28", "2024-03-01") == 2     # leap year
    assert tables.days_between("2024-03-01 23:59", "2024-02-28") == -2
    assert tables.days_between(datetime(2024, 1, 1).timestamp(), "2024-01-31") == 30
    with pytest.raises(tables.StatsError, match="must be a date"):
        tables.days_between("soon", "2024-01-01")
    with pytest.raises(tables.StatsError, match="period must be one of"):
        tables.group(FINDS, "d", period="decade")
    with pytest.raises(tables.StatsError, match="more than"):
        tables.fill([{"key": "1000-01-01"}], period="day", end="9999-01-01")


def test_html_table_tiles_and_page():
    out = report_html.table(
        [{"code": "GC1", "n": 3.0, "url": "https://coord.info/GC1", "x": "<b>"},
         {"code": "GC2", "n": None, "url": "javascript:alert(1)", "x": None}],
        columns=["code", ["n", "Count"], "x"], link={"code": "url"})
    assert '<th class="num">Count</th>' in out and '<td class="num">3</td>' in out
    assert '<a href="https://coord.info/GC1">GC1</a>' in out
    assert "javascript" not in out and "<td>GC2</td>" in out
    assert "&lt;b&gt;" in out and "<b>" not in out
    default = report_html.table([{"b": 1, "a": 2}])
    assert default.index(">a<") < default.index(">b<")       # sorted field names
    tiles = report_html.tiles([{"label": "finds", "value": 12}])
    assert '<div class="value">12</div><div class="label">finds</div>' in tiles
    page = report_html.page(["<p>x</p>"], title="A & B", subtitle="<s>", footer="f")
    assert page.startswith("<!doctype html>") and "<title>A &amp; B</title>" in page
    assert "&lt;s&gt;" in page and "<p>x</p>" in page and "<footer>f</footer>" in page
    assert "prefers-color-scheme: dark" in page
    with pytest.raises(report_html.HtmlError, match="css"):
        report_html.page([], css="</style><script>")


def test_heatmap_chart():
    m = tables.pivot(FINDS, "type", "dt")
    svg = charts.heatmap(m, title="T", row_title="Type", col_title="D")
    assert svg.startswith("<svg") and "<title>Traditional / 1.5: 2</title>" in svg
    assert "<title>Multi / 1.5: 0</title>" in svg
    assert "Type \\ D" in svg
    with pytest.raises(charts.ChartError, match="one line of cells"):
        charts.heatmap({"rows": [1], "cols": [1], "cells": []})


# ── opensak.stats.* / html.* from Lua ───────────────────────────────────────

def test_stats_functions_from_lua():
    out = _run("""
        local rows = {
            { code = "GC1", d = "2024-03-04", t = "A", n = 2 },
            { code = "GC2", d = "2024-03-04", t = "B", n = 1 },
            { code = "GC3", d = "2024-05-01", t = "A" },
            { code = "GC4", d = "2023-12-31", t = "A", extra = { 1, 2 } },
        }
        local s = opensak.stats
        for _, g in ipairs(s.group(rows, "d", { period = "month", count_distinct = "d", sum = "n" })) do
            print(g.key, g.count, g.distinct, g.sum)
        end
        print(#s.group(rows, "d", { period = "weekday" }))
        local months = s.running(s.fill(s.group(rows, "d", { period = "month" })))
        print(#months, months[2].key, months[2].count, months[#months].total)
        local sorted = s.sort(rows, "d", "-code")
        print(sorted[1].code, sorted[2].code, sorted[1].extra[2], sorted[1] == rows[4])
        local m = s.every(sorted, 2, { date = "d" })
        print(#m, m[2].n, m[2].code, m[2].days, m[1].extra[1])
        local p = s.pivot(rows, "t", "d")
        print(#p.rows, #p.cols, p.cells[1][2], p.filled)
        local f = s.summary(rows, "n")
        print(f.count, f.sum, f.median, opensak.date.diff("2026-01-01", "2026-10-06"))
        print(opensak.html.escape("<a>"), opensak.html.escape(nil) == "", opensak.html.escape(2.0))
        local page = opensak.html.page{ title = "R", body = "<p>b</p>",
            opensak.html.tiles{ { label = "x", value = 1 } },
            opensak.html.columns(opensak.html.table(rows, { columns = { "code" } }), "<i>2</i>"),
            opensak.chart.heatmap(p) }
        print(page:find('<div class="tiles">', 1, true) < page:find("<p>b</p>", 1, true),
              page:find("<td>GC3</td>", 1, true) ~= nil, page:find("<svg", 1, true) ~= nil)
    """)
    assert out == [
        "2023-12\t1\t1\t0", "2024-03\t2\t1\t3", "2024-05\t1\t1\t0",
        "7",
        "6\t2024-01\t0\t4",
        "GC4\tGC2\t2\ttrue",
        "3\t2\tGC2\t64\t1",
        "2\t3\t1\t4",
        "2\t3\t1.5\t278",
        "&lt;a&gt;\ttrue\t2",
        "true\ttrue\ttrue",
    ]


@pytest.mark.parametrize("call, message", [
    ('opensak.stats.group("x", "d")', "expects a list of rows"),
    ('opensak.stats.group({ 1 }, "d")', "row 1 is not a table"),
    ('opensak.stats.group({}, nil)', "by must be a field name"),
    ('opensak.stats.group({}, "d", { nope = 1 })', r"unknown option\(s\) \['nope'\]"),
    ('opensak.stats.group({}, "d", { sort = "x" })', "sort must be"),
    ('opensak.stats.group({}, "d", { top = 0 })', "top must be"),
    ('opensak.stats.group({ {d = "x"} }, "d", { period = "year" })', "must be a date"),
    ('opensak.stats.fill({}, { period = "week" })', "period must be one of day, month, year"),
    ('opensak.stats.running({ {count = "x"} })', "count must be a number"),
    ('opensak.stats.every({}, 0)', "step must be"),
    ('opensak.stats.sort({})', "needs at least one field"),
    ('opensak.stats.pivot({}, "a", "b", { rows = 1 })', "rows must be a list"),
    ('opensak.date.diff("x", "2024-01-01")', "opensak.date.diff: first date must be a date"),
    ('opensak.html.escape({})', "expects a string"),
    ('opensak.html.table({}, { link = { code = 1 } })', "link must be a table"),
    ('opensak.html.page("x")', "expects a table"),
    ('opensak.html.page{ titel = "x" }', r"unknown key\(s\) \['titel'\]"),
    ('opensak.chart.heatmap({ 1 })', "expects a matrix"),
])
def test_stats_and_html_errors_from_lua(call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


# ── opensak.caches{ all = true } and database_statistics.lua ───────────────

DB_EXAMPLE = EXAMPLE.with_name("database_statistics.lua")


class EmptyFilterHost(FakeHost):
    """The active filter shows no cache at all."""

    def filtered_caches(self):
        return []


def _describe(db, updates):
    with session_for(db.path) as s:
        for code, values in updates.items():
            cache = s.query(Cache).filter_by(gc_code=code).one()
            for key, value in values.items():
                setattr(cache, key, value)


def test_caches_all_ignores_the_active_filter(finds_db):
    out = _run("""
        local n, all = 0, 0
        for _ in opensak.caches() do n = n + 1 end
        for c in opensak.caches{ all = true, fields = { "found" } } do all = all + 1 end
        print(n, all)
    """, EmptyFilterHost())
    assert out == ["0\t5"]
    with pytest.raises(MacroError, match="cannot be combined with filter keys"):
        _run("for _ in opensak.caches{ all = true, found = true } do end", EmptyFilterHost())
    with pytest.raises(MacroError, match="all must be true or false"):
        _run('for _ in opensak.caches{ all = "yes" } do end', EmptyFilterHost())


def test_database_statistics_example(finds_db, boundaries):
    _describe(finds_db, {
        "GCA": {"hidden_date": datetime(2020, 5, 1), "country": "Alpha", "state": "North A",
                "owner_name": "Alice", "favorite_points": 7, "container": "Small",
                "latitude": 47.0, "longitude": 5.0},
        "GCB": {"hidden_date": datetime(2022, 1, 1), "country": "Alpha", "state": "North A",
                "owner_name": "Alice", "favorite_points": 3, "container": "Micro",
                "latitude": 46.0, "longitude": 6.0},
        "GCZ": {"hidden_date": datetime(2022, 1, 2), "country": "Beta", "state": "Beta One",
                "owner_name": "Bob", "favorite_points": 12, "archived": True,
                "latitude": 45.0, "longitude": 15.0},
        "GCX": {"hidden_date": datetime(2023, 3, 3), "country": "Alpha", "state": "South A",
                "owner_name": "<Eve>", "favorite_points": 0, "latitude": 42.0, "longitude": 5.0},
    })
    out = _run(DB_EXAMPLE.read_text(encoding="utf-8"), EmptyFilterHost())
    target = temp_dir() / "database_statistics.html"
    assert out == [f"5 caches → {target}", "Open the file in your browser."]
    page = target.read_text(encoding="utf-8")

    assert _tile(page, "caches") == "5"
    assert _tile(page, "found by you (80 %)") == "4"
    assert _tile(page, "still to find (active)") == "1"        # GCX
    assert (_tile(page, "active"), _tile(page, "disabled"), _tile(page, "archived")) == ("4", "0", "1")
    assert _tile(page, "owners") == "3"
    assert _tile(page, "countries") == "2 / 2"
    assert _tile(page, "states in Alpha") == "2 / 2"
    assert _tile(page, "favourite points") == "22"
    assert _tile(page, "most days in a row with a new cache") == "2"   # 1 and 2 January 2022
    assert "placed 2020-05-01 to 2023-03-03" in page
    # Per type with the found share, and placed per year stacked by status.
    assert ('<td>Traditional Cache</td><td class="num">3</td>' in page
            and "<td>67 %</td>" in page)
    assert "<title>2022 · active: 1 (total 2)</title>" in page
    assert "<title>2022 · archived: 1 (total 2)</title>" in page
    # Maps: caches per country; still to find per state of Alpha.
    assert "<title>Alpha: 3</title>" in page and "<title>Beta: 1</title>" in page
    assert "<title>South Ä: 1</title>" in page and "<title>North A</title>" in page
    assert "&lt;Eve&gt;" in page and "<Eve>" not in page
    # Most favourite points first, linked to the listing.
    first = re.search(r'<td class="num">(\d+)</td><td><a href="https://coord.info/(GC\w+)">', page)
    assert first.groups() == ("12", "GCZ")


# ── Maps, regions, streaks, period, rose, stacked bars, html.matrix ────────

def _square(lon0, lat0, lon1, lat1):
    return {"type": "Polygon",
            "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]]}


def build_boundaries(folder: Path) -> None:
    """Two countries side by side: Alpha (lon 0-10, lat 40-50) with the
    states North A and South Ä, Beta (lon 10-20) with the state Beta One."""
    import json
    import sqlite3

    layers = {
        "country": [("Alpha", None, "world.geojson", _square(0, 40, 10, 50)),
                    ("Beta", None, "world.geojson", _square(10, 40, 20, 50))],
        "state": [("North A", "alp", "alp.geojson", _square(0, 45, 10, 50)),
                  ("South Ä", "alp", "alp.geojson", _square(0, 40, 10, 45)),
                  ("Beta One", "bet", "bet.geojson", _square(10, 40, 20, 50))],
        "county": [],
    }
    dirs = {"country": "countries", "state": "states", "county": "counties"}
    db = sqlite3.connect(folder / "boundaries.db")
    db.execute("CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)")
    for layer, regions in layers.items():
        db.execute(f"CREATE VIRTUAL TABLE rtree_{layer} USING rtree(id, min_lat, max_lat, min_lon, max_lon)")
        db.execute(f"CREATE TABLE region_{layer} (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
                   "parent TEXT, pack TEXT NOT NULL, feature_index INTEGER NOT NULL, "
                   "poly_version INTEGER NOT NULL DEFAULT 1, is_bundled INTEGER NOT NULL DEFAULT 1)")
        packs: dict[str, list] = {}
        for i, (name, parent, pack, geom) in enumerate(regions, 1):
            features = packs.setdefault(pack, [])
            ring = geom["coordinates"][0]
            lons, lats = [p[0] for p in ring], [p[1] for p in ring]
            db.execute(f"INSERT INTO rtree_{layer} VALUES (?, ?, ?, ?, ?)",
                       (i, min(lats), max(lats), min(lons), max(lons)))
            db.execute(f"INSERT INTO region_{layer} VALUES (?, ?, ?, ?, ?, 1, 1)",
                       (i, name, parent, pack, len(features)))
            features.append({"type": "Feature", "properties": {"name": name, "parent": parent},
                             "geometry": geom})
        (folder / dirs[layer]).mkdir(parents=True, exist_ok=True)
        for pack, features in packs.items():
            (folder / dirs[layer] / pack).write_text(
                json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    db.commit()
    db.close()


@pytest.fixture
def boundaries(tmp_path, monkeypatch):
    folder = tmp_path / "boundaries"
    folder.mkdir()
    build_boundaries(folder)
    monkeypatch.setenv("OPENSAK_BOUNDARIES_DIR", str(folder))
    return folder


@pytest.fixture
def map_data(boundaries):
    from opensak.geo import BoundaryStore

    data = report_maps.MapData(BoundaryStore(boundaries))
    yield data
    data.close()


def test_regions_and_their_countries(map_data):
    assert report_maps.regions(map_data) == [{"name": "Alpha"}, {"name": "Beta"}]
    assert report_maps.regions(map_data, "state") == [
        {"name": "North A", "country": "Alpha"}, {"name": "South Ä", "country": "Alpha"},
        {"name": "Beta One", "country": "Beta"}]
    assert [r["name"] for r in report_maps.regions(map_data, "state", "alpha")] == \
        ["North A", "South Ä"]
    assert report_maps.regions(map_data, "county") == []
    with pytest.raises(report_maps.MapError, match="layer must be one of"):
        report_maps.regions(map_data, "city")


def test_normalize_matches_accents_case_and_punctuation():
    n = report_maps.normalize
    assert n("Graubünden") == n("graubunden") == n("GRAUBÜNDEN")
    assert n("Basel-Landschaft") == n("Basel Landschaft")
    assert n("Αθήνα") == "αθήνα"           # no Latin letters: kept as it is


def test_choropleth_colours_matched_regions_and_lists_the_others(map_data):
    svg = report_maps.choropleth(map_data, [
        {"key": "alpha", "count": 30}, {"key": "Atlantis", "count": 2}], title="T")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<title>Alpha: 30</title>" in svg and "<title>Beta</title>" in svg
    assert "Not on the map: Atlantis" in svg
    states = report_maps.choropleth(map_data, [
        {"key": "South A", "count": 3}, {"key": "North A", "count": 1}],
        layer="state", within="Alpha", extent="all")
    assert "<title>South Ä: 3</title>" in states and "<title>North A: 1</title>" in states
    assert "Beta One" not in states        # Beta is only drawn as a country
    assert "<title>Beta</title>" in states


def test_choropleth_zooms_to_the_data(map_data):
    def view(svg):
        return re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).groups()

    one = report_maps.choropleth(map_data, [{"key": "Alpha", "count": 1}], width=400)
    both = report_maps.choropleth(map_data, [{"key": "Alpha", "count": 1}, {"key": "Beta", "count": 1}],
                                  width=400)
    # Alpha alone is about square, both side by side are wider than high.
    assert int(view(one)[1]) > int(view(both)[1])


def test_class_edges():
    assert report_maps.class_edges([3, 24443, 900, 50]) == [3, 10, 100, 500, 2000]
    assert report_maps.class_edges([1, 2, 3, 4]) == [1, 2, 3, 4]
    assert report_maps.class_edges([0, 0]) == []


def test_points_map(map_data):
    rows = [{"lat": 45.0, "lon": 5.0, "name": "A<1>"}, {"lat": 45.0, "lon": 5.0, "name": "A2"},
            {"lat": 45.0, "lon": 15.0, "name": "B"}, {"lat": None, "lon": 1.0}]
    svg = report_maps.points(map_data, rows, label="name", layer="state")
    assert svg.count("<circle") == 2                      # same pixel drawn once
    assert "<title>A&lt;1&gt; (+1)</title>" in svg and "<title>B</title>" in svg
    with pytest.raises(report_maps.MapError, match="must be coordinates"):
        report_maps.points(map_data, [{"lat": 95.0, "lon": 1.0}])


def test_map_functions_from_lua(boundaries):
    out = _run("""
        local rows = { { key = "Alpha", count = 3 }, { key = "Beta", count = 1 } }
        local svg = opensak.chart.map(rows, { title = "Finds" })
        print(svg:sub(1, 4), svg:find("<title>Alpha: 3</title>", 1, true) ~= nil)
        local dots = opensak.chart.points({ { lat = 45, lon = 5, name = "x" } }, { label = "name" })
        print(dots:find("<title>x</title>", 1, true) ~= nil)
        local states = opensak.geo.regions("state", { within = "Alpha" })
        print(#states, states[2].name, states[2].country, #opensak.geo.regions())
    """)
    assert out == ["<svg\ttrue", "true", "2\tSouth Ä\tAlpha\t2"]


def test_map_functions_without_boundary_data():
    out = _run("""
        print(opensak.chart.map({}):find("not installed", 1, true) ~= nil)
        print(opensak.chart.points({}):find("not installed", 1, true) ~= nil)
        print(opensak.geo.regions("state"))
    """)
    assert out == ["true", "true", "nil"]


@pytest.mark.parametrize("call, message", [
    ('opensak.chart.map({}, { layer = "city" })', "layer must be one of"),
    ('opensak.chart.map({}, { extent = "some" })', "extent must be one of"),
    ('opensak.chart.map({}, { nope = 1 })', r"unknown option\(s\) \['nope'\]"),
    ('opensak.chart.map({ { key = "Alpha", count = "x" } })', "count must be a number"),
    ('opensak.chart.map({}, { within = 3 })', "within must be a country name"),
    ('opensak.chart.points({}, { radius = 50 })', "radius must be"),
    ('opensak.geo.regions("x")', "layer must be one of"),
])
def test_map_errors_from_lua(boundaries, call, message):
    with pytest.raises(MacroError, match=message):
        _run(call)


def test_streaks():
    rows = [{"d": d} for d in ("2024-01-01", "2024-01-02", "2024-01-03", "2024-01-10",
                               "2024-01-11", "2024-01-11 18:00", None)]
    assert tables.streaks(rows, "d") == {
        "days": 5,
        "longest": {"days": 3, "first": "2024-01-01", "last": "2024-01-03"},
        "gap": {"days": 6, "first": "2024-01-04", "last": "2024-01-09"},
        "last": {"days": 2, "first": "2024-01-10", "last": "2024-01-11"},
    }
    assert tables.streaks([{"d": "2024-02-28"}, {"d": "2024-03-01"}], "d")["gap"] == \
        {"days": 1, "first": "2024-02-29", "last": "2024-02-29"}
    assert tables.streaks([], "d") == {"days": 0}
    assert "gap" not in tables.streaks([{"d": "2024-01-01"}], "d")


def test_streaks_and_period_from_lua():
    out = _run("""
        local s = opensak.stats.streaks({ {d = "2024-01-01"}, {d = "2024-01-02"}, {d = "2024-01-05"} }, "d")
        print(s.days, s.longest.days, s.longest.first, s.gap.days, s.last.first)
        print(opensak.date.period("2026-10-06", "week"), opensak.date.period("2026-10-06 23:00", "weekday"))
    """)
    assert out == ["3\t2\t2024-01-01\t2\t2024-01-05", "2026-W41\tTue"]
    with pytest.raises(MacroError, match="period must be one of"):
        _run('opensak.date.period("2026-10-06", "decade")')


def test_rose_chart():
    rows = [{"key": d, "count": n} for d, n in zip("N E S W".split(), (9, 4, 0, 1))]
    svg = charts.rose(rows, title="R")
    assert svg.count("<path") == 3                         # S has no sector
    assert "<title>N: 9</title>" in svg and "<title>S: 0</title>" in svg
    # Area grows with the value: the radius with its square root.
    radii = [float(r) for r in re.findall(r"A([\d.]+),", svg)]
    assert radii[0] == pytest.approx(radii[1] * 1.5, rel=0.01)


def test_stacked_bar_chart():
    rows = [{"key": "2023", "a": 1, "b": 2}, {"key": "2024", "a": 3, "b": None}]
    svg = charts.bar(rows, y=["a", "b"], names=["Alpha", "Beta"])
    assert "<title>2023 · Beta: 2 (total 3)</title>" in svg
    assert "<title>2024 · Alpha: 3 (total 3)</title>" in svg
    assert ">Alpha</text>" in svg and ">Beta</text>" in svg          # legend
    assert "var(--chart-color-2, #eb6834)" in svg
    with pytest.raises(charts.ChartError, match="names must be"):
        charts.bar(rows, y=["a", "b"], names=["x"])
    with pytest.raises(charts.ChartError, match="1 to 8 field names"):
        charts.bar(rows, y=[f"f{i}" for i in range(9)])


def test_stacked_bar_from_a_matrix_in_lua():
    out = _run("""
        local rows = { {y = "2023", t = "Trad"}, {y = "2023", t = "Multi"}, {y = "2024", t = "Trad"} }
        local svg = opensak.chart.bar(opensak.stats.pivot(rows, "y", "t"), { title = "T" })
        print(svg:find("<title>2023 · Trad: 1 (total 2)</title>", 1, true) ~= nil,
              svg:find(">Multi</text>", 1, true) ~= nil)
        local two = opensak.chart.bar({ {key = 1, a = 1, b = 2} }, { y = { "a", "b" } })
        print(two:find("1 · b: 2", 1, true) ~= nil)
    """)
    assert out == ["true\ttrue", "true"]
    with pytest.raises(MacroError, match="x and y are taken from the matrix"):
        _run('opensak.chart.bar(opensak.stats.pivot({ {a = 1, b = 2} }, "a", "b"), { y = "c" })')


def test_html_matrix():
    m = {"rows": [1, 2], "cols": ["a", "b"], "cells": [[1, 0], [3, 4]]}
    out = report_html.matrix(m, totals=True, row_title="D", col_title="T")
    assert "D \\ T" in out and ">Total</th>" in out
    cells = re.findall(r"<td style=\"([^\"]*)\">([^<]*)</td>", out)
    assert [c[1] for c in cells] == ["1", "", "1", "3", "4", "7", "4", "4", "8"]
    assert "rgba(42,120,214,1.00);color:#fff" in out           # the largest cell
    percent = report_html.matrix(m, percent=True, shade=False)
    assert "13 %" in percent and "50 %" in percent and "38 %" in percent
    assert "rgba(42,120,214" not in percent
    with pytest.raises(report_html.HtmlError, match="one line of cells per row"):
        report_html.matrix({"rows": [1], "cols": [1], "cells": []})


def test_html_table_bars_and_matrix_from_lua():
    out = _run("""
        local rows = { {k = "x", n = 2}, {k = "y", n = 8} }
        local t = opensak.html.table(rows, { columns = { "k", "n" }, bars = { "n" }, color = "#ff0000" })
        print(t:find("width:25.0%;background:#ff0000", 1, true) ~= nil, t:find("width:100.0%", 1, true) ~= nil)
        local m = opensak.html.matrix(opensak.stats.pivot(rows, "k", "n"), { totals = true })
        print(m:find(">Total</th>", 1, true) ~= nil)
    """)
    assert out == ["true\ttrue", "true"]
    with pytest.raises(MacroError, match="bars must be a list"):
        _run('opensak.html.table({ {n = 1} }, { columns = { "n" }, bars = "n" })')
    with pytest.raises(MacroError, match="color must be"):
        _run('opensak.html.table({ {n = 1} }, { columns = { "n" }, bars = { "n" }, color = "red" })')
