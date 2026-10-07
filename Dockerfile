# One image for every nyc-rides Python service (worker, api, agentglow); compose picks the command.
FROM python:3.12-slim
# libgomp for LightGBM (rides.ml)
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
ENV PATH=/opt/venv/bin:$PATH UV_NO_SYNC=1
CMD ["python", "-m", "rides.worker"]
