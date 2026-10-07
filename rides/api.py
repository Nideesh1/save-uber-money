"""FastAPI :8420 for the nyc-rides UI.

  POST /api/search {q, system_prompt, deep?} -> text/event-stream, each `data:` line one JSON event:
       {"kind":"status","text"} | {"kind":"ui","id","line"} | {"kind":"error","text"} | {"kind":"done","meta":{ms,tools}}
       Default: triggers the Hatchet `search` run and relays its stream (any event the live stream missed is replayed
       from the run output). SEARCH_INLINE=1 runs the agent in-process instead.
  POST /api/ingest {rows}  -> triggers the Hatchet `ingest` run
  GET  /api/health

Run: uv run uvicorn rides.api:app --port 8420   (or: uv run python -m rides.api)
"""
import rides.config as cfg

import asyncio
import contextvars
import json
import os
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="nyc-rides API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
cfg.setup_tracing("nyc-rides-api", app=app)

KEEPALIVE_S = 15
REPLAY_GRACE_S = 1.5  # after the run finishes, wait this long for the live stream before replaying from output


class SearchIn(BaseModel):
    q: str
    system_prompt: str = ""
    deep: bool = False


class IngestIn(BaseModel):
    rows: int = 50000


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


async def _inline(body: SearchIn, q: asyncio.Queue) -> None:
    from rides.agent import run_search

    try:
        async for ev in run_search(body.q, body.system_prompt, body.deep):
            await q.put(ev)
    except Exception as e:  # noqa: BLE001
        await q.put({"kind": "error", "text": f"{type(e).__name__}: {str(e)[:300]}"})
        await q.put({"kind": "done", "meta": {"ms": 0, "tools": []}})
    finally:
        await q.put(None)


async def _via_hatchet(body: SearchIn, q: asyncio.Queue) -> None:
    from rides.workflows import SearchInput, hatchet, search

    seen: set[int] = set()
    finished = asyncio.Event()
    lock = asyncio.Lock()

    async def emit(ev: dict) -> None:
        async with lock:
            seq = ev.pop("seq", None)
            if finished.is_set() or (seq is not None and seq in seen):
                return
            if seq is not None:
                seen.add(seq)
            await q.put(ev)
            if ev.get("kind") == "done":
                finished.set()

    try:
        from opentelemetry.propagate import inject

        meta: dict[str, str] = {}
        inject(meta)  # traceparent: the worker's run is a child of this request (one tree in AgentGlow)
        ref = await asyncio.wait_for(search.aio_run_no_wait(SearchInput(**body.model_dump()), additional_metadata=meta), 15)
        run_id = ref.workflow_run_id

        async def live():
            async for chunk in hatchet.runs.subscribe_to_stream(run_id):
                try:
                    data = chunk.decode() if isinstance(chunk, bytes) else chunk
                    ev = json.loads(data)
                except (ValueError, AttributeError):
                    continue
                if isinstance(ev, dict):
                    await emit(ev)
                if finished.is_set():
                    return

        async def replay():
            out = await ref.aio_result()
            try:
                await asyncio.wait_for(finished.wait(), REPLAY_GRACE_S)
                return
            except TimeoutError:
                pass
            events = (out or {}).get("events") if isinstance(out, dict) else None
            for ev in sorted(events or [], key=lambda e: e.get("seq", 0)):
                await emit(dict(ev))
            if not finished.is_set():
                await emit({"kind": "done", "meta": {"ms": 0, "tools": [], "run_id": run_id}})

        tasks = [asyncio.create_task(live()), asyncio.create_task(replay())]
        try:
            await _first_finished(tasks, finished)
        finally:
            for t in tasks:
                t.cancel()
    except Exception as e:  # noqa: BLE001
        if not finished.is_set():
            await q.put({"kind": "error", "text": f"hatchet: {type(e).__name__}: {str(e)[:300]}"})
            await q.put({"kind": "done", "meta": {"ms": 0, "tools": []}})
    finally:
        await q.put(None)


async def _first_finished(tasks: list[asyncio.Task], finished: asyncio.Event) -> None:
    """Return once `done` was emitted; surface an exception only if every task failed without finishing."""
    waiter = asyncio.create_task(finished.wait())
    pending = set(tasks) | {waiter}
    try:
        while not finished.is_set():
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if finished.is_set():
                return
            errs = [t.exception() for t in done if t is not waiter and not t.cancelled() and t.exception()]
            if not (pending - {waiter}):
                raise errs[0] if errs else RuntimeError("search run ended without a done event")
    finally:
        waiter.cancel()


async def _stream(body: SearchIn) -> AsyncIterator[str]:
    q: asyncio.Queue = asyncio.Queue()
    inline = cfg.SEARCH_INLINE or os.environ.get("SEARCH_INLINE") == "1"
    task = asyncio.create_task((_inline if inline else _via_hatchet)(body, q), context=contextvars.copy_context())
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=KEEPALIVE_S)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            yield _sse(item)
    finally:
        if not task.done():
            task.cancel()


@app.post("/api/search")
async def api_search(body: SearchIn):
    return StreamingResponse(_stream(body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ingest")
async def api_ingest(body: IngestIn):
    from rides.workflows import IngestInput, ingest

    ref = await ingest.aio_run_no_wait(IngestInput(rows=body.rows))
    return {"run_id": ref.workflow_run_id, "rows": body.rows}


def _ids(v: str) -> int | list[int]:
    ids = [int(x) for x in v.split(",") if x.strip()]
    return ids[0] if len(ids) == 1 else ids


@app.get("/api/quote")
async def api_quote(pu: str, do: str, dow: int):
    """Map time scrubber: ML-only price curve for a route and weekday (no LLM)."""
    import agentglow

    def run() -> dict:
        import rides.ml as M

        with agentglow.inference("lightgbm fare model", units=24, unit="predictions", group="fare model"):
            return M.best_time_to_travel(_ids(pu), _ids(do), dow, 0, 23)

    try:
        return await asyncio.to_thread(run)
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(400, f"bad zone ids: {e}") from e


@app.get("/api/predict")
async def api_predict(pu: str, do: str, hour: int, dow: int):
    """Price card follows the map scrubber: ML-only quote (with Uber / Lyft) for one hour and weekday (no LLM)."""
    import agentglow

    def run() -> dict:
        import rides.ml as M

        with agentglow.inference("lightgbm fare model", units=1, unit="predictions", group="fare model"):
            return M.predict_fare(_ids(pu), _ids(do), hour, dow)

    try:
        return await asyncio.to_thread(run)
    except ValueError as e:
        from fastapi import HTTPException

        raise HTTPException(400, f"bad zone ids: {e}") from e


@app.get("/api/health")
async def health():
    return {"ok": True, "mode": "inline" if cfg.SEARCH_INLINE else "hatchet", "model": cfg.MODEL}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("API_HOST", "127.0.0.1"), port=cfg.API_PORT)
