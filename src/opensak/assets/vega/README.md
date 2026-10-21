# Vega-Lite (vendored)

Used by `opensak.chart.vega()` (`src/opensak/stats/vega.py`): `opensak.html.page` puts these three files
inline into a report that contains a Vega-Lite chart, so the page works offline. Unchanged minified
builds from npm (via jsDelivr), BSD-3-Clause, © University of Washington Interactive Data Lab — see the
`LICENSE-*.txt` files.

| File | Package | SHA-256 |
|---|---|---|
| `vega.min.js` | `vega@5.30.0` (`build/vega.min.js`) | `e432c751a6363f4a61da62920cc7d7ebd13cf09d82949f8f486248f8071dc3ce` |
| `vega-lite.min.js` | `vega-lite@5.21.0` (`build/vega-lite.min.js`) | `cd32314b1e76e7d879dc9f0534b62be714df03554486c7ca2381abfd0a92d2f4` |
| `vega-embed.min.js` | `vega-embed@6.26.0` (`build/vega-embed.min.js`) | `072c054f2a6310725e038c38a71e00052705e31835632462c9717a23a384e895` |

The hashes were checked against jsDelivr's published file hashes for these package versions. To update:
download the same three files of the new versions, check their hashes, and update `VERSIONS` in
`src/opensak/stats/vega.py` and this table.
