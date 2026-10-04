# tests/unit-tests/test_gsak_to_opensak.py — GSAK boundary converter output contract.

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

from tools.boundaries import gsak_to_opensak as conv

_SQUARE = "1.0,1.0\n1.0,2.0\n2.0,2.0\n2.0,1.0\n1.0,1.0\n"


def _write_bb_db3(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE Version (Type, Country, State, Version integer);
        CREATE TABLE bb_country (File, Country, MaxLat real, MinLat real, MaxLon real, MinLon real);
        CREATE TABLE bb_state (Country, File, MaxLat real, MinLat real, MaxLon real, MinLon real, Sname);
        CREATE TABLE bb_county (Country, State, File, MaxLat real, MinLat real, MaxLon real, MinLon real, Cname);
        """
    )
    db.execute("INSERT INTO Version VALUES ('c', '', '', 1)")
    db.execute("INSERT INTO Version VALUES ('s', 'usa', '', 2)")
    db.execute("INSERT INTO Version VALUES ('y', 'usa', 'california', 3)")
    db.execute("INSERT INTO Version VALUES ('y', 'usa', 'texas', 5)")
    db.execute("INSERT INTO bb_country VALUES ('1', 'United States', 2.0, 1.0, 2.0, 1.0)")
    db.execute(
        "INSERT INTO bb_state VALUES ('usa', '1', 2.0, 1.0, 2.0, 1.0, 'California')"
    )
    db.execute(
        "INSERT INTO bb_county VALUES ('usa', 'california', '1', 2.0, 1.0, 2.0, 1.0, 'Alpha County')"
    )
    db.execute(
        "INSERT INTO bb_county VALUES ('usa', 'texas', '2', 2.0, 1.0, 2.0, 1.0, 'Beta County')"
    )
    db.commit()
    db.close()


def _write_county_zip(path: Path, file_id: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{file_id}.txt", f"# GsakName={name}\n{_SQUARE}")


def _write_country_zip(path: Path, file_id: str, name: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(file_id, f"# GsakName={name}\n{_SQUARE}")


def _write_state_zip(path: Path, file_id: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{file_id}.txt", f"# GsakName={name}\n{_SQUARE}")


def test_county_packs_are_flat_and_manifest_matches_real_versions(tmp_path: Path) -> None:
    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    _write_state_zip(tmp_path / "states" / "usa_v2.zip", "1", "California")
    _write_county_zip(tmp_path / "counties" / "usa" / "california_v3.zip", "1", "Alpha County")
    _write_county_zip(tmp_path / "counties" / "usa" / "texas_v5.zip", "2", "Beta County")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    counties_dir = out_dir / "counties"
    packs = sorted(p.name for p in counties_dir.iterdir())
    assert packs == ["usa_california.geojson", "usa_texas.geojson"]
    # No nested per-country subdirectory should be created.
    assert all(p.is_file() for p in counties_dir.iterdir())

    db = sqlite3.connect(out_dir / "boundaries.db")
    db.row_factory = sqlite3.Row
    rows = {r["name"]: r["pack"] for r in db.execute("SELECT name, pack FROM region_county")}
    assert rows == {"Alpha County": "usa_california.geojson", "Beta County": "usa_texas.geojson"}
    assert all("/" not in pack for pack in rows.values())

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["dataset_version"] == "1"
    assert manifest["baseline"]["world.geojson"]["version"] == "1"
    assert manifest["baseline"]["usa.geojson"]["version"] == "2"
    assert manifest["packs"]["usa_california.geojson"]["version"] == "3"
    assert manifest["packs"]["usa_texas.geojson"]["version"] == "5"

    def _digest(path: Path) -> tuple[str, int]:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest(), len(data)

    db_sha, db_size = _digest(out_dir / "boundaries.db")
    assert (manifest["boundaries_db"]["sha256"], manifest["boundaries_db"]["size"]) == (db_sha, db_size)

    world_sha, world_size = _digest(out_dir / "countries" / "world.geojson")
    assert manifest["baseline"]["world.geojson"]["sha256"] == world_sha
    assert manifest["baseline"]["world.geojson"]["size"] == world_size

    usa_sha, usa_size = _digest(out_dir / "states" / "usa.geojson")
    assert manifest["baseline"]["usa.geojson"]["sha256"] == usa_sha
    assert manifest["baseline"]["usa.geojson"]["size"] == usa_size

    pack_sha, pack_size = _digest(out_dir / "counties" / "usa_california.geojson")
    assert manifest["packs"]["usa_california.geojson"]["sha256"] == pack_sha
    assert manifest["packs"]["usa_california.geojson"]["size"] == pack_size


def test_simplify_preserves_shape_when_tolerance_is_zero() -> None:
    # tolerance=0 skips Douglas-Peucker, but the result still round-trips through
    # shapely for the validity check (counties rely on this — see the comment
    # on _simplify), so compare geometrically rather than by raw dict equality.
    from shapely.geometry import shape

    geom: dict[str, object] = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}
    result = conv._simplify(geom, 0.0)
    assert shape(result).equals(shape(geom))


def test_simplify_repairs_self_intersection_from_preserve_topology(monkeypatch) -> None:
    # preserve_topology=True doesn't fully guarantee validity on complex
    # multi-ring geometries (see gsak_to_opensak.py's _simplify comment) — force
    # that failure mode with a bowtie (classic self-intersecting polygon) to
    # confirm the buffer(0) repair kicks in and always yields valid output.
    from shapely.geometry import Polygon

    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    assert not bowtie.is_valid

    class _FakeShape:
        def simplify(self, tolerance: float, preserve_topology: bool) -> Polygon:
            return bowtie

    monkeypatch.setattr(conv, "_shp_shape", lambda geom: _FakeShape())

    fake_geom: dict[str, object] = {"type": "Polygon", "coordinates": [[[0, 0]]]}
    result = conv._simplify(fake_geom, 1.0)
    from shapely.geometry import shape

    assert shape(result).is_valid


def test_county_output_is_validity_repaired(tmp_path: Path) -> None:
    # Real GSAK county rings are self-intersecting for ~6% of real counties —
    # not something simplification introduces, present in the raw parsed data
    # itself. Counties are never Douglas-Peucker simplified, but must still go
    # through the same validity repair as the baseline layers (see _simplify).
    from shapely.geometry import shape

    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    # Bowtie ring (lat,lon lines): (0,0),(10,10),(10,0),(0,10),(0,0) in [lon,lat].
    bowtie_txt = "0,0\n10,10\n0,10\n10,0\n0,0\n"
    zip_path = tmp_path / "counties" / "usa" / "california_v3.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("1.txt", f"# GsakName=Bowtie County\n{bowtie_txt}")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    fc = json.loads((out_dir / "counties" / "usa_california.geojson").read_text())
    assert shape(fc["features"][0]["geometry"]).is_valid


def test_sanitize_filename_part_strips_spaces_and_accents() -> None:
    # GitHub Release assets silently rewrite spaces to dots on upload — a raw
    # GSAK state name in the output filename would permanently diverge from
    # the real asset name (found via Canada's "British Columbia", "Québec").
    assert conv._sanitize_filename_part("British Columbia") == "British_Columbia"
    assert conv._sanitize_filename_part("Québec") == "Quebec"
    assert conv._sanitize_filename_part("Newfoundland and Labrador") == "Newfoundland_and_Labrador"
    assert conv._sanitize_filename_part("Alberta") == "Alberta"


def test_county_pack_filename_is_sanitized(tmp_path: Path) -> None:
    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    _write_county_zip(tmp_path / "counties" / "usa" / "california_v3.zip", "1", "Alpha County")
    _write_county_zip(tmp_path / "counties" / "usa" / "texas_v5.zip", "2", "Beta County")

    # Rename the "california" pack to something with a space, mirroring a
    # real GSAK state name — bb.db3/Version rows drive the version lookup,
    # the zip filename drives which file gets opened, so both need updating.
    db = sqlite3.connect(bb_path)
    db.execute("UPDATE bb_county SET State = 'New Brunswick' WHERE State = 'california'")
    db.execute("UPDATE Version SET State = 'New Brunswick' WHERE State = 'california'")
    db.commit()
    db.close()
    (tmp_path / "counties" / "usa" / "california_v3.zip").rename(
        tmp_path / "counties" / "usa" / "New Brunswick_v3.zip"
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)

    packs = sorted(p.name for p in (out_dir / "counties").iterdir())
    assert packs == ["usa_New_Brunswick.geojson", "usa_texas.geojson"]

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "usa_New_Brunswick.geojson" in manifest["packs"]
    assert " " not in "".join(manifest["packs"].keys())


def _run(bb_path: Path, gsak_dir: Path, out_dir: Path) -> None:
    import sys

    argv = sys.argv
    sys.argv = [
        "gsak_to_opensak.py",
        "--bb-path", str(bb_path),
        "--gsak-dir", str(gsak_dir),
        "--out-dir", str(out_dir),
    ]
    try:
        conv.main()
    finally:
        sys.argv = argv


def _converted_dataset(tmp_path: Path) -> Path:
    bb_path = tmp_path / "bb.db3"
    _write_bb_db3(bb_path)
    _write_country_zip(tmp_path / "country_v1.zip", "1", "United States")
    _write_state_zip(tmp_path / "states" / "usa_v2.zip", "1", "California")
    _write_county_zip(tmp_path / "counties" / "usa" / "california_v3.zip", "1", "Alpha County")
    _write_county_zip(tmp_path / "counties" / "usa" / "texas_v5.zip", "2", "Beta County")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    _run(bb_path, tmp_path, out_dir)
    return out_dir


def test_merge_state_folder_adds_baseline_pack(tmp_path: Path) -> None:
    # Old GSAK loose state files (e.g. states/che): <state>.TXT, display name
    # from '# GsakName=', one state split over several files as <name>_N.
    out_dir = _converted_dataset(tmp_path)
    src = tmp_path / "che"
    src.mkdir()
    (src / "Zurich.TXT").write_bytes(b"# GsakName=Z\xfcrich\n" + _SQUARE.encode())
    (src / "Bern_1.txt").write_text("3.0,3.0\n3.0,4.0\n4.0,4.0\n4.0,3.0\n")
    (src / "Bern_2.txt").write_text("3.0,4.0\n3.0,5.0\n4.0,5.0\n4.0,4.0\n")
    # An exclave packed into the ring via an out-and-back bridge — GSAK's
    # even-odd test keeps both parts; buffer(0) would not.
    (src / "Fribourg.txt").write_text(
        "10,10\n10,11\n11,11\n11,10.5\n11,12\n12,12\n12,13\n11,13\n11,12\n11,10.5\n11,10\n"
    )

    pack = conv.merge_region_folder(src, "state", "che", out_dir, 1, 0.0005)
    assert pack == "che.geojson"

    db = sqlite3.connect(out_dir / "boundaries.db")
    rows = db.execute(
        "SELECT name, parent, feature_index, is_bundled FROM region_state WHERE pack = ? ORDER BY id",
        (pack,),
    ).fetchall()
    db.close()
    assert rows == [("Bern", "che", 0, 1), ("Fribourg", "che", 1, 1), ("Zürich", "che", 2, 1)]

    features = json.loads((out_dir / "states" / pack).read_text(encoding="utf-8"))["features"]
    fribourg = conv._shp_shape(features[1]["geometry"])
    assert fribourg.contains(conv._shp_shape({"type": "Point", "coordinates": [10.5, 10.5]}))
    assert fribourg.contains(conv._shp_shape({"type": "Point", "coordinates": [12.5, 11.5]}))
    assert features[0]["geometry"]["type"] == "Polygon"  # Bern_1 + Bern_2 dissolved

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert pack in manifest["baseline"]
    assert pack not in manifest["packs"]


def test_merge_county_folder_appends_pack_and_is_rerunnable(tmp_path: Path) -> None:
    # Loose GSAK macro polygons (e.g. Schweiz_Gemeinden): <state>/<county>.txt,
    # every '#' line starts a new section of the same county.
    out_dir = _converted_dataset(tmp_path)

    src = tmp_path / "Gemeinden"
    (src / "AG").mkdir(parents=True)
    (src / "BE").mkdir()
    (src / "AG" / "Aarau.txt").write_text(f"#Aarau\n{_SQUARE}", encoding="utf-8")
    # Two touching sections + a Latin-1 header, as in merged municipalities.
    (src / "BE" / "Münsingen.txt").write_bytes(
        b"#M\xfcnsingen\n3.0,3.0\n3.0,4.0\n4.0,4.0\n4.0,3.0\n"
        b"#Fusionen 2017\n#Tr\xe4gertschi\n4.0,3.0\n4.0,4.0\n5.0,4.0\n5.0,3.0\n"
    )

    for _ in range(2):
        pack = conv.merge_region_folder(src, "county", "che", out_dir, 23)
    assert pack == "che_all_che.geojson"

    db = sqlite3.connect(out_dir / "boundaries.db")
    rows = db.execute(
        "SELECT id, name, parent, feature_index FROM region_county WHERE pack = ? ORDER BY id",
        (pack,),
    ).fetchall()
    assert [r[1:] for r in rows] == [("Aarau", "che", 0), ("Münsingen", "che", 1)]
    assert rows[0][0] > 2  # appended after the bb.db3 counties, ids reused on re-run
    assert db.execute("SELECT COUNT(*) FROM region_county").fetchone()[0] == 4
    bbox = db.execute(
        "SELECT min_lat, max_lat, min_lon, max_lon FROM rtree_county WHERE id = ?", (rows[1][0],)
    ).fetchone()
    assert bbox == (3.0, 5.0, 3.0, 4.0)
    db.close()

    features = json.loads((out_dir / "counties" / pack).read_text(encoding="utf-8"))["features"]
    assert features[1]["geometry"]["type"] == "Polygon"  # sections dissolved into one

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["packs"][pack]["version"] == "23"
    assert "usa_texas.geojson" in manifest["packs"]
    db_bytes = (out_dir / "boundaries.db").read_bytes()
    assert manifest["boundaries_db"]["sha256"] == hashlib.sha256(db_bytes).hexdigest()
