"""Smoke test for the ML tools. Run: uv run python scripts/smoke_ml.py (after `python -m rides.ml train`)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rides.ml import best_time_to_travel, predict_fare

FRI = 4
MIDTOWN = [161, 162, 163, 164, 230, 100, 186]
UPTOWN = [236, 237, 238, 239, 41, 42, 74, 75, 116, 152, 243, 127]


def q(r):
    return f"${r['low']:.2f} / ${r['mid']:.2f} / ${r['high']:.2f}  wait {r['wait_s']}s"


def check(label, val, lo, hi):
    ok = lo <= val <= hi
    print(f"  [{'ok' if ok else 'WARN'}] {label}: {val} (expected {lo}-{hi})")
    return ok


t0 = time.time()
ok = True
r = predict_fare(132, 80, 18, FRI)
print("JFK -> East Williamsburg Fri 18h:", q(r), r["route"])
r22 = predict_fare(132, 80, 22, FRI)
print("JFK -> East Williamsburg Fri 22h:", q(r22))
for c in r22["company_quotes"]:
    print(f"    {c['company']}: {q(c)}")
ok &= check("JFK->Brooklyn mid", r["mid"], 50, 90)
print(f"  model: {r['model']}")

b = best_time_to_travel(132, 80, FRI, 15, 23)
print("best_time JFK -> East Williamsburg Fri 15-23:")
for h in b["hours"]:
    print(f"    {h['hour']:02d}h {h['company']:4s} {q(h)}")
print(f"  best {b['best']}  worst {b['worst']}  savings ${b['savings_usd']}")

for c in ("Uber", "Lyft"):
    print(f"LaGuardia -> Times Sq Fri 18h {c}:", q(predict_fare(138, 230, 18, FRI, c)))

h = predict_fare(237, 236, 13, 2)  # Upper East Side South -> Upper East Side North, Wed 1pm
print("Short hop UES South -> UES North Wed 13h:", q(h))
ok &= check("short hop mid", h["mid"], 12, 25)

a = predict_fare(MIDTOWN, UPTOWN, 18, FRI)
print(f"Midtown ({len(MIDTOWN)}) -> Uptown ({len(UPTOWN)}) Fri 18h: {q(a)}  n_pairs={a['n_pairs']}")
for p in a["pairs"]:
    print(f"    {p['pu']} -> {p['do']}: ${p['mid']}")
ab = best_time_to_travel(MIDTOWN, UPTOWN, FRI, 15, 23)
print(f"  area best {ab['best']} worst {ab['worst']} savings ${ab['savings_usd']}")

print(f"smoke {'PASS' if ok else 'WARN'} in {time.time() - t0:.2f}s")
