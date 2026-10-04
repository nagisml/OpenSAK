#!/usr/bin/env python3
# tools/boundaries/gsak_to_opensak.py — convert GSAK boundary data to OpenSAK format.
#
# Run with the project's own venv (3.11+), not a bare system python3 — older
# zipfile implementations (e.g. 3.8) fall back to legacy CP437 decoding for
# zip entries that don't set the UTF-8 flag bit (several GSAK county zips
# don't), silently mangling non-ASCII filenames and dropping those rows with
# no warning:
#   .venv/bin/python3 tools/boundaries/gsak_to_opensak.py [--gsak-dir DATA] [--out-dir DATA]
#
# Reads:  <gsak-dir>/bb.db3
#          <gsak-dir>/country_v<N>.zip          (N from bb.db3 Version table)
#          <gsak-dir>/states/<cc>[_vN].zip
#          <gsak-dir>/counties/<cc>/<pack>[_vN].zip
#
# Writes: <out-dir>/boundaries.db              (OpenSAK schema)
#          <out-dir>/manifest.json              (dataset version; "baseline" = world.geojson
#                                                + state packs, fetched wholesale on first run;
#                                                "packs" = county packs, fetched on demand)
#          <out-dir>/countries/world.geojson         simplified baseline (--simplify-tolerance)
#          <out-dir>/states/<cc>.geojson             simplified baseline, one per country code
#          <out-dir>/counties/<cc>_<pack>.geojson    full-resolution, on-demand (one per pack, flat)
#
# Regions GSAK ships as loose polygon files instead of a bb.db3 pack are merged
# into an already converted <out-dir> as one more pack, e.g. the Swiss cantons
# (old GSAK states/che/<canton>.txt) and municipalities (Schweiz_Gemeinden
# macro: <DIR>/<canton>/<municipality>.txt):
#   .venv/bin/python3 tools/boundaries/gsak_to_opensak.py --out-dir DATA \
#       --merge-dir DIR --merge-layer state --merge-country che
#   .venv/bin/python3 tools/boundaries/gsak_to_opensak.py --out-dir DATA \
#       --merge-dir DIR --merge-layer county --merge-country che --merge-version 24
# The generated packs are kept in tools/boundaries/packs/{states,counties}/.
#
# After this script finishes, BoundaryStore(Path("<out-dir>")) resolves
# coordinates offline using the engine in src/opensak/geo/.

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
import zipfile
from pathlib import Path

import numpy as np
from shapely.geometry import mapping as _shp_mapping
from shapely.geometry import shape as _shp_shape
from shapely.geometry import LineString
from shapely.ops import polygonize, unary_union

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATA = _REPO_ROOT / "data"

# ── Output schema (matches BoundaryStore expectations) ────────────────────────

_SCHEMA = """\
CREATE VIRTUAL TABLE rtree_country USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_country (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE rtree_state USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_state (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE rtree_county USING rtree(id, min_lat, max_lat, min_lon, max_lon);
CREATE TABLE region_county (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    parent        TEXT,
    pack          TEXT NOT NULL,
    feature_index INTEGER NOT NULL,
    poly_version  INTEGER NOT NULL DEFAULT 1,
    is_bundled    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE file_version (layer TEXT, country TEXT, state TEXT, version INTEGER)
"""

# ── GSAK polygon text parser ──────────────────────────────────────────────────

