# Demo reports (temporary)

Output of the four statistics example macros, for reviewing the statistics, chart and map functions
without running OpenSAK:

| Page | Macro | Charts |
|---|---|---|
| `find_statistics.html` | `macros/examples/find_statistics.lua` | SVG (`opensak.chart.*`), no JavaScript, profile-safe tables |
| `find_statistics_vega.html` | `macros/examples/find_statistics_vega.lua` | the same statistics with interactive Vega-Lite charts: drag a period in the timeline, zoom the total |
| `database_statistics.html` | `macros/examples/database_statistics.lua` | SVG |
| `database_statistics_vega.html` | `macros/examples/database_statistics_vega.lua` | interactive Vega-Lite: drag over years in "caches placed per year" |
| `find_statistics_profile.html` | `macros/examples/find_statistics.lua` | geocaching.com profile version (`opensak.html.profile`): tables with coloured bars, inline styles only — paste the file's content into the profile |
| `database_statistics_profile.html` | `macros/examples/database_statistics.lua` | geocaching.com profile version, as above |

The profile keeps only text and tables with inline styles: no stylesheet, classes, JavaScript or SVG, and a
`data:` image makes the save fail with a server error (500). So the maps of single caches and the Vega
charts are a short note there; region maps are a list of the regions.

They were generated from a synthetic, anonymous database (5,000 made-up caches across Europe, 2,000 of
them found by "DemoCacher") — no real cache, owner or finder data. Note: the made-up GC codes still look
like real ones, so the code links in the tables lead to real listings (see the demo-database TODO).

**Remove this folder before merging to beta.**
