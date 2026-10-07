# save-uber-money

Every Uber and Lyft ride in NYC is public. We put them in Elasticsearch, trained a fare model on 16.7 million of
them, and gave Mistral a search bar, so you know the price before you tap, and when it's cheaper.

Ask in plain English:

- "When should I leave JFK for Williamsburg on Friday to save money?"
- "I wanna get from midtown to uptown"
- "From 350 5th Ave to Columbia University, when should I leave tonight?"
- "I have $25, where can I go from Astoria?"
- "Is Lyft cheaper than Uber from LaGuardia?"

The answer is a page generated on the fly: a live NYC route map with an hour-by-hour price scrubber, a predicted
fare range (Uber vs Lyft), the best time to leave and how much you save, plus the historical trips behind it.

Built at the Elastic x Mistral NYC Hack Night (Oct 7, 2026).

## How it works

```
search bar ──> FastAPI (SSE) ──> Hatchet `search` run ──> deepagents
                                                           │
                         Mistral Large 4 (planner) ────────┤ picks tools, reads the numbers
                                                           │
     Elasticsearch: hybrid zone search, geo_shape lookup, ─┤
     percentile / terms aggregations over 2M trips          │
     LightGBM fare + wait model (16.7M trips) ─────────────┘
                                                           │
                         Mistral Medium (renderer) ────────> OpenUI Lang, streamed line by line into React
```

- **Data:** NYC TLC High Volume For-Hire Vehicle trips (Uber + Lyft), August 2026: 20.5M trips, 19.3M after
  cleaning. Each trip has pickup/dropoff zone, request and pickup time, miles, minutes, fare, fees, tip and driver pay.
- **Elasticsearch (Serverless):**
  - `rides`: 2M sampled trips (keyword, numeric, date, geo_point) for percentile, terms and date aggregations.
  - `zones`: the 263 TLC zones with aliases ("billyburg", "uptown"), `semantic_text` on Mistral embeddings
    (`mistral-embed` inference endpoint), centroids as `geo_point` and polygons as `geo_shape`.
  - Place lookup is hybrid search (BM25 + semantic, RRF). Street addresses go through NYC GeoSearch to a point,
    then a `geo_shape` intersects query finds the exact zone.
- **ML:** LightGBM quantile models (p10 / p50 / p90 rider cost) and a wait-time model, using only what you know
  before booking (zones, distance, boroughs, airport, hour, day, company). Trained on 16.7M trips, tested on the
  last 4 days of August (2.6M trips it never saw): typical error $6.00 (vs $15.55 for a naive guess), 78.9% of real
  fares inside the predicted range.
- **Agents:** deepagents on Mistral Large 4 (thinking off for speed, "Think harder" turns it on) plans and calls the
  tools; Mistral Medium writes the generative UI (OpenUI Lang) from the tool results. Every number on screen comes
  from a tool result, and each answer shows the trip count and the Elasticsearch query behind it.
- **Backend:** FastAPI streams Server-Sent Events; each search is a Hatchet run. The whole pipeline (Hatchet job,
  planner, tools, Elasticsearch, Mistral, the fare model) is traced to [AgentGlow](https://github.com/Nideesh1/agentglow)
  and shown live on `/backend`.

## Run it

Needs Docker, [uv](https://docs.astral.sh/uv/), Node 20+, an Elasticsearch Serverless project and a Mistral API key.

```bash
cp .env.example .env          # ELASTIC_ENDPOINT, ELASTIC_API_KEY, MISTRAL_API_KEY

# one-time: create the Mistral embedding endpoint in Elasticsearch (Kibana Dev Tools)
#   PUT _inference/text_embedding/mistral-embeddings
#   {"service": "mistral", "service_settings": {"api_key": "<MISTRAL_API_KEY>", "model": "mistral-embed"}}

uv sync
uv run python -m rides.etl --rows 2000000      # download Aug 2026 trips, clean, index zones + 2M rides (~6 min)
uv run python -m rides.ml train --rows 25000000 # optional: models/ already ships trained (~13 min)

docker compose up -d --build                   # Hatchet, AgentGlow, worker, API
cd ui && npm install && npm run dev            # http://localhost:5420  (/backend = live AgentGlow scene)
```

| Service | URL |
|---|---|
| Search UI | http://localhost:5420 |
| Live backend scene | http://localhost:5420/backend |
| API | http://localhost:8420 |
| Hatchet dashboard | http://localhost:8288 |
| AgentGlow | http://localhost:8103 |

Smoke tests: `uv run python scripts/smoke_tools.py`, `scripts/smoke_ml.py`, `scripts/smoke_search.py`.

## Layout

```
rides/etl.py        download, clean (DuckDB), zones + polygons, bulk index
rides/tools.py      Elasticsearch tools: resolve_zone, geocode_address, fare_by_hour, compare_companies,
                    wait_stats, reachable_under_budget, driver_cut
rides/ml.py         LightGBM training + predict_fare, best_time_to_travel
rides/agent.py      planner (Large 4) + renderer (Medium), OpenUI statement streaming
rides/workflows.py  Hatchet `search` and `ingest` tasks
rides/api.py        FastAPI: /api/search (SSE), /api/quote (ML only), /api/health
ui/                 Vite + React, OpenUI component library, MapLibre route map, AgentGlow scene
```

## Data credit

Trip records: NYC Taxi & Limousine Commission, High Volume FHV Trip Records. Zone lookup and shapefile: NYC TLC.
Geocoding: NYC GeoSearch (NYC Planning Labs). Basemap: CARTO dark matter, OpenStreetMap contributors.

## License

MIT
