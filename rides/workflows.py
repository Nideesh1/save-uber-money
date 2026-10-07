"""Hatchet workflows (namespace "nycrides", set by rides.config).

  search   runs the rides agent once and streams every contract event with ctx.aio_put_stream(json); the task output
           also carries the full event list (with `seq`) so the API can replay anything the live stream missed.
  ingest   rides.etl.run wrapped in agentglow.job("ingest-<rows>", kind="ingest") with a stage span per ETL stage and
           overall progress.
"""
import rides.config as cfg  # noqa: F401  (first: Hatchet env)

import asyncio
import json
import time
from datetime import timedelta

from hatchet_sdk import Context, Hatchet
from pydantic import BaseModel

hatchet = Hatchet()


class SearchInput(BaseModel):
    q: str
    system_prompt: str = ""
    deep: bool = False


class IngestInput(BaseModel):
    rows: int = 50000


STREAM_GRACE_S = 0.7  # give the API time to subscribe before the first event (Hatchet streams are not buffered)


@hatchet.task(name="search", input_validator=SearchInput, execution_timeout=timedelta(minutes=5), retries=0)
async def search(input: SearchInput, ctx: Context) -> dict:
    from rides.agent import run_search

    import agentglow

    await asyncio.sleep(STREAM_GRACE_S)
    events = []
    with agentglow.job(ctx.workflow_run_id, kind="search") as job:
        job.set("agentglow.job.title", input.q[:80])
        async for ev in run_search(input.q, input.system_prompt, input.deep):
            ev = {**ev, "seq": len(events)}
            events.append(ev)
            try:
                await ctx.aio_put_stream(json.dumps(ev, default=str))
            except Exception:  # noqa: BLE001  stream is best effort; the output replays everything
                pass
        job.set("agentglow.final", f"{sum(1 for e in events if e['kind'] == 'ui')} UI statements")
    return {"events": events}


STAGES = ["download", "clean", "sample", "zones", "index_zones", "index_rides"]
WEIGHTS = {"download": 0.15, "clean": 0.15, "sample": 0.1, "zones": 0.05, "index_zones": 0.05, "index_rides": 0.5}


def _ingest_sync(rows: int) -> dict:
    import agentglow

    from rides import etl

    with agentglow.job(f"ingest-{rows}", kind="ingest") as job:
        cur: dict = {"name": None, "span": None}

        def on_stage(name: str, frac: float) -> None:
            if name != cur["name"]:
                if cur["span"] is not None:
                    cur["span"].__exit__(None, None, None)
                cur["name"], cur["span"] = name, agentglow.stage(name)
                cur["span"].__enter__()
            done = sum(WEIGHTS.get(s, 0) for s in STAGES[:STAGES.index(name)]) if name in STAGES else 0.0
            agentglow.progress(min(1.0, done + WEIGHTS.get(name, 0) * max(0.0, min(1.0, frac))),
                               label=f"{name} {frac:.0%}")

        try:
            out = etl.run(rows, on_stage=on_stage)
        finally:
            if cur["span"] is not None:
                cur["span"].__exit__(None, None, None)
        summary = f"indexed {out.get('rides_indexed', '?')} rides, {out.get('zones_indexed', '?')} zones"
        job.set("agentglow.final", summary)
        agentglow.progress(1.0, label=summary)
        return out


@hatchet.task(name="ingest", input_validator=IngestInput, execution_timeout=timedelta(hours=2), retries=0)
async def ingest(input: IngestInput, ctx: Context) -> dict:
    t0 = time.monotonic()
    out = await asyncio.to_thread(_ingest_sync, input.rows)
    return {**out, "s": round(time.monotonic() - t0, 1)}


WORKFLOWS = [search, ingest]
