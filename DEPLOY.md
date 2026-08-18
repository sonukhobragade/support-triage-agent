# Deploy — Hostinger KVM VPS (Docker)

The agent runs as a Docker container (`--loop`, polls Gmail every 5 min → Slack,
optional Notion). 24/7, survives reboot.

Two paths:
- **A. Dokploy (recommended)** — `docker-compose.yml` is already Dokploy-built;
  env from Dokploy's Environment tab, redeploy on Git push / click. See below.
- **B. rsync + raw Docker (no GitHub)** — copy the code from your workstation and
  run compose by hand. Older flow, kept as fallback (sections 0–6).

Nothing here is Hostinger-specific beyond section 0: any KVM VPS with Docker
works. For Dokploy itself, see its own docs at https://docs.dokploy.com.

---

## A. Dokploy

1. VPS + Dokploy installed (one-time) — section 0 below, or Dokploy's install
   guide at https://docs.dokploy.com/docs/core/installation.
2. Dokploy UI → Create Service → **Compose** → point at this repo (Git) or paste
   `docker-compose.yml`.
3. **Environment** tab — add the keys from section 3 below (ROTATED secrets).
   Dokploy writes them to `.env` beside the compose file.
4. **Deploy**. Logs tab → expect `[loop] starting — polling every 300s`.
5. RAG grounding: ship `data/mail_extract.jsonl.gz` to the `cs-data` volume
   (volume persists across redeploys), else drafts ground on SOP only.
6. Notion auto-write: add `--write` to the `command:` in `docker-compose.yml`
   once `NOTION_TOKEN` is set → redeploy.

---

## B. rsync + raw Docker (no GitHub) — fallback

Code copied straight from the Mac via rsync — no GitHub.

## 0. One-time: VPS setup (Hostinger hPanel)
- OS: **Ubuntu 22.04** (or the **Docker** template — then skip step 2).
- Note the **IP** and root SSH access.

## 1. Copy code to the VPS (run on the MAC)
```bash
cd /path/to/projects
rsync -av \
  --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
  --exclude 'data/*.db' --exclude 'data/*.log' --exclude '.env' \
  support-triage-agent/  root@<VPS_IP>:/root/support-triage-agent/
```
`.env` is intentionally NOT copied — create it fresh on the VPS (step 3).
Treat any secret that has ever left a `.env` file as burned and mint new ones.

## 2. Install Docker (on the VPS; skip if Docker template)
```bash
ssh root@<VPS_IP>
curl -fsSL https://get.docker.com | sh
docker --version
```

## 3. Create .env on the VPS (rotated secrets)
```bash
cd /root/support-triage-agent
nano .env
```
Required keys:
```
ANTHROPIC_API_KEY=...
GMAIL_USER=support@example.com
GMAIL_APP_PASSWORD=...          # NEW app password (rotate the dev one)
SLACK_WEBHOOK_URL=...           # webhook for the channel you want alerts in
SLACK_ENABLED=true
SUPPORT_ADDRESSES=support@example.com
CLASSIFY_MODEL=claude-haiku-4-5-20251001
DRAFT_MODEL=claude-haiku-4-5-20251001
# Notion auto-write (only once an owner-created integration token exists):
# NOTION_TOKEN=ntn_...
# NOTION_DB_ID=<the 32-character id from your Notion database URL>
```

## 4. (Optional) ship RAG grounding
Without `data/mail_extract.jsonl.gz` (or `data/reply_library.json`) on the box,
drafts ground on SOP only — still works, less "sounds like us". To keep full RAG,
copy the extract to the data volume after first launch:
```bash
# on the MAC
rsync -av support-triage-agent/data/mail_extract.jsonl.gz root@<VPS_IP>:/root/support-triage-agent/data/
```
(The compose mounts `data/` into the container, so this is picked up.)

## 5. Launch
```bash
cd /root/support-triage-agent
docker compose up -d --build
docker compose logs -f cs-agent          # expect: [loop] starting — polling every 300s
```

## 6. Verify
- Mail a test to support@example.com → Slack card within 5 min.
- Reboot test: `reboot`, then after it's back `docker ps` shows cs-agent up
  (`restart: unless-stopped`).

## Operate
```bash
docker compose logs -f cs-agent     # live logs
docker compose restart cs-agent     # restart
docker compose down                 # stop
docker compose up -d --build        # redeploy after an rsync of new code
```

## Enable Notion auto-write (later)
Once an Example Co Notion **owner** creates an integration, shares the
"Customer Support Tickets" DB with it, and you put `NOTION_TOKEN` in `.env`:
edit `docker-compose.yml` → uncomment the `command:` line with `--write` →
`docker compose up -d`.

## Adding more agents (fleet)
Each new agent (stock/Hermes, news, ...) = its own image + `.env` + a service
block in `docker-compose.yml`. One VPS hosts the fleet. See the commented blocks
there.
