# support-triage-agent — slim, API-based (Claude/Notion/Slack all remote; no local model).
FROM python:3.12-slim

# No .pyc, unbuffered stdout so `docker logs` is live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPPORT_DATA_DIR=/app/data \
    TICKETS_DB=/app/data/tickets.db

WORKDIR /app

# Install deps first (layer cache) — copy only what pip needs.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

# Runtime data (SQLite, logs) lives here — mount a volume over it to persist.
COPY data/sop_playbook.md ./data/sop_playbook.md
RUN mkdir -p /app/data

# Default: run the live Gmail->Slack loop every 5 min. Override in compose.
# (Notion auto-write needs NOTION_TOKEN; add --write once that's set.)
CMD ["python", "-m", "support_triage_agent.gmail_fetch", "--loop", "--interval", "300"]
