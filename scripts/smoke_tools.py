"""Exercise every tool in rides.tools against the live indices and print compact results.

uv run python scripts/smoke_tools.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rides import tools as T


def show(label: str, fn, *a, **kw) -> dict:
    t = time.perf_counter()
    out = fn(*a, **kw)
    ms = (time.perf_counter() - t) * 1000
    assert "n" in out and "query" in out, f"{label}: missing n/query"
    brief = {k: v for k, v in out.items() if k != "query"}
    print(f"\n== {label} ({ms:.0f} ms) ==")
    print(json.dumps(brief, default=str)[:900])
    return out


def main() -> None:
    ids = {}
    for q in ["JFK", "billyburg", "times square", "astoria", "midtown", "uptown"]:
        r = show(f"resolve_zone({q!r})", T.resolve_zone, q)
        ids[q] = r["matches"]

    jfk = ids["JFK"][0]["zone_id"]
    # JFK -> East Williamsburg (80), else the best-populated JFK -> Brooklyn route
    route = T.fare_by_hour(jfk, 80)
    do = 80
    if len(route["hours"]) < 3:
        top = T._search({"size": 0, "query": {"bool": {"filter": [{"term": {"pu_zone_id": jfk}},
                                                                 {"term": {"do_borough": "Brooklyn"}}]}},
                         "aggs": {"d": {"terms": {"field": "do_zone_id", "size": 1}}}})
        do = top["aggregations"]["d"]["buckets"][0]["key"]
        print(f"\n(JFK -> East Williamsburg sparse: {route['n']} trips; using JFK -> zone {do})")
    show(f"fare_by_hour(JFK -> {do})", T.fare_by_hour, jfk, do)
    show(f"compare_companies(JFK -> {do})", T.compare_companies, jfk, do)

    midtown = [m["zone_id"] for m in ids["midtown"]]
    uptown = [m["zone_id"] for m in ids["uptown"]]
    show("fare_by_hour(Midtown zones -> Uptown zones)", T.fare_by_hour, midtown, uptown)
    show("compare_companies(Midtown zones -> Uptown zones)", T.compare_companies, midtown, uptown)

    # dense route so hourly buckets clear the 20-trip floor even at smoke size
    show("fare_by_hour(Midtown zones -> Midtown zones)", T.fare_by_hour, midtown, midtown)

    show("wait_stats(JFK)", T.wait_stats, jfk)
    show("wait_stats(Times Sq, hour=18)", T.wait_stats, ids["times square"][0]["zone_id"], hour=18)
    astoria = next(m["zone_id"] for m in ids["astoria"] if m["zone"] == "Astoria")
    r = show("reachable_under_budget(Astoria, $25)", T.reachable_under_budget, astoria, 25)
    print(f"   -> {len(r['zones'])} zones under $25")
    show("driver_cut(group_by=company)", T.driver_cut, group_by="company")
    show("driver_cut(group_by=airport)", T.driver_cut, group_by="airport")
    show("driver_cut(JFK, group_by=company)", T.driver_cut, jfk, group_by="company")
    show("driver_cut(group_by=pu_borough)", T.driver_cut, group_by="pu_borough")


if __name__ == "__main__":
    main()