def _parse_gsak_txt(content: str) -> list[list[list[list[float]]]]:
    """Parse GSAK lat,lon polygon text into GeoJSON polygon coordinate groups.

    Returns a list of [outer_ring, *holes] lists, one per '# Inclusion area'
    section. A single element means Polygon; multiple means MultiPolygon.
    GeoJSON convention: coordinates are [lon, lat].
    """
    polygons: list[list[list[list[float]]]] = []
    poly_rings: list[list[list[float]]] = []  # rings for the current inclusion area
    cur_ring: list[list[float]] = []           # ring being accumulated

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "Inclusion area" in line or "Exclusion area" in line:
                # Flush current ring into the current polygon group
                if cur_ring:
                    poly_rings.append(cur_ring)
                    cur_ring = []
                # On a new inclusion area, also flush the polygon group
                if "Inclusion area" in line and poly_rings:
                    polygons.append(poly_rings)
                    poly_rings = []
        else:
            try:
                # GSAK uses several formats: tab (county/USA), comma (most states/countries),
                # comma with trailing comma (France/Italy), space-separated (Great Britain).
                if "\t" in line:
                    parts = line.split("\t")
                elif "," in line:
                    parts = [p.strip() for p in line.split(",")]
                else:
                    parts = line.split()
                parts = [p for p in parts if p]
                cur_ring.append([float(parts[1]), float(parts[0])])  # [lon, lat]
            except (ValueError, IndexError):
                pass

    # Flush whatever is left after the last line
    if cur_ring:
        poly_rings.append(cur_ring)
    if poly_rings:
        polygons.append(poly_rings)

    return polygons


def _parse_gsak_loose_txt(content: str) -> list[list[list[list[float]]]]:
    """Parse a stand-alone GSAK macro polygon file (e.g. Schweiz_Gemeinden).

    Unlike the bb.db3 zips these have no '# Inclusion area' markers — every
    '#<name>' comment line starts a new polygon (a municipality that absorbed
    others keeps one section per former municipality). Returns one
    single-ring polygon group per section, in the same shape as _parse_gsak_txt.
    """
    polygons: list[list[list[list[float]]]] = []
    cur_ring: list[list[float]] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if cur_ring:
                polygons.append([cur_ring])
                cur_ring = []
            continue
        parts = [p for p in re.split(r"[\s,]+", line) if p]
        try:
            cur_ring.append([float(parts[1]), float(parts[0])])  # [lon, lat]
        except (ValueError, IndexError):
            pass
    if cur_ring:
        polygons.append([cur_ring])
    return [p for p in polygons if len(p[0]) >= 3]


def _even_odd_ring(ring: list[list[float]]):
    """Shapely geometry covering what GSAK's even-odd point-in-polygon treats as inside.

    Loose GSAK files pack exclaves into one ring via out-and-back "bridge"
    edges (e.g. Fribourg's enclaves inside Vaud). buffer(0) resolves such a
    self-touching ring by winding, dropping or swallowing whole exclaves.
    Instead: node the ring into faces and keep each face whose interior point
    crosses the ring an odd number of times — exactly GSAK's semantics.
    """
    shp = _shp_shape({"type": "Polygon", "coordinates": [ring]})
    if shp.is_valid:
        return shp
    closed = ring if ring[0] == ring[-1] else ring + [ring[0]]
    xs = np.array([p[0] for p in closed])
    ys = np.array([p[1] for p in closed])
    x0, y0, x1, y1 = xs[:-1], ys[:-1], xs[1:], ys[1:]
    inside = []
    for face in polygonize(unary_union(LineString(closed))):
        pt = face.representative_point()
        straddles = (y0 > pt.y) != (y1 > pt.y)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_cross = x0 + (pt.y - y0) * (x1 - x0) / (y1 - y0)
        if np.count_nonzero(straddles & (x_cross > pt.x)) % 2:
            inside.append(face)
    return unary_union(inside)


def _union_geometry(polygons: list[list[list[list[float]]]]) -> dict[str, object]:
    # Sections of a merged municipality share borders — dissolve them into one
    # (Multi)Polygon so point-in-polygon never trips over the seams. Each part
    # is repaired first; unary_union fails on invalid input.
    parts = []
    for rings in polygons:
        shp = _even_odd_ring(rings[0])
        if not shp.is_valid:
            shp = shp.buffer(0)
        if not shp.is_empty:
            parts.append(shp)
    if not parts:
        return {"type": "Polygon", "coordinates": []}
    return _simplify(_shp_mapping(unary_union(parts)), 0.0)


