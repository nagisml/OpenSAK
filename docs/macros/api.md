<!-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand. -->

# OpenSAK Lua macro API

API version: **2** (`opensak.api_version()`).

Macros are Lua 5.4 scripts run in a sandbox. They talk to OpenSAK through the global `opensak` table. File access is limited to the folders listed in Settings → Folder permissions. When a macro needs a file in another folder, OpenSAK asks the user whether to allow that folder for this run only or always, or to deny it; reading and writing are asked separately. OpenSAK's own settings and database files are never accessible.

See [Example macros](#example-macros) for complete scripts and [Editor support](#editor-support-vs-code) for autocompletion in VS Code.

## Functions

| Function | Since |
|---|---|
| [`opensak.api_version`](#opensakapiversion) | 1 |
| [`opensak.filter`](#opensakfilter) | 1 |
| [`opensak.filter_profile`](#opensakfilterprofile) | 1 |
| [`opensak.clear_filter`](#opensakclearfilter) | 1 |
| [`opensak.count`](#opensakcount) | 1 |
| [`opensak.profiles`](#opensakprofiles) | 1 |
| [`opensak.cache`](#opensakcache) | 2 |
| [`opensak.caches`](#opensakcaches) | 2 |
| [`opensak.current`](#opensakcurrent) | 2 |
| [`opensak.selected`](#opensakselected) | 2 |
| [`opensak.codes`](#opensakcodes) | 2 |
| [`opensak.description`](#opensakdescription) | 2 |
| [`opensak.sql`](#opensaksql) | 2 |
| [`opensak.sql_each`](#opensaksqleach) | 2 |
| [`opensak.tables`](#opensaktables) | 2 |
| [`opensak.columns`](#opensakcolumns) | 2 |
| [`opensak.databases`](#opensakdatabases) | 2 |
| [`opensak.database`](#opensakdatabase) | 2 |
| [`opensak.database_exists`](#opensakdatabaseexists) | 2 |
| [`opensak.create_database`](#opensakcreatedatabase) | 2 |
| [`opensak.switch_database`](#opensakswitchdatabase) | 2 |
| [`opensak.move_caches`](#opensakmovecaches) | 2 |
| [`opensak.copy_caches`](#opensakcopycaches) | 2 |
| [`opensak.set_corrected`](#opensaksetcorrected) | 1 |
| [`opensak.clear_corrected`](#opensakclearcorrected) | 1 |
| [`opensak.read_csv`](#opensakreadcsv) | 1 |
| [`opensak.export_file`](#opensakexportfile) | 1 |
| [`opensak.confirm`](#opensakconfirm) | 1 |
| [`opensak.choose_file`](#opensakchoosefile) | 1 |
| [`opensak.temp_dir`](#opensaktempdir) | 1 |
| [`opensak.macros_dir`](#opensakmacrosdir) | 1 |

### opensak.api_version

```lua
opensak.api_version()
```

The API version of this OpenSAK build. Each function lists the version it was added in.

Returns `integer` — The API version.

Since API version 1.

Example:

```lua
if opensak.api_version() < 1 then
    error("this macro needs a newer OpenSAK")
end
```

### opensak.filter

```lua
opensak.filter(spec)
```

Build a filter from the given keys (see Filter keys; all combined with AND) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.

Parameters:

- `spec` (`opensak.FilterSpec`) — The filter keys.

Returns `integer` — Number of matching caches (0 = view unchanged).

Since API version 1.

Example:

```lua
local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
print("Easy unfound traditionals: " .. n)
```

### opensak.filter_profile

```lua
opensak.filter_profile(name)
```

Apply a saved filter profile.

Parameters:

- `name` (`string`) — Name of the saved profile.

Returns `integer` — Number of matching caches.

Since API version 1.

Example:

```lua
local n = opensak.filter_profile("Unfound nearby")
```

### opensak.clear_filter

```lua
opensak.clear_filter()
```

Remove the active filter, so all caches are shown again.

Since API version 1.

Example:

```lua
opensak.clear_filter()
```

### opensak.count

```lua
opensak.count()
```

The number of caches matching the active filter.

Returns `integer` — Number of caches shown.

Since API version 1.

Example:

```lua
print(opensak.count() .. " caches shown")
```

### opensak.profiles

```lua
opensak.profiles()
```

The names of all saved filter profiles.

Returns `string[]` — Profile names.

Since API version 1.

Example:

```lua
for _, name in ipairs(opensak.profiles()) do
    print(name)
end
```

### opensak.cache

```lua
opensak.cache(code)
```

One cache as a table (see Cache fields). It is a snapshot: changing it changes nothing in the database.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".

Returns `opensak.Cache?` — The cache, or nil if it is not in the database.

Since API version 2.

Example:

```lua
local c = opensak.cache("GC12345")
if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end
```

### opensak.caches

```lua
opensak.caches([spec])
```

Iterate over caches, one table per cache (see Cache fields), in a generic `for`. Without arguments: the caches of the active filter, in grid order. With filter keys (see Filter keys): the caches matching them, sorted by name; the view and the active filter stay unchanged. `fields` limits the fields loaded (`code` is always included), which makes loops over many caches faster. Caches are loaded in chunks, so large databases do not hit the memory limit.

Parameters:

- `spec` (`opensak.CachesSpec`, optional) — Filter keys and/or `fields`; nothing = the active filter.

Returns `fun(): opensak.Cache?` — Iterator for a generic `for`.

Since API version 2.

Example:

```lua
for c in opensak.caches() do print(c.code, c.name) end
for c in opensak.caches{ found = true, country = "Switzerland",
                         fields = {"difficulty", "terrain"} } do
    print(c.code, c.difficulty, c.terrain)
end
```

### opensak.current

```lua
opensak.current()
```

The cache selected in the grid.

Returns `opensak.Cache?` — The cache, or nil if no row is selected.

Since API version 2.

Example:

```lua
local c = opensak.current()
if c then print(c.code .. " " .. c.name) end
```

### opensak.selected

```lua
opensak.selected()
```

The GC codes of the rows selected in the grid.

Returns `string[]` — GC codes; empty if nothing is selected.

Since API version 2.

Example:

```lua
for _, code in ipairs(opensak.selected()) do print(code) end
```

### opensak.codes

```lua
opensak.codes()
```

The GC codes of the caches of the active filter, in grid order. Cheaper than opensak.caches() when only the codes are needed.

Returns `string[]` — GC codes.

Since API version 2.

Example:

```lua
print(table.concat(opensak.codes(), ", "))
```

### opensak.description

```lua
opensak.description(code)
```

The listing description of a cache. Not part of the cache table because it can be large.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".

Returns `{short: string?, long: string?, html: boolean}?` — Short and long description and whether they are HTML; nil if the cache is not in the database.

Since API version 2.

Example:

```lua
local d = opensak.description("GC12345")
if d and d.long and d.long:find("bonus") then print("bonus cache") end
```

### opensak.sql

```lua
opensak.sql(query [, params])
```

Run a read-only SQL query (SQLite) against the active database and return all rows. Only reading statements are allowed; the connection itself is read-only. Column names follow the database schema, which may change between versions (see opensak.tables() and opensak.columns()). Use `AS` to name computed columns. NULL values are nil. At most 100,000 rows; use opensak.sql_each() for more. A query is aborted after 60 s.

Parameters:

- `query` (`string`) — One SQL statement.
- `params` (`table`, optional) — Values for `?` placeholders ({ v1, v2 }) or for `:name` placeholders ({ name = v }).

Returns `table<string, any>[]` — One table per row, keyed by column name.

Since API version 2.

Example:

```lua
local rows = opensak.sql(
  "SELECT country, COUNT(*) AS n FROM caches WHERE found = ? GROUP BY country", { 1 })
for _, r in ipairs(rows) do print(r.country, r.n) end
```

### opensak.sql_each

```lua
opensak.sql_each(query [, params])
```

Like opensak.sql(), but returns an iterator for a generic `for` that fetches the rows in chunks — for results of any size.

Parameters:

- `query` (`string`) — One SQL statement.
- `params` (`table`, optional) — As for opensak.sql().

Returns `fun(): table<string, any>?` — Iterator for a generic `for`.

Since API version 2.

Example:

```lua
for r in opensak.sql_each("SELECT gc_code, name FROM caches WHERE found = 0") do
    print(r.gc_code, r.name)
end
```

### opensak.tables

```lua
opensak.tables()
```

The tables and views of the active database, for use with opensak.sql().

Returns `string[]` — Table and view names, sorted.

Since API version 2.

Example:

```lua
print(table.concat(opensak.tables(), ", "))
```

### opensak.columns

```lua
opensak.columns(table)
```

The columns of a table or view of the active database.

Parameters:

- `table` (`string`) — Table or view name.

Returns `{name: string, type: string}[]` — Column names and SQL types, in table order.

Since API version 2.

Example:

```lua
for _, c in ipairs(opensak.columns("caches")) do print(c.name, c.type) end
```

### opensak.databases

```lua
opensak.databases()
```

All databases in OpenSAK's database list, sorted by name.

Returns `{name: string, path: string, active: boolean, size_mb: number}[]` — One table per database.

Since API version 2.

Example:

```lua
for _, db in ipairs(opensak.databases()) do
    print(db.name, db.active and "(active)" or "", db.size_mb .. " MB")
end
```

### opensak.database

```lua
opensak.database()
```

The name of the active database.

Returns `string` — Database name.

Since API version 2.

Example:

```lua
print("Working on " .. opensak.database())
```

### opensak.database_exists

```lua
opensak.database_exists(name)
```

Whether a database with this name is in the database list (exact, case-sensitive match).

Parameters:

- `name` (`string`) — Database name.

Returns `boolean` — true if it exists.

Since API version 2.

Example:

```lua
if not opensak.database_exists("CH_Zurich") then
    opensak.create_database("CH_Zurich")
end
```

### opensak.create_database

```lua
opensak.create_database(name)
```

Create a new, empty database in the default database folder and add it to the list. The active database does not change. Fails if the name is taken or its file already exists.

Parameters:

- `name` (`string`) — Name of the new database.

Returns `string` — The name actually used (surrounding spaces removed).

Since API version 2.

Example:

```lua
local name = opensak.create_database("CH_Zurich")
```

### opensak.switch_database

```lua
opensak.switch_database(name)
```

Make another database the active one, as the toolbar dropdown does. The active filter is cleared.

Parameters:

- `name` (`string`) — Database name.

Since API version 2.

Example:

```lua
opensak.switch_database("CH_Zurich")
print(opensak.count() .. " caches in " .. opensak.database())
```

### opensak.move_caches

```lua
opensak.move_caches(target [, options])
```

Move caches from the active database to another one, with all logs, waypoints, attributes, trackables and notes (like Database → Move caches). By default the caches of the active filter. When a cache already exists in the target, `if_exists` decides: `"newer"` (default) replaces it only if the active database's copy was imported later, `"replace"` always, `"skip"` never. A cache not written to the target stays in the active database.

Parameters:

- `target` (`string`) — Name of the target database (not the active one).
- `options` (`opensak.TransferOptions`, optional) — codes, if_exists.

Returns `integer` — Number of caches moved.

Since API version 2.

Example:

```lua
local n = opensak.move_caches("CH_Zurich")
print(n .. " caches moved")
```

### opensak.copy_caches

```lua
opensak.copy_caches(target [, options])
```

Like opensak.move_caches(), but the caches stay in the active database.

Parameters:

- `target` (`string`) — Name of the target database (not the active one).
- `options` (`opensak.TransferOptions`, optional) — codes, if_exists.

Returns `integer` — Number of caches copied.

Since API version 2.

Example:

```lua
local n = opensak.copy_caches("CH_Zurich", { codes = {"GC1", "GC2"}, if_exists = "replace" })
```

### opensak.set_corrected

```lua
opensak.set_corrected(code, lat, lon)
opensak.set_corrected(code, coords)
```

Set corrected coordinates, either as decimal degrees or as one coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".
- `lat` (`number|string`) — Latitude in decimal degrees.
- `lon` (`number|string`) — Longitude in decimal degrees.
- `coords` (`string`) — Coordinates, e.g. "N47 22.123 E008 32.456".

Returns `boolean` — false if the cache is not in the database.

Since API version 1.

Example:

```lua
opensak.set_corrected("GC12345", 47.36872, 8.54093)
opensak.set_corrected("GC12345", "N47 22.123 E008 32.456")
```

### opensak.clear_corrected

```lua
opensak.clear_corrected(code)
```

Remove the corrected coordinates of a cache.

Parameters:

- `code` (`string`) — GC code, e.g. "GC12345".

Returns `boolean` — false if the cache is not in the database.

Since API version 1.

Example:

```lua
opensak.clear_corrected("GC12345")
```

### opensak.read_csv

```lua
opensak.read_csv(path [, sep])
```

Read a CSV file (UTF-8) into an array of rows keyed by the header line. A relative path is resolved against the macro file's folder. If the file's folder has no read permission (Settings → Folder permissions), OpenSAK asks the user to allow it for this run or always. The file may be at most 10 MB.

Parameters:

- `path` (`string`) — The CSV file.
- `sep` (`string`, optional) — Separator character; detected among , ; and tab if omitted.

Returns `table<string, string>[]` — One table per data row, keyed by header.

Since API version 1.

Example:

```lua
for _, row in ipairs(opensak.read_csv("solved.csv")) do
    opensak.set_corrected(row.code, row.coords)
end
```

### opensak.export_file

```lua
opensak.export_file(setting)
```

Export the caches of the active filter with a saved export setting (File → Export → GPX/LOC/GGZ: format, folder, file name, if the file exists, corrected coordinates, max. caches). The file name variables are filled in as in the dialog, {filter} with the name of the active filter and {center} with the active centre point. The setting needs a folder, and that folder needs write permission (Settings → Folder permissions). Nothing is written when no cache with coordinates is shown, or when the file exists and the setting says skip (or ask, and the user answers No).

Parameters:

- `setting` (`string`) — Name of the saved export setting.

Returns `string?, integer?` — The file written and the number of caches in it; nil if nothing was written.

Since API version 1.

Example:

```lua
local path, n = opensak.export_file("GPX Export")
if path then print(n .. " caches → " .. path) end
```

### opensak.confirm

```lua
opensak.confirm(message)
```

Ask the user a Yes/No question.

Parameters:

- `message` (`string`) — The question.

Returns `boolean` — true on Yes.

Since API version 1.

Example:

```lua
if not opensak.confirm("Update 12 caches?") then return end
```

### opensak.choose_file

```lua
opensak.choose_file([title] [, filter] [, mode])
```

Let the user pick a file in a file dialog. The picked file may be used for the rest of this run without a folder permission: read with mode "open" (the default), written with mode "save". OpenSAK's own settings and database files cannot be picked. The dialog starts in the macro file's folder.

Parameters:

- `title` (`string`, optional) — Dialog title.
- `filter` (`string`, optional) — File types, e.g. "CSV files (*.csv);;All files (*)".
- `mode` (`"open"|"save"`, optional) — "open" picks an existing file to read (default), "save" a file to write (the dialog asks before replacing an existing one).

Returns `string?` — Full path of the picked file, or nil if cancelled.

Since API version 1.

Example:

```lua
local path = opensak.choose_file("Solved puzzles", "CSV files (*.csv)")
if not path then return end      -- cancelled
for _, row in ipairs(opensak.read_csv(path)) do
    opensak.set_corrected(row.code, row.coords)
end
```

### opensak.temp_dir

```lua
opensak.temp_dir()
```

OpenSAK's folder inside the system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.

Returns `string` — Folder path.

Since API version 1.

Example:

```lua
local rows = opensak.read_csv(opensak.temp_dir() .. "/solved.csv")
```

### opensak.macros_dir

```lua
opensak.macros_dir()
```

OpenSAK's macros folder (read permission by default), without a trailing separator.

Returns `string` — Folder path.

Since API version 1.

Example:

```lua
local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")
```

## Filter keys

Keys understood by `opensak.filter{}`, all combined with AND.

| Key | Value | Meaning |
|---|---|---|
| `type` | `"Traditional" \| {"Traditional", "Multi-cache", ...}` | Cache type(s); the " Cache" suffix may be left out. |
| `container` | `"Small" \| {"Micro", "Small", ...}` | Container size(s). |
| `difficulty` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `terrain` | `2 \| {1, 2.5}` | Exact value or {min, max}. |
| `found` | `true \| false` | Only found or only unfound caches. |
| `available` | `true` | Only available caches (not disabled or archived). |
| `name`, `code`, `owner`, `country`, `state`, `county` | `"text"` | "Contains" match on that field. |
| `where` | `"SQL WHERE clause"` | Raw clause against the caches table. |
| `label` | `"text"` | Shown in the toolbar (optional, default "Macro"). |

`opensak.caches{}` also takes `fields`, an array of the cache fields to load (`code` is always included).

## Cache fields

Keys of the table returned by `opensak.cache()`, `opensak.current()` and `opensak.caches()`. The table is a snapshot; missing data is `nil`.

| Field | Type | Meaning |
|---|---|---|
| `code` | `string` | GC code, e.g. "GC12345". |
| `name` | `string` | Cache name. |
| `type` | `string` | Cache type, e.g. "Traditional Cache". |
| `container` | `string?` | Container size, e.g. "Small". |
| `lat` | `number` | Posted latitude (decimal degrees). |
| `lon` | `number` | Posted longitude (decimal degrees). |
| `difficulty` | `number?` | Difficulty 1–5. |
| `terrain` | `number?` | Terrain 1–5. |
| `owner` | `string?` | Owner name. |
| `placed_by` | `string?` | Placed-by name as shown on the listing. |
| `hidden` | `string?` | Hidden date, "YYYY-MM-DD". |
| `found` | `boolean` | Found by you. |
| `found_date` | `string?` | Your find date, "YYYY-MM-DD". |
| `dnf` | `boolean` | Your latest log is a Didn't find it. |
| `dnf_date` | `string?` | Date of your DNF, "YYYY-MM-DD". |
| `ftf` | `boolean` | You were first to find. |
| `available` | `boolean` | Not disabled. |
| `archived` | `boolean` | Archived. |
| `premium` | `boolean` | Premium-member only. |
| `country` | `string?` | Country. |
| `state` | `string?` | State / region. |
| `county` | `string?` | County. |
| `distance` | `number?` | Distance from the active centre point in km. |
| `bearing` | `number?` | Bearing from the active centre point in degrees. |
| `elevation` | `number?` | Elevation in metres. |
| `favorite_points` | `integer?` | Favourite points (nil until known). |
| `find_count` | `integer?` | Number of Found it logs by anyone. |
| `user_flag` | `boolean` | User flag. |
| `user_sort` | `integer?` | User sort value. |
| `user_data` | `string[]` | User data fields 1–4; "" when empty. |
| `color` | `string?` | Colour tag, e.g. "#FF5733". |
| `locked` | `boolean` | Locked against import changes. |
| `watch` | `boolean` | On the watchlist. |
| `note` | `string?` | Your local note. |
| `gc_note` | `string?` | Personal note from geocaching.com. |
| `hint` | `string?` | Hint text as imported. |
| `url` | `string?` | Listing URL. |
| `corrected` | `{lat: number, lon: number}?` | Corrected coordinates; nil if the cache is not solved. |
| `waypoint_count` | `integer` | Number of additional waypoints. |
| `log_count` | `integer` | Number of logs stored. |
| `trackable_count` | `integer` | Number of trackables in the cache. |
| `last_log_date` | `string?` | Date of the latest log, "YYYY-MM-DD". |

## Example macros

Ready-to-use scripts to copy and adapt are in [`macros/examples/`](../../macros/examples/). Each one starts with a comment explaining what it does and which files it expects.

- [`corrected_coords_from_csv.lua`](../../macros/examples/corrected_coords_from_csv.lua) — set corrected coordinates from a CSV file
- [`export_filters_to_gpx.lua`](../../macros/examples/export_filters_to_gpx.lua) — one GPX file per saved filter

## Editor support (VS Code)

[`macros/types/opensak.lua`](../../macros/types/opensak.lua) describes this API for the [Lua Language Server](https://luals.github.io/) (VS Code extension "Lua" by sumneko): autocompletion, parameter hints and these docs while you type. Macros inside the OpenSAK repository pick it up automatically. For macros in another folder, put a `.luarc.json` next to them that points at the folder holding the stub:

```json
{
  "runtime.version": "Lua 5.4",
  "workspace.library": ["C:/path/to/OpenSAK/macros/types"]
}
```

## Globals

### print

```lua
print(...)
```

Write the arguments, separated by tabs, to the macro output pane.
