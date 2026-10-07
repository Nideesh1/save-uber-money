# nyc-rides contracts (fixed; all three build tracks code against this)

Ask NYC about Uber/Lyft rides in plain English; a Mistral Large 4 deepagent queries Elasticsearch over real TLC
High Volume FHV trips and streams a generative UI (OpenUI Lang). AgentGlow shows the backend live.

Rules: uv for Python (py>=3.11). No em dashes anywhere. Never print or log secrets (.env holds ELASTIC_ENDPOINT,
ELASTIC_API_KEY, MISTRAL_API_KEY). Synthetic-free: real public TLC data only.

## Ports
API 8420 | UI 5420 | AgentGlow 8103 | Hatchet dashboard 8288 (127.0.0.1) | Hatchet engine 7278 (127.0.0.1)
(AGENTFLEET's stack owns 5320/8320/8102/8188/7178/6390: do not touch it.)

## Models
- Mistral Large 4: `mistral-large-4`, ALWAYS `reasoning_effort="none"` unless the request sets `deep: true`.
  SDK v2 import: `from mistralai.client import Mistral`. LangChain: `init_chat_model("mistralai:mistral-large-4",
  model_kwargs={"reasoning_effort": "none"})` (verified: deepagents tool call + answer in ~2s).
- Embeddings: Elastic inference endpoint `mistral-embeddings` (mistral-embed, 1024d) via `semantic_text`. Exists.

## Data (Python package `rides/`)
Source: `https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_2026-08.parquet` (20.5M rows, ~500MB),
zones: `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv` + `taxi_zones.zip` (shapefile -> centroid lat/lon).
Download to `data/` once. DuckDB cleans + samples. `SAMPLE_ROWS` env (smoke: 50000, full: 2000000).
Keep only HV0003 (Uber) / HV0005 (Lyft); drop fare<=0, trip_miles<=0, trip_time<=0, outliers (fare>500, wait>3600s).

Index `rides`:
company keyword ("Uber"|"Lyft") | pu_zone_id, do_zone_id integer | pu_zone, do_zone text+.kw keyword |
pu_borough, do_borough keyword | request_ts date | hour integer (0-23, of request) | dow integer (0=Mon..6=Sun) |
wait_s integer (pickup-request) | trip_miles float | trip_min float | fare float (base_passenger_fare) |
total float (rider cost excl tip = fare+tolls+bcf+sales_tax+congestion_surcharge+airport_fee+cbd_congestion_fee) |
tips float | driver_pay float | airport boolean (either end is an airport zone) | pu_loc, do_loc geo_point (zone centroid)

Index `zones` (263 rows): zone_id integer | zone text+.kw | borough keyword | service_zone keyword |
name_sem semantic_text (inference_id mistral-embeddings; text = "zone, borough" + common aliases e.g. Williamsburg ->
"Billyburg", JFK -> "Kennedy airport", LaGuardia -> "LGA", Times Sq -> "Midtown theater") | loc geo_point

`rides/es.py`: `client()` -> Elasticsearch from .env.
`rides/etl.py`: `run(sample_rows:int, on_stage=lambda name, frac: None) -> dict` stages: download, clean, sample, zones,
index_zones, index_rides (bulk, report frac). CLI: `uv run python -m rides.etl --rows 50000`. Idempotent (recreate idx).

`rides/tools.py`: plain functions, typed args, docstrings (deepagents uses them as tools). Every return is a JSON-able
dict that includes `n` (trips behind the number) and `query` (the ES request body used, for the EvidenceStrip).
- `resolve_zone(text: str) -> {"matches": [{zone_id, zone, borough, score}], "query"}` hybrid BM25+semantic (rrf)
- `fare_by_hour(pu_zone_id: int, do_zone_id: int, dow: int|None=None, company: str|None=None)`
  -> {route:{pu,do}, n, hours:[{hour,n,p25,p50,p75}], cheapest_hour, priciest_hour, query}   (uses `total`)
- `compare_companies(pu_zone_id: int, do_zone_id: int, hour: int|None=None, dow: int|None=None)`
  -> {n, companies:[{company,n,p50_total,p50_wait_s,driver_share}], query}
- `wait_stats(pu_zone_id: int, hour: int|None=None, dow: int|None=None)` -> {n, p50_wait_s, p90_wait_s, by_hour:[{hour,p50_wait_s}], query}
- `reachable_under_budget(pu_zone_id: int, budget_usd: float, hour: int|None=None)`
  -> {n, zones:[{zone_id,zone,borough,p50_total,p50_min,n,lat,lon}] sorted by p50_total, query}
- `driver_cut(pu_zone_id: int|None=None, group_by: str="company")` group_by in company|airport|pu_borough
  -> {n, groups:[{key,n,rider_total,driver_pay,driver_share}], query}
driver_share = sum(driver_pay)/sum(total). Percentiles via ES `percentiles` agg; min doc count 20 per bucket.

## Backend
- `rides/config.py`: load .env, `setup_tracing(service)` = agentglow.watch() to http://localhost:8103 (env AGENTGLOW_URL).
- `rides/agent.py`: `build_agent(system_prompt: str, deep: bool)` deepagents create_deep_agent on Large 4 with the
  tools above (+ subagents optional). Final assistant message = an OpenUI Lang program ONLY (no prose, no fences).
- `rides/workflows.py` (Hatchet, namespace `nycrides`): `search` task (input {q, system_prompt, deep}) runs the agent and
  streams events with `ctx.put_stream(json)`; `ingest` task wraps etl.run with agentglow.job/stage/progress.
  Worker: `uv run python -m rides.worker`.
- `rides/api.py` FastAPI :8420: `POST /api/search {q, system_prompt, deep?}` -> `text/event-stream`, each `data:` line JSON:
  {"kind":"status","text"} | {"kind":"ui","id","line"} (one OpenUI Lang statement `id = Comp(...)`; same id replaces) |
  {"kind":"error","text"} | {"kind":"done","meta":{ms, tools:[...]}}.
  Triggers the Hatchet `search` run and relays its stream; env `SEARCH_INLINE=1` runs the agent in-process instead.
  `POST /api/ingest {rows}` triggers Hatchet `ingest`. `GET /api/health`.
- docker-compose.yml: hatchet (postgres, migrate, admin token, engine, dashboard; copy AGENTFLEET's pattern),
  agentglow (`agentglow serve` on 8103), worker, api. UI runs via `npm run dev` on 5420.

## UI (`ui/`, Vite + React + TS, `@openuidev/react-lang`, `agentglow` npm)
- `/` Search: search bar at top; posts `{q, system_prompt: library.prompt(...)}`; renders streamed statements with
  `<Renderer library=...>` (same as AGENTFLEET ui/src/pages/Steer.tsx); faint status lines while loading; "deep" toggle.
- `/backend`: full-screen `<AgentScene theme="neural" source="http://localhost:8103" />`.
- Component library (positional args, zod schemas): Stack, AnswerHeadline(value, unit, label, summary),
  FareByHour(title, hours[{hour,p25,p50,p75,n}], highlight_hour?), UberVsLyft(companies[...]), WaitMeter(p50_wait_s, p90_wait_s, label),
  ZoneMap(title, origin{zone,lat,lon}, zones[{zone,lat,lon,p50_total,n}]), DriverCut(groups[...]), RankedList(title, items[{label,value,unit,sub?}]),
  Tips(items: string[]), Narrative(text), EvidenceStrip(n, source, query?).
- Reference implementation to copy: ~/Documents/CODING/AGENTFLEET/ui/src/steer/library.tsx, ui/src/pages/Steer.tsx,
  ui/steer_api.py (SSE + statement parsing).

## ML (`rides/ml.py`, owner: ML track) "money + when to travel"
Train LightGBM on the CLEANED local trips (not Elastic): up to ~2M rows from data/fhvhv_tripdata_2026-08.parquet via
DuckDB, same filters as ETL. Features known BEFORE a trip: pu lat/lon, do lat/lon, haversine km, pu/do borough,
airport flag, hour, dow, company. Targets: `total` (quantile models alpha 0.1/0.5/0.9) and `wait_s` (p50).
Artifacts: `models/fare_p10.txt`, `fare_p50.txt`, `fare_p90.txt`, `wait_p50.txt`, `models/meta.json` (metrics: MAE,
pinball loss, interval coverage on a holdout, train rows, trained_at). CLI: `uv run python -m rides.ml train --rows 2000000`.
Tools (plain functions, docstrings, JSON dicts, include `model` meta {mae_usd, coverage_80, trained_on}):
- `predict_fare(pu_zone_id: int, do_zone_id: int, hour: int, dow: int, company: str|None=None)`
  -> {low, mid, high, wait_s, company_quotes:[{company, low, mid, high, wait_s}], model}
- `best_time_to_travel(pu_zone_id: int, do_zone_id: int, dow: int, earliest_hour: int=0, latest_hour: int=23)`
  -> {hours:[{hour, low, mid, high, wait_s}], best:{hour, company, mid}, worst:{hour, mid}, savings_usd, model}
Backend: register both as agent tools. UI components: `FareQuote(low, mid, high, wait_s, label, quotes?)` (predicted
range card, "model" badge with MAE) and `TravelWindow(title, hours[{hour,low,mid,high,wait_s}], best_hour, savings_usd)`
(predicted price curve with band, best hour marked, "leave at X, save $Y"). Prompt rules: questions about a specific
trip or "when should I travel/leave" -> FareQuote and/or TravelWindow; historical stats -> FareByHour etc.

## Areas (update): every `pu_zone_id` / `do_zone_id` arg in tools.py AND ml.py accepts `int | list[int]`
- Elastic tools: a list becomes a `terms` filter (the answer covers the whole area, e.g. Midtown -> Uptown = all pairs).
  Add `areas: {pu: [zone names], do: [zone names]}` to the returned dict when a list was passed.
- ML tools: predict every pu x do pair, combine with trip-count weights from data/ (or zone pickup counts saved at train
  time in models/zones.json); return the combined low/mid/high plus `pairs: [{pu, do, mid}]` (top 5 cheapest +
  priciest) so the UI can show the spread. best_time_to_travel uses the weighted mid per hour.
- resolve_zone returns up to 8 matches with `trips`; the agent passes ALL zones of an area (not a representative).

## Map (update)
- `ui/public/zones.geojson`: TLC zone polygons in lat/lon (EPSG:4326), simplified (<1.5MB), properties {zone_id, zone, borough}.
  Built by `scripts/zones_geojson.py` from data/taxi_zones.zip (pyshp + pyproj, already deps; shapely simplify ok).
- Basemap: maplibre-gl with keyless CARTO dark-matter style (https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json).
- `RouteMap(title, pu_zone_ids: int[], do_zone_ids: int[], pu_label, do_label, price_label?)`: fit bounds to both areas,
  fill pickup area (cyan) and dropoff area (taxi yellow) with glow outlines, animated glowing arc between area centroids,
  floating price pill mid-arc. Use for EVERY trip/route answer, right after AnswerHeadline.
- `ZoneMap` upgraded to the same map: choropleth of reachable zones by p50_total, origin highlighted.
- Agent: pass the zone ids it resolved (all zones of an area) to RouteMap.

## Addresses (done, in tools.py)
- zones index has `shape` geo_shape (TLC polygons, lon/lat). ETL writes it on rebuild.
- `geocode_address(address: str)` -> {address, lat, lon, zone_id, zone, borough, n, query}: NYC GeoSearch (free, no key)
  for the point, then Elastic `geo_shape` intersects for the exact zone. Use for street addresses / specific places;
  resolve_zone for neighborhoods / areas / nicknames.

## Map time scrubber (update)
- RouteMap gains optional 7th arg `hours: [{hour, low, mid, high, wait_s}]` (from best_time_to_travel) and 8th
  `dow: int`. UI: hour slider (6am-11pm default, full 24h toggle) + day pills (Mon..Sun) on the map; price pill, arc
  color (green cheap -> red pricey), arc glow and wait update live; play button animates through the day.
- Day change (or missing hours) -> `GET /api/quote?pu=1,2&do=3,4&dow=4` (no LLM, ML only, ~0.3s) returning
  best_time_to_travel(pu, do, dow, 0, 23) JSON. Backend owns the endpoint.
