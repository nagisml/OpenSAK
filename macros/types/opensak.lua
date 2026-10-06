---@meta
-- Generated from src/opensak/macro/runtime.py by scripts/generate_macro_api_docs.py — do not edit by hand.
-- OpenSAK Lua macro API, version 2. Reference: docs/macros/api.md

---Keys understood by `opensak.filter{}`, all combined with AND.
---@class opensak.FilterSpec
---@field type? string|string[] Cache type(s); the " Cache" suffix may be left out.
---@field container? string|string[] Container size(s).
---@field difficulty? number|number[] Exact value or {min, max}.
---@field terrain? number|number[] Exact value or {min, max}.
---@field found? boolean Only found or only unfound caches.
---@field available? boolean Only available caches (not disabled or archived).
---@field name? string "Contains" match on that field.
---@field code? string "Contains" match on that field.
---@field owner? string "Contains" match on that field.
---@field country? string "Contains" match on that field.
---@field state? string "Contains" match on that field.
---@field county? string "Contains" match on that field.
---@field where? string Raw clause against the caches table.
---@field label? string Shown in the toolbar (optional, default "Macro").

---Filter keys plus `fields`, understood by `opensak.caches{}`.
---@class opensak.CachesSpec: opensak.FilterSpec
---@field fields? string[] Cache fields to load (`code` is always included).

---A cache as returned by `opensak.cache()` and `opensak.caches()` (a snapshot).
---@class opensak.Cache
---@field code string GC code, e.g. "GC12345".
---@field name string Cache name.
---@field type string Cache type, e.g. "Traditional Cache".
---@field container string? Container size, e.g. "Small".
---@field lat number Posted latitude (decimal degrees).
---@field lon number Posted longitude (decimal degrees).
---@field difficulty number? Difficulty 1–5.
---@field terrain number? Terrain 1–5.
---@field owner string? Owner name.
---@field placed_by string? Placed-by name as shown on the listing.
---@field hidden string? Hidden date, "YYYY-MM-DD".
---@field found boolean Found by you.
---@field found_date string? Your find date, "YYYY-MM-DD".
---@field dnf boolean Your latest log is a Didn't find it.
---@field dnf_date string? Date of your DNF, "YYYY-MM-DD".
---@field ftf boolean You were first to find.
---@field available boolean Not disabled.
---@field archived boolean Archived.
---@field premium boolean Premium-member only.
---@field country string? Country.
---@field state string? State / region.
---@field county string? County.
---@field distance number? Distance from the active centre point in km.
---@field bearing number? Bearing from the active centre point in degrees.
---@field elevation number? Elevation in metres.
---@field favorite_points integer? Favourite points (nil until known).
---@field find_count integer? Number of Found it logs by anyone.
---@field user_flag boolean User flag.
---@field user_sort integer? User sort value.
---@field user_data string[] User data fields 1–4; "" when empty.
---@field color string? Colour tag, e.g. "#FF5733".
---@field locked boolean Locked against import changes.
---@field watch boolean On the watchlist.
---@field note string? Your local note.
---@field gc_note string? Personal note from geocaching.com.
---@field hint string? Hint text as imported.
---@field url string? Listing URL.
---@field corrected {lat: number, lon: number}? Corrected coordinates; nil if the cache is not solved.
---@field waypoint_count integer Number of additional waypoints.
---@field log_count integer Number of logs stored.
---@field trackable_count integer Number of trackables in the cache.
---@field last_log_date string? Date of the latest log, "YYYY-MM-DD".

---The OpenSAK API, available as a global in every macro.
opensak = {}

---The API version of this OpenSAK build. Each function lists the version it was added in.
---
---Since API version 1.
---
---```lua
---if opensak.api_version() < 1 then
---    error("this macro needs a newer OpenSAK")
---end
---```
---@return integer # The API version.
function opensak.api_version() end

