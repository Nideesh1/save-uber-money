# 3-minute demo

Setup: two windows side by side. Left: http://localhost:5420 (search). Right: http://localhost:5420/backend
(AgentGlow). Kibana open in a third tab on the `rides` index (Discover) for the Elastic beat.

**0:00 Hook (15s)**
"Every Uber and Lyft ride in NYC is public: 20 million in August alone, with the fare, the tip and what the driver
got. We built a search bar that tells you what your ride will cost and when it's cheaper."

**0:15 Search 1: when to leave (45s)**
Type: "When should I leave JFK for Williamsburg on Friday to save money?"
- Right window: the Hatchet job, the Large 4 planner, tool calls, Elasticsearch and the fare model light up.
- Left: the map flies to JFK and Williamsburg, the price pill, "leave at 11pm, save about $20".
- Drag the hour scrubber: price and arc color change hour by hour. Click Fri -> Sun.

**1:00 Search 2: areas + addresses (30s)**
Type: "I wanna get from midtown to uptown"
- "Midtown is 13 zones, Uptown is 22": hybrid search (BM25 + Mistral embeddings) resolves areas and slang.
- Optional: "from 350 5th Ave to Columbia University": NYC GeoSearch point -> Elasticsearch geo_shape -> exact zone.

**1:30 Elastic beat (30s)**
Open the EvidenceStrip: the exact query (percentile + terms aggregations over 2M trips). Kibana Discover on `rides`.
Mention the `zones` index: `semantic_text` on the `mistral-embeddings` inference endpoint, `geo_shape` polygons.

**2:00 Mistral beat (30s)**
"Large 4 plans and calls the tools with thinking off for speed (flip 'Think harder' for hard questions); Mistral
Medium writes the UI as OpenUI Lang, streamed line by line, so every chart you see was generated for this question.
Every number comes from a tool result."

**2:30 ML + close (30s)**
"The price range comes from a LightGBM model trained on 16.7M trips: typical error $6, versus $15.50 for a naive
guess. Fun fact from the data: the same Friday-morning LaGuardia trip cost $101 on Uber and $61 on Lyft."
"Repo: github.com/Nideesh1/save-uber-money."

Backup searches: "I have $25, where can I go from Astoria?" (budget map), "Is Lyft cheaper than Uber from
LaGuardia?", "Do drivers get screwed on airport runs?" (airport trips: drivers keep about 56% vs 67%).
