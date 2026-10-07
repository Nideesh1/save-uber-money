"""Agent tools over the `rides` and `zones` indices.

Every function returns a JSON-able dict with `n` (trips behind the numbers) and `query` (the ES request body used).
All money values are USD; `total` is the rider cost excluding tip. Percentiles come from ES `percentiles` aggs;
buckets with fewer than 20 trips are dropped.
Every pu_zone_id / do_zone_id accepts one zone id or a list of zone ids (a whole area, e.g. all Midtown zones);
with a list the answer covers every matching trip and the result includes `areas: {pu: [names], do: [names]}`.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from functools import lru_cache

from rides.es import RIDES_INDEX, ZONES_INDEX, client

MIN_DOCS = 20


def _r(v, nd: int = 2):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), nd)


def _pct(agg: dict, p: float):
    vals = agg.get("values", {})
    return _r(vals.get(str(float(p))) if str(float(p)) in vals else vals.get(str(p)))


@lru_cache(maxsize=1)
def _zones() -> dict[int, dict]:
    res = client().search(index=ZONES_INDEX, size=300, source=["zone_id", "zone", "borough", "loc"], query={"match_all": {}})
    return {h["_source"]["zone_id"]: h["_source"] for h in res["hits"]["hits"]}


ZoneArg = int | list[int]


def _zone_name(zid: ZoneArg | None) -> str | None:
    """Zone name for an id; for a list of ids, the names joined with ' + '."""
    if zid is None:
        return None
    if isinstance(zid, (list, tuple)):
        return " + ".join(n for n in (_zone_name(z) for z in zid) if n)
    z = _zones().get(int(zid))
    return z["zone"] if z else None


def _names(zid: ZoneArg | None) -> list[str]:
    ids = zid if isinstance(zid, (list, tuple)) else ([] if zid is None else [zid])
    return [n for n in (_zone_name(int(z)) for z in ids) if n]


def _areas(out: dict, **kw) -> dict:
    """Attach `areas: {pu: [...], do: [...]}` when any zone arg was a list (an area, not one zone)."""
    if any(isinstance(v, (list, tuple)) for v in kw.values()):
        out["areas"] = {k: _names(v) for k, v in kw.items() if v is not None}
    return out


def _filters(**kw) -> list[dict]:
    out = []
    for k, v in kw.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            out.append({"terms": {k: [int(x) for x in v]}})
        else:
            out.append({"term": {k: v}})
    return out


def _search(body: dict) -> dict:
    return client().search(index=RIDES_INDEX, **body)


def resolve_zone(text: str) -> dict:
    """Resolve a place name, nickname or vague area (e.g. "JFK", "billyburg", "midtown") to TLC taxi zones.

    Hybrid search: BM25 over zone name and aliases fused (RRF) with semantic search over zone descriptions.
    Returns up to 8 matches (every member zone for an area word like "uptown"), each with zone_id, zone, borough, score and `trips` (pickups in the rides sample),
    so you can pick the busiest zone of an area.
    """
    body = {
        "size": 8,
        "_source": ["zone_id", "zone", "borough"],
        "retriever": {
            "rrf": {
                "retrievers": [
                    {"standard": {"query": {"multi_match": {
                        "query": text, "fields": ["zone^3", "aliases^2", "borough"], "fuzziness": "AUTO"}}}},
                    {"standard": {"query": {"semantic": {"field": "name_sem", "query": text}}}},
                ],
                "rank_window_size": 50,
            }
        },
    }
    # an area word ("uptown", "midtown") is an alias on every member zone: return the whole area, not the top 8
    area = {"size": 40, "_source": ["zone_id", "zone", "borough"], "query": {"match_phrase": {"aliases": text}}}
    res = client().search(index=ZONES_INDEX, **area)
    if len(res["hits"]["hits"]) >= 2:
        body = area
    else:
        res = client().search(index=ZONES_INDEX, **body)
    matches = [{"zone_id": h["_source"]["zone_id"], "zone": h["_source"]["zone"],
                "borough": h["_source"]["borough"], "score": _r(h["_score"], 4)} for h in res["hits"]["hits"]]
    ids = [m["zone_id"] for m in matches]
    trips_body = {"size": 0, "query": {"terms": {"pu_zone_id": ids}},
                  "aggs": {"by_zone": {"terms": {"field": "pu_zone_id", "size": max(len(ids), 1)}}}}
    counts = {}
    if ids:
        tr = _search(trips_body)
        counts = {b["key"]: b["doc_count"] for b in tr["aggregations"]["by_zone"]["buckets"]}
    for m in matches:
        m["trips"] = counts.get(m["zone_id"], 0)
    return {"matches": matches, "n": sum(m["trips"] for m in matches),
            "query": {"zones": body, "rides": trips_body}}


GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/search"


def geocode_address(address: str) -> dict:
    """Turn a street address or landmark ("350 5th Ave", "Columbia University", "Barclays Center") into its exact TLC
    zone. NYC GeoSearch (the city's free geocoder) gives the point; Elasticsearch finds the zone polygon containing
    it (geo_shape intersects). Use this instead of resolve_zone when the user gives a street address or a specific place.
    Returns {address, lat, lon, zone_id, zone, borough, query} (zone fields are None if the point is outside NYC zones).
    """
    url = GEOSEARCH + "?" + urllib.parse.urlencode({"text": address, "size": 1})
    with urllib.request.urlopen(url, timeout=10) as r:
        feats = json.load(r).get("features") or []
    if not feats:
        return {"address": address, "lat": None, "lon": None, "zone_id": None, "zone": None, "borough": None, "n": 0,
                "query": None, "error": "address not found in NYC GeoSearch"}
    lon, lat = feats[0]["geometry"]["coordinates"]
    body = {"size": 1, "_source": ["zone_id", "zone", "borough"],
            "query": {"geo_shape": {"shape": {"shape": {"type": "point", "coordinates": [lon, lat]}, "relation": "intersects"}}}}
    hits = client().search(index=ZONES_INDEX, **body)["hits"]["hits"]
    z = hits[0]["_source"] if hits else {}
    return {"address": feats[0]["properties"].get("label", address), "lat": lat, "lon": lon,
            "zone_id": z.get("zone_id"), "zone": z.get("zone"), "borough": z.get("borough"), "n": len(hits), "query": body}


def fare_by_hour(pu_zone_id: ZoneArg, do_zone_id: ZoneArg, dow: int | None = None, company: str | None = None) -> dict:
    """Rider cost (total, excl. tip) by hour of request for a pickup->dropoff zone pair.

    Zone args take one zone id or a list of ids (a whole area, e.g. every Midtown zone from resolve_zone).

    dow: 0=Mon..6=Sun (optional). company: "Uber" or "Lyft" (optional).
    Returns p25/p50/p75 per hour (hours with >=20 trips), plus cheapest_hour and priciest_hour by p50.
    """
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": _filters(pu_zone_id=pu_zone_id, do_zone_id=do_zone_id, dow=dow, company=company)}},
        "aggs": {"by_hour": {"terms": {"field": "hour", "size": 24, "min_doc_count": MIN_DOCS, "order": {"_key": "asc"}},
                             "aggs": {"total": {"percentiles": {"field": "total", "percents": [25, 50, 75]}}}}},
    }
    res = _search(body)
    hours = [{"hour": b["key"], "n": b["doc_count"], "p25": _pct(b["total"], 25),
              "p50": _pct(b["total"], 50), "p75": _pct(b["total"], 75)}
             for b in res["aggregations"]["by_hour"]["buckets"]]
    priced = [h for h in hours if h["p50"] is not None]
    return _areas({
        "route": {"pu": _zone_name(pu_zone_id), "do": _zone_name(do_zone_id)},
        "n": res["hits"]["total"]["value"],
        "hours": hours,
        "cheapest_hour": min(priced, key=lambda h: h["p50"])["hour"] if priced else None,
        "priciest_hour": max(priced, key=lambda h: h["p50"])["hour"] if priced else None,
        "query": body,
    }, pu=pu_zone_id, do=do_zone_id)


def compare_companies(pu_zone_id: ZoneArg, do_zone_id: ZoneArg, hour: int | None = None, dow: int | None = None) -> dict:
    """Uber vs Lyft on a pickup->dropoff zone pair: median rider total, median wait (s), and driver share.

    Zone args take one zone id or a list of ids (a whole area, e.g. every Midtown zone from resolve_zone).

    hour: 0-23 request hour (optional). dow: 0=Mon..6=Sun (optional).
    driver_share = sum(driver_pay) / sum(total). Companies with <20 trips are omitted.
    """
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": _filters(pu_zone_id=pu_zone_id, do_zone_id=do_zone_id, hour=hour, dow=dow)}},
        "aggs": {"by_company": {"terms": {"field": "company", "size": 2, "min_doc_count": MIN_DOCS},
                                "aggs": {"total_p": {"percentiles": {"field": "total", "percents": [50]}},
                                         "wait_p": {"percentiles": {"field": "wait_s", "percents": [50]}},
                                         "pay": {"sum": {"field": "driver_pay"}},
                                         "total": {"sum": {"field": "total"}}}}},
    }
    res = _search(body)
    companies = [{"company": b["key"], "n": b["doc_count"], "p50_total": _pct(b["total_p"], 50),
                  "p50_wait_s": _r(_pct(b["wait_p"], 50), 0),
                  "driver_share": _r(b["pay"]["value"] / b["total"]["value"], 3) if b["total"]["value"] else None}
                 for b in res["aggregations"]["by_company"]["buckets"]]
    return _areas({"n": res["hits"]["total"]["value"], "companies": companies, "query": body},
                  pu=pu_zone_id, do=do_zone_id)


def wait_stats(pu_zone_id: ZoneArg, hour: int | None = None, dow: int | None = None) -> dict:
    """Pickup wait time (seconds from request to pickup) at a zone: p50, p90, and p50 by request hour.

    Zone args take one zone id or a list of ids (a whole area, e.g. every Midtown zone from resolve_zone).

    hour: 0-23 (optional). dow: 0=Mon..6=Sun (optional). Hours with <20 trips are omitted from by_hour.
    """
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": _filters(pu_zone_id=pu_zone_id, hour=hour, dow=dow)}},
        "aggs": {"wait": {"percentiles": {"field": "wait_s", "percents": [50, 90]}},
                 "by_hour": {"terms": {"field": "hour", "size": 24, "min_doc_count": MIN_DOCS, "order": {"_key": "asc"}},
                             "aggs": {"wait": {"percentiles": {"field": "wait_s", "percents": [50]}}}}},
    }
    res = _search(body)
    aggs = res["aggregations"]
    return _areas({
        "n": res["hits"]["total"]["value"],
        "p50_wait_s": _r(_pct(aggs["wait"], 50), 0),
        "p90_wait_s": _r(_pct(aggs["wait"], 90), 0),
        "by_hour": [{"hour": b["key"], "p50_wait_s": _r(_pct(b["wait"], 50), 0)} for b in aggs["by_hour"]["buckets"]],
        "query": body,
    }, pu=pu_zone_id)


def reachable_under_budget(pu_zone_id: ZoneArg, budget_usd: float, hour: int | None = None) -> dict:
    """Destination zones reachable from a pickup zone where the median rider total is within budget_usd.

    Zone args take one zone id or a list of ids (a whole area, e.g. every Midtown zone from resolve_zone).

    hour: 0-23 request hour (optional). Destinations need >=20 trips. Sorted by p50_total ascending,
    with zone centroid lat/lon for mapping.
    """
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": _filters(pu_zone_id=pu_zone_id, hour=hour)}},
        "aggs": {"by_do": {"terms": {"field": "do_zone_id", "size": 300, "min_doc_count": MIN_DOCS},
                           "aggs": {"total": {"percentiles": {"field": "total", "percents": [50]}},
                                    "mins": {"percentiles": {"field": "trip_min", "percents": [50]}}}}},
    }
    res = _search(body)
    zmap = _zones()
    zones = []
    for b in res["aggregations"]["by_do"]["buckets"]:
        p50 = _pct(b["total"], 50)
        if p50 is None or p50 > budget_usd:
            continue
        z = zmap.get(b["key"], {})
        loc = z.get("loc") or {}
        zones.append({"zone_id": b["key"], "zone": z.get("zone"), "borough": z.get("borough"), "p50_total": p50,
                      "p50_min": _r(_pct(b["mins"], 50), 1), "n": b["doc_count"],
                      "lat": loc.get("lat"), "lon": loc.get("lon")})
    zones.sort(key=lambda z: z["p50_total"])
    return _areas({"n": sum(z["n"] for z in zones), "zones": zones, "query": body}, pu=pu_zone_id)


def driver_cut(pu_zone_id: ZoneArg | None = None, group_by: str = "company") -> dict:
    """How much of what riders pay goes to drivers, grouped by company, airport (true/false) or pu_borough.

    Zone args take one zone id or a list of ids (a whole area, e.g. every Midtown zone from resolve_zone).

    pu_zone_id: restrict to one pickup zone (optional). driver_share = sum(driver_pay) / sum(total).
    Groups with <20 trips are omitted.
    """
    if group_by not in ("company", "airport", "pu_borough"):
        raise ValueError("group_by must be one of: company, airport, pu_borough")
    body = {
        "size": 0, "track_total_hits": True,
        "query": {"bool": {"filter": _filters(pu_zone_id=pu_zone_id)}},
        "aggs": {"groups": {"terms": {"field": group_by, "size": 20, "min_doc_count": MIN_DOCS},
                            "aggs": {"pay": {"sum": {"field": "driver_pay"}}, "total": {"sum": {"field": "total"}}}}},
    }
    res = _search(body)
    groups = []
    for b in res["aggregations"]["groups"]["buckets"]:
        rider, pay = b["total"]["value"], b["pay"]["value"]
        groups.append({"key": b.get("key_as_string", b["key"]), "n": b["doc_count"], "rider_total": _r(rider),
                       "driver_pay": _r(pay), "driver_share": _r(pay / rider, 3) if rider else None})
    return _areas({"n": res["hits"]["total"]["value"], "groups": groups, "query": body}, pu=pu_zone_id)
