/* MapLibre maps over TLC zone polygons (public/zones.geojson) on the keyless CARTO dark-matter basemap. */
import { Map as MLMap, setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { setPick } from "./scrub";
import { API, hourLabel as hl } from "./config";

setWorkerUrl(workerUrl);

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const CYAN = "#22d3ee";
const TAXI = "#f7c600";
const NYC: [number, number] = [-73.94, 40.71];

type Pos = [number, number];
type Geom = { type: "Polygon"; coordinates: Pos[][] } | { type: "MultiPolygon"; coordinates: Pos[][][] };
export type ZoneFeature = { type: "Feature"; id: number; properties: { zone_id: number; zone: string; borough: string }; geometry: Geom };
type FC = { type: "FeatureCollection"; features: ZoneFeature[] };

let zonesP: Promise<FC> | null = null;
export const loadZones = (): Promise<FC> =>
  (zonesP ??= fetch(`${import.meta.env.BASE_URL}zones.geojson`).then((r) => {
    if (!r.ok) throw new Error(`zones.geojson HTTP ${r.status}`);
    return r.json();
  }));

const rings = (g: Geom): Pos[] => (g.type === "Polygon" ? g.coordinates[0] : g.coordinates.flatMap((p) => p[0]));

function ringCentroid(r: Pos[]): [Pos, number] {
  let cx = 0, cy = 0, a = 0;
  for (let i = 0; i < r.length - 1; i++) {
    const f = r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1];
    cx += (r[i][0] + r[i + 1][0]) * f;
    cy += (r[i][1] + r[i + 1][1]) * f;
    a += f;
  }
  return a === 0 ? [r[0], 0] : [[cx / (3 * a), cy / (3 * a)], Math.abs(a / 2)];
}
/** Area-weighted centroid of a set of zones (outer rings only). */
export function centroidOf(fs: ZoneFeature[]): Pos | null {
  let x = 0, y = 0, w = 0;
  for (const f of fs) {
    const outers = f.geometry.type === "Polygon" ? [f.geometry.coordinates[0]] : f.geometry.coordinates.map((p) => p[0]);
    for (const r of outers) {
      const [c, a] = ringCentroid(r);
      x += c[0] * a; y += c[1] * a; w += a;
    }
  }
  return w ? [x / w, y / w] : null;
}
export function boundsOf(fs: ZoneFeature[], extra: Pos[] = []): [Pos, Pos] | null {
  const pts = [...fs.flatMap((f) => rings(f.geometry)), ...extra];
  if (!pts.length) return null;
  let [w, s, e, n] = [180, 90, -180, -90];
  for (const [lo, la] of pts) { w = Math.min(w, lo); e = Math.max(e, lo); s = Math.min(s, la); n = Math.max(n, la); }
  return [[w, s], [e, n]];
}

/** Quadratic bezier arc between two points, bowed to the left of travel. */
function arc(a: Pos, b: Pos, steps = 96): Pos[] {
  const k = Math.cos((40.73 * Math.PI) / 180);
  const dx = (b[0] - a[0]) * k, dy = b[1] - a[1];
  const len = Math.hypot(dx, dy) || 1e-6;
  const bow = 0.38 * len;
  const c: Pos = [(a[0] + b[0]) / 2 + (-dy / len) * bow / k, (a[1] + b[1]) / 2 + (dx / len) * bow];
  return Array.from({ length: steps + 1 }, (_, i) => {
    const t = i / steps, u = 1 - t;
    return [u * u * a[0] + 2 * u * t * c[0] + t * t * b[0], u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]] as Pos;
  });
}

const line = (coords: Pos[]) => ({ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: coords } });
const point = (p: Pos) => ({ type: "Feature" as const, properties: {}, geometry: { type: "Point" as const, coordinates: p } });
const fc = (features: unknown[]) => ({ type: "FeatureCollection" as const, features }) as never;

