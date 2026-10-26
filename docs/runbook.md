# Runbook

Keep this current: every setting, procedure or credential location changes
here in the same commit as the change itself.

## Local, no Docker (fastest)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                              # must be green before any push
python scripts/dev.py                  # portal+api+ingest+aggregator on :8000, in-memory
python scripts/seed_traffic.py --url http://localhost:8000/v1/ingest --key devkey --hits 2000
open http://localhost:8000
```
`devkey` → site `dev-site` is the default in `config.py` when `BS_SENSOR_KEYS` is unset.

## Free temporary demo (Render, in-memory)
`render.yaml` deploys `scripts/dev.py` as a free Render web service: no DB, data resets when the
service sleeps (15 min idle) or redeploys. Use it to show the product and test the UI on a public URL.
1. Push the repo to GitHub.
2. render.com → New → Blueprint → pick the repo → Apply. URL: `https://botspotter-demo.onrender.com`.
3. Seed it: `python scripts/seed_traffic.py --url https://botspotter-demo.onrender.com/v1/ingest --key demokey --hits 2000`
4. First request after sleep takes ~30 s (cold start). That is Render's free tier, not us.
5. `CERTIFICATE_VERIFY_FAILED` on macOS = python.org build without root CAs: run `/Applications/Python 3.12/Install Certificates.command` once, or `pip install certifi` (the seed script uses certifi when installed).
Do **not** hand out `demokey` as a real sensor key; the demo forgets everything on restart.

## Full stack with Docker (what runs on the VM)
```bash
cp .env.example .env    # fill every CHANGE value; openssl rand -hex 16 for keys
docker compose up -d --build
docker compose ps       # all healthy
curl -s localhost/healthz; curl -s localhost/v1/snapshot | head -c 300
python scripts/seed_traffic.py --url http://localhost/v1/ingest --key <a key from .env>
```
Schema is applied automatically from `db/schema.sql` on first Postgres start. Topics auto-create.

## Production: shared Oracle A1 VM behind Sharpen's Caddy (current setup)
VM `ubuntu@129.213.153.181` (A1, 1 OCPU / 6 GB, aarch64), key `~/.ssh/sharpen_vm`. Sharpen's Compose
project (`deploy-*` containers) owns :80/:443 via Caddy; BotSpotter runs as a second site behind it.
```bash
deploy/oracle.sh          # from your Mac; idempotent — rerun for every upgrade
```
What it does: clone/pull `main` into `~/botspotter` on the VM → generate `.env` on first run (secrets,
sensor keys, `CADDY_NETWORK`) → `docker compose -f docker-compose.yml -f docker-compose.vm.yml up -d --build`
→ wait for `/healthz` → copy `deploy/Caddyfile.botspotter` next to Sharpen's Caddyfile, append one
`import` line (Caddyfile backed up first), validate, reload Caddy → print the sensor keys.
Cloudflare: DNS `A @ → 129.213.153.181` proxied, `CNAME www → botspotter.app` proxied; SSL mode **Full**
until Caddy has its certificate, then **Full (strict)**. Rate-limit rule on `/v1/ingest` (see below).
Memory budget on this box (`docker-compose.vm.yml`): Redpanda 896M, Postgres 640M, Redis 256M, 3× app 192M ≈ 2.4 GB.
Logs: `ssh -i ~/.ssh/sharpen_vm ubuntu@129.213.153.181 'cd botspotter && docker compose logs -f --tail 100 aggregator'`.

