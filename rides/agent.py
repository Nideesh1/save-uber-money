"""The rides deep agent (Mistral Large 4 + Elasticsearch tools) and the event pipeline shared by the API (inline mode)
and the Hatchet `search` task.

`run_search(q, system_prompt, deep)` is an async generator of contract events:
  {"kind":"status","text"} | {"kind":"ui","id","line"} | {"kind":"error","text"} | {"kind":"done","meta":{ms, tools}}
"""
import rides.config as cfg  # noqa: F401  (first: .env)

import asyncio
import functools
import json
import os
import re
import time
from typing import Any, AsyncIterator, Callable

SEARCH_TIMEOUT_S = int(os.environ.get("SEARCH_TIMEOUT_S", "180"))
SOURCE = "NYC TLC High Volume FHV trips (Uber + Lyft), Aug 2026"

DOMAIN_PROMPT = """You are NYC Rides, an analyst over real NYC TLC High Volume FHV trip records (Uber and Lyft,
August 2026) indexed in Elasticsearch, plus a fare model trained on those trips. Answer the user's question with tools,
then render the answer as a generative UI.

Workflow (be FAST, target under 10 seconds: at most 3 model turns. Turn 1: resolve all places in parallel. Turn 2:
call ALL data tools you need in parallel in ONE turn. Turn 3: reply with the single word READY (a separate, faster
model writes the UI from your tool results). Never call the same tool twice):
1. Resolve every place the user mentions FIRST (one call per place, in parallel). Never guess zone ids.
   A street address or a specific place/landmark ("350 5th Ave", "Columbia University", "JFK Terminal 4") ->
   geocode_address (use its zone_id). A neighborhood, area or nickname -> resolve_zone. For an area, call
   resolve_zone with the area word itself (e.g. "uptown") and use the returned list as-is.
   A specific place (an airport, a named neighborhood that is one zone) -> use the top match's zone_id. A vague
   AREA ("midtown", "uptown", "downtown", "brooklyn waterfront") -> pass ALL matching zone_ids as a list (every
   pu_zone_id / do_zone_id arg accepts int or list[int]); do not pick one representative. Never ask clarifying
   questions: answer with the assumption stated in ONE Narrative line, e.g. "Midtown = 7 zones, Uptown = 12 zones".
   For areas, show the spread from the ML `pairs` field as a RankedList (the cheapest and priciest zone pairs).
2. Pick the right tool(s):
   - a specific trip ("how much from A to B", "what will it cost") or WHEN to travel/leave ("cheapest time", "when
     should I leave", "best time") -> ONE best_time_to_travel call (for "when") and/or ONE predict_fare call (its
     company_quotes already cover Uber and Lyft; never call it per company or per hour), rendered as TravelWindow
     and/or FareQuote. Add ONE fare_by_hour call in the same turn as historical evidence. best_time_to_travel:
     earliest_hour=6, latest_hour=23 unless the user asks about late night / early morning or names hours.
   - ANY question about a specific route (A to B) also gets ONE predict_fare call (the price card), even when the
     question is about timing or Uber vs Lyft.
   - historical price patterns by hour -> fare_by_hour (FareByHour)
   - Uber vs Lyft -> compare_companies (UberVsLyft)
   - pickup waits -> wait_stats (WaitMeter)
   - "where can I go for $X" -> reachable_under_budget (ZoneMap and/or RankedList)
   - how much drivers keep -> driver_cut (DriverCut)
   Weekday numbers: dow 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun. Hours are 0-23 local time of the request
   ("evening" = 17-22, "morning" = 6-10, "night" = 22-5). "Today"/"tonight" or no day given = today's weekday
   (given at the end of this prompt); "tonight" = 18-23.
   If a tool returns an error (e.g. the fare model is not trained yet), fall back to the historical tools.

"""

