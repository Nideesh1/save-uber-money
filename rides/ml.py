"""ML track: "money + when to travel". LightGBM quantile models over cleaned local TLC HVFHV trips.

Train:  uv run python -m rides.ml train --rows 2000000
Tools:  predict_fare(...), best_time_to_travel(...)  (plain functions, JSON-able dict returns)
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MODELS = ROOT / "models"
PARQUET = DATA / "fhvhv_tripdata_2026-08.parquet"

COMPANIES = {"HV0003": "Uber", "HV0005": "Lyft"}
COMPANY_CODE = {"Uber": 0, "Lyft": 1}
BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR", "Unknown"]
BOROUGH_CODE = {b: i for i, b in enumerate(BOROUGHS)}
FEATURES = ["pu_lat", "pu_lon", "do_lat", "do_lon", "km", "pu_borough", "do_borough",
            "airport", "hour", "dow", "company"]
CATEGORICAL = ["pu_borough", "do_borough", "company"]
QUANTILES = {"fare_p10": 0.1, "fare_p50": 0.5, "fare_p90": 0.9}
HOLDOUT_DAYS = 4  # last N days of the month (by request date) are the holdout


# ---------------------------------------------------------------- zones

def build_zones() -> dict[int, dict]:
    """zone_id -> {zone, borough, service_zone, lat, lon}. Reuses the data track's shapefile centroid logic
    (area-weighted polygon centroid in EPSG:2263, reprojected to WGS84)."""
    from rides.etl import load_zones

    return {z["zone_id"]: {"zone": z["zone"], "borough": z["borough"], "service_zone": z["service_zone"],
                           "lat": z["loc"]["lat"], "lon": z["loc"]["lon"]}
            for z in load_zones() if z.get("loc")}


AIRPORT_ZONES = (1, 132, 138)  # Newark, JFK, LaGuardia (same as rides.etl)


def _is_airport(zid: int) -> int:
    return int(zid in AIRPORT_ZONES)


def _haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * np.arcsin(np.sqrt(h))


# ---------------------------------------------------------------- data

def load_sample(rows: int) -> dict[str, np.ndarray]:
    """Cleaned sample via DuckDB (same filters as the ETL)."""
    import duckdb

    sql = f"""
    SELECT * FROM (
    SELECT
      CASE hvfhs_license_num WHEN 'HV0003' THEN 0 ELSE 1 END AS company,
      PULocationID AS pu, DOLocationID AS dobj,
      hour(request_datetime) AS hour,
      isodow(request_datetime) - 1 AS dow,
      day(request_datetime) AS day,
      date_diff('second', request_datetime, pickup_datetime) AS wait_s,
      base_passenger_fare + coalesce(tolls,0) + coalesce(bcf,0) + coalesce(sales_tax,0)
        + coalesce(congestion_surcharge,0) + coalesce(airport_fee,0) + coalesce(cbd_congestion_fee,0) AS total
    FROM read_parquet('{PARQUET}')
    WHERE hvfhs_license_num IN ('HV0003','HV0005')
      AND base_passenger_fare > 0 AND base_passenger_fare <= 500
      AND trip_miles > 0 AND trip_time > 0
      AND request_datetime IS NOT NULL AND pickup_datetime IS NOT NULL
      AND date_diff('second', request_datetime, pickup_datetime) BETWEEN 0 AND 3600
      AND PULocationID NOT IN (264,265) AND DOLocationID NOT IN (264,265)
      AND request_datetime >= TIMESTAMP '2026-08-01' AND request_datetime < TIMESTAMP '2026-09-01'
    ) USING SAMPLE reservoir({int(rows)} ROWS) REPEATABLE (42)
    """
    con = duckdb.connect()
    con.execute("SET threads TO 8")
    return con.execute(sql).fetchnumpy()


def featurize(pu, do, hour, dow, company, zones: dict) -> np.ndarray:
    """Feature matrix (float64) in FEATURES order. pu/do/hour/dow/company are int arrays."""
    ids = np.array(sorted(zones))
    lut = np.full((ids.max() + 1, 5), np.nan)
    for zid in ids:
        z = zones[zid]
        lut[zid] = (z["lat"], z["lon"], BOROUGH_CODE.get(z["borough"], BOROUGH_CODE["Unknown"]),
                    _is_airport(zid), 1)
    pu = np.asarray(pu, dtype=int)
    do = np.asarray(do, dtype=int)
    p, d = lut[pu], lut[do]
    km = _haversine_km(p[:, 0], p[:, 1], d[:, 0], d[:, 1])
    airport = np.maximum(p[:, 3], d[:, 3])
    return np.column_stack([p[:, 0], p[:, 1], d[:, 0], d[:, 1], km, p[:, 2], d[:, 2], airport,
                            np.asarray(hour, float), np.asarray(dow, float), np.asarray(company, float)])


def _pinball(y, q, alpha):
    d = y - q
    return float(np.mean(np.maximum(alpha * d, (alpha - 1) * d)))


# ---------------------------------------------------------------- train

def train(rows: int = 2_000_000, rounds: int = 400) -> dict:
    import lightgbm as lgb

    t0 = time.time()
    MODELS.mkdir(exist_ok=True)
    zones = build_zones()
    print(f"zones: {len(zones)} centroids")

    s = load_sample(rows)
    keep = np.isin(s["pu"], list(zones)) & np.isin(s["dobj"], list(zones))
    s = {k: np.asarray(v)[keep] for k, v in s.items()}
    # per-zone trip counts in the sample: weights for area (multi-zone) predictions
    pu_n = np.bincount(s["pu"].astype(int), minlength=266)
    do_n = np.bincount(s["dobj"].astype(int), minlength=266)
    for zid, z in zones.items():
        z["pu_trips"], z["do_trips"] = int(pu_n[zid]), int(do_n[zid])
    (MODELS / "zones.json").write_text(json.dumps({str(k): v for k, v in sorted(zones.items())}, indent=0))
    X = featurize(s["pu"], s["dobj"], s["hour"], s["dow"], s["company"], zones)
    y_total = s["total"].astype(float)
    y_wait = s["wait_s"].astype(float)
    cutoff = 31 - HOLDOUT_DAYS + 1  # Aug 28..31 holdout
    te = s["day"] >= cutoff
    tr = ~te
    print(f"sample {len(y_total):,} rows ({time.time() - t0:.1f}s); train {tr.sum():,} / holdout {te.sum():,}")

    params = dict(objective="quantile", learning_rate=0.08, num_leaves=127, min_data_in_leaf=200,
                  feature_fraction=0.9, bagging_fraction=0.8, bagging_freq=1, max_bin=255,
                  num_threads=0, verbose=-1, seed=42)
    preds = {}
    for name, alpha in [*QUANTILES.items(), ("wait_p50", 0.5)]:
        t1 = time.time()
        y = y_wait if name.startswith("wait") else y_total
        ds = lgb.Dataset(X[tr], y[tr], feature_name=FEATURES, categorical_feature=CATEGORICAL, free_raw_data=False)
        b = lgb.train({**params, "alpha": alpha}, ds, num_boost_round=rounds)
        b.save_model(str(MODELS / f"{name}.txt"))
        preds[name] = b.predict(X[te])
        print(f"  {name}: {time.time() - t1:.1f}s")

    lo, mid, hi = np.sort(np.column_stack([preds["fare_p10"], preds["fare_p50"], preds["fare_p90"]]), axis=1).T
    yt, yw = y_total[te], y_wait[te]
    metrics = {
        "mae_usd": round(float(np.mean(np.abs(yt - mid))), 3),
        "mape_p50": round(float(np.median(np.abs(yt - mid) / yt)), 4),
        "pinball": {"p10": round(_pinball(yt, lo, 0.1), 4), "p50": round(_pinball(yt, mid, 0.5), 4),
                    "p90": round(_pinball(yt, hi, 0.9), 4)},
        "coverage_80": round(float(np.mean((yt >= lo) & (yt <= hi))), 4),
        "wait_mae_s": round(float(np.mean(np.abs(yw - preds["wait_p50"]))), 1),
        "baseline_mae_usd": round(float(np.mean(np.abs(yt - np.median(y_total[tr])))), 3),
    }
    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trained_on": "TLC HVFHV 2026-08 (Uber+Lyft, cleaned)",
        "train_rows": int(tr.sum()), "holdout_rows": int(te.sum()),
        "holdout": f"request date 2026-08-{cutoff:02d}..2026-08-31",
        "features": FEATURES, "categorical": CATEGORICAL, "boroughs": BOROUGHS,
        "companies": list(COMPANY_CODE), "num_boost_round": rounds,
        "metrics": metrics, "train_seconds": round(time.time() - t0, 1),
    }
    (MODELS / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta["metrics"], indent=2))
    print(f"train time {meta['train_seconds']}s")
    return meta


# ---------------------------------------------------------------- inference (lazy, cached once)

_CACHE: dict = {}
_LOCK = threading.Lock()


def _load():
    if _CACHE:
        return _CACHE
    with _LOCK:
        if not _CACHE:
            import lightgbm as lgb

            if not (MODELS / "meta.json").exists():
                raise RuntimeError("models not trained; run `uv run python -m rides.ml train --rows 2000000`")
            boosters = {n: lgb.Booster(model_file=str(MODELS / f"{n}.txt"))
                        for n in [*QUANTILES, "wait_p50"]}
            zones = {int(k): v for k, v in json.loads((MODELS / "zones.json").read_text()).items()}
            meta = json.loads((MODELS / "meta.json").read_text())
            _CACHE.update(boosters=boosters, zones=zones, meta=meta)
    return _CACHE


def _model_meta(m: dict) -> dict:
    return {"mae_usd": m["metrics"]["mae_usd"], "coverage_80": m["metrics"]["coverage_80"],
            "trained_on": f"{m['trained_on']}, {m['train_rows']:,} trips", "trained_at": m["trained_at"]}


def _norm_company(company: str | None) -> list[str]:
    if company is None or str(company).strip().lower() in ("", "any", "both", "all"):
        return list(COMPANY_CODE)
    c = str(company).strip().capitalize()
    if c not in COMPANY_CODE:
        raise ValueError(f"company must be Uber or Lyft, got {company!r}")
    return [c]


def _ids(x) -> list[int]:
    ids = [int(v) for v in (x if isinstance(x, (list, tuple, set)) else [x])]
    if not ids:
        raise ValueError("empty zone list")
    return list(dict.fromkeys(ids))


def _grid(pus: list[int], dos: list[int], hours: list[int], dow: int):
    """Predict every (pu, do, hour, company) in one model call per booster.
    Returns q[P, H, C, 4] with (low, mid, high, wait_s), pair list and normalized trip-count weights[P]."""
    c = _load()
    zones = c["zones"]
    for z in pus + dos:
        if z not in zones:
            raise ValueError(f"unknown zone id {z} (valid: 1-263)")
    pairs = [(p, d) for p in pus for d in dos]
    P, H, C = len(pairs), len(hours), len(COMPANY_CODE)
    pp = np.repeat([p for p, _ in pairs], H * C)
    dd = np.repeat([d for _, d in pairs], H * C)
    hh = np.tile(np.repeat(hours, C), P)
    cc = np.tile(list(COMPANY_CODE.values()), P * H)
    X = featurize(pp, dd, hh, np.full(len(pp), dow), cc, zones)
    b = c["boosters"]
    q = np.sort(np.column_stack([b[n].predict(X) for n in QUANTILES]), axis=1)
    w = np.maximum(b["wait_p50"].predict(X), 0)
    out = np.column_stack([q, w]).reshape(P, H, C, 4)
    wt = np.array([(zones[p].get("pu_trips", 0) + 1) * (zones[d].get("do_trips", 0) + 1) for p, d in pairs], float)
    return out, pairs, wt / wt.sum()


def _quote(v) -> dict:
    return {"low": round(float(v[0]), 2), "mid": round(float(v[1]), 2), "high": round(float(v[2]), 2),
            "wait_s": int(round(float(v[3])))}


def _spread(pairs, mids, zones, k: int = 5) -> list[dict]:
    """Top-k cheapest + top-k priciest pairs by mid (all pairs if few)."""
    order = list(np.argsort(mids))
    idx = order if len(order) <= 2 * k else order[:k] + order[-k:]
    return [{"pu": zones[pairs[i][0]]["zone"], "do": zones[pairs[i][1]]["zone"], "pu_zone_id": pairs[i][0],
             "do_zone_id": pairs[i][1], "mid": round(float(mids[i]), 2)} for i in idx]


def _route(pus, dos, zones) -> dict:
    out = {"route": {"pu": " / ".join(zones[z]["zone"] for z in pus), "do": " / ".join(zones[z]["zone"] for z in dos)}}
    if len(pus) > 1 or len(dos) > 1:
        out["areas"] = {"pu": [zones[z]["zone"] for z in pus], "do": [zones[z]["zone"] for z in dos]}
    return out


def predict_fare(pu_zone_id: int | list[int], do_zone_id: int | list[int], hour: int, dow: int,
                 company: str | None = None) -> dict:
    """Predict the rider's total cost (fare + tolls + fees + taxes, excluding tip) for a future Uber/Lyft trip.

    Uses LightGBM quantile models trained on real NYC TLC trips. Returns an 80% range (low=p10, mid=p50,
    high=p90) in USD plus the expected pickup wait in seconds, with per-company quotes.
    Pass lists of zone ids for areas (e.g. all Midtown zones -> all Uptown zones): every pair is predicted and
    combined with trip-count weights; `pairs` lists the 5 cheapest and 5 priciest pairs.

    Args:
        pu_zone_id: TLC pickup zone id (1-263) or a list of ids for an area, e.g. from resolve_zone.
        do_zone_id: TLC dropoff zone id (1-263) or a list of ids.
        hour: request hour 0-23.
        dow: day of week, 0=Monday .. 6=Sunday.
        company: "Uber" or "Lyft"; None quotes both and blends them for the headline range.
    """
    hour, dow = int(hour) % 24, int(dow) % 7
    pus, dos = _ids(pu_zone_id), _ids(do_zone_id)
    q, pairs, wt = _grid(pus, dos, [hour], dow)
    q = q[:, 0]  # [P, C, 4]
    comb = np.einsum("p,pck->ck", wt, q)  # weighted per company
    names = list(COMPANY_CODE)
    pick = [names.index(c) for c in _norm_company(company)]
    zones = _CACHE["zones"]
    out = {
        **_quote(comb[pick].mean(axis=0)),
        "company": names[pick[0]] if len(pick) == 1 else "any",
        **_route(pus, dos, zones),
        "hour": hour, "dow": dow,
        "company_quotes": [{"company": n, **_quote(comb[i])} for i, n in enumerate(names)],
    }
    if len(pairs) > 1:
        out["n_pairs"] = len(pairs)
        out["pairs"] = _spread(pairs, q[:, pick, 1].mean(axis=1), zones)
    out["model"] = _model_meta(_CACHE["meta"])
    return out


def best_time_to_travel(pu_zone_id: int | list[int], do_zone_id: int | list[int], dow: int,
                        earliest_hour: int = 0, latest_hour: int = 23) -> dict:
    """Find the cheapest hour to request an Uber/Lyft for a route (or area to area) on a given weekday.

    Predicts the p10/p50/p90 total cost and expected wait for every hour in [earliest_hour, latest_hour]
    (cheaper company per hour; trip-count-weighted over all zone pairs when lists are passed), and reports the
    best and worst hours and how much leaving at the best hour saves.

    Args:
        pu_zone_id: TLC pickup zone id (1-263) or a list of ids for an area.
        do_zone_id: TLC dropoff zone id (1-263) or a list of ids.
        dow: day of week, 0=Monday .. 6=Sunday.
        earliest_hour: first candidate hour 0-23 (inclusive).
        latest_hour: last candidate hour 0-23 (inclusive); if < earliest_hour the window wraps past midnight.
    """
    e, l, dow = int(earliest_hour) % 24, int(latest_hour) % 24, int(dow) % 7
    hours = list(range(e, l + 1)) if e <= l else list(range(e, 24)) + list(range(0, l + 1))
    pus, dos = _ids(pu_zone_id), _ids(do_zone_id)
    q, pairs, wt = _grid(pus, dos, hours, dow)  # [P, H, C, 4]
    comb = np.einsum("p,phck->hck", wt, q)
    names = list(COMPANY_CODE)
    best_c = comb[:, :, 1].argmin(axis=1)  # cheaper company per hour
    per_hour = [{"hour": h, **_quote(comb[i, best_c[i]]), "company": names[best_c[i]]} for i, h in enumerate(hours)]
    bi = int(np.argmin([r["mid"] for r in per_hour]))
    wi = int(np.argmax([r["mid"] for r in per_hour]))
    zones = _CACHE["zones"]
    out = {
        **_route(pus, dos, zones),
        "dow": dow,
        "hours": per_hour,
        "best": {"hour": per_hour[bi]["hour"], "company": per_hour[bi]["company"], "mid": per_hour[bi]["mid"]},
        "worst": {"hour": per_hour[wi]["hour"], "mid": per_hour[wi]["mid"]},
        "savings_usd": round(per_hour[wi]["mid"] - per_hour[bi]["mid"], 2),
    }
    if len(pairs) > 1:
        out["n_pairs"] = len(pairs)
        out["pairs"] = _spread(pairs, q[:, bi, best_c[bi], 1], zones)  # spread at the best hour
    out["model"] = _model_meta(_CACHE["meta"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="rides.ml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--rows", type=int, default=2_000_000)
    t.add_argument("--rounds", type=int, default=400)
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.rows, a.rounds)


if __name__ == "__main__":
    main()
