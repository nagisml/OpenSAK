"""
src/opensak/stats/profile.py — a geocaching.com profile version of a report (Qt-free).

The profile keeps only a small part of HTML: no <style>, no classes, no
JavaScript, no SVG and no data: images (a data: image even makes the save
fail with a server error). What works is text and tables with inline styles,
so every chart has a profile twin built from tables: bars as table cells with
a background colour, maps as a list of their regions.

The opensak.chart.* / html.* functions of the macro API put that twin into
the HTML they return, as a comment right after the normal markup:

    <!--osk-w-->normal markup<!--osk-p:profile markup-->

A browser shows the normal markup and ignores the comment; convert() keeps
the comment and drops the normal markup, inlines the styles of the page
stylesheet and removes everything a profile does not accept.
"""

from __future__ import annotations

import re
from html import escape as _escape
from typing import Any, Mapping, Optional, Sequence

FG, MUTED, LINE = "#0b0b0b", "#52514e", "#e4e3df"
FONT = "Arial, Helvetica, sans-serif"
TD = f"padding:3px 10px;border-bottom:1px solid {LINE};text-align:left"
TH = TD + f";color:{MUTED};font-weight:bold"
TABLE = 'cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin-top:8px"'
BAR_WIDTH = 320
# A line chart with more points than this is shown as fewer rows: the last
# value of each year for monthly/daily keys, else every n-th point.
MAX_LINE_ROWS = 30

_WEB, _PROFILE, _END = "<!--osk-w-->", "<!--osk-p:", "-->"
_DUAL = re.compile(r"<!--osk-w-->.*?<!--osk-p:(.*?)-->", re.S)


def dual(web: str, profile: str) -> str:
    """*web* for the browser, *profile* for convert(). Neither may hold a
    comment; "--" in the profile text is written as "-&#45;" so that the
    comment stays valid."""
    return f"{_WEB}{web}{_PROFILE}{profile.replace('--', '-&#45;')}{_END}"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def _bar_cell(parts: Sequence[tuple[float, str]], top: float, width: int = BAR_WIDTH) -> str:
    """One cell holding the bar: a nested table, one coloured cell per part."""
    total = sum(max(v, 0) for v, _ in parts)
    if total <= 0 or top <= 0:
        return f'<td style="width:{width}px;padding:3px 0"></td>'
    cells = "".join(
        f'<td width="{v / total * 100:.1f}%" bgcolor="{c}" style="background-color:{c};'
        f'height:12px;font-size:1px;line-height:1px;padding:0">&nbsp;</td>'
        for v, c in parts if v > 0)
    return (f'<td style="width:{width}px;padding:3px 0">'
            f'<table width="{max(total / top * 100, 0.5):.1f}%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse"><tr>{cells}</tr></table></td>')


def table_bar(share: float, color: str) -> str:
    """The bar of html.table{ bars = }: *share* 0..1 of the cell."""
    if share <= 0:
        return ""
    return (f'<table width="{max(share * 100, 0.5):.1f}%" cellpadding="0" cellspacing="0"><tr>'
            f'<td bgcolor="{color}" style="background-color:{color};height:10px;font-size:1px;'
            f'line-height:1px;padding:0">&nbsp;</td></tr></table>')


def bars(rows: Sequence[tuple[str, Sequence[float]]], colors: Sequence[str],
         names: Optional[Sequence[str]] = None, title: Optional[str] = None,
         label_head: str = "", value_head: str = "") -> str:
    """A bar chart as a table: one row per (label, values), the values
    stacked in *colors*; *names* gives the legend of a stacked chart."""
    out = [f"<p><b>{_escape(title)}</b></p>"] if title else []
    if names and len(names) > 1:
        legend = "".join(
            f'<td bgcolor="{c}" style="background-color:{c};width:12px;font-size:1px">&nbsp;</td>'
            f'<td style="padding:0 14px 0 4px">{_escape(n)}</td>' for n, c in zip(names, colors))
        out.append(f'<table cellpadding="0" cellspacing="0" style="margin:8px 0;font-size:13px">'
                   f"<tr>{legend}</tr></table>")
    top = max((sum(max(v, 0) for v in values) for _, values in rows), default=0)
    out.append(f"<table {TABLE}>")
    if label_head or value_head:
        out.append(f'<tr><th style="{TH}">{_escape(label_head)}</th><th style="{TH}"></th>'
                   f'<th style="{TH};text-align:right">{_escape(value_head)}</th></tr>')
    for label, values in rows:
        out.append(f'<tr><td style="{TD}">{_escape(label)}</td>'
                   f"{_bar_cell(list(zip(values, colors)), top)}"
                   f'<td style="{TD};text-align:right">{_fmt(sum(values))}</td></tr>')
    out.append("</table>")
    return "".join(out)


