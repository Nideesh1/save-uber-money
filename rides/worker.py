"""Hatchet worker for nyc-rides (search + ingest). Run: uv run python -m rides.worker"""
import rides.config as cfg

cfg.setup_tracing("nyc-rides-worker")  # agentglow.watch() before any agent runs

import os  # noqa: E402

from rides.workflows import WORKFLOWS, hatchet  # noqa: E402


def main() -> None:
    print("workflows: " + ", ".join(w.name for w in WORKFLOWS) + f" | model {cfg.MODEL}", flush=True)
    hatchet.worker("nyc-rides", workflows=WORKFLOWS, slots=int(os.environ.get("WORKER_SLOTS", "10"))).start()


if __name__ == "__main__":
    main()
