"""Elasticsearch client built from the repo-root .env (never logs secrets)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RIDES_INDEX = "rides"
ZONES_INDEX = "zones"
INFERENCE_ID = "mistral-embeddings"


@lru_cache(maxsize=1)
def client() -> Elasticsearch:
    """Return a shared Elasticsearch client using ELASTIC_ENDPOINT and ELASTIC_API_KEY."""
    endpoint = os.environ.get("ELASTIC_ENDPOINT")
    api_key = os.environ.get("ELASTIC_API_KEY")
    if not endpoint or not api_key:
        raise RuntimeError("ELASTIC_ENDPOINT and ELASTIC_API_KEY must be set in .env")
    return Elasticsearch(endpoint, api_key=api_key, request_timeout=120, retry_on_timeout=True, max_retries=3)