RENDER_PROMPT = """You render the answer to a NYC Uber/Lyft ride question as a generative UI. The tool results below
(real NYC TLC trips, Aug 2026, from Elasticsearch, plus a fare model) are your ONLY source of numbers.

Areas: when a place is an area (a list of zone ids), say so in ONE Narrative line ("Midtown = 13 zones, Uptown = 22
zones") and show the spread from the ML `pairs` field as a RankedList (cheapest and priciest zone pairs).
Zone ids for RouteMap come from resolve_zone (all `matches` zone_ids for an area, the top match for a single place)
or geocode_address (`zone_id`).

Streaming layout (REQUIRED): line 1 is `root = Stack([headline, map, ...])` using ONLY reference names; then define
each referenced component on its OWN line in the same order (`headline = AnswerHeadline(...)`, `map = RouteMap(...)`).
Never nest component calls inline inside root: each line renders on screen the moment it is written.

Consistency (REQUIRED): every "best/cheapest hour" and "save $X" claim comes ONLY from best_time_to_travel's `best`
and `savings_usd` (the model's prediction). Never name a cheapest hour from fare_by_hour; describe fare_by_hour only
as historical context ("historically ..."). The headline, TravelWindow and RouteMap must name the same hour and amount.

Price card (REQUIRED for every trip/route answer): right after RouteMap, render FareQuote(low, mid, high, wait_s,
"<A> to <B>, <Day> <hour>", company_quotes, model) from predict_fare. It follows the map's hour scrubber live.

3. Money first: start with an AnswerHeadline whose value is in US dollars (the cheapest / predicted / median price),
   unit "$", a short label and a one-sentence summary that names the hour and the saving when relevant.
   For EVERY trip/route answer, put RouteMap(title, pu_zone_ids, do_zone_ids, pu_label, do_label, price_label) right
   after the AnswerHeadline, with ALL zone ids you resolved for each end (the whole area list as-is) and a price_label
   like "$42 at 5am". When you called best_time_to_travel, add HOURS and the dow as 7th and 8th args.
   HOURS (bare word, no quotes) is a placeholder the server replaces with best_time_to_travel's `hours` list: NEVER
   type the hours list yourself. Use it in TravelWindow too: TravelWindow("title", HOURS, best_hour, savings_usd)
   and RouteMap("title", [pu ids], [do ids], "pu label", "do label", "$42 at 5am", HOURS, 4).
4. Every number you show MUST come from a tool result in this conversation. Never invent, round wildly or extrapolate.
   If data is thin (n small or a tool returned nothing), say so in a Narrative.
5. ALWAYS end the Stack with EvidenceStrip(n, source) where n is the trip count (`n`) of the main historical tool
   result you used (prefer fare_by_hour, then compare_companies, reachable_under_budget, driver_cut, wait_stats; the
   model's trained_on only when no historical tool was called) and source is
   "NYC TLC HVFHV trips, Aug 2026". The server attaches the exact Elasticsearch query.

Keep the UI compact (it is generated token by token): Tips at most 3 short items, RankedList at most 6 items,
Narrative one or two sentences, zone lists named by count not by listing every zone.

Style: plain punctuation; never use em dashes or en dashes (use commas, colons or "to"); write "JFK to Williamsburg".

Output: your FINAL message must be ONLY an OpenUI Lang program (no prose, no markdown, no code fences), one statement
per line, `root = Stack([...])` first, following the syntax rules below exactly.

"""

STATUS: dict[str, Callable[[dict], str]] = {
    "resolve_zone": lambda a: f"resolving zone \"{a.get('text', '')}\"...",
    "geocode_address": lambda a: f"finding the exact zone for {a.get('address', '')}...",
    "fare_by_hour": lambda a: "fare percentiles by hour...",
    "compare_companies": lambda a: "comparing Uber vs Lyft...",
    "wait_stats": lambda a: "pickup wait times...",
    "reachable_under_budget": lambda a: f"zones reachable under ${a.get('budget_usd', '?')}...",
    "driver_cut": lambda a: "how much drivers keep...",
    "predict_fare": lambda a: "predicting fare range...",
    "best_time_to_travel": lambda a: "scanning the best time to leave...",
}
DATA_TOOLS = ["resolve_zone", "geocode_address", "fare_by_hour", "compare_companies", "wait_stats", "reachable_under_budget", "driver_cut"]
ML_TOOLS = ["predict_fare", "best_time_to_travel"]


