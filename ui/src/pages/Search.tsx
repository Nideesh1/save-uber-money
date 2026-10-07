import { Renderer } from "@openuidev/react-lang";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { API, library, searchPrompt } from "../lib/library";
import { mockStream, type Ev } from "../lib/mock";

const EXAMPLES = [
  "When should I leave JFK for Williamsburg on Friday to save money?",
  "Cheapest time to get from JFK to Williamsburg on Friday",
  "I have $25, where can I go from Astoria?",
  "Is Lyft cheaper than Uber from LaGuardia?",
  "Do drivers get screwed on airport runs?",
];

type Turn = {
  id: number;
  q: string;
  deep: boolean;
  status: { text: string; err?: boolean }[];
  stmts: Record<string, string>; // OpenUI Lang statements by id; same id replaces
  live: boolean;
  start: number;
  end?: number;
  meta?: { ms?: number; tools?: string[] };
};

const program = (s: Record<string, string>) =>
  [s.root, ...Object.entries(s).filter(([k]) => k !== "root").map(([, v]) => v)].filter(Boolean).join("\n");

async function* sse(q: string, system_prompt: string, deep: boolean): AsyncGenerator<Ev> {
  const res = await fetch(`${API}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ q, system_prompt, deep }),
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const data = buf.slice(0, i).split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
      buf = buf.slice(i + 2);
      if (data) yield JSON.parse(data) as Ev;
    }
  }
}

type Group = { text: string; n: number; err?: boolean };

function useElapsed(t: Turn) {
  const [now, setNow] = useState(performance.now());
  useEffect(() => {
    if (!t.live) return;
    const id = setInterval(() => setNow(performance.now()), 100);
    return () => clearInterval(id);
  }, [t.live]);
  return ((t.end ?? now) - t.start) / 1000;
}

/** Centered loader while waiting for the first UI statement. */
function Loader({ t, groups, leaving }: { t: Turn; groups: Group[]; leaving: boolean }) {
  const secs = useElapsed(t);
  return (
    <div className={"loader " + (leaving ? "leaving" : "")}>
      <div className="loader-q">{t.q}</div>
      <svg className="loader-route" viewBox="0 0 240 70" aria-hidden>
        <defs>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3" result="b" /><feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        </defs>
        <path className="track" d="M20 52 Q120 -12 220 52" />
        <path className="draw" d="M20 52 Q120 -12 220 52" pathLength={100} filter="url(#glow)" />
        <circle className="dot a" cx="20" cy="52" r="5" />
        <circle className="dot b" cx="220" cy="52" r="5" />
      </svg>
      <ol className="loader-steps">
        {groups.map((g, i) => {
          const current = t.live && i === groups.length - 1 && !g.err;
          return (
            <li key={g.text} className={g.err ? "err" : current ? "cur" : "done"}>
              <span className="ic">{g.err ? "!" : current ? <i className="spin" /> : "\u2713"}</span>
              <span className="tx">{g.text}</span>
              {g.n > 1 && <span className="times">{"\u00d7"}{g.n}</span>}
            </li>
          );
        })}
      </ol>
      <div className="loader-time">{secs.toFixed(1)}s</div>
    </div>
  );
}

/** Loader while waiting; then one muted summary line that expands. */
function Steps({ t }: { t: Turn }) {
  const [open, setOpen] = useState(false);
  const waiting = !t.stmts.root;
  const [leaving, setLeaving] = useState(false);
  const wasWaiting = useRef(waiting);
  useEffect(() => {
    if (wasWaiting.current && !waiting) {
      setLeaving(true);
      const id = setTimeout(() => setLeaving(false), 550);
      wasWaiting.current = false;
      return () => clearTimeout(id);
    }
    wasWaiting.current = waiting;
  }, [waiting]);
  const groups = useMemo(() => {
    const m = new Map<string, Group>();
    for (const s of t.status) {
      const g = m.get(s.text);
      if (g) g.n++;
      else m.set(s.text, { text: s.text, n: 1, err: s.err });
    }
    return [...m.values()];
  }, [t.status]);
  if (waiting || leaving) return <Loader t={t} groups={groups} leaving={leaving} />;
  const errs = groups.filter((g) => g.err);
  const ms = t.meta?.ms ?? (t.end != null ? t.end - t.start : null);
  return (
    <div className="steps">
      <div className="steps-line">
        <button onClick={() => setOpen(!open)}>
          {t.status.length} steps{ms != null ? ` \u00b7 ${(ms / 1000).toFixed(1)}s` : t.live ? " \u00b7 running" : ""}
          <span className="caret">{open ? " \u25b4" : " \u25be"}</span>
        </button>
        {" \u00b7 "}<Link to="/backend">view backend</Link>
      </div>
      {open && (
        <div className="status expanded">
          {groups.map((g) => (
            <div key={g.text} className={g.err ? "err" : ""}>{g.text}{g.n > 1 && <span className="times"> {"\u00d7"}{g.n}</span>}</div>
          ))}
          {t.meta?.tools?.length ? <div className="tools">tools: {t.meta.tools.join(", ")}</div> : null}
        </div>
      )}
      {!open && errs.map((g) => <div key={g.text} className="status err">{g.text}</div>)}
    </div>
  );
}

export default function Search() {
  const mock = useMemo(() => new URLSearchParams(location.search).has("mock"), []);
  const [q, setQ] = useState("");
  const [deep, setDeep] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const busy = turns.some((t) => t.live);
  const prompt = useMemo(() => searchPrompt(), []);
  const lastRef = useRef<HTMLElement>(null);
  const started = useRef(false);

  const patch = (id: number, f: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? f(t) : t)));

  async function run(text: string) {
    const query = text.trim();
    if (!query || busy) return;
    setQ("");
    const id = Date.now();
    setTurns((ts) => [{ id, q: query, deep, status: [], stmts: {}, live: true, start: performance.now() }, ...ts]);
    try {
      for await (const ev of mock ? mockStream() : sse(query, prompt, deep)) {
        if (ev.kind === "status") patch(id, (t) => ({ ...t, status: [...t.status, { text: ev.text }] }));
        else if (ev.kind === "ui") patch(id, (t) => ({ ...t, stmts: { ...t.stmts, [ev.id]: ev.line } }));
        else if (ev.kind === "error") patch(id, (t) => ({ ...t, status: [...t.status, { text: ev.text, err: true }] }));
        else if (ev.kind === "done") patch(id, (t) => ({ ...t, live: false, meta: ev.meta, end: t.end ?? performance.now() }));
      }
    } catch (e) {
      patch(id, (t) => ({ ...t, status: [...t.status, { text: `api unreachable at ${API}: ${String(e)}`, err: true }] }));
    }
    patch(id, (t) => ({ ...t, live: false, end: t.end ?? performance.now() }));
  }

  useEffect(() => {
    if (mock && !started.current) {
      started.current = true;
      run(EXAMPLES[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mock]);

  useEffect(() => {
    lastRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [turns.length]);

  return (
    <main className="page">
      <header className={"hero " + (turns.length ? "compact" : "")}>
        <h1>Ask the city about your ride.</h1>
        <p className="lead">Fares, waits and driver pay from real NYC TLC Uber and Lyft trips, answered as live charts.{mock && <span className="mock-flag">mock mode</span>}</p>
      </header>
      <form className="search" onSubmit={(e) => { e.preventDefault(); run(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cheapest time to get from JFK to Williamsburg on Friday..." autoFocus />
        <label className={"deep " + (deep ? "on" : "")} title="Slower: Mistral Large 4 reasons step by step before answering">
          <input type="checkbox" checked={deep} onChange={(e) => setDeep(e.target.checked)} />
          <span className="knob" />Think harder
        </label>
        <button className="go" disabled={busy || !q.trim()}>{busy ? "..." : "Ask"}</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((x) => <button key={x} className="chip" disabled={busy} onClick={() => run(x)}>{x}</button>)}
      </div>

      <div className="thread">
        {turns.map((t, k) => (
          <article className="turn" key={t.id} ref={k === 0 ? lastRef : undefined}>
            <h2 className={"q " + (t.stmts.root ? "" : "hidden")}>{t.q}{t.deep && <span className="pill">think harder</span>}</h2>
            <Steps t={t} />
            {t.stmts.root && <Renderer response={program(t.stmts)} library={library} isStreaming={t.live} />}
          </article>
        ))}
      </div>
    </main>
  );
}
