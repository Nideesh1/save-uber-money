// The map's time scrubber publishes the selected route + hour + weekday; the FareQuote card follows it.
import { useEffect, useState, useSyncExternalStore } from "react";
import { API } from "./config";

export type Pick = { puIds: number[]; doIds: number[]; hour: number; dow: number };
export type Prediction = {
  low: number; mid: number; high: number; wait_s?: number | null;
  company_quotes?: { company: string; low: number; mid: number; high: number; wait_s?: number | null }[];
};

let current: Pick | null = null;
const subs = new Set<() => void>();

export function setPick(p: Pick | null) {
  current = p;
  subs.forEach((f) => f());
}

export function usePick(): Pick | null {
  return useSyncExternalStore((f) => (subs.add(f), () => subs.delete(f)), () => current);
}

const cache = new Map<string, Prediction>();

/** ML-only quote for the scrubbed hour (GET /api/predict), debounced and cached; null until loaded. */
export function usePrediction(p: Pick | null): Prediction | null {
  const key = p ? `${p.puIds.join(",")}|${p.doIds.join(",")}|${p.hour}|${p.dow}` : "";
  const [pred, setPred] = useState<Prediction | null>(() => cache.get(key) ?? null);
  useEffect(() => {
    if (!p) return setPred(null);
    const hit = cache.get(key);
    if (hit) return setPred(hit);
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetch(`${API}/api/predict?pu=${p.puIds.join(",")}&do=${p.doIds.join(",")}&hour=${p.hour}&dow=${p.dow}`, { signal: ctl.signal })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: Prediction | null) => { if (d && typeof d.mid === "number") { cache.set(key, d); setPred(d); } })
        .catch(() => { /* aborted or offline: keep the agent's numbers */ });
    }, 120);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return pred;
}