def _split_antimeridian(ring: list[list[float]]) -> list[list[list[float]]]:
    """Split a ring that crosses the antimeridian via teleportation edges (|Δlon| > 180°).

    GSAK stores antimeridian-spanning countries (e.g. Russia) as one ring with synthetic
    "jump" edges that skip ≥180° of longitude.  Those edges also create a figure-8 topology
    where the ring revisits ring[0] in the middle, making the implicit closing edge synthetic
    too.  We detect both kinds and emit valid, independently-closed sub-rings so that
    standard ray-casting PIP gives correct results everywhere.
    """
    n = len(ring)
    split_after: set[int] = set()

    for i in range(n):
        if abs(ring[i][0] - ring[(i + 1) % n][0]) > 180:
            split_after.add(i)

    if not split_after:
        return [ring]

    # If ring[0] recurs mid-ring the implicit closing edge ring[n-1]→ring[0] is also synthetic.
    v0 = ring[0]
    for i in range(1, n - 1):
        if ring[i][0] == v0[0] and ring[i][1] == v0[1]:
            split_after.add(n - 1)
            break

    sorted_splits = sorted(split_after)
    sub_rings: list[list[list[float]]] = []
    for k, sp in enumerate(sorted_splits):
        start = sp + 1
        end = sorted_splits[(k + 1) % len(sorted_splits)]
        seg: list[list[float]] = (
            list(ring[start : end + 1]) if start <= end
            else list(ring[start:]) + list(ring[: end + 1])
        )
        if seg and seg[0] != seg[-1]:
            seg.append(seg[0])
        if len(seg) >= 4:
            sub_rings.append(seg)

    return sub_rings or [ring]


def _geometry(polygons: list[list[list[list[float]]]]) -> dict[str, object]:
    # Split outer rings that span the antimeridian into valid sub-polygons.
    all_polys: list[list[list[list[float]]]] = []
    for rings in polygons:
        holes = rings[1:]
        for split_ring in _split_antimeridian(rings[0]):
            all_polys.append([split_ring] + holes)
    if not all_polys:
        return {"type": "Polygon", "coordinates": []}
    if len(all_polys) == 1:
        return {"type": "Polygon", "coordinates": all_polys[0]}
    return {"type": "MultiPolygon", "coordinates": all_polys}


def _simplify(geom: dict[str, object], tolerance: float) -> dict[str, object]:
    # Douglas-Peucker (degrees-based tolerance) for the baseline layers
    # (country/state) only — counties stay full-resolution since they're
    # fetched on demand, not bundled. Called with tolerance=0 for counties to
    # skip simplification but still get the validity repair below: the raw
    # GSAK-derived rings are self-intersecting for ~6% of real counties (not
    # something simplification introduces), and shapely's .contains() doesn't
    # raise on an invalid geometry — it silently returns wrong answers.
    if not geom.get("coordinates"):
        return geom
    shp = _shp_shape(geom)
    if tolerance > 0:
        shp = shp.simplify(tolerance, preserve_topology=True)
    if not shp.is_valid:
        # preserve_topology doesn't fully guarantee validity on complex multi-ring
        # geometries (nearby-but-separate rings can end up crossing) — buffer(0)
        # is the standard GEOS trick to repair self-intersections.
        shp = shp.buffer(0)
    if shp.is_empty:
        return geom
    return _shp_mapping(shp)


def _feature(name: str, parent: str | None, geom: dict[str, object], version: int) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {
            "name": name,
            "parent": parent,
            "version": version,
            "source": "gsak",
            "licence": "ODbL",
        },
        "geometry": geom,
    }


# ── Zip helpers ───────────────────────────────────────────────────────────────

def _find_zip(parent: Path, name: str, version: int) -> Path | None:
    """Locate the zip for a given pack name + version.

    GSAK names the current version as <name>.zip (version 1) or
    <name>_v<N>.zip / <name>V<N>.zip (version N > 1), keeping older copies
    with the version suffix. Falls back to the base <name>.zip.
    """
    if version <= 1:
        candidates = [parent / f"{name}.zip"]
    else:
        candidates = [
            parent / f"{name}_v{version}.zip",
            parent / f"{name}V{version}.zip",   # ArizonaV3.zip style
            parent / f"{name}.zip",              # fallback
        ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _read_zip_entry(zf: zipfile.ZipFile, entry: str) -> str | None:
    try:
        raw = zf.read(entry)
    except KeyError:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # GSAK files pre-2020 often use Latin-1


def _sanitize_filename_part(name: str) -> str:
    # GSAK state/pack names can contain spaces and accents (e.g. Canada's
    # "British Columbia", "Québec") — GitHub Release assets silently rewrite
    # such characters on upload (spaces become dots), which would otherwise
    # make manifest.json permanently diverge from the real asset names.
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_only).strip("_")


