"""Env setup shared by every backend process. Import this FIRST (loads .env, sets Hatchet env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

_tok_file = os.environ.get("HATCHET_CLIENT_TOKEN_FILE")  # docker: token minted into a shared volume
if _tok_file and Path(_tok_file).is_file() and Path(_tok_file).read_text().strip():
    os.environ["HATCHET_CLIENT_TOKEN"] = Path(_tok_file).read_text().strip()
_local_tok = ROOT / "data" / "hatchet" / "token"  # host: compose bind-mounts the minted token here
if not os.environ.get("HATCHET_CLIENT_TOKEN") and _local_tok.is_file():
    os.environ["HATCHET_CLIENT_TOKEN"] = _local_tok.read_text().strip()
os.environ.setdefault("HATCHET_CLIENT_HOST_PORT", "localhost:7278")
os.environ.setdefault("HATCHET_CLIENT_SERVER_URL", "http://localhost:8288")
os.environ.setdefault("HATCHET_CLIENT_TLS_STRATEGY", "none")
os.environ.setdefault("HATCHET_CLIENT_NAMESPACE", "nycrides")

AGENTGLOW_URL = os.environ.get("AGENTGLOW_URL", "http://localhost:8103")
API_PORT = int(os.environ.get("API_PORT", "8420"))
MODEL = os.environ.get("AGENT_MODEL", "mistralai:mistral-large-4")  # planner: picks tools, reads the numbers
RENDER_MODEL = os.environ.get("RENDER_MODEL", "mistralai:mistral-medium-latest")  # renderer: writes the OpenUI (~1.7x faster than Large 4)
SEARCH_INLINE = os.environ.get("SEARCH_INLINE", "0") == "1"

_traced = False


def setup_tracing(service: str, app=None) -> None:
    """agentglow.watch(): LangChain/deepagents + Hatchet + FastAPI spans go to AgentGlow. The elasticsearch client
    emits its own OTel CLIENT spans (db.system=elasticsearch) on the global provider, so ES shows as a satellite."""
    global _traced
    if _traced:
        return
    import agentglow

    os.environ.setdefault("OTEL_PYTHON_INSTRUMENTATION_ELASTICSEARCH_ENABLED", "true")
    agentglow.watch(AGENTGLOW_URL, service_name=service, app=app)
    _traced = True
