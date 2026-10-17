-- database_statistics.lua — write an HTML page with statistics and maps of all caches in a database
--
-- Reads every cache of the active database (or of DATABASE), whatever the
-- active filter shows, and writes database_statistics.html into OpenSAK's
-- temp folder: key figures, caches per type, size, status and year placed,
-- difficulty × terrain (all caches and the ones you still can find), maps of
-- the countries and states, where the caches still to find are, the
-- direction from home, owners and the caches with the most favourite
-- points. Open the file in your browser.
--
-- Built from the generic opensak.stats.*, opensak.chart.*, opensak.geo.* and
-- opensak.html.* functions, like find_statistics.lua.

local DATABASE = nil    -- e.g. "Switzerland"; nil = the active database
local HOME = nil        -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = middle of your finds
local TOP = 15          -- rows in the region, owner and favourite lists
local MAX_DOTS = 20000  -- the dot map shows at most this many caches
local FILE = opensak.temp_dir() .. "/database_statistics.html"

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date

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

-- Home: given, or the middle of the caches you found (else of all caches).
local found_caches, open_caches = {}, {}
for _, c in ipairs(caches) do
    if c.found then found_caches[#found_caches + 1] = c end
    if c.to_find == 1 then open_caches[#open_caches + 1] = c end
end
local around = #found_caches > 0 and found_caches or caches
local home = HOME or { lat = stats.summary(around, "lat").median, lon = stats.summary(around, "lon").median }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
for _, c in ipairs(open_caches) do
    local deg = opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon)
    c.dir = DIRS[math.floor((deg + 22.5) / 45) % 8 + 1]
end

-- ── Statistics ───────────────────────────────────────────────────────────
local found = #found_caches
local to_find = #open_caches
local hidden = stats.summary(caches, "hidden")
local favs = stats.summary(caches, "favorite_points")
local status = stats.group(caches, "status", { sort = "count" })
local placing = stats.streaks(caches, "hidden")

-- Per type, with the share you have found.
local types = stats.group(caches, "type", { sort = "count", sum = "found_n" })
for _, t in ipairs(types) do t.share = percent(t.sum, t.count) end

local sizes = stats.group(caches, "container", { sort = "count" })
local by_status = stats.pivot(caches, "year", "status", { cols = { "active", "disabled", "archived" } })
local placed = stats.running(stats.fill(stats.group(caches, "hidden", { period = "year" }), { period = "year" }))

local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local dt_all = stats.pivot(caches, "difficulty", "terrain", { rows = STEPS, cols = STEPS })
local dt_todo = stats.pivot(caches, "difficulty", "terrain",
    { rows = STEPS, cols = STEPS, sum = "to_find" })

local countries = stats.group(caches, "country", { sort = "count", sum = "found_n" })
for _, c in ipairs(countries) do c.share = percent(c.sum, c.count) end
local main_country = countries[1] and countries[1].key
local in_main = {}
for _, c in ipairs(open_caches) do
    if c.country == main_country then in_main[#in_main + 1] = c end
end
local open_states = stats.group(in_main, "state")
local all_countries = opensak.geo.regions("country")
local all_states = main_country and opensak.geo.regions("state", { within = main_country })
local states_with_caches = stats.summary(
    (function()
        local rows = {}
        for _, c in ipairs(caches) do
            if c.country == main_country then rows[#rows + 1] = c end
        end
        return rows
    end)(), "state").distinct

local by_dir = {}
for _, g in ipairs(stats.group(open_caches, "dir")) do by_dir[g.key] = g.count end
local directions = {}
for i, d in ipairs(DIRS) do directions[i] = { key = d, count = by_dir[d] or 0 } end

-- The dot map: every n-th cache still to find, so it stays fast.
local dots = open_caches
if #dots > MAX_DOTS then
    dots = {}
    local every = math.ceil(#open_caches / MAX_DOTS)
    for i = 1, #open_caches, every do dots[#dots + 1] = open_caches[i] end
end

local regions = stats.group(caches, "region", { sort = "count", top = TOP })
local owners = stats.group(caches, "owner", { sort = "count", sum = "favorite_points", top = TOP })

local best = {}
for i, c in ipairs(stats.sort(caches, "-favorite_points", "name")) do
    if i > TOP then break end
    best[i] = c
end

-- ── Page ─────────────────────────────────────────────────────────────────
local count_of = {}
for _, s in ipairs(status) do count_of[s.key] = s.count end
local premium_count = 0
for _, c in ipairs(caches) do if c.premium then premium_count = premium_count + 1 end end

local page = html.page{
    title = "Database statistics",
    subtitle = name .. " · placed " .. (hidden.min or "?") .. " to " .. (hidden.max or "?"),
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),

    html.tiles{
        { label = "caches", value = #caches },
        { label = "found by you (" .. percent(found, #caches) .. ")", value = found },
        { label = "still to find (active)", value = to_find },
        { label = "active", value = count_of.active or 0 },
        { label = "disabled", value = count_of.disabled or 0 },
        { label = "archived", value = count_of.archived or 0 },
        { label = "premium only", value = premium_count },
        { label = "owners", value = stats.summary(caches, "owner").distinct },
        { label = "countries", value = #countries .. (all_countries and (" / " .. #all_countries) or "") },
        { label = "states in " .. (main_country or "?"),
          value = states_with_caches .. (all_states and (" / " .. #all_states) or "") },
        { label = "favourite points", value = favs.sum or 0 },
        { label = "most days in a row with a new cache",
          value = placing.longest and placing.longest.days or 0 },
    },

    "<h2>Caches per type</h2>",
    chart.bar(types, { horizontal = true, width = 900 }),
    html.table(types, { columns = { { "key", "Type" }, { "count", "Caches" },
                                    { "sum", "Found" }, { "share", "Found %" } },
                        bars = { "count" } }),

    html.columns(
        "<h2>Size</h2>" .. chart.bar(sizes, { width = 440 }),
        "<h2>Status</h2>" .. chart.bar(status, { width = 440 })
    ),

    "<h2>Caches placed per year</h2>",
    chart.bar(by_status, { width = 900, height = 320 }),
    "<h2>Caches placed, total</h2>",
    chart.line(placed, { y = "total", area = true, width = 900 }),

    "<h2>Difficulty / terrain: all caches</h2>",
    chart.heatmap(dt_all, { row_title = "D", col_title = "T", width = 520 }),
    "<h2>Difficulty / terrain: still to find</h2>",
    html.matrix(dt_todo, { row_title = "D", col_title = "T", totals = true }),

    "<h2>Countries</h2>",
    chart.map(countries, { width = 900 }),
    html.table(countries, { columns = { { "key", "Country" }, { "count", "Caches" },
                                        { "sum", "Found" }, { "share", "Found %" } },
                            bars = { "count" } }),

    "<h2>Still to find in " .. html.escape(main_country or "?") .. "</h2>",
    chart.map(open_states, { layer = "state", within = main_country, extent = "all", width = 900 }),

    html.columns(
        "<h2>Still to find: direction from home</h2>" .. chart.rose(directions, { width = 360 }),
        "<h2>Regions</h2>" .. chart.bar(regions, { horizontal = true, width = 440 })
    ),

    "<h2>Where the caches still to find are</h2>",
    chart.points(dots, { label = "name", width = 900, radius = 2 }),

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
}

local path = opensak.write_text(FILE, page)
print(#caches .. " caches → " .. path)
print("Open the file in your browser.")
