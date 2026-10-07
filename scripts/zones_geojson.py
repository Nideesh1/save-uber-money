"""Build ui/public/zones.geojson from data/taxi_zones.zip.

TLC zone polygons are in EPSG:2263 (NY Long Island, US feet). Reproject to EPSG:4326, simplify with
Douglas-Peucker in source feet (pure Python, no shapely), round to 5 decimals (~1 m) and write a compact GeoJSON
with properties {zone_id, zone, borough}.

Run: uv run python scripts/zones_geojson.py [--tolerance-ft 20]
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

import shapefile  # pyshp
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "taxi_zones.zip"
OUT = ROOT / "ui" / "public" / "zones.geojson"


def rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Iterative Douglas-Peucker on an open polyline."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-9
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x0, y0 = pts[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, idx = d, i
        if best > eps and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def simplify_ring(ring: list[tuple[float, float]], eps: float) -> list[tuple[float, float]] | None:
    if len(ring) < 4:
        return None
    # split closed ring into two halves so the endpoints are not degenerate
    mid = len(ring) // 2
    out = rdp(ring[: mid + 1], eps)[:-1] + rdp(ring[mid:], eps)
    if out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def signed_area(ring: list[tuple[float, float]]) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:])) / 2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tolerance-ft", type=float, default=20.0)
    args = ap.parse_args()

    z = zipfile.ZipFile(SRC)
    name = next(n for n in z.namelist() if n.endswith(".shp"))[:-4]
    rd = shapefile.Reader(
        shp=io.BytesIO(z.read(name + ".shp")), shx=io.BytesIO(z.read(name + ".shx")), dbf=io.BytesIO(z.read(name + ".dbf"))
    )
    fields = [f[0].lower() for f in rd.fields[1:]]
    tf = Transformer.from_crs("EPSG:2263", "EPSG:4326", always_xy=True)

    feats = []
    for sr in rd.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        shp = sr.shape
        parts = list(shp.parts) + [len(shp.points)]
        polys: list[list[list[list[float]]]] = []
        for a, b in zip(parts, parts[1:]):
            ring = simplify_ring([tuple(p) for p in shp.points[a:b]], args.tolerance_ft)
            if not ring:
                continue
            lon, lat = tf.transform([p[0] for p in ring], [p[1] for p in ring])
            coords = [[round(x, 5), round(y, 5)] for x, y in zip(lon, lat)]
            # shapefile: outer rings clockwise, holes counter-clockwise (in source plane)
            if signed_area(ring) < 0 or not polys:
                polys.append([coords[::-1]])  # GeoJSON outer ring counter-clockwise
            else:
                polys[-1].append(coords[::-1])
        if not polys:
            continue
        geom = {"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1 else {"type": "MultiPolygon", "coordinates": polys}
        feats.append({
            "type": "Feature",
            "id": int(rec.get("locationid") or rec.get("objectid")),
            "properties": {"zone_id": int(rec.get("locationid") or rec.get("objectid")), "zone": rec.get("zone"), "borough": rec.get("borough")},
            "geometry": geom,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(ROOT)}: {len(feats)} zones, {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