# ── Version table ─────────────────────────────────────────────────────────────

def _load_versions(bb: sqlite3.Connection) -> dict[tuple[str, str, str], int]:
    """Read all rows from the GSAK Version table."""
    cur = bb.execute("SELECT Type, Country, State, Version FROM Version")
    return {
        (str(r[0]), str(r[1] or ""), str(r[2] or "")): int(r[3])
        for r in cur.fetchall()
    }


# ── Conversion passes ─────────────────────────────────────────────────────────

def _convert_countries(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
    simplify_tolerance: float = 0.0,
) -> None:
    version = versions.get(("c", "", ""), 46)
    zip_path = gsak_dir / f"country_v{version}.zip"
    if not zip_path.exists():
        print(f"  ! country zip not found: {zip_path.name}")
        return

    print(f"  {zip_path.name}")
    features: list[dict[str, object]] = []
    skipped = 0

    with zipfile.ZipFile(zip_path) as zf:
        rows = bb.execute(
            "SELECT rowid, File, Country, MaxLat, MinLat, MaxLon, MinLon FROM bb_country"
        ).fetchall()

        for rowid, file_name, country_name, max_lat, min_lat, max_lon, min_lon in rows:
            content = _read_zip_entry(zf, file_name)
            if content is None:
                skipped += 1
                continue

            geom = _simplify(_geometry(_parse_gsak_txt(content)), simplify_tolerance)
            feature_index = len(features)
            features.append(_feature(country_name, None, geom, version))

            conn.execute(
                "INSERT INTO rtree_country VALUES (?, ?, ?, ?, ?)",
                (rowid, min_lat, max_lat, min_lon, max_lon),
            )
            conn.execute(
                "INSERT INTO region_country VALUES (?, ?, NULL, 'world.geojson', ?, 1, 1)",
                (rowid, country_name, feature_index),
            )

    out_path = out_dir / "countries" / "world.geojson"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  → {len(features)} countries, {skipped} skipped")


def _convert_states(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
    simplify_tolerance: float = 0.0,
) -> dict[str, int]:
    rows = bb.execute(
        "SELECT rowid, Country, File, MaxLat, MinLat, MaxLon, MinLon, Sname FROM bb_state"
    ).fetchall()

    # group by country code so we open each state zip once
    by_cc: dict[str, list] = {}
    for row in rows:
        by_cc.setdefault(str(row[1]), []).append(row)

    pack_versions: dict[str, int] = {}
    total = 0
    missing = 0
    for cc in sorted(by_cc):
        version = versions.get(("s", cc, ""), 1)
        zip_path = _find_zip(gsak_dir / "states", cc, version)
        if zip_path is None:
            missing += 1
            continue

        features: list[dict[str, object]] = []
        pack_name = f"{cc}.geojson"

        with zipfile.ZipFile(zip_path) as zf:
            for row in by_cc[cc]:
                rowid, _, file_id, max_lat, min_lat, max_lon, min_lon, sname = row
                content = _read_zip_entry(zf, f"{file_id}.txt")
                if content is None:
                    continue

                geom = _simplify(_geometry(_parse_gsak_txt(content)), simplify_tolerance)
                feature_index = len(features)
                features.append(_feature(sname, cc, geom, version))

                conn.execute(
                    "INSERT INTO rtree_state VALUES (?, ?, ?, ?, ?)",
                    (rowid, min_lat, max_lat, min_lon, max_lon),
                )
                conn.execute(
                    "INSERT INTO region_state VALUES (?, ?, ?, ?, ?, 1, 1)",
                    (rowid, sname, cc, pack_name, feature_index),
                )

        if features:
            out_path = out_dir / "states" / pack_name
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
                encoding="utf-8",
            )
            total += len(features)
            pack_versions[pack_name] = version

    print(f"  → {total} states across {len(by_cc)} codes ({missing} zip(s) missing)")
    return pack_versions


