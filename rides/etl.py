"""ETL: TLC High Volume FHV trips -> DuckDB clean + sample -> Elasticsearch `rides` and `zones`.

Stages (each reported via on_stage(name, frac)): download, clean, sample, zones, index_zones, index_rides.
CLI: uv run python -m rides.etl --rows 50000
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Callable

import duckdb
import shapefile
from elasticsearch import helpers
from pyproj import Transformer

from rides.es import INFERENCE_ID, RIDES_INDEX, ZONES_INDEX, client

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BASE = "https://d37ci6vzurychx.cloudfront.net"
FILES = {
    "fhvhv_tripdata_2026-08.parquet": f"{BASE}/trip-data/fhvhv_tripdata_2026-08.parquet",
    "taxi_zone_lookup.csv": f"{BASE}/misc/taxi_zone_lookup.csv",
    "taxi_zones.zip": f"{BASE}/misc/taxi_zones.zip",
}
PARQUET = DATA / "fhvhv_tripdata_2026-08.parquet"
AIRPORT_ZONES = (1, 132, 138)  # Newark, JFK, LaGuardia
EXCLUDED_ZONES = (264, 265)  # Unknown, Outside of NYC

# Common names people actually type; fed into the semantic text and the BM25 `aliases` field.
ALIASES: dict[int, list[str]] = {
    1: ["EWR", "Newark", "Newark Liberty airport"],
    132: ["JFK", "Kennedy airport", "John F Kennedy airport", "JFK International"],
    138: ["LGA", "LaGuardia", "La Guardia airport"],
    230: ["Times Square", "Midtown theater", "Broadway theaters", "Theater District"],
    255: ["Williamsburg", "Billyburg", "Billy burg", "North Williamsburg", "Bedford Ave"],
    256: ["Williamsburg", "Billyburg", "Billy burg", "South Williamsburg"],
    217: ["Williamsburg", "Billyburg", "South Williamsburg"],
    80: ["Williamsburg", "Billyburg", "East Williamsburg", "Bushwick border"],
    7: ["Astoria", "Astoria Queens"],
    179: ["Astoria", "Old Astoria"],
    8: ["Astoria", "Astoria Park"],
    161: ["Midtown", "Midtown Manhattan", "Rockefeller Center"],
    162: ["Midtown East", "Grand Central area"],
    163: ["Midtown North"],
    164: ["Midtown South", "Herald Square", "Koreatown"],
    186: ["Penn Station", "Madison Square Garden", "MSG"],
    170: ["Murray Hill", "Grand Central"],
    100: ["Garment District"],
    234: ["Union Square", "Union Sq"],
    249: ["West Village", "Greenwich Village"],
    113: ["Greenwich Village", "NYU"],
    114: ["Greenwich Village South", "Washington Square"],
    79: ["East Village"],
    148: ["Lower East Side", "LES"],
    144: ["Little Italy", "SoHo", "Nolita"],
    211: ["SoHo"],
    231: ["Tribeca", "TriBeCa", "Civic Center"],
    261: ["World Trade Center", "WTC", "Oculus"],
    87: ["Financial District", "FiDi", "Wall Street"],
    88: ["Financial District", "FiDi", "Wall Street"],
    209: ["Seaport", "South Street Seaport"],
    68: ["Chelsea", "East Chelsea"],
    246: ["Chelsea", "West Chelsea", "Hudson Yards", "High Line"],
    90: ["Flatiron", "Flatiron District"],
    107: ["Gramercy", "Gramercy Park"],
    43: ["Central Park"],
    142: ["Upper West Side", "UWS", "Lincoln Center"],
    143: ["Upper West Side", "UWS", "Lincoln Square"],
    238: ["Upper West Side", "UWS"],
    239: ["Upper West Side", "UWS"],
    236: ["Upper East Side", "UES"],
    237: ["Upper East Side", "UES"],
    140: ["Lenox Hill", "Upper East Side"],
    141: ["Lenox Hill", "Upper East Side"],
    41: ["Harlem", "Central Harlem"],
    74: ["East Harlem", "Spanish Harlem", "El Barrio"],
    116: ["Hamilton Heights", "Harlem"],
    65: ["Downtown Brooklyn", "DTBK", "MetroTech"],
    66: ["DUMBO", "Vinegar Hill"],
    33: ["Brooklyn Heights"],
    181: ["Park Slope"],
    112: ["Greenpoint"],
    17: ["Bedford-Stuyvesant", "Bed-Stuy", "Bed Stuy"],
    37: ["Bushwick"],
    36: ["Bushwick"],
    97: ["Fort Greene"],
    49: ["Clinton Hill"],
    25: ["Boerum Hill", "Cobble Hill"],
    40: ["Carroll Gardens"],
    61: ["Crown Heights"],
    62: ["Crown Heights"],
    55: ["Coney Island"],
    29: ["Brighton Beach"],
    106: ["Gowanus"],
    228: ["Sunset Park"],
    226: ["Sunnyside"],
    129: ["Jackson Heights"],
    260: ["Woodside"],
    82: ["Elmhurst"],
    83: ["Elmhurst"],
    92: ["Flushing"],
    93: ["Flushing Meadows", "Citi Field", "US Open", "Mets stadium"],
    145: ["Long Island City", "LIC", "Hunters Point"],
    146: ["Long Island City", "LIC", "Queens Plaza"],
    202: ["Roosevelt Island"],
    247: ["Yankee Stadium", "West Concourse"],
    159: ["Melrose South"],
    69: ["Grand Concourse"],
}

# Vague area words people type; every member zone gets the area name as an alias.
AREAS: dict[str, tuple[int, ...]] = {
    "Midtown": (161, 162, 163, 164, 170, 230, 100, 186, 48, 50, 229, 233, 137),
    "Uptown": (236, 237, 238, 239, 140, 141, 262, 263, 41, 42, 74, 75, 116, 152, 166, 243, 244, 127, 128, 120, 151, 24),
    "Upper Manhattan": (41, 42, 74, 75, 116, 152, 166, 243, 244, 127, 128, 120, 153),
    "Harlem": (41, 42, 74, 75, 116, 152),
    "Downtown": (87, 88, 261, 209, 12, 13, 231, 125, 211, 144, 45, 232, 148, 79, 4, 113, 114, 249, 158, 234),
    "Lower Manhattan": (87, 88, 261, 209, 12, 13, 231, 45, 232, 148),
    "The Village": (113, 114, 249, 158, 79),
    "Hell's Kitchen": (48, 50),
    "Clinton": (48, 50),
    "Turtle Bay": (229, 233),
    "Yorkville": (262, 263),
    "Chinatown": (45, 232),
    "Battery Park": (12, 13),
    "Washington Heights": (243, 244),
    "Inwood": (127, 128),
    "The airport": (132, 138, 1),
    "Airport": (132, 138, 1),
    "NYC airports": (132, 138, 1),
}
for _area, _ids in AREAS.items():
    for _zid in _ids:
        ALIASES.setdefault(_zid, [])
        if _area not in ALIASES[_zid]:
            ALIASES[_zid].append(_area)


def _noop(name: str, frac: float) -> None:  # pragma: no cover
    return None


# ---------------------------------------------------------------- download

def _download(on_stage: Callable[[str, float], None]) -> None:
    DATA.mkdir(exist_ok=True)
    names = list(FILES)
    for i, name in enumerate(names):
        dest = DATA / name
        url = FILES[name]
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            size = int(r.headers.get("Content-Length") or 0)
        if dest.exists() and (size == 0 or dest.stat().st_size == size):
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
            got, last = 0, 0.0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                got += len(chunk)
                frac = (i + (got / size if size else 0)) / len(names)
                if frac - last >= 0.02:
                    on_stage("download", frac)
                    last = frac
        tmp.replace(dest)
        on_stage("download", (i + 1) / len(names))


# ---------------------------------------------------------------- clean + sample (DuckDB)

CLEAN_SQL = f"""
CREATE OR REPLACE TABLE clean AS
SELECT
  CASE hvfhs_license_num WHEN 'HV0003' THEN 'Uber' ELSE 'Lyft' END AS company,
  CAST(PULocationID AS INTEGER) AS pu_zone_id,
  CAST(DOLocationID AS INTEGER) AS do_zone_id,
  request_datetime AS request_ts,
  CAST(hour(request_datetime) AS INTEGER) AS hour,
  CAST(isodow(request_datetime) - 1 AS INTEGER) AS dow,
  CAST(date_diff('second', request_datetime, pickup_datetime) AS INTEGER) AS wait_s,
  CAST(trip_miles AS DOUBLE) AS trip_miles,
  CAST(trip_time AS DOUBLE) / 60.0 AS trip_min,
  CAST(base_passenger_fare AS DOUBLE) AS fare,
  CAST(base_passenger_fare + coalesce(tolls, 0) + coalesce(bcf, 0) + coalesce(sales_tax, 0)
       + coalesce(congestion_surcharge, 0) + coalesce(airport_fee, 0) + coalesce(cbd_congestion_fee, 0)
       AS DOUBLE) AS total,
  CAST(coalesce(tips, 0) AS DOUBLE) AS tips,
  CAST(coalesce(driver_pay, 0) AS DOUBLE) AS driver_pay,
  (PULocationID IN {AIRPORT_ZONES} OR DOLocationID IN {AIRPORT_ZONES}) AS airport
