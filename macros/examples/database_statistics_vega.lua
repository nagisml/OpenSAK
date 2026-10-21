-- OpenSAK Macro
-- Name: Database statistics (interactive)
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page with interactive Vega-Lite charts and maps of all caches in a database
--
-- Known limitations:
-- - This is a template macro: copy it and adapt the statistics, texts and
--   layout to your needs. It is not a finished report.
-- - The charts need JavaScript, so the page cannot be used on
--   geocaching.com (e.g. pasted into your profile); for the profile use
--   database_statistics.lua and its PROFILE_FILE.
-- - The page holds the Vega files (about 0.8 MB) so it works offline.
-- - Maps use OpenSAK's boundary data (the outlines of Update location),
--   which is not yet stable: outlines, names and the country of a state may
--   still change. Without the boundary data there are no maps, and county
--   outlines are downloaded per country on first use.
-- - Region names come from the cache data (GPX / GSAK import or Update
--   location) and are matched to the outlines ignoring case, accents and
--   punctuation; names spelled differently (e.g. in another language) are
--   listed at the end of the page. Caches without a country or state are
--   left out of those statistics.
-- - The page is in English only; dates are shown as stored (YYYY-MM-DD).
-- - The statistics describe what is in the database, not geocaching.com:
--   archived and disabled caches are only as current as your last import,
--   and favourite points as of the import of each cache.
-- - All caches of the database are read, whatever the active filter shows;
--   a large database (100 000+ caches) takes a while. The dot map shows at
--   most MAX_DOTS caches (every n-th one).
-- - "Home" for the direction is HOME below, else the active centre point
--   (the centre drop-down in the toolbar).
-- - The states shown are those of one country: COUNTRY below, or else the
--   country with the most caches in the database. In a database mixing
--   countries (e.g. a holiday area larger than your home), set COUNTRY.
-- - Countries and states are counted as the regions of the boundary data
--   that the names in the cache data match, so "states in …" can never be
--   more than all states. Names that match no region are listed at the end.
--
-- The same statistics of all caches in a database as database_statistics.lua,
-- but the charts and maps are interactive Vega-Lite charts made with
-- opensak.chart.vega(): drag over some years in "caches placed per year"
-- and the charts below it show only the caches placed then; hover for
-- details. Writes database_statistics_vega.html into OpenSAK's temp folder
-- and opens it in your browser. Each chart's ⋯ menu saves it as SVG or PNG.
--
-- A database can hold 100 000 caches and more, so the caches are counted in
-- Lua first (one row per combination of year, status, type, size, D/T and
-- found, with the number of caches in n) and Vega-Lite adds up n — that
-- keeps the page small.

local DATABASE = nil    -- e.g. "Switzerland"; nil = the active database
local HOME = nil        -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = the active centre point
local COUNTRY = nil     -- e.g. "United States": whose states are shown; nil = the country with most caches
local TOP = 15          -- rows in the region, owner and favourite lists
local MAX_DOTS = 20000  -- the dot map shows at most this many caches
local FILE = opensak.temp_dir() .. "/database_statistics_vega.html"
local OPEN = true        -- open the page in the browser when done

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date
local WIDE, HALF = 760, 340

