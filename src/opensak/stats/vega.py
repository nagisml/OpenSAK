"""
src/opensak/stats/vega.py — Vega-Lite charts for HTML reports (Qt-free).

A Vega-Lite chart is a JSON description (data, mark, encodings), so a Lua
macro builds it as a table and opensak.chart.vega() turns it into an HTML
snippet: an empty <div> plus the spec as JSON. opensak.html.page() sees the
snippets and puts the vendored Vega files (assets/vega, about 0.8 MB) inline
into the page once, with a small script that draws every chart — so the page
works offline, in a browser and in an in-app viewer. The colours follow the
page's theme (light/dark), as the SVG charts do.

Interactive charts need JavaScript, which the geocaching.com profile strips;
for a static copy see the "Static copy" item in the statistics TODO (render
the same spec to SVG/PNG with QtWebEngine). The chart's ⋯ menu ("Save as
SVG/PNG") already gives one by hand.
"""

from __future__ import annotations

import json
import math
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

VERSIONS = {"vega": "5.30.0", "vega-lite": "5.21.0", "vega-embed": "6.26.0"}
SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"
# Attribute of the <script type="application/json"> that holds a spec;
# html.page() looks for it to decide whether the Vega files are needed.
MARKER = "data-opensak-vega"
# One chart's JSON (its data included); a report is limited by write_text anyway.
MAX_SPEC_BYTES = 16 * 1024 * 1024
_FILES = ("vega.min.js", "vega-lite.min.js", "vega-embed.min.js")


class VegaError(ValueError):
    """A spec that cannot be used."""


def assets_dir() -> Path:
    """The vendored Vega files: src/opensak/assets/vega in a source
    checkout, opensak/assets/vega in the PyInstaller bundle (same layout)."""
    return Path(__file__).resolve().parents[1] / "assets" / "vega"


def _check(value: Any, path: str = "spec") -> None:
    """Only JSON values: tables, strings, finite numbers, booleans, nil."""
    if isinstance(value, Mapping):
        for k, v in value.items():
            if not isinstance(k, str):
                raise VegaError(f"{path}: keys must be strings, got {k!r}")
            _check(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value, 1):
            _check(v, f"{path}[{i}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise VegaError(f"{path}: {value!r} is not a number JSON can hold")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise VegaError(f"{path}: {type(value).__name__} values cannot go into a chart")


def snippet(spec: Mapping[str, Any], actions: bool = True) -> str:
    """The HTML for one chart. *actions* shows Vega's ⋯ menu (save as
    SVG/PNG, view source)."""
    if not isinstance(spec, Mapping):
        raise VegaError("spec must be a table, e.g. { data = { values = rows }, mark = \"bar\", ... }")
    _check(spec)
    spec = dict(spec)
    spec.setdefault("$schema", SCHEMA)
    text = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_SPEC_BYTES:
        raise VegaError(f"the chart (with its data) is larger than {MAX_SPEC_BYTES // (1024 * 1024)} MB")
    # "<" as \u003c: valid JSON, and nothing in it can end or confuse the
    # <script> element ("</script>", "<!--").
    text = text.replace("<", "\\u003c")
    chart_id = "vega-" + secrets.token_hex(6)
    return (f'<div class="vega-chart" id="{chart_id}"></div>'
            f'<script type="application/json" {MARKER}="{chart_id}" '
            f'data-actions="{"true" if actions else "false"}">{text}</script>')


_RUN = """
(function () {
  function theme() {
    var css = getComputedStyle(document.documentElement);
    function v(name, fallback) { return (css.getPropertyValue(name) || "").trim() || fallback; }
    var fg = v("--fg", "#0b0b0b"), muted = v("--muted", "#52514e"), line = v("--line", "#e4e3df");
    var series = [v("--chart-color", "#2a78d6"), v("--chart-color-2", "#eb6834"),
                  v("--chart-color-3", "#1baf7a"), v("--chart-color-4", "#eda100"),
                  v("--chart-color-5", "#e87ba4"), v("--chart-color-6", "#008300"),
                  v("--chart-color-7", "#6250d6"), v("--chart-color-8", "#e34948")];
    return {
      background: null,
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
      title: { color: fg, fontSize: 15, anchor: "start" },
      axis: { labelColor: muted, titleColor: fg, gridColor: line, domainColor: muted,
              tickColor: muted },
      legend: { labelColor: fg, titleColor: fg },
      header: { labelColor: fg, titleColor: fg },
      view: { stroke: null },
      mark: { color: series[0] },
      range: { category: series, ramp: { scheme: "blues" }, heatmap: { scheme: "blues" } }
    };
  }
  function draw() {
    var config = theme();
    document.querySelectorAll("script[data-opensak-vega]").forEach(function (el) {
      var target = document.getElementById(el.getAttribute("data-opensak-vega"));
      var spec;
      try { spec = JSON.parse(el.textContent); } catch (e) {
        target.textContent = "Chart error: " + e; return;
      }
      vegaEmbed(target, spec, { config: config, renderer: "svg",
                                actions: el.getAttribute("data-actions") === "true" })
        .catch(function (e) { target.textContent = "Chart error: " + e; });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw);
  else draw();
})();
"""


@lru_cache(maxsize=1)
def page_scripts() -> str:
    """The <script> elements a page with Vega charts needs: the three
    vendored files and the start-up script."""
    folder = assets_dir()
    parts = []
    for name in _FILES:
        try:
            code = (folder / name).read_text(encoding="utf-8")
        except OSError as exc:
            raise VegaError(f"the Vega file {name} is missing: {exc}") from None
        safe = code.replace("</script", "<\\/script")
        parts.append(f"<script>{safe}</script>")
    parts.append(f"<script>{_RUN}</script>")
    return "\n".join(parts)


def uses_vega(html: str) -> bool:
    return MARKER in html