def line_rows(points: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    """The points of a line chart, thinned out to at most MAX_LINE_ROWS."""
    if len(points) <= MAX_LINE_ROWS:
        return list(points)
    if all(re.match(r"\d{4}-\d\d", k) for k, _ in points):
        last: dict[str, float] = {}
        for k, v in points:
            last[k[:4]] = v
        if len(last) <= MAX_LINE_ROWS:
            return list(last.items())
    step = -(-len(points) // MAX_LINE_ROWS)
    picked = list(points[::step])
    if picked[-1] != points[-1]:
        picked.append(points[-1])
    return picked


def regions(rows: Sequence[Mapping[str, Any]], key: str, value: str, color: str,
            title: Optional[str] = None) -> str:
    """A region map as a table of its regions with a value, largest first."""
    items = []
    for row in rows:
        v = row.get(value)
        if row.get(key) is not None and isinstance(v, (int, float)) and not isinstance(v, bool) \
                and v > 0:
            items.append((str(row.get(key)), [float(v)]))
    items.sort(key=lambda r: -r[1][0])
    return bars(items, [color], title=title)


def chart(kind: str, rows: Any, opts: Mapping[str, Any]) -> str:
    """The profile twin of opensak.chart.<kind>(rows, opts) (bar, rose,
    line or heatmap), after the macro API has checked the arguments."""
    from opensak.stats import charts, html

    color = opts.get("color") or charts.DEFAULT_COLOR
    title = opts.get("title")
    if kind == "heatmap":
        head = f"<p><b>{_escape(title)}</b></p>" if title else ""
        return head + html.matrix(rows, row_title=opts.get("row_title"),
                                  col_title=opts.get("col_title"), color=color)
    x, y = opts.get("x", "key"), opts.get("y", "count")
    if kind == "line":
        points = line_rows(charts._points(rows, x, y))
        return bars([(k, [v]) for k, v in points], [color], title=title)
    ys = [y] if isinstance(y, str) else list(y)
    colors = [color, *charts.SERIES_COLORS[1:len(ys)]]
    return bars(charts._stack_rows(rows, x, ys), colors, names=opts.get("names") or ys,
                title=title)


def note(text: str) -> str:
    return f'<p style="color:{MUTED};font-style:italic;margin:8px 0">{_escape(text)}</p>'


def tiles(items: Sequence[tuple[str, str]], per_row: int = 5) -> str:
    """Key figures as a table, *per_row* in a row: (value, label), both escaped."""
    cells = [f'<td style="border:1px solid {LINE};padding:10px 12px;width:{100 // per_row}%;'
             f'vertical-align:top"><span style="font-size:22px;font-weight:bold">{value}</span>'
             f'<br><span style="color:{MUTED};font-size:12px">{label}</span></td>'
             for value, label in items]
    rows = "".join("<tr>" + "".join(cells[i:i + per_row]) + "</tr>"
                   for i in range(0, len(cells), per_row))
    return f'<table width="100%" cellspacing="6" cellpadding="0">{rows}</table>'


# ── Page → profile fragment ──────────────────────────────────────────────

def _rgba_to_hex(m: re.Match) -> str:
    r, g, b, a = int(m[1]), int(m[2]), int(m[3]), float(m[4])
    return "#%02x%02x%02x" % tuple(round(c * a + 255 * (1 - a)) for c in (r, g, b))


_REPLACE = (
    ('<div class="columns"><!--osk-cols-->',
     '<table width="100%" cellpadding="0" cellspacing="0"><tr>'),
    ("<div><!--osk-c-->", '<td style="vertical-align:top;padding-right:16px">'),
    ("</div><!--/osk-c-->", "</td>"),
    ("</div><!--/osk-cols-->", "</tr></table>"),
    ('<div class="table">', ""),
    ("</table></div>", "</table>"),
    ('<th class="num">', f'<th style="{TH};text-align:right">'),
    ('<td class="num">', f'<td style="{TD};text-align:right">'),
    ("<th>", f'<th style="{TH}">'),
    ("<td>", f'<td style="{TD}">'),
    ("<table>", f"<table {TABLE}>"),
    ("<h1>", '<h1 style="margin:0 0 4px;font-size:26px">'),
    ("<h2>", '<h2 style="margin:32px 0 8px;font-size:18px">'),
    ("<h3>", '<h3 style="margin:24px 0 6px;font-size:15px">'),
    ('<p class="sub">', f'<p style="color:{MUTED};margin:0 0 16px">'),
    ("<footer>", f'<p style="margin-top:40px;color:{MUTED};font-size:12px">'),
    ("</footer>", "</p>"),
)


def convert(page: str) -> str:
    """The profile version of an html.page (or of any HTML built with the
    chart/html functions): one <div> to paste into the profile."""
    m = re.search(r"<!--osk-body-->(.*?)<!--/osk-body-->", page, re.S)
    body = m.group(1) if m else page
    body = _DUAL.sub(lambda d: d.group(1), body)
    for old, new in _REPLACE:
        body = body.replace(old, new)
    body = re.sub(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)", _rgba_to_hex, body)
    # What a profile does not accept, from HTML the macro wrote itself.
    body = re.sub(r"<(script|style|svg)\b.*?</\1>", "", body, flags=re.S | re.I)
    body = re.sub(r"<img\b[^>]*\bsrc\s*=\s*[\"']?\s*data:[^>]*>", "", body, flags=re.I)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r'\s+class="[^"]*"', "", body)
    return (f'<div style="font-family:{FONT};font-size:14px;line-height:1.45;color:{FG};'
            f'background-color:#ffffff;max-width:960px;padding:16px">\n{body.strip()}\n</div>\n')
