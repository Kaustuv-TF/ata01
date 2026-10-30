FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev --extra postgres
ENV ATA_MARKET_MODE=paper ATA_LIVE_TRADING_ENABLED=false ATA_BROKER_COMPLIANCE_GATE=false TZ=Asia/Kolkata
USER nobody
ENTRYPOINT ["uv", "run", "--no-sync", "ata"]
CMD ["simulate", "--days", "30"]
