-- find_statistics.lua — write an HTML page with statistics, charts and maps of your finds
--
-- Reads the caches marked as found in the active database (or in DATABASE,
-- e.g. a database with your "My Finds" pocket query) and writes
-- find_statistics.html into OpenSAK's temp folder: key figures and streaks,
-- finds per year (by cache type) and per month, the running total, weekday,
-- direction and distance from home, maps of your countries and states,
-- difficulty × terrain and the milestones. Open the file in your browser.
--
-- Everything is built from the generic opensak.stats.*, opensak.chart.*,
-- opensak.geo.* and opensak.html.* functions — copy the macro and add your
-- own statistics.

local DATABASE = nil          -- e.g. "My Finds"; nil = the active database
local HOME = nil              -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = middle of your finds
local MILESTONE_STEP = nil    -- e.g. 500; nil = chosen from the number of finds
local MAIN_TYPES = 4          -- cache types shown on their own in "finds per year"
local FILE = opensak.temp_dir() .. "/find_statistics.html"

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date

-- ── The finds: one table per cache ───────────────────────────────────────
local finds = {}
for c in opensak.caches{
    found = true, database = DATABASE,
    fields = { "name", "type", "difficulty", "terrain", "found_date", "url",
               "lat", "lon", "country", "state" },
} do
    if c.found_date then finds[#finds + 1] = c end
end
if #finds == 0 then
    print("No found caches with a found date in " .. (DATABASE or opensak.database()) .. ".")
    return
end

-- Finds on the same day: in the import order of their "Found it" log, then
-- by code. That decides which cache is e.g. your 1000th find.
local log_row = {}
for _, r in ipairs(opensak.sql([[
    SELECT c.gc_code AS code, MIN(l.id) AS log_row
    FROM caches c JOIN logs l ON l.cache_id = c.id
    WHERE c.found AND l.log_type IN ('Found it', 'Attended', 'Webcam Photo Taken')
      AND date(l.log_date) = date(c.found_date)
    GROUP BY c.id]], nil, { database = DATABASE })) do
    log_row[r.code] = r.log_row
end
for _, c in ipairs(finds) do c.log_row = log_row[c.code] end
finds = stats.sort(finds, "found_date", "log_row", "code")

-- ── Fields of our own, to group and pivot by ─────────────────────────────
local home = HOME or { lat = stats.summary(finds, "lat").median, lon = stats.summary(finds, "lon").median }
local DIRS = { "N", "NE", "E", "SE", "S", "SW", "W", "NW" }
local BANDS = { 10, 25, 50, 100, 250, 500, 1000, 2500 }     -- km

-- The most found cache types on their own, the rest as "Other".
local main, kinds = {}, {}
for i, t in ipairs(stats.group(finds, "type", { sort = "count" })) do
    if i <= MAIN_TYPES then
        main[t.key] = true
        kinds[#kinds + 1] = t.key
    elseif i == MAIN_TYPES + 1 then
        kinds[#kinds + 1] = "Other"
    end
end
local function band_of(km)
    local low = 0
    for _, b in ipairs(BANDS) do
        if km < b then return string.format("%d–%d km", low, b) end
        low = b
    end
    return "≥ " .. low .. " km"
end
for _, c in ipairs(finds) do
    c.year = date.period(c.found_date, "year")
    c.month = c.found_date:sub(6, 7)
    c.kind = main[c.type] and c.type or "Other"
    local deg = opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon)
    c.dir = DIRS[math.floor((deg + 22.5) / 45) % 8 + 1]
    c.band = band_of(opensak.coords.distance(home.lat, home.lon, c.lat, c.lon))
end

-- ── Statistics ───────────────────────────────────────────────────────────
local days = stats.summary(finds, "found_date")      -- count, distinct days, min, max
local span = date.diff(days.min, days.max) + 1
local streak = stats.streaks(finds, "found_date")
local year_ago = date.format(os.time() - 365 * 86400, "%Y-%m-%d")
local last_year = 0
for _, c in ipairs(finds) do
    if c.found_date >= year_ago then last_year = last_year + 1 end
end

local years = stats.group(finds, "found_date", { period = "year", count_distinct = "found_date" })
local by_type = stats.pivot(finds, "year", "kind", { cols = kinds })
local MONTHS = { "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12" }
local per_month = stats.pivot(finds, "year", "month", { cols = MONTHS })
per_month.cols = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
local total = stats.running(stats.fill(stats.group(finds, "found_date", { period = "month" })))
local weekdays = stats.group(finds, "found_date", { period = "weekday" })
local types = stats.group(finds, "type", { sort = "count" })

local by_dir = {}
for _, g in ipairs(stats.group(finds, "dir")) do by_dir[g.key] = g.count end
local directions = {}
for i, d in ipairs(DIRS) do directions[i] = { key = d, count = by_dir[d] or 0 } end
local by_band = {}
for _, g in ipairs(stats.group(finds, "band")) do by_band[g.key] = g.count end
local distances = {}
for _, c in ipairs({ 0, table.unpack(BANDS) }) do
    local key = band_of(c)
    distances[#distances + 1] = { key = key, count = by_band[key] or 0 }
end

local countries = stats.group(finds, "country", { sort = "count" })
local home_country = countries[1] and countries[1].key
local states = stats.group(finds, "state", { sort = "count" })
local home_states = {}
for _, c in ipairs(finds) do
    if c.country == home_country then home_states[#home_states + 1] = c end
end
home_states = stats.group(home_states, "state", { sort = "count" })
local all_countries = opensak.geo.regions("country")
local all_home_states = home_country and opensak.geo.regions("state", { within = home_country })

local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local dt = stats.pivot(finds, "difficulty", "terrain", { rows = STEPS, cols = STEPS })

local step = MILESTONE_STEP or (#finds < 1000 and 100 or #finds < 5000 and 500 or 1000)
local milestones = stats.every(finds, step, { date = "found_date" })

-- ── Page ─────────────────────────────────────────────────────────────────
local function of(part, list)
    return list and (part .. " / " .. #list) or tostring(part)
end

local name = DATABASE or opensak.database()
local page = html.page{
    title = "Find statistics",
    subtitle = name .. " · " .. days.min .. " to " .. days.max,
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),

    html.tiles{
        { label = "finds", value = days.count },
        { label = "cache days", value = days.distinct },
        { label = "finds per cache day", value = string.format("%.2f", days.count / days.distinct) },
        { label = "finds per day", value = string.format("%.2f", days.count / span) },
        { label = "in the last 365 days", value = last_year },
        { label = "longest streak (days)", value = streak.longest.days },
        { label = "longest break (days)", value = streak.gap and streak.gap.days or 0 },
        { label = "D/T combinations", value = dt.filled .. " / 81" },
        { label = "countries", value = of(#countries, all_countries) },
        { label = "states in " .. (home_country or "?"), value = of(#home_states, all_home_states) },
    },

    "<h2>Finds per year</h2>",
    chart.bar(by_type, { width = 900, height = 320 }),
    html.table(years, { columns = { { "key", "Year" }, { "count", "Finds" }, { "distinct", "Cache days" } },
                        bars = { "count" } }),

    "<h2>Finds per month</h2>",
    html.matrix(per_month, { totals = true }),

    "<h2>Total finds</h2>",
    chart.line(total, { y = "total", area = true, width = 900 }),

    html.columns(
        "<h2>Finds per weekday</h2>" .. chart.bar(weekdays, { width = 440 }),
        "<h2>Direction from home</h2>" .. chart.rose(directions, { width = 360 })
    ),
    html.columns(
        "<h2>Distance from home</h2>" .. chart.bar(distances, { horizontal = true, width = 440 }),
        "<h2>Finds per cache type</h2>" .. chart.bar(types, { horizontal = true, width = 440 })
    ),

    "<h2>Countries</h2>",
    chart.map(countries, { width = 900 }),
    html.table(countries, { columns = { { "key", "Country" }, { "count", "Finds" } }, bars = { "count" } }),

    "<h2>States in " .. html.escape(home_country or "?") .. "</h2>",
    chart.map(home_states, { layer = "state", within = home_country, extent = "all", width = 900 }),

    "<h2>Where you found them</h2>",
    chart.points(finds, { label = "name", width = 900 }),

    "<h2>All states</h2>",
    html.table(states, { columns = { { "key", "State" }, { "count", "Finds" } }, bars = { "count" } }),

    "<h2>Difficulty / terrain</h2>",
    html.matrix(dt, { row_title = "D", col_title = "T", totals = true }),

    "<h2>Milestones</h2>",
    html.table(milestones, {
        columns = { { "n", "Find" }, { "found_date", "Date" }, { "days", "Days" },
                    { "code", "Code" }, { "name", "Name" }, { "type", "Type" } },
        link = { code = "url" },
    }),
}

local path = opensak.write_text(FILE, page)
print(#finds .. " finds → " .. path)
print("Open the file in your browser.")
