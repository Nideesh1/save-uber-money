/* MOCK DATA for ?mock=1: plausible placeholder values, NOT real TLC numbers. Replays a canned OpenUI Lang program
   statement by statement (same {"kind":"ui","id","line"} shape the API streams) so every component can be checked
   without the backend. */
export type Ev =
  | { kind: "status"; text: string }
  | { kind: "ui"; id: string; line: string }
  | { kind: "error"; text: string }
  | { kind: "done"; meta?: { ms?: number; tools?: string[] } };

const j = (v: unknown) => JSON.stringify(v);
const r2 = (x: number) => Math.round(x * 100) / 100;

const curve = (h: number) => 58 + 14 * Math.sin(((h - 11) / 24) * 2 * Math.PI) + (h >= 16 && h <= 19 ? 9 : 0);
const hours = Array.from({ length: 24 }, (_, h) => {
  const p50 = r2(curve(h));
  return { hour: h, p25: r2(p50 * 0.86), p50, p75: r2(p50 * 1.19), n: 40 + Math.round(60 * Math.abs(Math.sin(h / 3))) };
});
const win = Array.from({ length: 13 }, (_, i) => {
  const hour = i + 10;
  const mid = r2(curve(hour) + 2);
  return { hour, low: r2(mid * 0.78), mid, high: r2(mid * 1.31), wait_s: 180 + ((hour * 37) % 240) };
});
const day = Array.from({ length: 24 }, (_, hour) => {
  const mid = r2(14 + 6 * Math.sin(((hour - 11) / 24) * 2 * Math.PI) + (hour >= 16 && hour <= 19 ? 5 : 0) + (hour >= 7 && hour <= 9 ? 3 : 0));
  return { hour, low: r2(mid * 0.8), mid, high: r2(mid * 1.3), wait_s: 150 + ((hour * 53) % 260) };
});
const zones = [
  [7, "Astoria", 40.7644, -73.9235, 0, 0],
  [145, "Long Island City/Hunters Point", 40.7447, -73.9485, 11.9, 412],
  [226, "Sunnyside", 40.7434, -73.9196, 13.4, 233],
  [112, "Greenpoint", 40.7304, -73.9515, 16.2, 310],
  [255, "Williamsburg (North Side)", 40.7178, -73.9585, 19.1, 288],
  [236, "Upper East Side North", 40.7826, -73.9531, 17.8, 351],
  [75, "East Harlem South", 40.7930, -73.9410, 15.6, 198],
  [161, "Midtown Center", 40.7580, -73.9787, 21.7, 502],
  [230, "Times Sq/Theatre District", 40.7580, -73.9855, 23.9, 377],
  [129, "Jackson Heights", 40.7557, -73.8831, 14.8, 265],
  [138, "LaGuardia Airport", 40.7769, -73.8740, 22.6, 444],
  [92, "Flushing", 40.7580, -73.8303, 24.4, 121],
  [79, "East Village", 40.7265, -73.9815, 24.8, 169],
  [37, "Bushwick South", 40.7004, -73.9220, 23.1, 96],
] as const;
const origin = { zone_id: zones[0][0], zone: zones[0][1], lat: zones[0][2], lon: zones[0][3] };
const reach = zones.slice(1).map(([zone_id, zone, lat, lon, p50_total, n]) => ({ zone_id, zone, lat, lon, p50_total, n }));
const query = { size: 0, query: { bool: { filter: [{ term: { pu_zone_id: 132 } }, { term: { do_zone_id: 255 } }, { term: { dow: 4 } }] } },
  aggs: { by_hour: { terms: { field: "hour", size: 24, min_doc_count: 20 }, aggs: { total: { percentiles: { field: "total", percents: [25, 50, 75] } } } } } };

export const MOCK_PROGRAM: [string, string][] = [
  ["head", `AnswerHeadline(41.2, "usd", "MOCK: median total at 5am", "MOCK DATA. Leaving JFK for Williamsburg around 5am on a Friday is about $27 cheaper than the 6pm peak.")`],
  ["route", `RouteMap("MOCK: Midtown to Uptown", ${j([161, 162, 163, 164, 230, 100, 186])}, ${j([236, 237, 238, 239, 41, 42, 74, 75, 116, 152, 243, 166])}, "Midtown", "Uptown", "$24.80 median", ${j(day)}, 4)`],
  ["quote", `FareQuote(52.4, 61.8, 79.5, 312, "MOCK: JFK to Williamsburg, Fri 6pm", ${j([
    { company: "Uber", low: 54.1, mid: 63.9, high: 82.0, wait_s: 290 },
    { company: "Lyft", low: 50.2, mid: 59.6, high: 76.1, wait_s: 355 },
  ])}, ${j({ mae_usd: 6.8, coverage_80: 0.81, trained_on: 2000000 })})`],
  ["window", `TravelWindow("MOCK: JFK to Williamsburg, Friday 10am to 10pm", ${j(win)}, 13, 18.4)`],
  ["fbh", `FareByHour("MOCK: JFK to Williamsburg, Fridays", ${j(hours)}, 5)`],
  ["vs", `UberVsLyft(${j([
    { company: "Uber", n: 1840, p50_total: 58.3, p50_wait_s: 244, driver_share: 0.71 },
    { company: "Lyft", n: 702, p50_total: 55.9, p50_wait_s: 301, driver_share: 0.74 },
  ])})`],
  ["wait", `WaitMeter(268, 611, "MOCK: LaGuardia pickups, all hours")`],
  ["map", `ZoneMap("MOCK: under $25 from Astoria", ${j(origin)}, ${j(reach)})`],
  ["cut", `DriverCut(${j([
    { key: "airport", n: 5120, rider_total: 352000, driver_pay: 221000, driver_share: 0.628 },
    { key: "non-airport", n: 44880, rider_total: 1130000, driver_pay: 812000, driver_share: 0.719 },
  ])})`],
  ["rank", `RankedList("MOCK: cheapest destinations from Astoria", ${j(reach.slice().sort((a, b) => a.p50_total - b.p50_total).slice(0, 5)
    .map((z) => ({ label: z.zone, value: z.p50_total, unit: "usd", sub: `${z.n} trips` })))})`],
  ["tips", `Tips(${j(["MOCK: request before 6am to save about $17 versus the evening peak.", "MOCK: Lyft median was $2.40 lower on this route.", "MOCK: expect a 10 minute wait on a bad day at LaGuardia."])})`],
  ["note", `Narrative("MOCK DATA for UI validation only. Values are placeholders, not real TLC statistics.")`],
  ["ev", `EvidenceStrip(2542, "NYC TLC High Volume FHV trips, Aug 2026", ${j(query)})`],
];

export async function* mockStream(): AsyncGenerator<Ev> {
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
  for (const t of ["mock mode: no backend", "resolve_zone: JFK Airport, Williamsburg (North Side)", "predict_fare + best_time_to_travel", "fare_by_hour: 24 buckets"]) {
    yield { kind: "status", text: t };
    await sleep(180);
  }
  const ids: string[] = [];
  for (const [id, body] of MOCK_PROGRAM) {
    ids.push(id);
    yield { kind: "ui", id: "root", line: `root = Stack([${ids.join(", ")}])` };
    yield { kind: "ui", id, line: `${id} = ${body}` };
    await sleep(120);
  }
  yield { kind: "done", meta: { ms: 2140, tools: ["resolve_zone", "predict_fare", "best_time_to_travel", "fare_by_hour", "compare_companies", "wait_stats", "reachable_under_budget", "driver_cut"] } };
}