function newMap(el: HTMLDivElement) {
  const m = new MLMap({
    container: el,
    style: STYLE,
    center: NYC,
    zoom: 9.2,
    pitch: 0,
    attributionControl: { compact: true },
    cooperativeGestures: true,
    fadeDuration: 0,
  });
  // start the compact attribution collapsed ("i" button) instead of expanded
  m.once("idle", () => el.querySelector(".maplibregl-compact-show")?.classList.remove("maplibregl-compact-show"));
  return m;
}

const merc = ([lon, lat]: Pos): Pos => {
  const r = (lat * Math.PI) / 180;
  return [(lon + 180) / 360, (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2];
};
const unmerc = ([x, y]: Pos): Pos => [x * 360 - 180, (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI];

/** Tight camera for points in a ROTATED frame (fitBounds only fits axis-aligned lon/lat boxes). */
function rotatedCamera(pts: Pos[], bearing: number, w: number, h: number, pad: { top: number; bottom: number; left: number; right: number }, maxZoom: number) {
  const b = (bearing * Math.PI) / 180;
  const r: Pos = [Math.cos(b), Math.sin(b)], u: Pos = [Math.sin(b), -Math.cos(b)];
  const ms = pts.map(merc);
  let [x0, x1, y0, y1] = [Infinity, -Infinity, Infinity, -Infinity];
  for (const [x, y] of ms) {
    const sx = x * r[0] + y * r[1], sy = x * u[0] + y * u[1];
    x0 = Math.min(x0, sx); x1 = Math.max(x1, sx); y0 = Math.min(y0, sy); y1 = Math.max(y1, sy);
  }
  const aw = Math.max(50, w - pad.left - pad.right), ah = Math.max(50, h - pad.top - pad.bottom);
  const zoom = Math.min(maxZoom, Math.log2(Math.min(aw / ((x1 - x0) * 512 || 1e-9), ah / ((y1 - y0) * 512 || 1e-9))));
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const center = unmerc([mx * r[0] + my * u[0], mx * r[1] + my * u[1]]);
  return { center, zoom, bearing, padding: pad };
}

/** Fit now (animated), then re-fit whenever the container resizes (streaming layout shifts) until the user moves the map. */
function autoFit(m: MLMap, fit: (duration: number) => void) {
  let user = false, t = 0;
  m.on("movestart", (e) => { if ((e as { originalEvent?: unknown }).originalEvent) user = true; });
  m.on("resize", () => {
    if (user) return;
    clearTimeout(t);
    t = window.setTimeout(() => fit(700), 150);
  });
  try { fit(2600); } catch (e) { console.warn("fit", e); }
}

/** Overlay elements pinned to lng/lat; repositioned on every render frame. */
function useOverlay() {
  const [, force] = useState(0);
  return () => force((n) => n + 1);
}

/* ---------------------------------------------------------------- RouteMap */

export type HourQuote = { hour: number; low: number; mid: number; high: number; wait_s?: number | null };
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const mmss = (s?: number | null) => (s == null ? "" : s < 60 ? `${Math.round(s)}s` : `${Math.round(s / 60)} min`);
const hex = (c: string) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
function mix(a: string, b: string, t: number) {
  const [x, y] = [hex(a), hex(b)];
  return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
}
/** 0 = cheapest (cyan accent) .. 1 = priciest (taxi yellow): the one cheap-to-pricey scale. */
export const priceColor = (t: number) => mix(CYAN, TAXI, Math.max(0, Math.min(1, t)));

/** Hour quotes for a route + day: props first, else GET /api/quote (ML only). 404 -> null + note. */
function useQuotes(puIds: number[], doIds: number[], dow: number, given?: HourQuote[] | null, givenDow?: number | null) {
  const [state, setState] = useState<{ key: string; hours: HourQuote[] | null; note?: string }>({ key: "", hours: null });
  const useGiven = !!given?.length && (givenDow == null || givenDow === dow);
  const key = `${puIds.join(",")}|${doIds.join(",")}|${dow}`;
  useEffect(() => {
    if (useGiven || !puIds.length || !doIds.length) return;
    let dead = false;
    fetch(`${API}/api/quote?pu=${puIds.join(",")}&do=${doIds.join(",")}&dow=${dow}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 404 ? "live quotes not available yet" : `quote HTTP ${r.status}`);
        const j = await r.json();
        if (!dead) setState({ key, hours: Array.isArray(j?.hours) ? j.hours : null });
      })
      .catch((e) => !dead && setState({ key, hours: null, note: e instanceof Error ? e.message : String(e) }));
    return () => { dead = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, useGiven]);
  if (useGiven) return { hours: given!, loading: false, note: undefined };
  return { hours: state.key === key ? state.hours : null, loading: state.key !== key, note: state.key === key ? state.note : undefined };
}

export function RouteMapView(p: { puIds: number[]; doIds: number[]; puLabel: string; doLabel: string; price?: string | null; hours?: HourQuote[] | null; dow?: number | null }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const [pins, setPins] = useState<{ pu?: Pos; do?: Pos; mid?: Pos }>({});
  const [err, setErr] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const rerender = useOverlay();
  const [dow, setDow] = useState<number>(p.dow ?? (new Date().getDay() + 6) % 7);
  const [full, setFull] = useState(false);
  const [hour, setHour] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const q = useQuotes(p.puIds, p.doIds, dow, p.hours, p.dow);
  const [h0, h1] = full ? [0, 23] : [6, 23];
  const rows = useMemo(() => (q.hours ?? []).filter((r) => r.hour >= h0 && r.hour <= h1 && typeof r.mid === "number").sort((a, b) => a.hour - b.hour), [q.hours, h0, h1]);
  const best = rows.length ? rows.reduce((a, b) => (b.mid < a.mid ? b : a)) : null;
  const [lo, hi] = rows.length ? [Math.min(...rows.map((r) => r.mid)), Math.max(...rows.map((r) => r.mid))] : [0, 1];
  const cur = rows.find((r) => r.hour === hour) ?? best;
  const tNorm = (v: number) => (hi === lo ? 0.5 : (v - lo) / (hi - lo));
  const curT = cur ? tNorm(cur.mid) : null;
  useEffect(() => { if (hour == null || !rows.some((r) => r.hour === hour)) setHour(best?.hour ?? null); }, [rows]); // eslint-disable-line react-hooks/exhaustive-deps
  const curHour = cur?.hour ?? null;
  useEffect(() => { // the FareQuote card follows the scrubber (lib/scrub.ts)
    if (curHour != null) setPick({ puIds: p.puIds, doIds: p.doIds, hour: curHour, dow });
  }, [curHour, dow, p.puIds.join(","), p.doIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => setPick(null), []);
  useEffect(() => {
    if (!playing || !rows.length) return;
    const id = setInterval(() => {
      setHour((h) => {
        const i = rows.findIndex((r) => r.hour === h);
        if (i >= rows.length - 1) { setPlaying(false); return h; }
        return rows[i + 1].hour;
      });
    }, 420);
    return () => clearInterval(id);
  }, [playing, rows]);
  const key = `${p.puIds.join(",")}|${p.doIds.join(",")}`;

  useEffect(() => {
    if (!el.current) return;
    let raf = 0, dead = false;
    const m = newMap(el.current);
    map.current = m;
    m.on("move", rerender);
    m.on("error", (e) => console.debug("maplibre", e?.error?.message ?? e));
    Promise.all([loadZones(), new Promise<void>((r) => (m.loaded() ? r() : m.once("load", () => r())))])
      .then(([zones]: [FC, void]) => {
        if (dead) return;
        const pu = new Set(p.puIds), dz = new Set(p.doIds);
        const pf = zones.features.filter((f) => pu.has(f.properties.zone_id));
        const df = zones.features.filter((f) => dz.has(f.properties.zone_id));
        const a = centroidOf(pf), b = centroidOf(df);
        const feats = [
          ...pf.map((f) => ({ ...f, properties: { ...f.properties, role: "pu" } })),
          ...df.map((f) => ({ ...f, properties: { ...f.properties, role: dz.has(f.properties.zone_id) && pu.has(f.properties.zone_id) ? "both" : "do" } })),
        ];
        const color = ["match", ["get", "role"], "pu", CYAN, TAXI] as never;
        m.addSource("areas", { type: "geojson", data: fc(feats) });
        m.addLayer({ id: "area-fill", type: "fill", source: "areas", paint: { "fill-color": color, "fill-opacity": 0 } });
        m.addLayer({ id: "area-glow", type: "line", source: "areas", paint: { "line-color": color, "line-width": 9, "line-blur": 7, "line-opacity": 0 } });
        m.addLayer({ id: "area-line", type: "line", source: "areas", paint: { "line-color": color, "line-width": 1.4, "line-opacity": 0 } });
        for (const [id, prop, v] of [["area-fill", "fill-opacity", 0.32], ["area-glow", "line-opacity", 0.55], ["area-line", "line-opacity", 0.95]] as const) {
          m.setPaintProperty(id, `${prop}-transition` as never, { duration: 1400, delay: 500 } as never);
          m.setPaintProperty(id, prop as never, v as never);
        }
        if (a && b) {
          const path = arc(a, b);
          m.addSource("arc", { type: "geojson", data: line(path) as never });
          m.addSource("trail", { type: "geojson", data: line(path.slice(0, 2)) as never });
          m.addSource("head", { type: "geojson", data: point(a) as never });
          m.addLayer({ id: "arc-glow", type: "line", source: "arc", layout: { "line-cap": "round" }, paint: { "line-color": "#fff6c8", "line-width": 12, "line-blur": 10, "line-opacity": 0.18 } });
          m.addLayer({ id: "arc-base", type: "line", source: "arc", layout: { "line-cap": "round" }, paint: { "line-color": "#fff", "line-width": 1.6, "line-opacity": 0.45, "line-dasharray": [1, 2.5] } });
          m.addLayer({ id: "trail-glow", type: "line", source: "trail", layout: { "line-cap": "round" }, paint: { "line-color": TAXI, "line-width": 10, "line-blur": 6, "line-opacity": 0.6 } });
          m.addLayer({ id: "trail", type: "line", source: "trail", layout: { "line-cap": "round" }, paint: { "line-color": "#fff3b0", "line-width": 3 } });
          m.addLayer({ id: "head-glow", type: "circle", source: "head", paint: { "circle-radius": 16, "circle-color": TAXI, "circle-blur": 1, "circle-opacity": 0.9 } });
          m.addLayer({ id: "head", type: "circle", source: "head", paint: { "circle-radius": 4.5, "circle-color": "#fff" } });
          const trail = m.getSource("trail") as unknown as { setData: (d: unknown) => void };
          const head = m.getSource("head") as unknown as { setData: (d: unknown) => void };
          const t0 = performance.now(), period = 2600, N = path.length - 1;
          const tick = (now: number) => {
            const t = (((now - t0) % period) / period);
            const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
            const i = Math.max(1, Math.round(e * N));
            trail.setData(line(path.slice(Math.max(0, i - Math.round(N * 0.3)), i + 1)));
            head.setData(point(path[i]));
            raf = requestAnimationFrame(tick);
          };
          raf = requestAnimationFrame(tick);
          setPins({ pu: a, do: b, mid: path[Math.floor(path.length / 2)] });
        }
        const bb = boundsOf([...pf, ...df]);
        if (bb) {
          // tight fit to the union of both areas; bottom padding clears the scrubber panel
          const box = el.current;
          // rotate so the trip runs across the (wide) map instead of up it; Manhattan routes fill the frame
          let bearing = -12;
          if (a && b) {
            const k = Math.cos((40.73 * Math.PI) / 180);
            const az = (Math.atan2((b[0] - a[0]) * k, b[1] - a[1]) * 180) / Math.PI;
            bearing = az - 90;
            while (bearing > 90) bearing -= 180;
            while (bearing < -90) bearing += 180;
            const w = box?.clientWidth ?? 1, h = box?.clientHeight ?? 1;
            if (w < h * 1.1) bearing = -12; // portrait / narrow screens: keep north-up-ish
          }
          autoFit(m, (duration) => {
            const scrub = 0;
            const pts = [...pf, ...df].flatMap((f) => rings(f.geometry));
            const cam = rotatedCamera(pts, bearing, box?.clientWidth ?? 800, box?.clientHeight ?? 440, { top: 48, bottom: 40 + scrub, left: 40, right: 40 }, 13);
            m.flyTo({ ...cam, zoom: cam.zoom - 0.2, pitch: 30, duration, essential: true });
          });
        }
        if (!pf.length && !df.length) setErr("zones not found");
        setReady(true);
      })
      .catch((e) => setErr(String(e)));
    return () => { dead = true; cancelAnimationFrame(raf); m.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // live restyle of the arc as the hour changes (no re-fit)
  useEffect(() => {
    const m = map.current;
    if (!ready || !m || !m.getLayer("trail")) return;
    const c = curT == null ? TAXI : priceColor(curT);
    const glow = curT == null ? 0.6 : 0.35 + 0.6 * curT;
    m.setPaintProperty("trail-glow", "line-color", c);
    m.setPaintProperty("trail-glow", "line-opacity", glow);
    m.setPaintProperty("trail-glow", "line-width", 8 + 10 * (curT ?? 0.3));
    m.setPaintProperty("head-glow", "circle-color", c);
    m.setPaintProperty("head-glow", "circle-radius", 12 + 12 * (curT ?? 0.3));
    m.setPaintProperty("arc-glow", "line-color", c);
    m.setPaintProperty("arc-glow", "line-opacity", 0.12 + 0.25 * (curT ?? 0.3));
    m.setPaintProperty("trail", "line-color", mix(c, "#ffffff", 0.55));
  }, [ready, curT]);

  const pillText = cur ? `$${cur.mid.toFixed(2)}` : p.price;

  const proj = (q?: Pos) => (q && map.current ? map.current.project(q) : null);
  const at = (q?: Pos) => { const s = proj(q); return s ? { transform: `translate(${s.x}px, ${s.y}px)` } : { display: "none" }; };

  const pick = (i: number) => { const r = rows[Math.max(0, Math.min(rows.length - 1, i))]; if (r) { setHour(r.hour); setPlaying(false); } };
  const fromX = (e: RPointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    pick(Math.floor(((e.clientX - box.left) / box.width) * rows.length));
  };
  const curIdx = rows.findIndex((r) => r.hour === cur?.hour);
  const bestIdx = rows.findIndex((r) => r.hour === best?.hour);

  return (
    <div className={"mapbox " + (ready ? "ready" : "")}>
      <div className="mapview">
        <div ref={el} className="mapgl" />
        <div className="map-overlay">
          <div className="pin pu" style={at(pins.pu)}><span>{p.puLabel}</span></div>
          <div className="pin do" style={at(pins.do)}><span>{p.doLabel}</span></div>
          {pillText && (
            <div className="price-pill" style={at(pins.mid)}>
              <span>{pillText}{cur && <small>{hl(cur.hour)}{cur.wait_s != null ? ` / ${mmss(cur.wait_s)} wait` : ""}</small>}</span>
            </div>
          )}
        </div>
        {err && <div className="map-err">{err}</div>}
      </div>
      <div className="scrub">
        <div className="scrub-row top">
          <div className="days" role="tablist">
            {DAYS.map((d, i) => <button key={d} role="tab" aria-selected={i === dow} className={i === dow ? "on" : ""} onClick={() => { setDow(i); setPlaying(false); }}>{d}</button>)}
          </div>
          <button className={"range-t " + (full ? "on" : "")} onClick={() => setFull(!full)} title="Show all 24 hours">24h</button>
          {rows.length > 0 && (
            <div className="scrub-read">
              <span className="h">{cur ? hl(cur.hour) : ""}</span>
              <span className="v">{cur ? `$${cur.mid.toFixed(2)}` : ""}</span>
              <span className="r">{cur ? `$${cur.low.toFixed(0)}-${cur.high.toFixed(0)}` : ""}</span>
            </div>
          )}
        </div>
        {rows.length > 0 ? (
          <div className="scrub-row">
            <button className="play" aria-label={playing ? "pause" : "play through the day"} onClick={() => {
              if (!playing && hour === rows[rows.length - 1].hour) setHour(rows[0].hour);
              setPlaying(!playing);
            }}>{playing ? "\u275a\u275a" : "\u25b6"}</button>
            <div className="slider" role="slider" tabIndex={0} aria-label="hour of departure" aria-valuemin={rows[0].hour} aria-valuemax={rows[rows.length - 1].hour}
              aria-valuenow={cur?.hour} aria-valuetext={cur ? hl(cur.hour) : undefined}
              onKeyDown={(e) => { if (e.key === "ArrowRight") pick(curIdx + 1); else if (e.key === "ArrowLeft") pick(curIdx - 1); }}
              onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); fromX(e); }}
              onPointerMove={(e) => { if (e.buttons) fromX(e); }}>
              <div className="bars">
                {rows.map((r, i) => (
                  <i key={r.hour} className={(i === curIdx ? "cur " : "") + (i === bestIdx ? "best" : "")} title={`${hl(r.hour)} $${r.mid.toFixed(2)}`}
                    style={{ height: `${18 + 82 * tNorm(r.mid)}%`, background: priceColor(tNorm(r.mid)) }}>
                    {i === bestIdx && <sup>best</sup>}
                  </i>
                ))}
              </div>
              <div className="ruler">
                {rows.map((r, i) => (
                  <span key={r.hour} className={(r.hour % 3 === 0 ? "major " : "") + (i === curIdx ? "cur" : "")}>{r.hour % 3 === 0 ? hl(r.hour) : ""}</span>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="scrub-note">{q.loading ? "loading hourly quotes..." : q.note ?? "no hourly quotes"}</div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- ZoneMap choropleth */

export type ReachZone = { zone_id?: number | null; zone: string; lat: number; lon: number; p50_total?: number | null; n?: number | null };
export type Origin = { zone_id?: number | null; zone: string; lat: number; lon: number };

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");

export function ChoroplethView({ origin, zones }: { origin?: Origin | null; zones: ReachZone[] }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MLMap | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number; z: ReachZone } | null>(null);
  const [ready, setReady] = useState(false);
  const rerender = useOverlay();
  const fares = zones.map((z) => z.p50_total).filter((v): v is number => typeof v === "number");
  const lo = fares.length ? Math.min(...fares) : 0, hi = fares.length ? Math.max(...fares) : 1;
  const key = JSON.stringify([origin?.zone, zones.map((z) => [z.zone_id ?? z.zone, z.p50_total])]);

  useEffect(() => {
    if (!el.current) return;
    let raf = 0, dead = false;
    const m = newMap(el.current);
    map.current = m;
    m.on("move", rerender);
    m.on("error", (e) => console.debug("maplibre", e?.error?.message ?? e));
    Promise.all([loadZones(), new Promise<void>((r) => (m.loaded() ? r() : m.once("load", () => r())))]).then(([all]: [FC, void]) => {
      if (dead) return;
      const byId = new Map(all.features.map((f) => [f.properties.zone_id, f]));
      const byName = new Map(all.features.map((f) => [norm(f.properties.zone), f]));
      const find = (z: { zone_id?: number | null; zone: string }) => (z.zone_id != null ? byId.get(z.zone_id) : undefined) ?? byName.get(norm(z.zone));
      const reach = zones.flatMap((z, i) => {
        const f = find(z);
        return f ? [{ ...f, id: i, properties: { ...f.properties, p50: z.p50_total ?? lo, idx: i } }] : [];
      });
      const of = origin ? find(origin) : undefined;
      const mid = (lo + hi) / 2;
      m.addSource("reach", { type: "geojson", data: fc(reach) });
      m.addSource("pts", { type: "geojson", data: fc(zones.map((z, i) => ({ ...point([z.lon, z.lat]), properties: { p50: z.p50_total ?? lo, idx: i } }))) });
      const ramp = ["interpolate", ["linear"], ["get", "p50"], lo, CYAN, hi === lo ? hi + 1 : hi, TAXI] as never;
      void mid;
      m.addLayer({ id: "reach-fill", type: "fill", source: "reach", paint: { "fill-color": ramp, "fill-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 0.85, 0.5] as never, "fill-opacity-transition": { duration: 200 } } });
      m.addLayer({ id: "reach-line", type: "line", source: "reach", paint: { "line-color": ramp, "line-width": 1, "line-opacity": 0.9 } });
      m.addLayer({ id: "pts", type: "circle", source: "pts", paint: { "circle-radius": 2.5, "circle-color": "#fff", "circle-opacity": 0.7 } });
      if (of || origin) {
        m.addSource("origin", { type: "geojson", data: fc(of ? [of] : []) });
        m.addSource("origin-pt", { type: "geojson", data: fc(origin ? [point(of ? centroidOf([of])! : [origin.lon, origin.lat])] : []) });
        m.addLayer({ id: "origin-fill", type: "fill", source: "origin", paint: { "fill-color": "#ffffff", "fill-opacity": 0.12 } });
        m.addLayer({ id: "origin-glow", type: "line", source: "origin", paint: { "line-color": "#ffffff", "line-width": 10, "line-blur": 8, "line-opacity": 0.7 } });
        m.addLayer({ id: "origin-line", type: "line", source: "origin", paint: { "line-color": "#ffffff", "line-width": 2 } });
        m.addLayer({ id: "origin-pulse", type: "circle", source: "origin-pt", paint: { "circle-radius": 10, "circle-color": "#ffffff", "circle-opacity": 0.5, "circle-blur": 0.4 } });
        m.addLayer({ id: "origin-dot", type: "circle", source: "origin-pt", paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#111111", "circle-stroke-width": 2 } });
        const t0 = performance.now();
        const tick = (now: number) => {
          const t = ((now - t0) % 1800) / 1800;
          m.setPaintProperty("origin-pulse", "circle-radius", 6 + 26 * t);
          m.setPaintProperty("origin-pulse", "circle-opacity", 0.6 * (1 - t));
          m.setPaintProperty("origin-glow", "line-opacity", 0.45 + 0.4 * Math.sin(t * Math.PI));
          raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
      }
      let hid: number | null = null;
      m.on("mousemove", "reach-fill", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        if (hid != null) m.setFeatureState({ source: "reach", id: hid }, { hover: false });
        hid = f.id as number;
        m.setFeatureState({ source: "reach", id: hid }, { hover: true });
        m.getCanvas().style.cursor = "pointer";
        setHover({ x: e.point.x, y: e.point.y, z: zones[(f.properties as { idx: number }).idx] });
      });
      m.on("mouseleave", "reach-fill", () => {
        if (hid != null) m.setFeatureState({ source: "reach", id: hid }, { hover: false });
        hid = null;
        m.getCanvas().style.cursor = "";
        setHover(null);
      });
      const bb = boundsOf([...reach, ...(of ? [of] : [])], zones.map((z) => [z.lon, z.lat] as Pos));
      if (bb) {
        autoFit(m, (duration) => m.fitBounds(bb, { padding: 40, maxZoom: 13, pitch: 25, bearing: -10, duration, essential: true }));
      }
      setReady(true);
    }).catch((e) => console.debug("zones", e));
    return () => { dead = true; cancelAnimationFrame(raf); m.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const s = origin && map.current ? map.current.project([origin.lon, origin.lat]) : null;
  return (
    <div className={"mapbox " + (ready ? "ready" : "")}>
      <div className="mapview">
      <div ref={el} className="mapgl" />
      <div className="map-overlay">
        {s && origin && <div className="pin origin" style={{ transform: `translate(${s.x}px, ${s.y}px)` }}><span>{origin.zone}</span></div>}
        {hover && (
          <div className="map-tip" style={{ transform: `translate(${hover.x + 14}px, ${hover.y - 10}px)` }}>
            <b>{hover.z.zone}</b>
            <div>{hover.z.p50_total != null ? `$${hover.z.p50_total.toFixed(2)} median` : ""}{hover.z.n != null ? ` / ${hover.z.n} trips` : ""}</div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