## Oracle Cloud deploy on a dedicated VM (Always Free, Ampere A1, 2 OCPU / 12 GB)
1. Create the instance (Ubuntu 24.04 aarch64). Open TCP 80 and 443 in the VCN security list.
2. `sudo apt install docker.io docker-compose-v2 git -y && sudo usermod -aG docker $USER`
3. `git clone <repo> ~/botspotter && cd ~/botspotter && cp .env.example .env && nano .env`
4. `docker compose up -d --build` (first build ~5 min on A1).
5. `docker compose logs -f aggregator` — expect "aggregator started".
6. Point Cloudflare DNS (proxied/orange cloud) at the VM's public IP. SSL mode: Flexible first, Full once you add Caddy/certbot (TODO in `growth.md` tier 1).
7. Cloudflare → Security → WAF → Rate limiting rule: path `/v1/ingest`, 600 req / 1 min per IP, block 10 min. This is the one free rule — spend it here.
8. Cloudflare → Caching → Cache Rules: respect origin `Cache-Control` for `/v1/*` (default behaviour; verify `cf-cache-status: HIT` on `/v1/snapshot`).

## Sensor keys
- v1: `BS_SENSOR_KEYS="key:site_id,..."` in `.env`. Restart `ingest` after editing (`docker compose up -d ingest`).
- Key requests arrive by email from the `/sensors` form (subject "BotSpotter sensor key: <site>"); the form requires ticking acceptance of `/terms`. Keep the email as the acceptance record. Then `openssl rand -hex 16`, add `hex:their-site`, reply with the key + link to `/sensors`.
- Set up the `hello@botspotter.app` mailbox (or change the address in `web/index.html`) before launch.
- To revoke: remove the pair, restart ingest. Their history stays under the `site_id`.
- Later (`growth.md` tier 2): move to the `sensors` table (`key_hash = sha256(key)`); ingest already accepts sha256-hashed keys in the map.

## Honeypots
1. Buy one domain. Create `hp1.`, `hp2.`, … subdomains, each with its **own** certificate (Cloudflare origin cert per name, or certbot) so each appears in CT logs.
2. Serve anything (a one-page Nginx site) with the `botspotter` log format and Vector (`sensors/vector/`). One key per honeypot so they count as separate sites — that is what lets unknown bots cross the 3-site discovery threshold.
3. Spread them across regions if possible (Oracle has free regions; Fly.io/Vercel free tiers work for the static part but can't run Vector — use a Python middleware honeypot there instead).

## Rotating secrets
- `PG_PASSWORD`: `docker compose exec postgres psql -U botspotter -c "ALTER USER botspotter PASSWORD '...'"`, update `.env`, `docker compose up -d`.
- `BS_VISITOR_SECRET`: safe to rotate any time (only affects "unique today" continuity for one day).
- `BS_NAMING_SALT`: **never** rotate; it renames every bot. If it must happen, bump `NAME_SCHEME_VERSION`, FLUSHDB, truncate `bots`/`events`, announce it.

## Operations
| Task | Command |
|---|---|
| Scale ingest | `docker compose up -d --scale ingest=3` |
| Rebuild Redis from Postgres | see header of `scripts/rebuild_counters.py` |
| Replay last 7 days into a fresh consumer | `docker compose exec redpanda rpk group seek aggregator --to start` (stop aggregator first) |
| Check consumer lag | `docker compose exec redpanda rpk group describe aggregator` |
| Update bot registry | merge the daily `bot/registry-sync` PR, `docker compose up -d --build ingest api` |
| Backup | `docker compose exec postgres pg_dump -U botspotter botspotter \| gzip > backup.sql.gz` (nightly cron, push to R2 — TODO) |
| Logs | `docker compose logs -f --tail 200 <service>` |

## Health checks
- `GET /healthz` on api and ingest. `bs:snapshot.ts` older than 10 s → aggregator is stuck.
- `rpk cluster health` must say `Healthy: true`.
- Redis `INFO memory` `used_memory` < 230 MB (eviction is `noeviction`; a full Redis makes the aggregator error).

## Known gaps (v1)
- No TLS at origin (Cloudflare Flexible). Add Caddy or certbot before accepting third-party sensor data at scale.
- `rebuild_counters.py` requires a stopped aggregator (no scratch prefix).
- The WordPress and Go log-tailer sensors do not exist yet (`growth.md`).