---Build a filter from the given keys (see Filter keys; all combined with AND) and apply it. Usually called with table syntax: `opensak.filter{ ... }`. When nothing matches, the view is left unchanged.
---
---Since API version 1.
---
---```lua
---local n = opensak.filter{ type = "Traditional", difficulty = {1, 2}, found = false }
---print("Easy unfound traditionals: " .. n)
---```
---@param spec opensak.FilterSpec The filter keys.
---@return integer # Number of matching caches (0 = view unchanged).
function opensak.filter(spec) end

---Apply a saved filter profile.
---
---Since API version 1.
---
---```lua
---local n = opensak.filter_profile("Unfound nearby")
---```
---@param name string Name of the saved profile.
---@return integer # Number of matching caches.
function opensak.filter_profile(name) end

---Remove the active filter, so all caches are shown again.
---
---Since API version 1.
---
---```lua
---opensak.clear_filter()
---```
function opensak.clear_filter() end

---The number of caches matching the active filter.
---
---Since API version 1.
---
---```lua
---print(opensak.count() .. " caches shown")
---```
---@return integer # Number of caches shown.
function opensak.count() end

---The names of all saved filter profiles.
---
---Since API version 1.
---
---```lua
---for _, name in ipairs(opensak.profiles()) do
---    print(name)
---end
---```
---@return string[] # Profile names.
function opensak.profiles() end

---One cache as a table (see Cache fields). It is a snapshot: changing it changes nothing in the database.
---
---Since API version 2.
---
---```lua
---local c = opensak.cache("GC12345")
---if c and c.corrected then print(c.name, c.corrected.lat, c.corrected.lon) end
---```
---@param code string GC code, e.g. "GC12345".
---@return opensak.Cache? # The cache, or nil if it is not in the database.
function opensak.cache(code) end

---Iterate over caches, one table per cache (see Cache fields), in a generic `for`. Without arguments: the caches of the active filter, in grid order. With filter keys (see Filter keys): the caches matching them, sorted by name; the view and the active filter stay unchanged. `fields` limits the fields loaded (`code` is always included), which makes loops over many caches faster. Caches are loaded in chunks, so large databases do not hit the memory limit.
---
---Since API version 2.
---
---```lua
---for c in opensak.caches() do print(c.code, c.name) end
---for c in opensak.caches{ found = true, country = "Switzerland",
---                         fields = {"difficulty", "terrain"} } do
---    print(c.code, c.difficulty, c.terrain)
---end
---```
---@param spec? opensak.CachesSpec Filter keys and/or `fields`; nothing = the active filter.
---@return fun(): opensak.Cache? # Iterator for a generic `for`.
function opensak.caches(spec) end

---The cache selected in the grid.
---
---Since API version 2.
---
---```lua
---local c = opensak.current()
---if c then print(c.code .. " " .. c.name) end
---```
---@return opensak.Cache? # The cache, or nil if no row is selected.
function opensak.current() end

---The GC codes of the rows selected in the grid.
---
---Since API version 2.
---
---```lua
---for _, code in ipairs(opensak.selected()) do print(code) end
---```
---@return string[] # GC codes; empty if nothing is selected.
function opensak.selected() end

---The GC codes of the caches of the active filter, in grid order. Cheaper than opensak.caches() when only the codes are needed.
---
---Since API version 2.
---
---```lua
---print(table.concat(opensak.codes(), ", "))
---```
---@return string[] # GC codes.
function opensak.codes() end

---The listing description of a cache. Not part of the cache table because it can be large.
---
---Since API version 2.
---
---```lua
---local d = opensak.description("GC12345")
---if d and d.long and d.long:find("bonus") then print("bonus cache") end
---```
---@param code string GC code, e.g. "GC12345".
---@return {short: string?, long: string?, html: boolean}? # Short and long description and whether they are HTML; nil if the cache is not in the database.
function opensak.description(code) end

