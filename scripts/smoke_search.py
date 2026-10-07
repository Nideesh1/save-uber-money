"""Smoke test: POST /api/search and print the streamed events.

  uv run python scripts/smoke_search.py                 # all smoke queries against http://localhost:8420
  uv run python scripts/smoke_search.py "your question"  # one query
  API=http://localhost:8420 DEEP=1 uv run python scripts/smoke_search.py
"""
import json
import os
import sys
import time

import httpx

API = os.environ.get("API", "http://localhost:8420")
QUERIES = [
    "cheapest time to get from JFK to Williamsburg on Friday",
    "When should I leave JFK for Williamsburg on Friday evening to save money?",
    "I wanna get from midtown to uptown",
    "from 350 5th Ave to Columbia University, when should I leave tonight?",
]

# Minimal stand-in for the UI's library.prompt(): same syntax-rules style; the UI sends the real one.
SYSTEM_PROMPT = """## OpenUI Lang
Write a program of statements, one per line: `identifier = Component(arg1, arg2, ...)`.
Syntax rules:
- The FIRST statement is `root = Stack([...])` listing the ids of the blocks in display order.
- Arguments are POSITIONAL, in the order shown in the signatures below. Optional args may be omitted from the end.
- Values: strings in double quotes, numbers, true/false, null, arrays [..], objects {key: value}.
- Reference other statements by their identifier. Never wrap the program in code fences. No prose.

## Components
Stack(children: ref[])
AnswerHeadline(value: number, unit: string, label: string, summary: string)
FareByHour(title: string, hours: {hour, p25, p50, p75, n}[], highlight_hour?: number)
UberVsLyft(companies: {company, n, p50_total, p50_wait_s, driver_share}[])
WaitMeter(p50_wait_s: number, p90_wait_s: number, label: string)
ZoneMap(title: string, origin: {zone, lat, lon}, zones: {zone, lat, lon, p50_total, n}[])
DriverCut(groups: {key, n, rider_total, driver_pay, driver_share}[])
RouteMap(title: string, pu_zone_ids: number[], do_zone_ids: number[], pu_label: string, do_label: string, price_label?: string, hours?: {hour, low, mid, high, wait_s}[], dow?: number)
RankedList(title: string, items: {label, value, unit, sub?}[])
FareQuote(low: number, mid: number, high: number, wait_s: number, label: string, quotes?: {company, low, mid, high, wait_s}[])
TravelWindow(title: string, hours: {hour, low, mid, high, wait_s}[], best_hour: number, savings_usd: number)
Tips(items: string[])
Narrative(text: string)
EvidenceStrip(n: number, source: string, query?: string)
"""


def run(q: str) -> dict:
    print(f"\n=== {q}")
    t0, ui, errors, done = time.monotonic(), {}, [], None
    with httpx.stream("POST", f"{API}/api/search", json={"q": q, "system_prompt": SYSTEM_PROMPT,
                                                          "deep": os.environ.get("DEEP") == "1"}, timeout=300) as r:
        r.raise_for_status()
        for raw in r.iter_lines():
            if not raw.startswith("data:"):
                continue
            ev = json.loads(raw[5:].strip())
            dt = time.monotonic() - t0
            if ev["kind"] == "status":
                print(f"  [{dt:5.1f}s] status  {ev['text']}")
            elif ev["kind"] == "ui":
                ui[ev["id"]] = ev["line"]
                line = ev["line"] if len(ev["line"]) <= 160 else ev["line"][:157] + "..."
                print(f"  [{dt:5.1f}s] ui      {line}")
            elif ev["kind"] == "error":
                errors.append(ev["text"])
                print(f"  [{dt:5.1f}s] ERROR   {ev['text']}")
            elif ev["kind"] == "done":
                done = ev["meta"]
                print(f"  [{dt:5.1f}s] done    {json.dumps(done)}")
    ok = bool(ui) and not errors and done is not None and any("EvidenceStrip(" in v for v in ui.values())
    print(f"  -> {'OK' if ok else 'FAIL'}: {len(ui)} statements, evidence={'yes' if ok else 'check'}")
    return {"ok": ok}


if __name__ == "__main__":
    qs = sys.argv[1:] or QUERIES
    results = [run(q) for q in qs]
    sys.exit(0 if all(r["ok"] for r in results) else 1)