def _convert_counties(
    bb: sqlite3.Connection,
    gsak_dir: Path,
    out_dir: Path,
    conn: sqlite3.Connection,
    versions: dict[tuple[str, str, str], int],
) -> dict[str, int]:
    rows = bb.execute(
        "SELECT rowid, Country, State, File, MaxLat, MinLat, MaxLon, MinLon, Cname FROM bb_county"
    ).fetchall()

    # group by (country, pack) so we open each county zip once, keeping
    # feature_index sequential within the per-country output GeoJSON
    by_cc_pack: dict[tuple[str, str], list] = {}
    for row in rows:
        key = (str(row[1]), str(row[2]))
        by_cc_pack.setdefault(key, []).append(row)

    pack_versions: dict[str, int] = {}
    total = 0
    missing = 0
    for (cc, pack_name) in sorted(by_cc_pack):
        version = versions.get(("y", cc, pack_name), 1)
        zip_path = _find_zip(gsak_dir / "counties" / cc, pack_name, version)
        if zip_path is None:
            missing += 1
            continue

        # Counties from the same pack are flattened into a single release-asset
        # filename (cc_pack.geojson) — GitHub Release assets can't hold subdirectories,
        # and packs.py fetches them by this exact flat name. pack_name is sanitized
        # here (not when locating the source zip above) since some GSAK state names
        # contain spaces/accents (e.g. Canada's "British Columbia", "Québec").
        pack_features: list[dict[str, object]] = []
        pack_region_rows: list[tuple] = []
        out_pack = f"{cc}_{_sanitize_filename_part(pack_name)}.geojson"

        with zipfile.ZipFile(zip_path) as zf:
            for row in by_cc_pack[(cc, pack_name)]:
                rowid, _, _, file_id, max_lat, min_lat, max_lon, min_lon, cname = row
                content = _read_zip_entry(zf, f"{file_id}.txt")
                if content is None:
                    continue

                # tolerance=0: no simplification, but still validity-checked/repaired.
                geom = _simplify(_geometry(_parse_gsak_txt(content)), 0.0)
                feature_index = len(pack_features)
                pack_features.append(_feature(cname, cc, geom, version))

                conn.execute(
                    "INSERT INTO rtree_county VALUES (?, ?, ?, ?, ?)",
                    (rowid, min_lat, max_lat, min_lon, max_lon),
                )
                conn.execute(
                    "INSERT INTO region_county VALUES (?, ?, ?, ?, ?, 1, 0)",
                    (rowid, cname, cc, out_pack, feature_index),
                )

        if pack_features:
            out_path = out_dir / "counties" / out_pack
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps({"type": "FeatureCollection", "features": pack_features}, ensure_ascii=False),
                encoding="utf-8",
            )
            total += len(pack_features)
            pack_versions[out_pack] = version

    print(f"  → {total} counties ({missing} zip(s) missing)")
    return pack_versions


_GSAK_NAME_RE = re.compile(r"^#\s*GsakName\s*=\s*(.+?)\s*$", re.MULTILINE)


def _loose_region_name(txt: Path, content: str) -> str:
    # '# GsakName=' (state files) carries the real display name — the file
    # stem is ASCII-folded ("Graubunden", "BaselLand"). Without it the stem
    # is the name (what GetPolygon() writes into GSAK's County field), minus
    # the _1/_2 suffix GSAK uses to split one region over several files.
    m = _GSAK_NAME_RE.search(content)
    name = m.group(1) if m else re.sub(r"_\d+$", "", txt.stem)
    return unicodedata.normalize("NFC", name)