FROM read_parquet(?)
WHERE hvfhs_license_num IN ('HV0003', 'HV0005')
  AND base_passenger_fare > 0 AND base_passenger_fare <= 500
  AND trip_miles > 0 AND trip_time > 0
  AND request_datetime IS NOT NULL AND pickup_datetime IS NOT NULL
  AND date_diff('second', request_datetime, pickup_datetime) BETWEEN 0 AND 3600
  AND PULocationID NOT IN {EXCLUDED_ZONES} AND DOLocationID NOT IN {EXCLUDED_ZONES}
  AND PULocationID BETWEEN 1 AND 263 AND DOLocationID BETWEEN 1 AND 263
"""


def _clean(con: duckdb.DuckDBPyConnection) -> int:
    con.execute(CLEAN_SQL, [str(PARQUET)])
    return con.execute("SELECT count(*) FROM clean").fetchone()[0]


def _sample(con: duckdb.DuckDBPyConnection, rows: int) -> int:
    con.execute(f"CREATE OR REPLACE TABLE sample AS SELECT * FROM clean USING SAMPLE reservoir({int(rows)} ROWS) REPEATABLE (42)")
    con.execute("DROP TABLE clean")
    return con.execute("SELECT count(*) FROM sample").fetchone()[0]


# ---------------------------------------------------------------- zones (shapefile centroids)

def _ring_centroid(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Signed area and area-weighted centroid sums for one ring (shoelace)."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    return a / 2.0, cx / 6.0, cy / 6.0


def load_zones() -> list[dict]:
    """263 zones with lat/lon centroids. Shapefile is NY State Plane Long Island (EPSG:2263, US feet)."""
    lookup = {}
    with open(DATA / "taxi_zone_lookup.csv", newline="") as f:
        for row in csv.DictReader(f):
            lookup[int(row["LocationID"])] = row
    zf = zipfile.ZipFile(DATA / "taxi_zones.zip")
    base = "taxi_zones/taxi_zones"
    sf = shapefile.Reader(
        shp=io.BytesIO(zf.read(base + ".shp")),
        shx=io.BytesIO(zf.read(base + ".shx")),
        dbf=io.BytesIO(zf.read(base + ".dbf")),
    )
    fields = [f[0].lower() for f in sf.fields[1:]]
    acc: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # area, sum_x*a, sum_y*a
    to_ll = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)
    shapes: dict[int, list] = defaultdict(list)  # zone_id -> polygons (lon/lat), for the geo_shape point-in-zone lookup
    for sr in sf.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        zid = int(rec["locationid"])
        shp = sr.shape
        geo = shp.__geo_interface__
        polys = [geo["coordinates"]] if geo["type"] == "Polygon" else geo["coordinates"]
        for poly in polys:
            shapes[zid].append([[[round(c, 6) for c in to_ll.transform(x, y)] for x, y in ring] for ring in poly])
        parts = list(shp.parts) + [len(shp.points)]
        for s, e in zip(parts, parts[1:]):
            ring = [tuple(p) for p in shp.points[s:e]]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            a, sx, sy = _ring_centroid(ring)
            # sums are already area-weighted; holes have opposite sign and subtract naturally
            acc[zid][0] += a
            acc[zid][1] += sx
            acc[zid][2] += sy
    zones = []
    for zid in sorted(lookup):
        if zid in EXCLUDED_ZONES:
            continue
        row = lookup[zid]
        loc = None
        if zid in acc and acc[zid][0] != 0:
            a, sx, sy = acc[zid]
            lon, lat = to_ll.transform(sx / a, sy / a)
            loc = {"lat": round(lat, 6), "lon": round(lon, 6)}
        aliases = ALIASES.get(zid, [])
        sem = f"{row['Zone']}, {row['Borough']}"
        if aliases:
            sem += ". Also known as: " + ", ".join(aliases)
        zones.append({
            "zone_id": zid,
            "zone": row["Zone"],
            "borough": row["Borough"],
            "service_zone": row["service_zone"],
            "aliases": " | ".join(aliases),
            "name_sem": sem,
            "loc": loc,
            "shape": {"type": "MultiPolygon", "coordinates": shapes[zid]} if shapes.get(zid) else None,
        })
    return zones


# ---------------------------------------------------------------- indexing

ZONES_MAPPING = {
    "properties": {
        "zone_id": {"type": "integer"},
        "zone": {"type": "text", "fields": {"kw": {"type": "keyword"}}},
        "borough": {"type": "keyword"},
        "service_zone": {"type": "keyword"},
        "aliases": {"type": "text"},
        "name_sem": {"type": "semantic_text", "inference_id": INFERENCE_ID},
        "loc": {"type": "geo_point"},
        "shape": {"type": "geo_shape"},
    }
}

RIDES_MAPPING = {
    "dynamic": "strict",
    "properties": {
        "company": {"type": "keyword"},
        "pu_zone_id": {"type": "integer"},
        "do_zone_id": {"type": "integer"},
        "pu_zone": {"type": "text", "fields": {"kw": {"type": "keyword"}}},
        "do_zone": {"type": "text", "fields": {"kw": {"type": "keyword"}}},
        "pu_borough": {"type": "keyword"},
        "do_borough": {"type": "keyword"},
        "request_ts": {"type": "date"},
        "hour": {"type": "integer"},
        "dow": {"type": "integer"},
        "wait_s": {"type": "integer"},
        "trip_miles": {"type": "float"},
        "trip_min": {"type": "float"},
        "fare": {"type": "float"},
        "total": {"type": "float"},
        "tips": {"type": "float"},
        "driver_pay": {"type": "float"},
        "airport": {"type": "boolean"},
        "pu_loc": {"type": "geo_point"},
        "do_loc": {"type": "geo_point"},
    },
}


def _recreate(index: str, mapping: dict) -> None:
    es = client()
    es.indices.delete(index=index, ignore_unavailable=True)
    es.indices.create(index=index, mappings=mapping)


def _index_zones(zones: list[dict], on_stage: Callable[[str, float], None]) -> int:
    _recreate(ZONES_INDEX, ZONES_MAPPING)
    actions = ({"_index": ZONES_INDEX, "_id": z["zone_id"], "_source": {k: v for k, v in z.items() if v is not None}} for z in zones)
    done = 0
    for ok, info in helpers.streaming_bulk(client(), actions, chunk_size=50, max_retries=3, raise_on_error=True):
        done += 1
        if done % 50 == 0:
            on_stage("index_zones", done / len(zones))
    client().indices.refresh(index=ZONES_INDEX)
    return done


def _ride_docs(con: duckdb.DuckDBPyConnection, zones: list[dict], batch: int = 10000):
    zmap = {z["zone_id"]: z for z in zones}
    cur = con.execute("SELECT * FROM sample")
    cols = [d[0] for d in cur.description]
    while rows := cur.fetchmany(batch):
        for r in rows:
            d = dict(zip(cols, r))
            pu, do = zmap.get(d["pu_zone_id"]), zmap.get(d["do_zone_id"])
            if pu is None or do is None:
                continue
            d["request_ts"] = d["request_ts"].isoformat(timespec="seconds")
            for k in ("trip_miles", "trip_min", "fare", "total", "tips", "driver_pay"):
                d[k] = round(float(d[k]), 2)
            d["pu_zone"], d["pu_borough"] = pu["zone"], pu["borough"]
            d["do_zone"], d["do_borough"] = do["zone"], do["borough"]
            if pu["loc"]:
                d["pu_loc"] = pu["loc"]
            if do["loc"]:
                d["do_loc"] = do["loc"]
            yield {"_index": RIDES_INDEX, "_source": d}


def _index_rides(con, zones, total: int, on_stage: Callable[[str, float], None]) -> int:
    _recreate(RIDES_INDEX, RIDES_MAPPING)
    done, failed, last = 0, 0, 0.0
    for ok, info in helpers.parallel_bulk(
        client(), _ride_docs(con, zones), thread_count=int(os.environ.get("ETL_BULK_THREADS", "6")),
        chunk_size=int(os.environ.get("ETL_BULK_CHUNK", "5000")), raise_on_error=False,
    ):
        if ok:
            done += 1
        else:
            failed += 1
        if total and (done + failed) / total - last >= 0.01:
            last = (done + failed) / total
            on_stage("index_rides", min(last, 1.0))
    client().indices.refresh(index=RIDES_INDEX)
    if failed:
        raise RuntimeError(f"{failed} ride docs failed to index")
    return done


# ---------------------------------------------------------------- driver

def run(sample_rows: int, on_stage: Callable[[str, float], None] = lambda name, frac: None) -> dict:
    """Run the full ETL. Recreates both indices (idempotent). Returns counts and per-stage seconds."""
    timings: dict[str, float] = {}
    out: dict = {"sample_rows": sample_rows}

    def stage(name, fn, *a):
        on_stage(name, 0.0)
        t = time.perf_counter()
        res = fn(*a)
        timings[name] = round(time.perf_counter() - t, 2)
        on_stage(name, 1.0)
        return res

    stage("download", _download, on_stage)
    con = duckdb.connect()
    tmp = DATA / "duckdb_tmp"
    con.execute(f"SET temp_directory = '{tmp}'")
    out["rows_clean"] = stage("clean", _clean, con)
    out["rows_sampled"] = stage("sample", _sample, con, sample_rows)
    zones = stage("zones", load_zones)
    out["zones"] = len(zones)
    out["zones_indexed"] = stage("index_zones", _index_zones, zones, on_stage)
    out["rides_indexed"] = stage("index_rides", _index_rides, con, zones, out["rows_sampled"], on_stage)
    con.close()
    out["timings_s"] = timings
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Load TLC HVFHV trips into Elasticsearch")
    p.add_argument("--rows", type=int, default=int(os.environ.get("SAMPLE_ROWS", "50000")))
    args = p.parse_args()
    last: dict[str, float] = {}

    def show(name: str, frac: float) -> None:
        if frac in (0.0, 1.0) or frac - last.get(name, 0) >= 0.1:
            last[name] = frac
            print(f"[{time.strftime('%H:%M:%S')}] {name} {frac:.0%}", flush=True)

    print(json.dumps(run(args.rows, show), indent=2))


if __name__ == "__main__":
    main()