def _safe(fn: Callable, system: str | None = None) -> Callable:
    """Tool errors become a JSON error the model can react to (e.g. ML models not trained yet).

    system: run the body inside a CLIENT span with db.system=<system> so AgentGlow draws the backend as a satellite
    (the elasticsearch client's own spans are INTERNAL with db.system.name, which AgentGlow does not pick up)."""

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            if system is None:
                return fn(*args, **kwargs)
            if system == "lightgbm":  # ML node: the fare model shows as an inference resource in its own group
                import agentglow

                with agentglow.inference("lightgbm fare model", units=1, unit="predictions", group="fare model"):
                    return fn(*args, **kwargs)
            from opentelemetry import trace

            index = "zones" if fn.__name__ in ("resolve_zone", "geocode_address") else "rides"
            with trace.get_tracer("nyc-rides.backends").start_as_current_span(
                    f"{system} {fn.__name__}", kind=trace.SpanKind.CLIENT,
                    attributes={"db.system": system, "db.system.name": system, "db.namespace": index,
                                "db.collection.name": index, "db.operation.name": fn.__name__}):
                return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {str(e)[:300]}", "n": 0}

    return wrapped


def load_tools() -> list[Callable]:
    import rides.tools as T

    tools = [_safe(getattr(T, n), "elasticsearch") for n in DATA_TOOLS if hasattr(T, n)]
    try:  # ML track: optional until rides/ml.py (and models/) exist
        import rides.ml as M

        tools += [_safe(getattr(M, n), "lightgbm") for n in ML_TOOLS if hasattr(M, n)]
    except Exception:  # noqa: BLE001
        pass
    return tools


def _model(deep: bool):
    from langchain.chat_models import init_chat_model

    kw = {} if deep else {"model_kwargs": {"reasoning_effort": "none"}}
    return init_chat_model(cfg.MODEL, **kw)