def merge_region_folder(
    src_dir: Path,
    layer: str,
    cc: str,
    out_dir: Path,
    version: int,
    simplify_tolerance: float = 0.0,
) -> str:
    """Add a folder of loose GSAK polygon files to an existing dataset as one pack.

    For data that GSAK ships as loose polygon files instead of a bb.db3 pack:
      layer="state":  <src>/<state>.txt — e.g. the old GSAK states/che folder;
                      written as baseline states/<cc>.geojson (simplified).
      layer="county": <src>/<state>/<county>.txt — e.g. Schweiz_Gemeinden;
                      written as on-demand counties/<cc>_all_<cc>.geojson.
    Files resolving to the same name are dissolved into one region. Rows go
    into <out-dir>/boundaries.db and the pack is registered in manifest.json;
    re-running replaces the previous run's rows. Returns the pack filename.
    """
    if layer not in ("state", "county"):
        raise ValueError(f"unsupported layer: {layer}")
    is_bundled = 1 if layer == "state" else 0
    out_pack = f"{cc}.geojson" if layer == "state" else f"{cc}_all_{cc}.geojson"
    pattern = "*" if layer == "state" else "*/*"
    out_db = out_dir / "boundaries.db"
    if not out_db.exists():
        raise SystemExit(f"boundaries.db not found: {out_db} (run the full conversion first)")

    # name -> polygon groups, in first-seen order
    regions: dict[str, list[list[list[list[float]]]]] = {}
    txts = [p for p in src_dir.glob(pattern) if p.is_file() and p.suffix.lower() == ".txt"]
    for txt in sorted(txts, key=lambda p: (p.parent.name, p.stem.casefold())):
        raw = txt.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("latin-1")
        name = _loose_region_name(txt, content)
        regions.setdefault(name, []).extend(_parse_gsak_loose_txt(content))

    conn = sqlite3.connect(out_db)
    old_ids = [r[0] for r in conn.execute(
        f"SELECT id FROM region_{layer} WHERE pack = ?", (out_pack,)
    )]
    for region_id in old_ids:
        conn.execute(f"DELETE FROM rtree_{layer} WHERE id = ?", (region_id,))
    conn.execute(f"DELETE FROM region_{layer} WHERE pack = ?", (out_pack,))
    # Reuse the previous id block on a re-run so ids stay stable.
    next_id = min(old_ids) if old_ids else (
        conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM region_{layer}").fetchone()[0] + 1
    )

    features: list[dict[str, object]] = []
    skipped = 0
    for name, polygons in regions.items():
        geom = _union_geometry(polygons)
        if not geom.get("coordinates"):
            skipped += 1
            continue
        # bbox from the full-resolution shape so simplification never shrinks
        # the R-Tree box below the real boundary
        min_lon, min_lat, max_lon, max_lat = _shp_shape(geom).bounds
        geom = _simplify(geom, simplify_tolerance)
        conn.execute(
            f"INSERT INTO rtree_{layer} VALUES (?, ?, ?, ?, ?)",
            (next_id, min_lat, max_lat, min_lon, max_lon),
        )
        conn.execute(
            f"INSERT INTO region_{layer} VALUES (?, ?, ?, ?, ?, 1, ?)",
            (next_id, name, cc, out_pack, len(features), is_bundled),
        )
        features.append(_feature(name, cc, geom, version))
        next_id += 1
    conn.commit()
    conn.close()

    out_path = out_dir / ("states" if layer == "state" else "counties") / out_pack
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )

    section = "baseline" if layer == "state" else "packs"
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest["boundaries_db"] = _file_digest(out_db)
    manifest.setdefault(section, {})[out_pack] = {"version": str(version), **_file_digest(out_path)}
    manifest[section] = dict(sorted(manifest[section].items()))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"  → {len(features)} {layer} regions in {out_pack}, {skipped} skipped")
    return out_pack


