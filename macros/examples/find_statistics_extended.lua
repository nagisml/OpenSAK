-- OpenSAK Macro
-- Name: Find statistics (extended)
-- Author: OpenSAK
-- Version: 1.0
-- Requires-API: 3
-- Description: Write an HTML page with all statistics, charts, maps and tables of your finds
--
-- Known limitations:
-- - This is a template macro: copy it and adapt the statistics, texts and
--   layout to your needs. It is not a finished report.
-- - The page itself cannot be pasted into your geocaching.com profile: the
--   profile editor only keeps very limited HTML and removes the inline SVG
--   charts, maps and the stylesheet. PROFILE_FILE is the version for the
--   profile (opensak.html.profile): the charts become tables with coloured
--   bars, the region maps lists of their regions, other maps a short note.
-- - Maps use OpenSAK's boundary data (the outlines of Update location),
--   which is not yet stable: outlines, names and the country of a state may
--   still change. Without the boundary data the maps are a short note, and
--   county outlines are downloaded per country on first use.
-- - Region names come from the cache data (GPX / GSAK import or Update
--   location) and are matched to the outlines ignoring case, accents and
--   punctuation; names spelled differently (e.g. in another language) are
--   listed below the map as "Not on the map". Caches without a country or
--   state are left out of those statistics.
-- - The page is in English only; dates are shown as stored (YYYY-MM-DD).
-- - Only finds in the database count: caches you found that are not in it
--   (e.g. archived ones missing from your pocket queries) are left out. For
--   complete statistics, import your "My Finds" pocket query into its own
--   database and set DATABASE.
-- - Only caches with a found date are counted. Finds on the same day are
--   ordered by the import order of their "Found it" log, so a milestone
--   (e.g. your 1000th find) can be off by a few caches on busy days.
-- - "Home" for direction and distance is HOME below, else the active centre
--   point (the centre drop-down in the toolbar).
-- - The states shown are those of one country: COUNTRY below, or else the
--   country with the most finds. If you found more caches abroad than at
--   home, set COUNTRY.
-- - Countries and states are counted as the regions of the boundary data
--   that the names in the cache data match, so "states in …" can never be
--   more than all states. Names that match no region are listed at the end.
-- - Distances are straight lines from cache to cache in the order found,
--   and from HOME, not routes.
-- - Elevation and favourite points are only as complete and current as
--   your imports; caches without them are left out of those tables.
-- - The tables of countries, states and counties per day use the names in
--   the cache data as they are, without matching them to the boundary data.
-- - Not included: log statistics (length, similarity — your own log texts
--   are not marked as yours), cache type icons and country flags; the
--   attributes are listed by name, without their icons.
--
-- Everything of find_statistics.lua (key figures, charts and maps of your
-- finds) plus detailed tables like FindStatGen: various figures (totals,
-- averages, streaks, best days and weeks, fastest finds, distances,
-- extreme finds) with footnotes, finds by type, size, difficulty, terrain,
-- month, weekday, hidden year, direction and distance, favourite points,
-- the oldest caches, elevation, calendars of your find and hidden dates,
-- owners and attributes. Writes find_statistics_extended.html into
-- OpenSAK's temp folder and opens it in your browser.
-- find_statistics_extended_profile.html is the same page as text and tables
-- only, to paste into your geocaching.com profile (see opensak.html.profile).
--
-- Built from opensak.stats.*, opensak.chart.*, opensak.geo.* and
-- opensak.html.*. For a shorter page to start from, see find_statistics.lua.

local DATABASE = nil          -- e.g. "My Finds"; nil = the active database
local HOME = nil              -- e.g. { lat = 47.3769, lon = 8.5417 }; nil = the active centre point
local COUNTRY = nil           -- e.g. "United States": whose states are shown; nil = the country with most finds
local MILESTONE_STEP = nil    -- e.g. 500; nil = chosen from the number of finds
local MAIN_TYPES = 4          -- cache types shown on their own in "finds per year"
local TOP = 10                -- rows of the favourite, oldest and elevation lists
local OWNERS = 20             -- owners in the table; the next ones are listed below it
local FILE = opensak.temp_dir() .. "/find_statistics_extended.html"
local OPEN = true             -- open the page in the browser when done
local PROFILE_FILE = opensak.temp_dir() .. "/find_statistics_extended_profile.html"   -- nil = none

local stats, chart, html, date = opensak.stats, opensak.chart, opensak.html, opensak.date