def _inference_middleware():
    """Every Large 4 call runs inside agentglow.inference(group="mistral") so the model shows as a node."""
    import agentglow
    from langchain.agents.middleware import AgentMiddleware

    model_name = cfg.MODEL.split(":", 1)[-1]

    class MistralInference(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            with agentglow.inference(model_name, units=1, unit="calls", group="mistral"):
                return await handler(request)

        def wrap_model_call(self, request, handler):
            with agentglow.inference(model_name, units=1, unit="calls", group="mistral"):
                return handler(request)

    return MistralInference()


def build_agent(system_prompt: str, deep: bool):
    from deepagents import create_deep_agent

    from langchain.agents.middleware import ToolCallLimitMiddleware

    limits = [ToolCallLimitMiddleware(tool_name=t, run_limit=1, exit_behavior="continue")  # one call each: speed
              for t in ("fare_by_hour", "compare_companies", "wait_stats", "reachable_under_budget", "driver_cut",
                        "predict_fare", "best_time_to_travel")]
    return create_deep_agent(model=_model(deep), tools=load_tools(), middleware=[_inference_middleware(), *limits],
                             system_prompt=DOMAIN_PROMPT + "\n" + _today(), name="nyc-rides")


def _today() -> str:
    import datetime as dt

    now = dt.datetime.now()
    return f"Today is {now:%A} (dow {now.weekday()}), {now:%H:%M} local time."


def _render_model():
    from langchain.chat_models import init_chat_model

    return init_chat_model(cfg.RENDER_MODEL)


def _render_messages(q: str, system_prompt: str, results: list[tuple[str, dict, Any]]) -> list[dict]:
    """The renderer sees the question and every tool call with its result (minus the bulky ES query bodies)."""
    def slim(v: Any) -> Any:
        return {k: x for k, x in v.items() if k != "query"} if isinstance(v, dict) else v

    calls = "\n".join(f"- {name}({json.dumps(args)}) -> {json.dumps(slim(res), default=str)}" for name, args, res in results)
    return [{"role": "system", "content": RENDER_PROMPT + (system_prompt or "") + "\n\n" + _today()},
            {"role": "user", "content": f"Question: {q}\n\nTool results:\n{calls or '(none)'}"}]


# ---------------------------------------------------------------- OpenUI Lang statement parsing


def text_of(content: Any) -> str:
    """Text blocks only (Large 4 may return [{type: thinking}, {type: text}])."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(c.get("text", "") if isinstance(c, dict) and c.get("type") in ("text", "output_text") else
                       (c if isinstance(c, str) else "") for c in content)
    return ""


_STMT = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(\S.*)$", re.S)


class StatementParser:
    """Incremental: feed text deltas, get complete `id = Comp(...)` statements (balanced brackets, outside strings)."""

    def __init__(self):
        self.buf, self.depth, self.quote, self.esc = "", 0, None, False

    def feed(self, text: str) -> list[tuple[str, str]]:
        out = []
        for ch in text:
            if ch == "\n" and self.depth <= 0 and self.quote is None:
                out += self._flush()
                continue
            self.buf += ch
            if self.quote:
                if self.esc:
                    self.esc = False
                elif ch == "\\":
                    self.esc = True
                elif ch == self.quote:
                    self.quote = None
            elif ch in "\"'":
                self.quote = ch
            elif ch in "([{":
                self.depth += 1
            elif ch in ")]}":
                self.depth -= 1
        return out

    def _flush(self) -> list[tuple[str, str]]:
        s, self.buf, self.depth, self.quote, self.esc = self.buf.strip(), "", 0, None, False
        if not s or s.startswith("```"):
            return []
        m = _STMT.match(s)
        return [(m.group(1), s)] if m else []

    def close(self) -> list[tuple[str, str]]:
        return self._flush()


def _top_level_args(inner: str) -> int:
    depth, quote, esc, n, seen = 0, None, False, 0, False
    for ch in inner:
        if quote:
            esc = (not esc and ch == "\\")
            if ch == quote and not esc:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
        if not ch.isspace():
            seen = True
    return n + 1 if seen else 0


def _with_query(line: str, query: Any) -> str:
    """EvidenceStrip(n, source) -> EvidenceStrip(n, source, "<ES query json>") using the main tool's query."""
    m = re.match(r"^(\s*\w+\s*=\s*EvidenceStrip\()(.*)\)\s*$", line, re.S)
    if not m or query is None or _top_level_args(m.group(2)) != 2:
        return line
    q = json.dumps(query, separators=(",", ":"), default=str)
    return f"{m.group(1)}{m.group(2)}, {json.dumps(q)})"


EVIDENCE_PRIORITY = ["fare_by_hour", "compare_companies", "reachable_under_budget", "driver_cut", "wait_stats"]


def _pick_query(line: str, evidence: list) -> Any:
    """The query behind the EvidenceStrip: the tool whose n matches its first arg, else the highest-priority tool."""
    if "EvidenceStrip(" not in line or not evidence:
        return None
    m = re.search(r"EvidenceStrip\(\s*([0-9.]+)", line)
    if m:
        for _tool, n, q in evidence:
            try:
                if n is not None and float(n) == float(m.group(1)):
                    return q
            except (TypeError, ValueError):
                pass
        return None  # n is not from a historical tool (e.g. model trained_on): no query rather than a wrong one
    rank = {t: i for i, t in enumerate(EVIDENCE_PRIORITY)}
    return min(evidence, key=lambda e: rank.get(e[0], 99))[2]


def _with_hours(line: str, window: dict) -> str:
    """RouteMap(6 args) -> RouteMap(..., hours, dow) from best_time_to_travel (saves the model re-typing the curve)."""
    m = re.match(r"^(\s*\w+\s*=\s*RouteMap\()(.*)\)\s*$", line, re.S)
    if not m or not window.get("hours") or _top_level_args(m.group(2)) != 6:
        return line
    hours = [{k: h.get(k) for k in ("hour", "low", "mid", "high", "wait_s")} for h in window["hours"]]
    dow = window.get("dow")
    extra = json.dumps(hours, separators=(", ", ": ")) + (f", {int(dow)}" if dow is not None else "")
    return f"{m.group(1)}{m.group(2)}, {extra})"


def _subst_hours(line: str, window: dict) -> str:
    """Replace the bare placeholder HOURS (outside strings) with the best_time_to_travel hours list."""
    if "HOURS" not in line or not window.get("hours"):
        return line
    hours = json.dumps([{k: h.get(k) for k in ("hour", "low", "mid", "high", "wait_s")} for h in window["hours"]],
                       separators=(", ", ": "))
    out, i, quote = [], 0, None
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif line.startswith("HOURS", i) and not (i and (line[i - 1].isalnum() or line[i - 1] == "_")) \
                and not line[i + 5:i + 6].isalnum():
            out.append(hours)
            i += 5
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _finish(line: str, evidence: list, window: dict) -> str:
    return _with_hours(_subst_hours(_with_query(line, _pick_query(line, evidence)), window), window)


# ---------------------------------------------------------------- the pipeline


def _parse_tool_result(content: Any) -> dict | None:
    s = text_of(content) if not isinstance(content, dict) else None
    if isinstance(content, dict):
        return content
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        try:  # deepagents may str() a dict
            import ast

            v = ast.literal_eval(s)
            return v if isinstance(v, dict) else None
        except Exception:  # noqa: BLE001
            return None


async def run_search(q: str, system_prompt: str = "", deep: bool = False) -> AsyncIterator[dict]:
    """Run the agent once; yield contract events. Always ends with exactly one `done`."""
    import agentglow

    t0 = time.monotonic()
    tools_used: list[str] = []
    evidence: list[tuple[str, Any, Any]] = []  # (tool, n, query) of every historical tool result
    window: dict = {}  # last best_time_to_travel {hours, dow}: attached to RouteMap server-side
    n_ui = 0
    queue: asyncio.Queue = asyncio.Queue()

    async def produce():
        nonlocal n_ui
        try:
            results: list[tuple[str, dict, Any]] = []  # (tool, args, result) for the renderer
            async with agentglow.agent("planner", task=q[:120]) as a:
                agent = build_agent(system_prompt, deep)
                args_by_id: dict[str, tuple[str, dict]] = {}
                async for mode, chunk in agent.astream({"messages": [{"role": "user", "content": q}]},
                                                       stream_mode=["updates"], config={"recursion_limit": 40}):
                    for val in (chunk or {}).values():
                        if not isinstance(val, dict):
                            continue
                        for m in val.get("messages", []) or []:
                            for tc in getattr(m, "tool_calls", None) or []:
                                name = tc.get("name", "")
                                args_by_id[tc.get("id", "")] = (name, tc.get("args") or {})
                                if name in STATUS:
                                    tools_used.append(name)
                                    await queue.put({"kind": "status", "text": STATUS[name](tc.get("args") or {})})
                            if getattr(m, "type", "") != "tool":
                                continue
                            res = _parse_tool_result(m.content)
                            name, args = args_by_id.get(getattr(m, "tool_call_id", ""), (getattr(m, "name", ""), {}))
                            if name in STATUS:
                                results.append((name, args, res if res is not None else str(m.content)[:2000]))
                            if name in ("resolve_zone", "geocode_address") or not res:
                                continue
                            if name == "best_time_to_travel" and res.get("hours") and not res.get("error"):
                                window.update(hours=res["hours"], dow=res.get("dow"))
                            if res.get("query") is not None:
                                evidence.append((name, res.get("n"), res["query"]))
                a.say(f"{len(tools_used)} tool calls")
            await queue.put({"kind": "status", "text": "drawing your answer..."})
            render = cfg.RENDER_MODEL.split(":", 1)[-1]
            async with agentglow.agent("renderer", task=q[:120]) as a:
                parser = StatementParser()
                with agentglow.inference(render, units=1, unit="calls", group="mistral"):
                    async for msg in _render_model().astream(_render_messages(q, system_prompt, results)):
                        for sid, line in parser.feed(text_of(msg.content)):
                            await queue.put({"kind": "ui", "id": sid, "line": _finish(line, evidence, window)})
                            n_ui += 1
                for sid, line in parser.close():
                    await queue.put({"kind": "ui", "id": sid, "line": _finish(line, evidence, window)})
                    n_ui += 1
                if not n_ui:
                    await queue.put({"kind": "error", "text": "the agent returned no UI"})
                a.say(f"{len(tools_used)} tool calls, {n_ui} UI statements")
        except Exception as e:  # noqa: BLE001
            await queue.put({"kind": "error", "text": f"{type(e).__name__}: {str(e)[:300]}"})
        finally:
            await queue.put(None)

    task = asyncio.create_task(produce())  # copies the current context: spans nest under the caller's span
    deadline = time.monotonic() + SEARCH_TIMEOUT_S
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=max(0.1, deadline - time.monotonic()))
            except TimeoutError:
                task.cancel()
                yield {"kind": "error", "text": f"timed out after {SEARCH_TIMEOUT_S}s"}
                break
            if item is None:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
    yield {"kind": "done", "meta": {"ms": int((time.monotonic() - t0) * 1000), "tools": tools_used}}