def _file_digest(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _write_manifest(
    out_dir: Path,
    dataset_version: int,
    baseline_versions: dict[str, int],
    pack_versions: dict[str, int],
) -> None:
    # "baseline" (world.geojson + state packs) is bundled in every install and
    # fetched wholesale on first run when not bundled (see geo/packs.py
    # fetch_baseline). "packs" (county-level) stays on-demand, per-country.
    # sha256/size let verify_release.py detect a corrupted or truncated
    # release asset without needing to reprocess the whole dataset.
    manifest = {
        "dataset_version": str(dataset_version),
        "boundaries_db": _file_digest(out_dir / "boundaries.db"),
        "baseline": {
            name: {"version": str(v), **_file_digest(
                out_dir / ("countries" if name == "world.geojson" else "states") / name
            )}
            for name, v in sorted(baseline_versions.items())
        },
        "packs": {
            name: {"version": str(v), **_file_digest(out_dir / "counties" / name)}
            for name, v in sorted(pack_versions.items())
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert GSAK boundary data (bb.db3 + polygon zips) to OpenSAK format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "After running, point BoundaryStore at <out-dir> or set\n"
            "OPENSAK_BOUNDARIES_DIR=<out-dir> to use the converted data."
        ),
    )
    parser.add_argument(
        "--gsak-dir",
        type=Path,
        default=_DEFAULT_DATA,
        metavar="DIR",
        help="Directory containing country_v*.zip, states/, counties/ "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--bb-path",
        type=Path,
        default=_REPO_ROOT / "bb.db3",
        metavar="FILE",
        help="Path to GSAK bb.db3 (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_DEFAULT_DATA,
        metavar="DIR",
        help="Output directory for boundaries.db and GeoJSON packs "
             "(default: same as --gsak-dir)",
    )
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.0005,
        metavar="DEGREES",
        help="Douglas-Peucker tolerance applied to the country/state baseline "
             "only (shipped in every install) — counties stay full-resolution "
             "since they're fetched on demand. 0 disables simplification "
             "(default: %(default)s, ~55m at the equator)",
    )
    parser.add_argument(
        "--merge-dir",
        type=Path,
        metavar="DIR",
        help="Instead of a full conversion, merge a folder of loose GSAK polygon "
             "files into the existing --out-dir dataset as one pack "
             "(see --merge-layer)",
    )
    parser.add_argument(
        "--merge-layer",
        choices=("state", "county"),
        default="county",
        help="Layer for --merge-dir: 'state' reads <DIR>/<state>.txt, 'county' "
             "reads <DIR>/<state>/<county>.txt, e.g. Schweiz_Gemeinden "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--merge-country",
        metavar="CC",
        help="Country code for --merge-dir (as in the dataset, e.g. che)",
    )
    parser.add_argument(
        "--merge-version",
        type=int,
        default=1,
        metavar="N",
        help="Polygon version recorded for --merge-dir (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.merge_dir is not None:
        if not args.merge_country:
            parser.error("--merge-dir requires --merge-country")
        print(f"Merging {args.merge_dir}…")
        merge_region_folder(
            args.merge_dir, args.merge_layer, args.merge_country, args.out_dir,
            args.merge_version,
            args.simplify_tolerance if args.merge_layer == "state" else 0.0,
        )
        return

    gsak_dir: Path = args.gsak_dir
    out_dir: Path = args.out_dir
    simplify_tolerance: float = args.simplify_tolerance

    bb_path: Path = args.bb_path
    if not bb_path.exists():
        raise SystemExit(f"bb.db3 not found: {bb_path}")

    out_db = out_dir / "boundaries.db"
    out_db.unlink(missing_ok=True)
    conn = sqlite3.connect(out_db)
    for stmt in _SCHEMA.split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()

    bb = sqlite3.connect(f"file:{bb_path}?mode=ro", uri=True)
    versions = _load_versions(bb)
    country_version = versions.get(("c", "", ""), 0)

    print("Converting countries…")
    _convert_countries(bb, gsak_dir, out_dir, conn, versions, simplify_tolerance)
    conn.commit()

    print("Converting states…")
    state_versions = _convert_states(bb, gsak_dir, out_dir, conn, versions, simplify_tolerance)
    conn.commit()
    baseline_versions = {"world.geojson": country_version, **state_versions}

    print("Converting counties…")
    pack_versions = _convert_counties(bb, gsak_dir, out_dir, conn, versions)
    conn.commit()

    conn.execute(
        "INSERT INTO file_version VALUES ('dataset', NULL, NULL, ?)",
        (country_version,),
    )
    conn.commit()

    bb.close()
    conn.close()

    _write_manifest(out_dir, country_version, baseline_versions, pack_versions)

    print(f"\nDone → {out_dir}")
    print(f"  boundaries.db   {out_db.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