-- ── The finds: one table per cache ───────────────────────────────────────
local finds = {}
for c in opensak.caches{
    found = true, database = DATABASE,
    fields = { "name", "type", "container", "difficulty", "terrain", "found_date", "url",
               "lat", "lon", "country", "state", "county", "elevation", "hidden", "archived",
               "owner", "favorite_points" },
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
-- Home: HOME, else the active centre point (the centre drop-down in the
-- toolbar, which OpenSAK's distances are measured from), else the middle
-- of your finds.
local center = opensak.center()
local home = HOME or (center.lat and center.lon and { lat = center.lat, lon = center.lon })
    or { lat = stats.summary(finds, "lat").median, lon = stats.summary(finds, "lon").median }
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
    c.place = c.state or c.country or ""
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
local home_country = COUNTRY or (countries[1] and countries[1].key)
local home_key = home_country and opensak.text.lower(home_country)
local states = stats.group(finds, "state", { sort = "count" })
local home_states = {}
for _, c in ipairs(finds) do
    if c.country and opensak.text.lower(c.country) == home_key then
        home_states[#home_states + 1] = c
    end
end
home_states = stats.group(home_states, "state", { sort = "count" })

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
local states_shown = home_country and coverage(home_states, "state", home_country) or "0"

local STEPS = { 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5 }
local dt = stats.pivot(finds, "difficulty", "terrain", { rows = STEPS, cols = STEPS })

local step = MILESTONE_STEP or (#finds < 1000 and 100 or #finds < 5000 and 500 or 1000)
local milestones = stats.every(finds, step, { date = "found_date" })

-- ── Various statistics ───────────────────────────────────────────────────
-- Days as numbers (days since 1970-01-01), computed from the "YYYY-MM-DD"
-- text, so no time zone can move a find to another day.
local function day_number(s)
    local y, m, d = tonumber(s:sub(1, 4)), tonumber(s:sub(6, 7)), tonumber(s:sub(9, 10))
    if m <= 2 then y = y - 1 end
    local era = (y >= 0 and y or y - 399) // 400
    local yoe = y - era * 400
    local doy = (153 * (m + (m > 2 and -3 or 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468
end
local function day_text(n)
    n = n + 719468
    local era = (n >= 0 and n or n - 146096) // 146097
    local doe = n - era * 146097
    local yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    local doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    local mp = (5 * doy + 2) // 153
    local m = mp + (mp < 10 and 3 or -9)
    local y = yoe + era * 400 + (m <= 2 and 1 or 0)
    return string.format("%04d-%02d-%02d", y, m, doy - (153 * mp + 2) // 5 + 1)
end
local WEEKDAYS = { "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday" }
local function weekday(n) return (n + 3) % 7 + 1 end          -- 1 = Monday

-- Locationless caches were not found at their coordinates; Adventure Labs
-- are counted like other finds (footnotes 1 and 2).
local function located(c)
    return c.lat and c.lon and not (c.type or ""):lower():find("locationless", 1, true)
end
local labs = 0
for _, c in ipairs(finds) do
    if (c.type or ""):lower():find("lab", 1, true) then labs = labs + 1 end
end
local LAB = labs > 0 and 2 or nil

local function b(v) return "<b>" .. html.escape(v) .. "</b>" end
local function f2(v) return string.format("%.2f", v) end
local function names(set)
    local list = {}
    for k in pairs(set) do list[#list + 1] = k end
    table.sort(list)
    return list
end
local function cache_link(c)
    return html.escape(c.name) .. ", " .. '<a href="' .. html.escape(c.url or "") .. '">'
        .. html.escape(c.code) .. "</a> (" .. html.escape(c.type or "?") .. ")"
end
local function dmm(v, pos, neg, width)
    local a = math.abs(v)
    local d = math.floor(a)
    return string.format("%s %0" .. width .. "d° %06.3f", v < 0 and neg or pos, d, (a - d) * 60)
end
local function km(a, c) return opensak.coords.distance(a.lat, a.lon, c.lat, c.lon) end
-- The item of *list* with the highest score (the first one on a tie).
local function best(list, score)
    local top, top_score
    for _, item in ipairs(list) do
        local s = score(item)
        if s and (not top_score or s > top_score) then top, top_score = item, s end
    end
    return top, top_score
end

-- Per cache day: finds, types, regions and the way from cache to cache.
local per_day, day_list, total_km, previous = {}, {}, 0, nil
for _, c in ipairs(finds) do
    c.day = c.found_date:sub(1, 10)
    c.dayno = day_number(c.day)
    local d = per_day[c.day]
    if not d then
        d = { day = c.day, dayno = c.dayno, n = 0, km = 0, located = 0,
              types = {}, countries = {}, states = {}, counties = {} }
        per_day[c.day] = d
        day_list[#day_list + 1] = d
    end
    d.n = d.n + 1
    d.types[c.type or "?"] = true
    if located(c) then
        if c.country then d.countries[c.country] = true end
        if c.state then d.states[c.state] = true end
        if c.county then d.counties[c.county] = true end
        if previous then total_km = total_km + km(previous, c) end
        if d.last then d.km = d.km + km(d.last, c) end
        d.last, previous = c, c
        d.located = d.located + 1
    end
end

local n = #finds
local today = day_number(os.date("%Y-%m-%d"))
local function summary_row(count, cache_days, all_days)
    return b(count) .. " finds on " .. b(cache_days) .. " cache days in " .. b(all_days)
        .. " days (" .. string.format("%.1f %%", 100 * cache_days / all_days) .. ")"
end
local function average_row(count, cache_days, all_days)
    local per_day_value = count / all_days
    return b(f2(count / cache_days)) .. " per cache day, " .. b(f2(per_day_value)) .. " per day, "
        .. b(f2(per_day_value * 7)) .. " per week, " .. b(f2(per_day_value * 365.25 / 12)) .. " per month"
end
local all_days = today - finds[1].dayno + 1
local recent, recent_days = 0, 0
for _, d in ipairs(day_list) do
    if d.dayno > today - 365 then
        recent, recent_days = recent + d.n, recent_days + 1
    end
end

local function run_row(run)
    return run and (b(run.days) .. ", from " .. b(run.first) .. " to " .. b(run.last)) or "–"
end

local top_days = {}
for i, d in ipairs(day_list) do top_days[i] = d end
table.sort(top_days, function(x, y) return x.n > y.n or (x.n == y.n and x.day < y.day) end)
local top_parts = {}
for i = 1, math.min(3, #top_days) do
    top_parts[i] = b(top_days[i].n) .. " on " .. b(top_days[i].day)
end

-- Weekends (Saturday + Sunday), weeks (Monday to Sunday) and months.
local weekends, weeks, months = {}, {}, {}
local function add(t, key, d)
    local e = t[key]
    if not e then
        e = { key = key, n = 0 }
        t[key] = e
        t[#t + 1] = e
    end
    e.n = e.n + d.n
    return e
end
for _, d in ipairs(day_list) do
    local wd = weekday(d.dayno)
    if wd >= 6 then
        local e = add(weekends, d.dayno - (wd - 6), d)
        if wd == 6 then e.sat = d.n else e.sun = d.n end
    end
    add(weeks, d.dayno - (wd - 1), d)
    add(months, d.day:sub(1, 7), d)
end
local weekend = best(weekends, function(e) return e.n end)
local week = best(weeks, function(e) return e.n end)
local month = best(months, function(e) return e.n end)

local function most_per_day(field)
    local d, count = best(day_list, function(x) return #names(x[field]) end)
    if not d or count == 0 then return "–" end
    return b(count) .. " (" .. html.escape(table.concat(names(d[field]), ", ")) .. ") on " .. b(d.day)
end

local function fastest(size)
    if n < size then return nil end
    local days_needed, last
    for i = 1, n - size + 1 do
        local span_days = finds[i + size - 1].dayno - finds[i].dayno + 1
        if not days_needed or span_days < days_needed then
            days_needed, last = span_days, finds[i + size - 1].day
        end
    end
    return b(days_needed) .. (days_needed == 1 and " day" or " days") .. " to " .. b(last)
end
local function latest(size)
    if n < size then return nil end
    local span_days = finds[n].dayno - finds[n - size + 1].dayno + 1
    return b(span_days) .. (span_days == 1 and " day" or " days")
end

local placed_finds = {}
for _, c in ipairs(finds) do if located(c) then placed_finds[#placed_finds + 1] = c end end
local function extreme(score, text)
    local c = best(placed_finds, score)
    return c and (b(text(c)) .. ", " .. cache_link(c)) or "–"
end
local far_day = best(day_list, function(d) return d.km end)

-- Centre of gravity: the mean of the finds as points on the globe.
local x, y, z = 0, 0, 0
for _, c in ipairs(placed_finds) do
    local la, lo = math.rad(c.lat), math.rad(c.lon)
    x, y, z = x + math.cos(la) * math.cos(lo), y + math.cos(la) * math.sin(lo), z + math.sin(la)
end
local centre = #placed_finds > 0 and {
    lat = math.deg(math.atan(z, math.sqrt(x * x + y * y))), lon = math.deg(math.atan(y, x)),
}

local with_height, with_hidden, archived, not_events = {}, {}, 0, 0
for _, c in ipairs(placed_finds) do
    if c.elevation then with_height[#with_height + 1] = c end
end
for _, c in ipairs(finds) do
    if c.hidden then with_hidden[#with_hidden + 1] = c end
    if not (c.type or ""):lower():find("event", 1, true) then
        not_events = not_events + 1
        if c.archived then archived = archived + 1 end
    end
end
local highest = best(with_height, function(c) return c.elevation end)
local lowest = best(with_height, function(c) return -c.elevation end)
local youngest = best(with_hidden, function(c) return day_number(c.hidden:sub(1, 10)) end)
local oldest = best(with_hidden, function(c) return -day_number(c.hidden:sub(1, 10)) end)

-- { label, value (HTML), footnote }
local various = {
    { "Total finds", summary_row(n, #day_list, all_days), LAB },
    { "Overall average", average_row(n, #day_list, all_days), LAB },
    { "Last 365 days", summary_row(recent, recent_days, 365), LAB },
    { "Average of the last 365 days", recent_days > 0 and average_row(recent, recent_days, 365) or "–", LAB },
    { "Most days in a row with a find", run_row(streak.longest), LAB },
    { "Most days in a row without a find", run_row(streak.gap), LAB },
    { "Latest days in a row with a find", run_row(streak.last), LAB },
    { "Most finds on one day", table.concat(top_parts, "; "), LAB },
    { "Weekend with the most finds", weekend and (b(weekend.n) .. " finds, Saturday " .. b(day_text(weekend.key))
        .. " (" .. (weekend.sat or 0) .. ") and Sunday " .. b(day_text(weekend.key + 1))
        .. " (" .. (weekend.sun or 0) .. ")") or "–", LAB },
    { "Best cache week", b(week.n) .. " finds, Monday " .. b(day_text(week.key)) .. " to Sunday "
        .. b(day_text(week.key + 6)), LAB },
    { "Most finds in a calendar month", b(month.n) .. " in " .. b(month.key), LAB },
    { "Most cache types on one day", most_per_day("types"), LAB },
    { "Most countries on one day", most_per_day("countries"), 1 },
    { "Most states on one day", most_per_day("states"), 1 },
    { "Most counties on one day", most_per_day("counties"), 1 },
    { "Fastest 100 finds", fastest(100) or "–", LAB },
    { "Last 100 finds", latest(100) or "–", LAB },
    { "Fastest 1000 finds", fastest(1000) or "–", LAB },
    { "Last 1000 finds", latest(1000) or "–", LAB },
    { "Distance from cache to cache", b(string.format("%.0f km", total_km)) .. "; "
        .. b(string.format("%.2f", total_km / 40075)) .. " times around the earth, or "
        .. b(string.format("%.3f", total_km / 384400)) .. " times the distance to the moon", 1 },
    { "Longest way on one day", far_day and far_day.km > 0 and (b(string.format("%.1f km", far_day.km))
        .. " with " .. b(far_day.located) .. " caches on " .. b(far_day.day)) or "–", 1 },
    { "Nearest find to home", extreme(function(c) return -km(home, c) end,
        function(c) return string.format("%.2f km", km(home, c)) end), 1 },
    { "Farthest find from home", extreme(function(c) return km(home, c) end,
        function(c) return string.format("%.2f km", km(home, c)) end), 1 },
    { "Northernmost find", extreme(function(c) return c.lat end,
        function(c) return dmm(c.lat, "N", "S", 2) end), 1 },
    { "Southernmost find", extreme(function(c) return -c.lat end,
        function(c) return dmm(c.lat, "N", "S", 2) end), 1 },
    { "Easternmost find", extreme(function(c) return c.lon end,
        function(c) return dmm(c.lon, "E", "W", 3) end), 1 },
    { "Westernmost find", extreme(function(c) return -c.lon end,
        function(c) return dmm(c.lon, "E", "W", 3) end), 1 },
    { "Highest find", highest and (b(string.format("%.0f m", highest.elevation)) .. ", "
        .. cache_link(highest)) or "– (no elevation imported)", 1 },
    { "Lowest find", lowest and (b(string.format("%.0f m", lowest.elevation)) .. ", "
        .. cache_link(lowest)) or "– (no elevation imported)", 1 },
    { "Centre of your finds", centre and (b(dmm(centre.lat, "N", "S", 2) .. " " .. dmm(centre.lon, "E", "W", 3))
        .. string.format(" (%.0f km from home)", km(home, centre))) or "–", 1 },
    { "Youngest cache found", youngest and (b(youngest.hidden:sub(1, 10)) .. ", " .. cache_link(youngest)) or "–" },
    { "Oldest cache found", oldest and (b(oldest.hidden:sub(1, 10)) .. ", " .. cache_link(oldest)) or "–" },
    { "Found caches archived since", b(archived) .. ", " .. string.format("%.2f %%", not_events > 0
        and 100 * archived / not_events or 0) .. " (without events)" },
}
local various_parts = { '<div class="table"><table class="various">' }
for _, row in ipairs(various) do
    various_parts[#various_parts + 1] = "<tr><th>" .. html.escape(row[1]) .. "</th><td>" .. row[2]
        .. (row[3] and ("<sup>" .. row[3] .. "</sup>") or "") .. "</td></tr>"
end
various_parts[#various_parts + 1] = '</table></div><p class="notes"><sup>1</sup> Without locationless '
    .. "caches: their coordinates are not where they were found."
    .. (LAB and ("<br><sup>2</sup> Includes " .. labs .. " Adventure Lab caches.") or "") .. "</p>"
local various_html = table.concat(various_parts)

-- ── Finds by type, size, difficulty, terrain, month and weekday ──────────
local function shares(rows, total)
    total = total or n
    for _, r in ipairs(rows) do
        r.share = total > 0 and string.format("%.2f %%", 100 * r.count / total) or "–"
    end
    return rows
end
local function share_table(rows, heading, total)
    return html.table(shares(rows, total), { columns = { { "key", heading }, { "count", "Finds" },
                                                  { "share", "Share" } }, bars = { "count" } })
end
local lab_note = LAB and ('<p class="notes">Includes ' .. labs .. " Adventure Lab caches.</p>") or ""
local function rating_rows(field)
    local rows = stats.group(finds, field)
    for _, r in ipairs(rows) do r.key = string.format("%.1f", r.key) end
    return rows
end
local function per_year_mean(field)
    local rows = stats.group(finds, "year", { sum = field })
    for _, r in ipairs(rows) do r.mean = r.sum / r.count end
    return html.table(rows, { columns = { { "key", "Year" }, { "count", "Finds" },
                                          { "mean", "Mean " .. (field == "difficulty" and "D" or "T") } },
                              bars = { "mean" } })
end
local MONTH_NAMES = { "January", "February", "March", "April", "May", "June", "July",
                      "August", "September", "October", "November", "December" }
local month_count = {}
for _, g in ipairs(stats.group(finds, "month")) do month_count[g.key] = g.count end
local by_month = {}
for i, m in ipairs(MONTHS) do by_month[i] = { key = MONTH_NAMES[i], count = month_count[m] or 0 } end
local by_weekday = {}
local weekend_finds = 0
for _, d in ipairs(day_list) do
    local wd = weekday(d.dayno)
    by_weekday[wd] = (by_weekday[wd] or 0) + d.n
    if wd >= 6 then weekend_finds = weekend_finds + d.n end
end
local weekday_rows = {}
for i, name_ in ipairs(WEEKDAYS) do weekday_rows[i] = { key = name_, count = by_weekday[i] or 0 } end
local mean_d = stats.summary(finds, "difficulty").mean
local mean_t = stats.summary(finds, "terrain").mean

-- ── Hidden year, the year so far, direction and distance from home ───────
local by_hidden_year = stats.group(with_hidden, "hidden", { period = "year" })
local cut_day = os.date("%m-%d")
local so_far = {}
for _, c in ipairs(finds) do
    if c.day:sub(6, 10) <= cut_day then so_far[#so_far + 1] = c end
end
local so_far_years = stats.group(so_far, "year")
local so_far_label = MONTH_NAMES[tonumber(cut_day:sub(1, 2))] .. " " .. tonumber(cut_day:sub(4, 5))

local HOME_BANDS = { 10, 20, 30, 40, 50, 100, 200, 500, 1000 }      -- km
local function home_band(d)
    if d < HOME_BANDS[1] then return 1 end
    for i = 2, #HOME_BANDS do
        if d < HOME_BANDS[i] then return i end
    end
    return #HOME_BANDS + 1
end
local sector_count, sector_km, band_count, distance_sum = {}, {}, {}, 0
for _, c in ipairs(placed_finds) do
    local d = km(home, c)
    local s = math.floor(opensak.coords.bearing(home.lat, home.lon, c.lat, c.lon) / 30) % 12 + 1
    sector_count[s] = (sector_count[s] or 0) + 1
    sector_km[s] = (sector_km[s] or 0) + d
    local band = home_band(d)
    band_count[band] = (band_count[band] or 0) + 1
    distance_sum = distance_sum + d
end
local sectors, sector_means = {}, {}
for s = 1, 12 do
    local label = ((s - 1) * 30) .. "–" .. (s * 30) .. "°"
    sectors[s] = { key = label, count = sector_count[s] or 0 }
    sector_means[s] = { key = label, count = sector_count[s]
        and math.floor(sector_km[s] / sector_count[s] + 0.5) or 0 }
end
local bands = {}
for i = 1, #HOME_BANDS + 1 do
    local label = i == 1 and ("< " .. HOME_BANDS[1]) or i > #HOME_BANDS and ("> " .. HOME_BANDS[#HOME_BANDS])
        or (HOME_BANDS[i - 1] .. "–" .. HOME_BANDS[i])
    bands[i] = { key = label, count = band_count[i] or 0 }
end
local placed_total = #placed_finds

-- ── Difficulty / terrain ─────────────────────────────────────────────────
local hard = 0
for _, c in ipairs(finds) do
    if (c.difficulty or 0) >= 3 or (c.terrain or 0) >= 3 then hard = hard + 1 end
end

-- The largest *value* of a cache per D/T combination, as a matrix for
-- html.matrix (0 = none).
local function dt_matrix(value)
    local index = {}
    for i, v in ipairs(STEPS) do index[v] = i end
    local cells = {}
    for i = 1, #STEPS do
        cells[i] = {}
        for j = 1, #STEPS do cells[i][j] = 0 end
    end
    for _, c in ipairs(finds) do
        local i, j, v = index[c.difficulty], index[c.terrain], value(c)
        if i and j and v and v > cells[i][j] then cells[i][j] = v end
    end
    return { rows = STEPS, cols = STEPS, cells = cells }
end

-- A row for the cache lists (a copy: one cache can be in several lists).
local function row_of(c)
    return { code = c.code, url = c.url, name = c.name, type = c.type, container = c.container,
             place = c.place, favorite_points = c.favorite_points, elevation = c.elevation,
             hidden = c.hidden and c.hidden:sub(1, 10), age = c.age, found = c.day }
end
local function first_rows(list, count)
    local rows = {}
    for i = 1, math.min(count, #list) do rows[i] = row_of(list[i]) end
    return rows
end
-- The cache with the highest *score* per value of *field*, best first.
local function best_per(field, score)
    local by, order = {}, {}
    for _, c in ipairs(finds) do
        local v, s = c[field], score(c)
        if v ~= nil and s ~= nil then
            if not by[v] then order[#order + 1] = v end
            if not by[v] or s > score(by[v]) then by[v] = c end
        end
    end
    local rows = {}
    for _, v in ipairs(order) do
        local r = row_of(by[v])
        r.group, r.score = v, score(by[v])
        rows[#rows + 1] = r
    end
    return stats.sort(rows, "-score", "group")
end

-- ── Favourite points ─────────────────────────────────────────────────────
local with_fp = {}
for _, c in ipairs(finds) do
    if c.favorite_points then with_fp[#with_fp + 1] = c end
end
local fp_top = first_rows(stats.sort(with_fp, "-favorite_points", "code"), TOP)
local fp = stats.summary(finds, "favorite_points")
local function favourites(c) return c.favorite_points end

-- ── The oldest caches ────────────────────────────────────────────────────
for _, c in ipairs(with_hidden) do c.age = today - day_number(c.hidden:sub(1, 10)) end
local oldest_top = first_rows(stats.sort(with_hidden, "-age", "code"), TOP)
local function age(c) return c.age end

-- ── Elevation ────────────────────────────────────────────────────────────
local by_height = stats.sort(with_height, "-elevation", "code")
local lowest_first = stats.sort(with_height, "elevation", "code")
local height_top, height_bottom = first_rows(by_height, TOP), {}
for i = math.min(TOP, #lowest_first), 1, -1 do height_bottom[#height_bottom + 1] = row_of(lowest_first[i]) end
local climb, last_height = 0, nil
for _, c in ipairs(finds) do
    if c.elevation then
        if last_height then climb = climb + math.abs(c.elevation - last_height) end
        last_height = c.elevation
    end
end
local HEIGHTS = { 0, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 750, 1000,
                  1250, 1500, 1750, 2000, 2500 }
local height_count = {}
for _, c in ipairs(with_height) do
    local i = #HEIGHTS + 1
    for k, limit in ipairs(HEIGHTS) do
        if c.elevation < limit then i = k break end
    end
    height_count[i] = (height_count[i] or 0) + 1
end
local heights = {}
for i = 1, #HEIGHTS + 1 do
    heights[i] = { key = i <= #HEIGHTS and ("<" .. HEIGHTS[i]) or ("≥" .. HEIGHTS[#HEIGHTS]),
                   count = height_count[i] or 0 }
end

-- ── Calendars: found dates, hidden dates, hidden months ──────────────────
local MONTH_ABBR = { "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" }
local MONTH_NUMBERS, DAYS = {}, {}
for m = 1, 12 do MONTH_NUMBERS[m] = m end
for d = 1, 31 do DAYS[d] = d end
for _, c in ipairs(finds) do
    c.fm, c.fd = tonumber(c.day:sub(6, 7)), tonumber(c.day:sub(9, 10))
end
for _, c in ipairs(with_hidden) do
    c.hy, c.hm, c.hd = c.hidden:sub(1, 4), tonumber(c.hidden:sub(6, 7)), tonumber(c.hidden:sub(9, 10))
end
local found_calendar = stats.pivot(finds, "fm", "fd", { rows = MONTH_NUMBERS, cols = DAYS })
found_calendar.rows = MONTH_ABBR
local hidden_calendar = stats.pivot(with_hidden, "hm", "hd", { rows = MONTH_NUMBERS, cols = DAYS })
hidden_calendar.rows = MONTH_ABBR
local hidden_months = stats.pivot(with_hidden, "hy", "hm", { cols = MONTH_NUMBERS })
hidden_months.cols = MONTH_ABBR
-- Caches exist since May 2000.
local this_year, this_month = tonumber(os.date("%Y")), tonumber(os.date("%m"))
local possible_months = (this_year - 2000) * 12 + this_month - 4

-- ── Owners and attributes ────────────────────────────────────────────────
local owners = stats.group(finds, "owner", { sort = "count" })
local owner_rows, next_owners = {}, {}
for i, o in ipairs(owners) do
    if i <= OWNERS then
        owner_rows[i] = { key = i .. " – " .. o.key, count = o.count,
                          share = string.format("%.2f %%", 100 * o.count / n) }
    elseif i <= 2 * OWNERS then
        next_owners[#next_owners + 1] = html.escape(o.key) .. " (" .. o.count .. ")"
    end
end
local half = math.ceil(#owner_rows / 2)
local owners_left, owners_right = {}, {}
for i, r in ipairs(owner_rows) do
    if i <= half then owners_left[#owners_left + 1] = r else owners_right[#owners_right + 1] = r end
end
local function owner_table(rows)
    return html.table(rows, { columns = { { "key", "Owner" }, { "count", "Finds" }, { "share", "Share" } },
                              bars = { "count" } })
end

local positive, negative = {}, {}
for _, r in ipairs(opensak.sql([[
    SELECT a.name AS name, a.is_on AS is_on, COUNT(*) AS count
    FROM attributes a JOIN caches c ON c.id = a.cache_id
    WHERE c.found
    GROUP BY a.name, a.is_on
    ORDER BY count DESC, a.name]], nil, { database = DATABASE })) do
    local row = { key = r.name, count = r.count }
    if r.is_on == 1 or r.is_on == true then
        positive[#positive + 1] = row
    else
        negative[#negative + 1] = row
    end
end
local function attribute_table(rows)
    if #rows == 0 then return "<p>None.</p>" end
    return html.table(rows, { columns = { { "key", "Attribute" }, { "count", "Finds" } }, bars = { "count" } })
end

-- ── The tables, after the basic page ─────────────────────────────────────────────────────────────────
local CACHE_COLUMNS = { { "code", "Code" }, { "name", "Name" }, { "type", "Type" }, { "place", "Place" } }
local function with_columns(first, last)
    local cols = {}
    for _, c in ipairs(first or {}) do cols[#cols + 1] = c end
    for _, c in ipairs(CACHE_COLUMNS) do cols[#cols + 1] = c end
    for _, c in ipairs(last or {}) do cols[#cols + 1] = c end
    return cols
end
local function cache_table(rows, first, last)
    return html.table(rows, { columns = with_columns(first, last), link = { code = "url" } })
end
local function best_table(rows, group_heading, value_column)
    return html.table(rows, { columns = { { "group", group_heading }, { "code", "Code" },
                                          { "name", "Name" }, value_column },
                              link = { code = "url" } })
end
local function pct(part, whole)
    return whole > 0 and string.format("%.1f %%", 100 * part / whole) or "–"
end

local body = {
    "<h2>Various statistics</h2>",
    various_html,

    html.columns(
        "<h2>Finds by type</h2>" .. share_table(stats.group(finds, "type", { sort = "count" }), "Type")
            .. lab_note,
        "<h2>Finds by size</h2>" .. share_table(stats.group(finds, "container", { sort = "count" }), "Size")
    ),
    html.columns(
        "<h2>Finds by difficulty</h2>" .. share_table(rating_rows("difficulty"), "Difficulty")
            .. "<p>Mean difficulty: " .. b(mean_d and f2(mean_d) or "–") .. "</p>",
        "<h2>Finds by terrain</h2>" .. share_table(rating_rows("terrain"), "Terrain")
            .. "<p>Mean terrain: " .. b(mean_t and f2(mean_t) or "–") .. "</p>"
    ),
    html.columns(
        "<h2>Mean difficulty per year</h2>" .. per_year_mean("difficulty"),
        "<h2>Mean terrain per year</h2>" .. per_year_mean("terrain")
    ),
    html.columns(
        "<h2>Finds by month</h2>" .. share_table(by_month, "Month") .. lab_note,
        "<h2>Finds by weekday</h2>" .. share_table(weekday_rows, "Weekday")
            .. "<p>" .. b(n - weekend_finds) .. " finds on weekdays (" .. pct(n - weekend_finds, n) .. ")<br>"
            .. b(weekend_finds) .. " finds on weekends (" .. pct(weekend_finds, n) .. ")</p>" .. lab_note
    ),
    html.columns(
        "<h2>Finds by year hidden</h2>" .. share_table(by_hidden_year, "Year"),
        "<h2>Finds until " .. so_far_label .. " in each year</h2>"
            .. share_table(so_far_years, "Year", #so_far) .. lab_note
    ),
    html.columns(
        "<h2>Finds by direction from home</h2>" .. share_table(sectors, "Direction", placed_total),
        "<h2>Finds by distance from home</h2>" .. share_table(bands, "Distance (km)", placed_total)
            .. "<p>Mean distance: " .. b(placed_total > 0
                and string.format("%.0f km", distance_sum / placed_total) or "–") .. "</p>"
    ),
    html.columns(
        "<h2>Finds by direction</h2>" .. chart.rose(sectors, { width = 400 }),
        "<h2>Mean distance by direction (km)</h2>" .. chart.rose(sector_means, { width = 400 })
    ),

    "<h2>Difficulty / terrain</h2>",
    html.matrix(dt, { row_title = "D", col_title = "T", totals = true }),
    "<p>" .. b(dt.filled) .. " of 81 difficulty / terrain combinations found. " .. b(hard)
        .. " finds (" .. pct(hard, n) .. ") have a difficulty or terrain of 3 or more.</p>",

    "<h2>Caches with the most favourite points</h2>",
    cache_table(fp_top, nil, { { "favorite_points", "FP" } }),
    "<p>" .. b(fp.sum or 0) .. " favourite points in total (mean " .. b(fp.mean and f2(fp.mean) or "–")
        .. ").</p>",
    html.columns(
        "<h2>Most favourites by type</h2>" .. best_table(best_per("type", favourites), "Type",
            { "favorite_points", "FP" }),
        "<h2>Most favourites by size</h2>" .. best_table(best_per("container", favourites), "Size",
            { "favorite_points", "FP" })
    ),
    "<h2>Most favourites by difficulty / terrain</h2>",
    html.matrix(dt_matrix(favourites), { row_title = "D", col_title = "T" }),
    '<p class="notes">The most favourite points of one cache per combination.</p>',

    "<h2>Oldest caches found</h2>",
    cache_table(oldest_top, { { "hidden", "Hidden" }, { "age", "Age (days)" }, { "found", "Found" } }),
    html.columns(
        "<h2>Oldest by type</h2>" .. best_table(best_per("type", age), "Type", { "age", "Age (days)" }),
        "<h2>Oldest by size</h2>" .. best_table(best_per("container", age), "Size", { "age", "Age (days)" })
    ),
    "<h2>Oldest by difficulty / terrain</h2>",
    html.matrix(dt_matrix(age), { row_title = "D", col_title = "T" }),
    '<p class="notes">Age in days of the oldest cache per combination.</p>',
}

if #with_height > 0 then
    local mean_height = stats.summary(with_height, "elevation").mean
    body[#body + 1] = "<h2>Highest and lowest finds</h2>"
    body[#body + 1] = cache_table(height_top, nil, { { "elevation", "Height (m)" } })
    body[#body + 1] = cache_table(height_bottom, nil, { { "elevation", "Height (m)" } })
    body[#body + 1] = "<p>Mean height of your finds: " .. b(string.format("%.0f m", mean_height))
        .. "<br>Height difference from find to find, in total: " .. b(string.format("%.0f m", climb)) .. "</p>"
    body[#body + 1] = chart.bar(heights, { title = "Finds by height (m)", width = 900 })
end

body[#body + 1] = "<h2>Finds by found date</h2>"
body[#body + 1] = html.matrix(found_calendar, { totals = true })
body[#body + 1] = "<p>" .. b(found_calendar.filled) .. " of 366 days of the year with a find ("
    .. pct(found_calendar.filled, 366) .. ").</p>"
body[#body + 1] = "<h2>Finds by hidden date</h2>"
body[#body + 1] = html.matrix(hidden_calendar, { totals = true })
body[#body + 1] = "<p>" .. b(hidden_calendar.filled) .. " of 366 hidden dates ("
    .. pct(hidden_calendar.filled, 366) .. ").</p>"
body[#body + 1] = "<h2>Finds by hidden month</h2>"
body[#body + 1] = html.matrix(hidden_months, { totals = true })
body[#body + 1] = "<p>" .. b(hidden_months.filled) .. " of " .. possible_months .. " hidden months ("
    .. pct(hidden_months.filled, possible_months) .. ").</p>"

body[#body + 1] = "<h2>Finds by owner</h2>"
body[#body + 1] = html.columns(owner_table(owners_left), #owners_right > 0 and owner_table(owners_right) or "")
if #next_owners > 0 then
    body[#body + 1] = "<p>Next: " .. table.concat(next_owners, ", ") .. "</p>"
end
body[#body + 1] = "<p>Caches of " .. b(#owners) .. " different owners found.</p>"

body[#body + 1] = "<h2>Attributes of your finds</h2>"
body[#body + 1] = html.columns(
    "<h3>Positive (" .. #positive .. ")</h3>" .. attribute_table(positive),
    "<h3>Negative (" .. #negative .. ")</h3>" .. attribute_table(negative))

-- ── Page ─────────────────────────────────────────────────────────────────
local name = DATABASE or opensak.database()
local page = html.page{
    title = "Find statistics",
    subtitle = name .. " · " .. days.min .. " to " .. days.max,
    footer = "Written by OpenSAK " .. opensak.version() .. " on " .. os.date("%Y-%m-%d %H:%M"),
    css = ".various th { font-weight: normal; width: 40%; vertical-align: top; }\n"
        .. ".various td, .various th { padding: 4px 10px; }\n"
        .. "sup { color: var(--muted); }\n"
        .. ".notes { color: var(--muted); font-size: 13px; }",
    body = body,       -- after the parts below

    html.tiles{
        { label = "finds", value = days.count },
        { label = "cache days", value = days.distinct },
        { label = "finds per cache day", value = string.format("%.2f", days.count / days.distinct) },
        { label = "finds per day", value = string.format("%.2f", days.count / span) },
        { label = "in the last 365 days", value = last_year },
        { label = "longest streak (days)", value = streak.longest.days },
        { label = "longest break (days)", value = streak.gap and streak.gap.days or 0 },
        { label = "D/T combinations", value = dt.filled .. " / 81" },
        { label = "countries", value = countries_shown },
        { label = "states in " .. (home_country or "?"), value = states_shown },
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

    #unmatched_rows > 0 and ("<h2>Names that match no region</h2>"
        .. "<p>Countries and states as written in the cache data that are not in the "
        .. "boundary data (e.g. another spelling or language); they are not counted "
        .. "in the tiles and maps above.</p>"
        .. html.table(unmatched_rows, { columns = { { "layer", "Kind" }, { "key", "Name" },
                                                    { "count", "Finds" } } })) or "",
}

local path = opensak.write_text(FILE, page)
print(#finds .. " finds → " .. path)
if PROFILE_FILE then
    -- The same page as text and tables only, for a geocaching.com profile.
    local profile_path = opensak.write_text(PROFILE_FILE, html.profile(page))
    print("For your geocaching.com profile → " .. profile_path)
    print("Open it in a text editor, copy everything and paste it into the profile.")
end
if OPEN then opensak.open(path) end
