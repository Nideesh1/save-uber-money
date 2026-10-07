/* OpenUI component library for NYC Rides. The API streams OpenUI Lang statements against these; <Renderer> draws them.
   Positional argument order = zod object key order and must match CONTRACTS.md. */
import { createLibrary, defineComponent } from "@openuidev/react-lang";
import { useMemo, useState, type ReactNode } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { z } from "zod";
import { ChoroplethView, RouteMapView } from "./maps";

export { API } from "./config";
export const SOURCE = "NYC TLC High Volume FHV trips, Aug 2026";

const arr = <T,>(v: T[] | undefined | null): T[] => (Array.isArray(v) ? v.filter((x) => x != null) : []);
const num = (v: unknown): number | null => (typeof v === "number" && isFinite(v) ? v : null);
export const usd = (v: number | null | undefined, d = 2) => (v == null ? "-" : `$${v.toFixed(d)}`);
export const int = (v: number | null | undefined) => (v == null ? "-" : Math.round(v).toLocaleString("en-US"));
export const pct = (v: number | null | undefined) => (v == null ? "-" : `${Math.round(v * 100)}%`);
export const mins = (s: number | null | undefined) =>
  s == null ? "-" : s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
export { hourLabel } from "./config";
import { hourLabel } from "./config";
import { usePick, usePrediction } from "./scrub";