---Set corrected coordinates, either as decimal degrees or as one coordinate string in any format OpenSAK understands (DMM, DMS, decimal degrees).
---
---Since API version 1.
---
---```lua
---opensak.set_corrected("GC12345", 47.36872, 8.54093)
---opensak.set_corrected("GC12345", "N47 22.123 E008 32.456")
---```
---@param code string GC code, e.g. "GC12345".
---@param lat number|string Latitude in decimal degrees.
---@param lon number|string Longitude in decimal degrees.
---@return boolean # false if the cache is not in the database.
---@overload fun(code: string, coords: string): boolean
function opensak.set_corrected(code, lat, lon) end

---Remove the corrected coordinates of a cache.
---
---Since API version 1.
---
---```lua
---opensak.clear_corrected("GC12345")
---```
---@param code string GC code, e.g. "GC12345".
---@return boolean # false if the cache is not in the database.
function opensak.clear_corrected(code) end

---Read a CSV file (UTF-8) into an array of rows keyed by the header line. A relative path is resolved against the macro file's folder. If the file's folder has no read permission (Settings → Folder permissions), OpenSAK asks the user to allow it for this run or always. The file may be at most 10 MB.
---
---Since API version 1.
---
---```lua
---for _, row in ipairs(opensak.read_csv("solved.csv")) do
---    opensak.set_corrected(row.code, row.coords)
---end
---```
---@param path string The CSV file.
---@param sep? string Separator character; detected among , ; and tab if omitted.
---@return table<string, string>[] # One table per data row, keyed by header.
function opensak.read_csv(path, sep) end

---Export the caches of the active filter with a saved export setting (File → Export → GPX/LOC/GGZ: format, folder, file name, if the file exists, corrected coordinates, max. caches). The file name variables are filled in as in the dialog, {filter} with the name of the active filter and {center} with the active centre point. The setting needs a folder, and that folder needs write permission (Settings → Folder permissions). Nothing is written when no cache with coordinates is shown, or when the file exists and the setting says skip (or ask, and the user answers No).
---
---Since API version 1.
---
---```lua
---local path, n = opensak.export_file("GPX Export")
---if path then print(n .. " caches → " .. path) end
---```
---@param setting string Name of the saved export setting.
---@return string?, integer? # The file written and the number of caches in it; nil if nothing was written.
function opensak.export_file(setting) end

---Ask the user a Yes/No question.
---
---Since API version 1.
---
---```lua
---if not opensak.confirm("Update 12 caches?") then return end
---```
---@param message string The question.
---@return boolean # true on Yes.
function opensak.confirm(message) end

---Let the user pick a file in a file dialog. The picked file may be used for the rest of this run without a folder permission: read with mode "open" (the default), written with mode "save". OpenSAK's own settings and database files cannot be picked. The dialog starts in the macro file's folder.
---
---Since API version 1.
---
---```lua
---local path = opensak.choose_file("Solved puzzles", "CSV files (*.csv)")
---if not path then return end      -- cancelled
---for _, row in ipairs(opensak.read_csv(path)) do
---    opensak.set_corrected(row.code, row.coords)
---end
---```
---@param title? string Dialog title.
---@param filter? string File types, e.g. "CSV files (*.csv);;All files (*)".
---@param mode? "open"|"save" "open" picks an existing file to read (default), "save" a file to write (the dialog asks before replacing an existing one).
---@return string? # Full path of the picked file, or nil if cancelled.
function opensak.choose_file(title, filter, mode) end

---OpenSAK's folder inside the system temp folder (read and write permission by default), without a trailing separator. "/" works as separator on every platform.
---
---Since API version 1.
---
---```lua
---local rows = opensak.read_csv(opensak.temp_dir() .. "/solved.csv")
---```
---@return string # Folder path.
function opensak.temp_dir() end

---OpenSAK's macros folder (read permission by default), without a trailing separator.
---
---Since API version 1.
---
---```lua
---local rows = opensak.read_csv(opensak.macros_dir() .. "/data/solved.csv")
---```
---@return string # Folder path.
function opensak.macros_dir() end