-- ── All caches, with a few fields derived for counting ───────────────────
local caches = {}
for c in opensak.caches{
    all = true, database = DATABASE,
    fields = { "name", "type", "container", "difficulty", "terrain", "hidden", "found",
               "archived", "available", "premium", "country", "state", "owner",
               "favorite_points", "url", "lat", "lon" },
} do
    c.status = c.archived and "archived" or (c.available and "active" or "disabled")
    c.found_n = c.found and 1 or 0
    c.to_find = (not c.found and c.status == "active") and 1 or 0
    c.region = (c.country or "?") .. (c.state and (" / " .. c.state) or "")
    c.year = c.hidden and date.period(c.hidden, "year")
    caches[#caches + 1] = c
end
local name = DATABASE or opensak.database()
if #caches == 0 then
    print("Database " .. name .. " has no caches.")
    return
end

local function percent(part, whole)
    return whole > 0 and string.format("%.0f %%", 100 * part / whole) or ""
end

local found_caches, open_caches = {}, {}
for _, c in ipairs(caches) do
    if c.found then found_caches[#found_caches + 1] = c end
    if c.to_find == 1 then open_caches[#open_caches + 1] = c end
end
local around = #found_caches > 0 and found_caches or caches
-- Home: HOME, else the active centre point (the centre drop-down in the
-- toolbar, which OpenSAK's distances are measured from), else the middle
-- of the caches you found (else of all caches).
local center = opensak.center()
local home = HOME or (center.lat and center.lon and { lat = center.lat, lon = center.lon })
    or { lat = stats.summary(around, "lat").median, lon = stats.summary(around, "lon").median }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
for _, c in ipairs(open_caches) do
    local deg = opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon)
    c.dir = DIRS[math.floor((deg + 22.5) / 45) % 8 + 1]
end

-- ── Statistics (the same as database_statistics.lua) ─────────────────────
local found = #found_caches
local to_find = #open_caches
local hidden = stats.summary(caches, "hidden")
local favs = stats.summary(caches, "favorite_points")
local status = stats.group(caches, "status", { sort = "count" })
local placing = stats.streaks(caches, "hidden")

local types = stats.group(caches, "type", { sort = "count", sum = "found_n" })
for _, t in ipairs(types) do t.share = percent(t.sum, t.count) end
local placed = stats.fill(stats.group(caches, "hidden", { period = "year" }), { period = "year" })

local countries = stats.group(caches, "country", { sort = "count", sum = "found_n" })
for _, c in ipairs(countries) do c.share = percent(c.sum, c.count) end
local main_country = COUNTRY or (countries[1] and countries[1].key)
local main_key = main_country and opensak.text.lower(main_country)
local in_main, open_in_main = {}, {}
for _, c in ipairs(caches) do
    if c.country and opensak.text.lower(c.country) == main_key then
        in_main[#in_main + 1] = c
        if c.to_find == 1 then open_in_main[#open_in_main + 1] = c end
    end
end
local open_states = stats.group(open_in_main, "state")

-- Regions named by *rows* ({key =, count =}), matched to the boundary data
-- like the maps match them → "n / all" and the names that match no region.
local unmatched_rows = {}
local function coverage(rows, layer, within)
    local all, unmatched = opensak.geo.regions(layer, { within = within, rows = rows })
    if not all then return tostring(#rows) end      -- no boundary data
    local n = 0
    for _, r in ipairs(all) do if r.count then n = n + 1 end end
    local count_of = {}
    for _, r in ipairs(rows) do count_of[r.key] = r.count end
    for _, k in ipairs(unmatched) do
        unmatched_rows[#unmatched_rows + 1] = { layer = layer, key = k, count = count_of[k] }
    end
    return n .. " / " .. #all
end
local countries_shown = coverage(countries, "country")
local states_shown = main_country and coverage(stats.group(in_main, "state"), "state", main_country) or "0"

local by_dir = {}
for _, g in ipairs(stats.group(open_caches, "dir")) do by_dir[g.key] = g.count end
local directions = {}
for i, d in ipairs(DIRS) do
    local a0 = (i - 1) * math.pi / 4 - math.pi / 8
    directions[i] = { key = d, count = by_dir[d] or 0,
                      a0 = a0 + 0.02, a1 = a0 + math.pi / 4 - 0.02, mid = a0 + math.pi / 8 }
end

-- The dot map: every n-th cache still to find, so it stays fast.
local dots = {}
local every = math.max(1, math.ceil(#open_caches / MAX_DOTS))
for i = 1, #open_caches, every do
    local c = open_caches[i]
    dots[#dots + 1] = { lat = c.lat, lon = c.lon, name = c.name, type = c.type }
end

local regions = stats.group(caches, "region", { sort = "count", top = TOP })
local owners = stats.group(caches, "owner", { sort = "count", sum = "favorite_points", top = TOP })
local best = {}
for i, c in ipairs(stats.sort(caches, "-favorite_points", "name")) do
    if i > TOP then break end
    best[i] = c
end

-- ── The caches counted per combination, for the dashboard ────────────────
local combos, combo_rows = {}, {}
for _, c in ipairs(caches) do
    local found_text = c.found and "found" or "not found"
    local k = table.concat({ c.year or "?", c.status, c.type, c.container or "?",
                             tostring(c.difficulty), tostring(c.terrain), found_text }, "|")
    local r = combos[k]
    if not r then
        r = { year = c.year, status = c.status, type = c.type, size = c.container or "?",
              d = c.difficulty, t = c.terrain, found = found_text, n = 0 }
        combos[k] = r
        combo_rows[#combo_rows + 1] = r
    end
    r.n = r.n + 1
end

-- ── Chart building blocks ────────────────────────────────────────────────
local N = { aggregate = "sum", field = "n", type = "quantitative", title = "Caches" }
local STATUS = { field = "status", type = "nominal", title = "Status",
                 scale = { domain = { "active", "disabled", "archived" } } }
local in_years = { { filter = { param = "years" } } }      -- the selected years only
local GREY = "rgba(128,128,128,0.15)"
local EDGE = "rgba(128,128,128,0.6)"
local FLAT = { labelAngle = 0 }

-- A rect heatmap with the number written in each cell. The cells are
-- counted first (or field *sum* added up), and the largest cell of the view
-- is looked up, so the number is white on dark cells and dark on light ones
-- — readable in the light and the dark theme, also in a filtered period.
local function heatmap(spec, x, y, sum, title)
    local value = sum and { op = "sum", field = sum, as = "v" } or { op = "count", as = "v" }
    local transform = {}
    for _, t in ipairs(spec.transform or {}) do transform[#transform + 1] = t end
    transform[#transform + 1] = { aggregate = { value }, groupby = { x.field, y.field } }
    transform[#transform + 1] = { joinaggregate = { { op = "max", field = "v", as = "top" } } }
    spec.transform = transform
    local V = { field = "v", type = "quantitative", title = title or "Finds" }
    spec.layer = {
        { mark = "rect",
          encoding = { x = x, y = y, tooltip = { x, y, V },
                       color = { field = "v", type = "quantitative", title = title or "Finds",
                                 scale = { scheme = "blues" } } } },
        { mark = { type = "text", fontSize = 10 },
          encoding = { x = x, y = y, text = V,
                       color = { condition = { test = "datum.v > datum.top * 0.55", value = "#ffffff" },
                                 value = "#1a1a19" } } },
    }
    return spec
end

local function geo(collection)
    return { values = collection, format = { type = "json", property = "features" } }
end

local function region_map(title, regions_, background, value_title)
    local layers = {}
    if background then
        layers[#layers + 1] = { data = geo(background),
                                mark = { type = "geoshape", fill = GREY, stroke = EDGE, strokeWidth = 0.5 } }
    end
    layers[#layers + 1] = {
        data = geo(regions_),
        mark = { type = "geoshape", stroke = EDGE, strokeWidth = 0.5 },
        encoding = {
            color = { condition = { test = "datum.properties.count > 0", field = "properties.count",
                                    type = "quantitative", title = value_title,
                                    scale = { type = "log" } },
                      value = GREY },
            tooltip = { { field = "properties.name", type = "nominal", title = "Region" },
                        { field = "properties.count", type = "quantitative", title = value_title } },
        },
    }
    return chart.vega{ title = title, width = WIDE, height = 480,
                       projection = { type = "mercator" }, layer = layers }
end

local D = { field = "d", type = "ordinal", title = "Difficulty" }
local T = { field = "t", type = "ordinal", title = "Terrain", axis = FLAT }

-- ── The dashboard: select years, the rest follows ────────────────────────
local dashboard = chart.vega{
    data = { values = combo_rows },
    vconcat = {
        {
            title = "Caches placed per year — drag to select years for the charts below",
            width = WIDE, height = 240,
            mark = "bar",
            params = { { name = "years", select = { type = "interval", encodings = { "x" } } } },
            encoding = {
                x = { field = "year", type = "temporal", timeUnit = "utcyear", title = "" },
                y = N, color = STATUS,
                tooltip = { { field = "year", type = "nominal", title = "Year" }, STATUS, N },
            },
        },
        {
            title = "Caches per type (found / not found)", width = WIDE, height = 300,
            transform = in_years, mark = "bar",
            encoding = {
                y = { field = "type", type = "nominal", sort = "-x", title = "" },
                x = N,
                color = { field = "found", type = "nominal", title = "",
                          scale = { domain = { "found", "not found" } } },
                tooltip = { { field = "type", type = "nominal" }, { field = "found", type = "nominal" }, N },
            },
        },
        {
            hconcat = {
                {
                    title = "Size", width = HALF - 40, height = 200,
                    transform = in_years, mark = "bar",
                    encoding = {
                        x = { field = "size", type = "nominal", sort = "-y", title = "", axis = FLAT },
                        y = N, tooltip = { { field = "size", type = "nominal" }, N },
                    },
                },
                {
                    title = "Status", width = HALF - 40, height = 200,
                    transform = in_years, mark = "bar",
                    encoding = {
                        x = { field = "status", type = "nominal", sort = "-y", title = "", axis = FLAT },
                        y = N, color = STATUS, tooltip = { STATUS, N },
                    },
                },
            },
        },
        {
            hconcat = {
                heatmap({ title = "Difficulty × terrain: all caches", width = HALF, height = 260,
                          transform = in_years }, T, D, "n", "Caches"),
                heatmap({ title = "Still to find", width = HALF, height = 260,
                          transform = { { filter = { param = "years" } },
                                        { filter = "datum.status == 'active' && datum.found == 'not found'" } } },
                        T, D, "n", "Caches"),
            },
        },
    },
    resolve = { scale = { x = "independent", y = "independent", color = "independent" } },
}

local total = chart.vega{
    title = "Caches placed, total — scroll or drag to zoom",
    width = WIDE, height = 240,
    data = { values = placed },
    transform = { { window = { { op = "sum", field = "count", as = "total" } },
                    sort = { { field = "key" } } } },
    params = { { name = "zoom", select = "interval", bind = "scales" } },
    mark = { type = "area", line = true, opacity = 0.25 },
    encoding = {
        x = { field = "key", type = "temporal", timeUnit = "utcyear", title = "" },
        y = { field = "total", type = "quantitative", title = "Caches" },
        tooltip = { { field = "key", type = "nominal", title = "Year" },
                    { field = "count", type = "quantitative", title = "Placed" },
                    { field = "total", type = "quantitative", title = "Total" } },
    },
}

local ANGLE = { domain = { -math.pi, 2 * math.pi }, range = { -math.pi, 2 * math.pi } }  -- radians as they are
local rose = chart.vega{
    title = "Still to find: direction from home", width = 300, height = 300,
    data = { values = directions },
    layer = {
        { mark = { type = "arc", stroke = EDGE, strokeWidth = 0.5 },
          encoding = {
              theta = { field = "a0", type = "quantitative", scale = ANGLE },
              theta2 = { field = "a1" },
              radius = { field = "count", type = "quantitative",
                         scale = { type = "sqrt", zero = true, rangeMax = 130 } },
              tooltip = { { field = "key", type = "nominal", title = "Direction" },
                          { field = "count", type = "quantitative", title = "Caches" } },
          } },
        { mark = { type = "text" },
          encoding = {
              theta = { field = "mid", type = "quantitative", scale = ANGLE },
              radius = { value = 142 },
              text = { field = "key", type = "nominal" },
              color = { value = "currentColor" },
          } },
    },
}
local region_order = {}             -- as stats.group sorted them, "Other" last
for i, r in ipairs(regions) do region_order[i] = r.key end
local region_chart = chart.vega{
    title = "Regions", width = HALF, height = 18 * #regions,
    data = { values = regions },
    mark = "bar",
    encoding = {
        y = { field = "key", type = "nominal", sort = region_order, title = "" },
        x = { field = "count", type = "quantitative", title = "Caches" },
        tooltip = { { field = "key", title = "Region" }, { field = "count", title = "Caches" } },
    },
}

-- ── Maps ─────────────────────────────────────────────────────────────────
local maps = {}
local country_outlines = opensak.geo.outlines("country", { rows = countries })
if country_outlines then
    maps[#maps + 1] = region_map("Caches per country", country_outlines, nil, "Caches")
    local state_outlines = opensak.geo.outlines("state",
        { within = main_country, rows = open_states, extent = "all" })
    maps[#maps + 1] = region_map("Still to find in " .. (main_country or "?"), state_outlines,
        opensak.geo.outlines("country", { box = state_outlines.bbox }), "Caches")
    maps[#maps + 1] = chart.vega{
        title = "Where the caches still to find are", width = WIDE, height = 520,
        projection = { type = "mercator" },
        layer = {
            { data = geo(opensak.geo.outlines("country", { around = dots })),
              mark = { type = "geoshape", fill = GREY, stroke = EDGE, strokeWidth = 0.5 } },
            { data = { values = dots },
              mark = { type = "circle", size = 8, opacity = 0.6 },
              encoding = {
                  longitude = { field = "lon", type = "quantitative" },
                  latitude = { field = "lat", type = "quantitative" },
                  tooltip = { { field = "name", title = "Cache" }, { field = "type", title = "Type" } },
              } },
        },
    }
else
    maps[1] = "<p>Maps need the boundary data of Update location, which is not installed.</p>"
end

-- ── Page ─────────────────────────────────────────────────────────────────
local count_of = {}
for _, s in ipairs(status) do count_of[s.key] = s.count end
local premium_count = 0
for _, c in ipairs(caches) do if c.premium then premium_count = premium_count + 1 end end

local page = html.page{
    title = "Database statistics",
    subtitle = name .. " · placed " .. (hidden.min or "?") .. " to " .. (hidden.max or "?") .. " · interactive",
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M")
        .. " · charts by Vega-Lite",

    html.tiles{
        { label = "caches", value = #caches },
        { label = "found by you (" .. percent(found, #caches) .. ")", value = found },
        { label = "still to find (active)", value = to_find },
        { label = "active", value = count_of.active or 0 },
        { label = "disabled", value = count_of.disabled or 0 },
        { label = "archived", value = count_of.archived or 0 },
        { label = "premium only", value = premium_count },
        { label = "owners", value = stats.summary(caches, "owner").distinct },
        { label = "countries", value = countries_shown },
        { label = "states in " .. (main_country or "?"), value = states_shown },
        { label = "favourite points", value = favs.sum or 0 },
        { label = "most days in a row with a new cache",
          value = placing.longest and placing.longest.days or 0 },
    },

    dashboard,
    html.table(types, { columns = { { "key", "Type" }, { "count", "Caches" },
                                    { "sum", "Found" }, { "share", "Found %" } },
                        bars = { "count" } }),
    total,
    html.columns(rose, region_chart),

    table.concat(maps, "\n"),
    "<h2>Countries</h2>",
    html.table(countries, { columns = { { "key", "Country" }, { "count", "Caches" },
                                        { "sum", "Found" }, { "share", "Found %" } },
                            bars = { "count" } }),

    "<h2>Owners with the most caches</h2>",
    html.table(owners, { columns = { { "key", "Owner" }, { "count", "Caches" },
                                     { "sum", "Favourite points" } },
                         bars = { "count" } }),

    "<h2>Most favourite points</h2>",
    html.table(best, {
        columns = { { "favorite_points", "Favourites" }, { "code", "Code" }, { "name", "Name" },
                    { "type", "Type" }, { "owner", "Owner" }, { "hidden", "Placed" } },
        link = { code = "url" },
    }),

    #unmatched_rows > 0 and ("<h2>Names that match no region</h2>"
        .. "<p>Countries and states as written in the cache data that are not in the "
        .. "boundary data (e.g. another spelling or language); they are not counted "
        .. "in the tiles and maps above.</p>"
        .. html.table(unmatched_rows, { columns = { { "layer", "Kind" }, { "key", "Name" },
                                                    { "count", "Caches" } } })) or "",
}

local path = opensak.write_text(FILE, page)
print(#caches .. " caches → " .. path)
if OPEN then opensak.open(path) end