const Card = ({ tag, right, children, className = "" }: { tag?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={"card " + className}>
    {(tag || right) && (
      <div className="card-tag"><span>{tag}</span><i className="rule" />{right != null && <span>{right}</span>}</div>
    )}
    {children}
  </section>
);

/* ---------------------------------------------------------------- layout */

const Stack = defineComponent({
  name: "Stack",
  description: "Root vertical container; children render top to bottom. Always the root.",
  props: z.object({ children: z.array(z.any()) }),
  component: ({ props, renderNode }) => <div className="stack">{renderNode(props.children)}</div>,
});

/* ---------------------------------------------------------------- headline */

const UNITS = ["usd", "min", "sec", "pct", "count", "mi"] as const;
const fmtUnit = (v: number, u: (typeof UNITS)[number]) =>
  u === "usd" ? usd(v) : u === "pct" ? pct(v > 1 ? v / 100 : v) : u === "sec" ? mins(v) : u === "min" ? `${Math.round(v)} min` : u === "mi" ? `${v.toFixed(1)} mi` : int(v);

const AnswerHeadline = defineComponent({
  name: "AnswerHeadline",
  description:
    "The one number that answers the question, shown huge, plus a short label and a one-sentence plain-English answer. " +
    "unit: usd (dollars), min (minutes), sec (seconds, shown as m:ss), pct (0-1 share), count, mi (miles). Always first, exactly once.",
  props: z.object({
    value: z.number(),
    unit: z.enum(UNITS),
    label: z.string().describe("short label, e.g. 'median fare at 5am'"),
    summary: z.string().describe("one sentence answer to the question"),
  }),
  component: ({ props }) => (
    <Card className="headline" tag={props.label}>
      <div className="big">{num(props.value) != null ? fmtUnit(props.value, props.unit ?? "count") : "-"}</div>
      {props.summary && <p className="sum">{props.summary}</p>}
    </Card>
  ),
});

/* ---------------------------------------------------------------- fare by hour */

const HourRow = z.object({ hour: z.number(), p25: z.number(), p50: z.number(), p75: z.number(), n: z.number().nullable().optional() });
type HourRowT = z.infer<typeof HourRow>;

function HourTip({ active, payload }: { active?: boolean; payload?: { payload: HourRowT }[] }) {
  const d = payload?.[0]?.payload;
  if (!active || !d) return null;
  return (
    <div className="tip">
      <b>{hourLabel(d.hour)}</b> median {usd(d.p50)}
      <div>middle half {usd(d.p25)} to {usd(d.p75)}</div>
      {d.n != null && <div className="dim">{int(d.n)} trips</div>}
    </div>
  );
}

const FareByHour = defineComponent({
  name: "FareByHour",
  description:
    "Rider total cost by hour of request (0-23) for one route: shaded band from p25 to p75, line at the median p50. " +
    "Pass hours straight from fare_by_hour. highlight_hour marks the hour to call out (usually cheapest_hour). Use for timing questions.",
  props: z.object({
    title: z.string(),
    hours: z.array(HourRow),
    highlight_hour: z.number().nullable().optional(),
  }),
  component: ({ props }) => {
    const data = useMemo(
      () => arr(props.hours).filter((h) => num(h.p50) != null).sort((a, b) => a.hour - b.hour).map((h) => ({ ...h, band: [h.p25, h.p75] })),
      [props.hours],
    );
    const hi = props.highlight_hour ?? (data.length ? data.reduce((a, b) => (b.p50 < a.p50 ? b : a)).hour : null);
    const hiRow = data.find((d) => d.hour === hi);
    return (
      <Card className="wide" tag="fare by hour of request" right={hiRow ? <span className="accent">{hourLabel(hiRow.hour)} {usd(hiRow.p50)}</span> : undefined}>
        <h3>{props.title}</h3>
        <div className="chart">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="hour" tickFormatter={hourLabel} stroke="var(--dim)" tick={{ fontSize: 11 }} interval={2} />
              <YAxis stroke="var(--dim)" tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v}`} width={48} />
              <Tooltip content={<HourTip />} cursor={{ stroke: "var(--dim)", strokeDasharray: "3 3" }} />
              <Area dataKey="band" stroke="none" fill="var(--taxi)" fillOpacity={0.16} isAnimationActive={false} />
              <Line dataKey="p50" stroke="var(--taxi)" strokeWidth={2} dot={false} isAnimationActive={false} />
              {hiRow && <ReferenceLine x={hiRow.hour} stroke="var(--go)" strokeDasharray="4 3" label={{ value: "cheapest", fill: "var(--go)", fontSize: 11, position: "insideTopRight" }} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="legend"><span className="sw band" />p25 to p75<span className="sw line" />median</div>
      </Card>
    );
  },
});

/* ---------------------------------------------------------------- uber vs lyft */

const CompanyRow = z.object({
  company: z.string(),
  n: z.number().nullable().optional(),
  p50_total: z.number().nullable().optional(),
  p50_wait_s: z.number().nullable().optional(),
  driver_share: z.number().nullable().optional(),
});

const UberVsLyft = defineComponent({
  name: "UberVsLyft",
  description:
    "Side-by-side Uber vs Lyft: median rider total, median wait and driver share, winner per metric highlighted. Pass companies from compare_companies. Use for comparison questions.",
  props: z.object({ companies: z.array(CompanyRow) }),
  component: ({ props }) => {
    const cs = arr(props.companies);
    const best = (k: "p50_total" | "p50_wait_s" | "driver_share", hi = false) => {
      const vals = cs.map((c) => c[k]).filter((v): v is number => num(v) != null);
      return vals.length < 2 ? null : hi ? Math.max(...vals) : Math.min(...vals);
    };
    const bT = best("p50_total"), bW = best("p50_wait_s"), bD = best("driver_share", true);
    return (
      <Card tag="uber vs lyft" right={`${int(cs.reduce((s, c) => s + (c.n ?? 0), 0))} trips`}>
        <div className="vs">
          {cs.map((c) => (
            <div key={c.company} className={"vs-col " + c.company.toLowerCase()}>
              <div className="co">{c.company}</div>
              <div className={"m " + (c.p50_total === bT ? "win" : "")}><label>median total</label><b>{usd(c.p50_total)}</b></div>
              <div className={"m " + (c.p50_wait_s === bW ? "win" : "")}><label>median wait</label><b>{mins(c.p50_wait_s)}</b></div>
              <div className={"m " + (c.driver_share === bD ? "win" : "")}><label>driver share</label><b>{pct(c.driver_share)}</b></div>
              <div className="dim small">{int(c.n)} trips</div>
            </div>
          ))}
        </div>
      </Card>
    );
  },
});

/* ---------------------------------------------------------------- wait meter */

const WaitMeter = defineComponent({
  name: "WaitMeter",
  description: "Pickup wait gauge: typical (p50) and bad-day (p90) wait in seconds from request to pickup. Pass values from wait_stats.",
  props: z.object({ p50_wait_s: z.number(), p90_wait_s: z.number(), label: z.string() }),
  component: ({ props }) => {
    const max = Math.max(900, (props.p90_wait_s ?? 0) * 1.15);
    const w = (v: number) => `${Math.min(100, ((v ?? 0) / max) * 100)}%`;
    return (
      <Card tag="pickup wait" right={props.label}>
        <div className="wait">
          <div><label>typical (p50)</label><b className="accent">{mins(props.p50_wait_s)}</b></div>
          <div><label>bad day (p90)</label><b>{mins(props.p90_wait_s)}</b></div>
        </div>
        <div className="meter">
          <div className="p90" style={{ width: w(props.p90_wait_s) }} />
          <div className="p50" style={{ width: w(props.p50_wait_s) }} />
        </div>
        <div className="meter-ticks"><span>0</span><span>{mins(max / 2)}</span><span>{mins(max)}</span></div>
      </Card>
    );
  },
});

/* ---------------------------------------------------------------- maps */

const RouteMap = defineComponent({
  name: "RouteMap",
  description:
    "Real NYC map of the trip: pickup area (cyan) and dropoff area (yellow) as TLC zone polygons, animated arc between them, price pill mid-arc. " +
    "pu_zone_ids / do_zone_ids = ALL zone ids resolved for each area (from resolve_zone). Labels are short area names. price_label e.g. '$41 median' or '$52 to $79'. " +
    "hours = best_time_to_travel hours (optional, enables the live hour scrubber); dow = day of week 0=Mon..6=Sun.",
  props: z.object({
    title: z.string(),
    pu_zone_ids: z.array(z.number()),
    do_zone_ids: z.array(z.number()),
    pu_label: z.string(),
    do_label: z.string(),
    price_label: z.string().nullable().optional(),
    hours: z.array(z.object({ hour: z.number(), low: z.number(), mid: z.number(), high: z.number(), wait_s: z.number().nullable().optional() })).nullable().optional(),
    dow: z.number().nullable().optional(),
  }),
  component: ({ props }) => (
    <Card tag="route" right={`${arr(props.pu_zone_ids).length} to ${arr(props.do_zone_ids).length} zones`} className="mapcard">
      <h3>{props.title}</h3>
      <RouteMapView puIds={arr(props.pu_zone_ids)} doIds={arr(props.do_zone_ids)} puLabel={props.pu_label ?? ""} doLabel={props.do_label ?? ""} price={props.price_label} hours={props.hours} dow={props.dow} />
      <div className="legend"><span className="sw pu-sw" />pickup<span className="sw do-sw" />dropoff</div>
    </Card>
  ),
});

const ZonePt = z.object({
  zone_id: z.number().nullable().optional(),
  zone: z.string(),
  lat: z.number(),
  lon: z.number(),
  p50_total: z.number().nullable().optional(),
  n: z.number().nullable().optional(),
});

const ZoneMap = defineComponent({
  name: "ZoneMap",
  description:
    "Choropleth map of reachable destination zones from an origin, filled by median rider total (green cheap, red pricey), origin pulsing, hover for names. " +
    "origin = pickup zone {zone, lat, lon, zone_id?}; zones from reachable_under_budget (include zone_id). Use for budget / where-can-I-go questions.",
  props: z.object({
    title: z.string(),
    origin: z.object({ zone: z.string(), lat: z.number(), lon: z.number(), zone_id: z.number().nullable().optional() }).nullable().optional(),
    zones: z.array(ZonePt),
  }),
  component: ({ props }) => {
    const zs = arr(props.zones).filter((x) => num(x.lat) != null && num(x.lon) != null);
    const fares = zs.map((x) => x.p50_total).filter((v): v is number => num(v) != null);
    return (
      <Card tag="reachable zones" right={`${zs.length} zones`} className="mapcard">
        <h3>{props.title}</h3>
        <ChoroplethView origin={props.origin} zones={zs} />
        <div className="legend">
          <span className="ramp" />{usd(fares.length ? Math.min(...fares) : null)} to {usd(fares.length ? Math.max(...fares) : null)} median total<span className="sw origin-sw" />origin
        </div>
      </Card>
    );
  },
});

/* ---------------------------------------------------------------- driver cut */

const DriverCut = defineComponent({
  name: "DriverCut",
  description:
    "How much of what riders pay reaches the driver, per group (company, airport vs not, or pickup borough): stacked bar of driver pay vs the rest. Pass groups from driver_cut.",
  props: z.object({
    groups: z.array(z.object({
      key: z.string(),
      n: z.number().nullable().optional(),
      rider_total: z.number().nullable().optional(),
      driver_pay: z.number().nullable().optional(),
      driver_share: z.number().nullable().optional(),
    })),
  }),
  component: ({ props }) => (
    <Card tag="driver cut" right="share of rider total">
      <ul className="cut">
        {arr(props.groups).map((g, i) => {
          const share = g.driver_share ?? (g.rider_total ? (g.driver_pay ?? 0) / g.rider_total : 0);
          return (
            <li key={i} style={{ animationDelay: `${i * 70}ms` }}>
              <div className="cut-hd"><span>{g.key}</span><b>{pct(share)}</b></div>
              <div className="cut-bar"><div style={{ width: `${Math.min(100, share * 100)}%` }} /></div>
              <div className="dim small">
                {g.rider_total != null && g.driver_pay != null && g.n
                  ? `avg ${usd(g.rider_total / g.n)} paid, ${usd(g.driver_pay / g.n)} to driver`
                  : ""}
                {g.n != null && ` / ${int(g.n)} trips`}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  ),
});


/* ---------------------------------------------------------------- ML: fare quote + travel window */

const Quote = z.object({
  company: z.string(),
  low: z.number().nullable().optional(),
  mid: z.number().nullable().optional(),
  high: z.number().nullable().optional(),
  wait_s: z.number().nullable().optional(),
});
const ModelMeta = z.object({ mae_usd: z.number().nullable().optional(), coverage_80: z.number().nullable().optional(), trained_on: z.any().optional() });

const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const FareQuote = defineComponent({
  name: "FareQuote",
  description:
    "Predicted price for one specific trip (ML model, not history): big mid price, low-high range (80% interval), estimated wait in seconds, " +
    "label like 'JFK to Williamsburg, Fri 6pm'. quotes = company_quotes from predict_fare (Uber vs Lyft). model = the tool's model meta.",
  props: z.object({
    low: z.number(),
    mid: z.number(),
    high: z.number(),
    wait_s: z.number().nullable().optional(),
    label: z.string(),
    quotes: z.array(Quote).nullable().optional(),
    model: ModelMeta.nullable().optional(),
  }),
  component: ({ props: agent }) => {
    // follows the map's hour scrubber: re-quoted by the fare model for the picked hour + day (GET /api/predict)
    const pick = usePick();
    const live = usePrediction(pick);
    const props = live
      ? { ...agent, low: live.low, mid: live.mid, high: live.high, wait_s: live.wait_s ?? agent.wait_s,
          quotes: live.company_quotes ?? agent.quotes,
          label: `${agent.label.split(",")[0]}, ${DAY_NAMES[pick!.dow]} ${hourLabel(pick!.hour)}` }
      : agent;
    const qs = arr(props.quotes);
    const cheapest = qs.length > 1 ? Math.min(...qs.map((q) => q.mid ?? Infinity)) : null;
    const lo = props.low ?? 0, hi = props.high ?? 0, span = hi - lo || 1;
    return (
      <Card className="quote" tag={props.label} right={<span className="badge" title="LightGBM quantile model">ML model{props.model?.mae_usd != null ? ` / MAE ${usd(props.model.mae_usd)}` : ""}</span>}>
        <div className="quote-main">
          <div>
            <label>predicted total</label>
            <div className="big">{usd(props.mid)}</div>
          </div>
          {props.wait_s != null && <div className="quote-wait"><label>est. wait</label><b>{mins(props.wait_s)}</b></div>}
        </div>
        <div className="range">
          <div className="range-bar"><i style={{ left: `${((props.mid - lo) / span) * 100}%` }} /></div>
          <div className="range-ends"><span>{usd(props.low)} low</span><span>80% of trips land here</span><span>{usd(props.high)} high</span></div>
        </div>
        {qs.length > 0 && (
          <div className="quotes">
            {qs.map((q) => (
              <div key={q.company} className={"q " + q.company.toLowerCase() + (q.mid === cheapest ? " win" : "")}>
                <span className="co">{q.company}</span>
                <b>{usd(q.mid)}</b>
                <span className="dim small">{usd(q.low, 0)} to {usd(q.high, 0)}{q.wait_s != null ? ` / ${mins(q.wait_s)} wait` : ""}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    );
  },
});

const WinRow = z.object({ hour: z.number(), low: z.number(), mid: z.number(), high: z.number(), wait_s: z.number().nullable().optional() });
type WinRowT = z.infer<typeof WinRow>;

function WinTip({ active, payload }: { active?: boolean; payload?: { payload: WinRowT }[] }) {
  const d = payload?.[0]?.payload;
  if (!active || !d) return null;
  return (
    <div className="tip">
      <b>{hourLabel(d.hour)}</b> predicted {usd(d.mid)}
      <div>{usd(d.low)} to {usd(d.high)}</div>
      {d.wait_s != null && <div className="dim">~{mins(d.wait_s)} wait</div>}
    </div>
  );
}

const TravelWindow = defineComponent({
  name: "TravelWindow",
  description:
    "Predicted price by departure hour for one trip (ML model): low-high band with mid line, best hour marked, headline 'leave at X, save $Y'. " +
    "Pass hours, best.hour and savings_usd from best_time_to_travel (the UI re-derives best hour and savings from hours, 6am-11pm). Use for 'when should I leave / travel' questions.",
  props: z.object({
    title: z.string(),
    hours: z.array(WinRow),
    best_hour: z.number(),
    savings_usd: z.number().nullable().optional(),
  }),
  component: ({ props }) => {
    // Same window as the RouteMap scrubber (6am-11pm) so the two can never disagree; best + savings derived from the data.
    const data = useMemo(() => {
      const all = arr(props.hours).filter((h) => num(h.mid) != null).sort((a, b) => a.hour - b.hour);
      const day = all.filter((h) => h.hour >= 6 && h.hour <= 23);
      return (day.length >= 3 ? day : all).map((h) => ({ ...h, band: [h.low, h.high] }));
    }, [props.hours]);
    const best = data.length ? data.reduce((a, b) => (b.mid < a.mid ? b : a)) : undefined;
    const savings = data.length ? Math.max(...data.map((d) => d.mid)) - (best?.mid ?? 0) : props.savings_usd ?? null;
    const bestHour = best?.hour ?? props.best_hour;
    return (
      <Card className="wide" tag="when to leave" right={<span className="badge">ML model</span>}>
        <div className="window-hd">
          leave at <span className="accent">{hourLabel(bestHour ?? 0)}</span>
          {savings != null && savings > 0 && <>, save <span className="go">{usd(savings)}</span></>}
        </div>
        <h3>{props.title}</h3>
        <div className="chart">
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="hour" tickFormatter={hourLabel} stroke="var(--dim)" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
              <YAxis stroke="var(--dim)" tick={{ fontSize: 11 }} tickFormatter={(v) => `$${v}`} width={48} />
              <Tooltip content={<WinTip />} cursor={{ stroke: "var(--dim)", strokeDasharray: "3 3" }} />
              <Area dataKey="band" stroke="none" fill="var(--taxi)" fillOpacity={0.14} isAnimationActive={false} />
              <Line dataKey="mid" stroke="var(--taxi)" strokeWidth={2} strokeDasharray="6 3" dot={false} isAnimationActive={false} />
              {best && <ReferenceLine x={best.hour} stroke="var(--go)" strokeDasharray="4 3" label={{ value: `best ${usd(best.mid)}`, fill: "var(--go)", fontSize: 11, position: "insideTopRight" }} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div className="legend"><span className="sw band" />80% range<span className="sw line dashed" />predicted mid</div>
      </Card>
    );
  },
});

/* ---------------------------------------------------------------- ranked list */

const RankedList = defineComponent({
  name: "RankedList",
  description: "Numbered ranking (best first), e.g. cheapest destinations or best hours. Each item: label, value, unit (usd|min|sec|pct|count|mi), optional sub line.",
  props: z.object({
    title: z.string(),
    items: z.array(z.object({ label: z.string(), value: z.number(), unit: z.enum(UNITS), sub: z.string().nullable().optional() })),
  }),
  component: ({ props }) => (
    <Card tag="ranked" right={`${arr(props.items).length}`}>
      <h3>{props.title}</h3>
      <ol className="rank">
        {arr(props.items).map((it, i) => (
          <li key={i} style={{ animationDelay: `${i * 50}ms` }}>
            <span className="no">{String(i + 1).padStart(2, "0")}</span>
            <span className="lab">{it.label}{it.sub && <span className="dim small"> {it.sub}</span>}</span>
            <b>{num(it.value) != null ? fmtUnit(it.value, it.unit ?? "count") : "-"}</b>
          </li>
        ))}
      </ol>
    </Card>
  ),
});

/* ---------------------------------------------------------------- text */

const Tips = defineComponent({
  name: "Tips",
  description: "2-4 short, actionable rider tips grounded in the numbers shown (e.g. 'Request before 6am to save about $14').",
  props: z.object({ items: z.array(z.string()) }),
  component: ({ props }) => (
    <Card tag="tips" className="tips">
      <ul>{arr(props.items).map((t, i) => <li key={i}>{t}</li>)}</ul>
    </Card>
  ),
});

const Narrative = defineComponent({
  name: "Narrative",
  description: "1-3 sentences of plain explanation or caveat. Use sparingly.",
  props: z.object({ text: z.string() }),
  component: ({ props }) => <p className="narrative">{props.text}</p>,
});

function QueryToggle({ query }: { query: unknown }) {
  const [open, setOpen] = useState(false);
  const text = typeof query === "string" ? query : JSON.stringify(query, null, 2);
  return (
    <>
      <button className="linkbtn" onClick={() => setOpen(!open)}>{open ? "hide ES query" : "show ES query"}</button>
      {open && <pre className="query">{text}</pre>}
    </>
  );
}

const EvidenceStrip = defineComponent({
  name: "EvidenceStrip",
  description: `Provenance footer: total trips behind the answer (n), the source ("${SOURCE}") and the main Elasticsearch query body from a tool result. Always last, exactly once.`,
  props: z.object({ n: z.number(), source: z.string(), query: z.any().optional() }),
  component: ({ props }) => (
    <div className="evidence">
      <span><b>{int(props.n)}</b> trips</span>
      <span className="dim">{props.source || SOURCE}</span>
      {props.query != null && props.query !== "" && <QueryToggle query={props.query} />}
    </div>
  ),
});

export const library = createLibrary({
  components: [Stack, AnswerHeadline, RouteMap, FareQuote, TravelWindow, FareByHour, UberVsLyft, WaitMeter, ZoneMap, DriverCut, RankedList, Tips, Narrative, EvidenceStrip],
  root: "Stack",
});

export const searchPrompt = () =>
  library.prompt({
    preamble:
      "You answer questions about NYC Uber and Lyft rides (real NYC TLC High Volume FHV trips, Aug 2026) as generative UI. " +
      "Use the tools to resolve zones and query Elasticsearch, then reply with an OpenUI Lang program only: no prose, no code fences.",
    additionalRules: [
      "Root is always Stack. AnswerHeadline first and exactly once; EvidenceStrip last and exactly once.",
      "Every number must come from a tool result. Never invent, round up or extrapolate data. If a tool returns too few trips, say so with a Narrative.",
      "Choose components that fit the question; 3-6 blocks total. Timing (when is it cheapest) -> FareByHour (+ RankedList of best hours). Budget / where can I go -> ZoneMap (pass zone_id) + RankedList. Uber vs Lyft -> UberVsLyft. Waits -> WaitMeter. Driver pay / fairness -> DriverCut.",
      "Every trip / route answer (any question with a pickup and a dropoff): RouteMap right after AnswerHeadline, with ALL resolved zone ids of each area, a short price_label from the tool results, and (when you called best_time_to_travel) its hours plus the dow.",
      "A specific trip, or 'when should I travel / leave' -> FareQuote (predict_fare) and/or TravelWindow (best_time_to_travel); money first: put them right after AnswerHeadline. Historical stats (what did it cost, patterns) -> FareByHour etc.",
      "FareQuote and TravelWindow show ML predictions; label them as estimates, never as observed history.",
      "Pass tool arrays through as-is (hours, companies, zones, groups); do not hand-edit values.",
      "Money is the rider total (excl tip) in USD. Shares are 0-1. Waits are seconds.",
      `EvidenceStrip: n = trips behind the headline number, source = "${SOURCE}", query = the ES query body from the main tool result.`,
      "Tips are optional, max 4, each tied to a number shown.",
    ],
  });
