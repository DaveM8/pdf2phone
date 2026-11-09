# Shared server notes

**For any Claude (or person) working on an app that runs on this server.** Two apps share one machine: **pdf2phone** (Docker) and **Yes Deputy** (systemd services and Elasticsearch, directly on the host). Read this before changing anything on the server that another app could notice: ports, nginx, Docker, firewall, memory/CPU limits, system packages or reboots.

The owner is dyslexic and reads on an iPhone. Keep instructions to them short, numbered, one command per line, and say which machine each command runs on.

This file lives in the pdf2phone repository (`deploy/SHARED-SERVER.md`, on the Mac at `/Users/dave/pdf2phone/deploy/SHARED-SERVER.md`, and on the server at `~/pdf2phone/deploy/SHARED-SERVER.md` after a `git pull`). Each app keeps its own section up to date. Change a section that isn't yours only after asking the owner.

---

## The server

| | |
|---|---|
| Provider | Hetzner dedicated server from the server auction, Falkenstein (FSN1). Ordered 2026-11-09, ready 2026-11-10. Replaces the old shared server (a Xeon E3-1275 v6); both apps move to it. |
| CPU | Intel Core i7-8700: 6 cores / 12 threads |
| Memory | 64 GB (4 × 16 GB DDR4, not ECC) |
| Disks | 2 × 1 TB NVMe in software RAID 1, OS on the mirror, so about 1 TB usable. Check with `cat /proc/mdstat`. Yes Deputy plans for about 430 GB, including Elasticsearch snapshots; pdf2phone needs about 30 GB (mostly its Docker image). That's about half the disk, which leaves room for the 15% free rule (rule 9). |
| Network | 1 Gbit, one stable IPv4 address (shared by both apps' DNS and Brevo's *Authorised IPs*) |
| OS | Ubuntu 24.04 LTS (planned) |
| Web front | nginx on the host; HTTPS from certbot (Let's Encrypt), one certificate per domain |
| Apps | Docker Compose, one project folder per app in the owner's home directory |

---

## Rules every app follows

1. **Everything listens on `127.0.0.1` only.** For Docker, publish ports as `"127.0.0.1:PORT:..."`, never `"PORT:..."`: Docker's published ports skip `ufw`, so `0.0.0.0` would expose an app to the internet past the firewall. Services on the host bind to `127.0.0.1` in their own settings.
2. **nginx is the only thing on ports 80 and 443.** Each app has its own file in `/etc/nginx/sites-available/`, linked from `sites-enabled/`. Don't edit another app's file, and don't put app settings in `nginx.conf` or `conf.d/` where they affect every site. Always run `sudo nginx -t` before `sudo systemctl reload nginx`. A broken file takes **every** site down.
3. **Pick a free port and add it to the table below.** Check with `sudo ss -tlnp | grep ':PORT '` first.
4. **Every service has a memory limit**: `mem_limit` in compose for Docker, `MemoryMax=` in the systemd unit on the host. The totals in the table below must fit in 64 GB with room to spare (aim for ≤ 48 GB). They come to 43 GB now (45 GB once Yes Deputy adds PostgreSQL), so anything that raises a limit or adds a service must be checked against this total first.
5. **Never run `docker volume prune`, `docker system prune --volumes` or `docker compose down -v`.** Volumes hold user data. pdf2phone's documents and accounts exist nowhere else. `docker image prune` is fine.
6. **Never read, print, copy or commit another app's `.env`.** It holds passwords and keys.
7. **Ask the owner before a reboot**, a system-wide upgrade (`apt-get upgrade`), or changing `ufw`, Docker's own settings or nginx itself. These affect both apps.
8. **Code reaches the server through git.** Each app folder is a git checkout. Change code on the Mac, push, then `git pull` on the server. Don't edit files in an app folder on the server.
9. **Keep at least 15% of the disk free.** At 95% full, Elasticsearch blocks all writes and Yes Deputy's nightly update fails. Check with `df -h` (Yes Deputy's data is under `/data`). Docker images and build cache are the usual way the disk fills up unnoticed.
10. **02:00–02:15 is busy.** Yes Deputy's nightly pipeline runs then. Don't schedule builds, backups or other heavy jobs in that window.
11. **Kernel settings are shared.** Don't change `sysctl` values (Elasticsearch needs `vm.max_map_count` ≥ 262144) without asking the owner.

---

## What uses what

| App | Folder | Port (127.0.0.1) | Domain | nginx file | Memory limit | Heavy CPU |
|---|---|---|---|---|---|---|
| pdf2phone | `~/pdf2phone` | 8090 | the owner's `read.` subdomain | `/etc/nginx/sites-available/pdf2phone` | 14 GB | up to 3 conversions at once |
| Yes Deputy | `/data/members_questions` (stable) and `/data/members_questions-beta` (beta) | 8000 (stable), 8001 (beta), 9200 + 9300 (Elasticsearch), 5432 reserved (PostgreSQL, later) | `yesdeputy.com`, `www.yesdeputy.com`, `beta.yesdeputy.com` | `/etc/nginx/sites-available/yesdeputy`, `/etc/nginx/sites-available/yesdeputy-beta` | 29 GB total (31 GB with PostgreSQL) | nightly pipeline at 02:00, about 2 minutes |

Memory total: 14 GB + 29 GB = 43 GB now, and 45 GB once Yes Deputy adds PostgreSQL.

Port 8091 is used only briefly by pdf2phone's upgrade rehearsal (`deploy/rehearsal.compose.yml`). Don't give it to anything permanent. pdf2phone's container listens on 8000 *inside* Docker, but that isn't a host port, so it doesn't clash with Yes Deputy's 8000.

---

## pdf2phone

A private web app that turns PDFs into large-print pages for reading on a phone. It has a few user accounts: the owner and their friends. Source: the private GitHub repository `DaveM8/pdf2phone`. Full details are in its `CLAUDE.md`, `docs/DEPLOY.md` and `docs/MOVE-SERVER.md`.

**On the server:**

- Folder `~/pdf2phone`. Must keep this name: Docker names the data volume after it.
- Container `pdf2phone`, image `pdf2phone:latest`, built on the server (`docker compose up -d --build`). The first build takes 10–20 minutes and downloads a few GB.
- Data: Docker volume **`pdf2phone_library`**. It holds every user's documents and PDFs, the user database (`_db/`) and job records. **This is other people's private data:** don't open, copy or list its contents, and never delete it.
- Settings: `~/pdf2phone/.env` (mode 600). Secrets inside; see rule 6.
- Health check: `curl http://127.0.0.1:8090/healthz` prints `ok`.
- Logs: `cd ~/pdf2phone && docker compose logs --tail 100`.
- Outgoing email: SMTP to Brevo (`smtp-relay.brevo.com:587`), with its own SMTP key. **The Brevo account is shared with Yes Deputy**: the free plan allows 300 a day for both together. pdf2phone stops itself at 100 a day, which leaves room for Yes Deputy. If Brevo's *Authorised IPs* is on, the server's IP is listed once for both apps.
- Backups: there's **no off-server backup yet**, for either app. Yes Deputy's plan lists a Hetzner Storage Box as an open decision; pdf2phone's `pdf2phone_library` volume should go there too (backup commands are in `docs/DEPLOY.md`). Never run backups at 02:00–02:15.
- GitHub access: a read-only deploy key, `~/.ssh/github-pdf2phone`, with a `github-pdf2phone` host alias in `~/.ssh/config`. Use your own key and alias for your app; don't reuse this one.

**Resources:**

- Memory is capped at 14 GB (`PDF2PHONE_MEM_LIMIT` in `.env`). The app itself is small; each Docling conversion takes up to ~4 GB while it runs. Don't raise this or `PDF2PHONE_WORKERS` without checking the memory total (rule 4).
- Disk: the image is several GB, and every rebuild leaves the old one behind. After a rebuild, run `docker image prune -f`. It removes only unused images, never volumes. Don't build between 02:00 and 02:15 (rule 10).
- CPU: when people convert with Docling, up to 3 conversions run at once (`PDF2PHONE_WORKERS=3`), and each one can keep several cores busy for a few minutes. On a 4-core server, use `PDF2PHONE_WORKERS=2`. While the owner runs one of Yes Deputy's manual index rebuilds (8 processes for an hour or more), conversions will be slower. If Yes Deputy needs steady CPU, tell the owner: pdf2phone can be set to `PDF2PHONE_WORKERS=1` or `2`.

**Its nginx file must keep these:**

- `proxy_set_header X-Forwarded-For $remote_addr;` (**overwrite** the header, never `$proxy_add_x_forwarded_for`). The app uses this address to block password guessing; appending would let a visitor fake it.
- `client_max_body_size 100m;` (uploads up to 100 MB), and `proxy_read_timeout 600s;`.
- HTTPS with a redirect from HTTP. Logins only work over HTTPS.

---

## Yes Deputy

A search and question-answering site over the Irish parliamentary record (Dáil, Seanad and committee debates since 1919, and parliamentary questions). It has a small number of approved user accounts. Source: the private GitHub repository `DaveM8/yes-deputy`. Full details are in its `CLAUDE.md` and in the owner's `~/server_migration.org`.

**It doesn't use Docker.** Yes Deputy runs directly on the host: two systemd services (uvicorn) and Elasticsearch installed from Elastic's apt repository. It meets the same rules in the host's own way:

- Everything listens on `127.0.0.1` only (rule 1).
- Every service has a `MemoryMax=` in its systemd unit, instead of `mem_limit` (rule 4).

**On the server:**

- Folders:
  - `/data/members_questions` is the stable site, on branch `main`.
  - `/data/members_questions-beta` is the beta site: a `git worktree` of the same repository, on branch `beta`.
  - The scripts, cron line and services use these absolute paths, so keep them.
- Services:
  - `yesdeputy` (port 8000) and `yesdeputy-beta` (port 8001), each running uvicorn with 2 workers.
  - `elasticsearch`, version 8.19.x. Stay on 8.x: the app's client doesn't support 9.
- Data:
  - **The Elasticsearch index `oireachtas-chunks-v2`** (about 64 GB, 7.8 million documents, behind the alias `oireachtas-search`). Rebuilding it means re-embedding everything with OpenAI: days of running, and real money. Never delete Elasticsearch's data directory or its indices.
  - **`data/users.db`** (SQLite): user accounts. This is other people's personal data: don't open or copy it.
  - Source files: `questions_xml/` (19 GB) and `chunks_all.jsonl` (12 GB), the only way to rebuild the index without starting from scratch.
- Settings:
  - `/data/members_questions/.env` (mode 600) and `/data/members_questions-beta/.env.beta`.
  - Secrets inside; see rule 6.
- Nightly pipeline: cron at 02:00, `bash /data/members_questions/run_pipeline.sh`. It downloads the day's debates, chunks them, embeds them with OpenAI and indexes them, and takes about 2 minutes.
- Health checks:
  - `curl http://127.0.0.1:8000/api/health` (stable)
  - `curl http://127.0.0.1:8001/api/health` (beta)
- Logs:
  - `sudo journalctl -u yesdeputy -f`
  - `sudo journalctl -u yesdeputy-beta -f`
  - pipeline: `/data/members_questions/pipeline_logs/`
- Outgoing email:
  - SMTP to Brevo (`smtp-relay.brevo.com:587`), sending as `noreply@yesdeputy.com`, with its own SMTP key.
  - **It shares the owner's Brevo account with pdf2phone.** The free plan's limit is 300 emails a day for both apps together. Yes Deputy sends a few a week, with at most 3 of each kind per address per hour.
  - If Brevo's *Authorised IPs* is on, the server's IP is listed once for both apps.
- GitHub access: Yes Deputy's own deploy key and host alias (not pdf2phone's).

**Resources:**

| Service | Memory limit | Notes |
|---|---|---|
| Elasticsearch | 22 GB (`MemoryMax=` on `elasticsearch.service`) | 8 GB Java heap, plus the index files Elasticsearch keeps in memory |
| `yesdeputy` | 2 GB | uses about 0.2 GB |
| `yesdeputy-beta` | 1 GB | admin-only; uses about 0.1 GB |
| Nightly pipeline | 4 GB | about 2 minutes a night |
| PostgreSQL (later, for planned lobbying data) | 2 GB | not installed yet |

- Total: 29 GB now, and 31 GB once PostgreSQL is added. With pdf2phone's 14 GB that's 45 GB, under the 48 GB target.
- CPU: steady and light. Each search runs one Elasticsearch query; the language model runs at outside APIs.
  - The busy times are the nightly pipeline, and occasional manual rebuilds that run 8 processes for an hour or more. The owner starts those by hand.
  - pdf2phone's 3 conversions at once are fine alongside it.

**Its nginx files must keep these:**

- `proxy_buffering off;`, `proxy_cache off;` and `proxy_read_timeout 120s;`. Answers stream to the browser token by token (Server-Sent Events), and buffering would hold the whole answer back.
- HTTPS with a redirect from HTTP. There's one certificate per domain: `yesdeputy.com` (with `www`) and `beta.yesdeputy.com`.
- A planned request limit on `/api/auth/`.
  - Its `limit_req_zone` line goes at the top of Yes Deputy's own site file, outside the `server { }` block. nginx reads site files at the `http` level, so nothing goes in `nginx.conf`.
  - The zone is named `yesdeputy_auth`, so it can't clash with a zone pdf2phone adds.

**What pdf2phone (or anyone) must not do to Yes Deputy:**

- **Don't let the disk fill up.** At 95% disk use Elasticsearch blocks all writes to its indices. The nightly pipeline then fails, and the index needs a manual unlock afterwards. Keep at least 15% of the disk free; watch for Docker images and build cache.
- **Don't change `vm.max_map_count`** (it must stay at 262144 or more) or other kernel settings without asking. Elasticsearch won't start below it.
- **Don't restart Elasticsearch casually.** After a restart, search is slow for several minutes while the index loads back into memory.
- Don't use ports 8000, 8001, 9200, 9300 or 5432.
- Don't run anything at 02:00–02:15 that needs the whole CPU or the disk, if it can wait.

---

## Change log

Add a line whenever something shared changes (dates as YYYY-MM-DD).

- 2026-11-09: file created while planning the move to the new FSN1 server (pdf2phone).
- 2026-11-09: Yes Deputy section and table row added (Yes Deputy).
- 2026-11-09: added the shared backup gap (pdf2phone).
- 2026-11-09: server bought: i7-8700 auction server, 2 × 1 TB NVMe, 64 GB, FSN1. The AX42 configuration in `server_migration.org` turned out not to be available at that price (pdf2phone).
- 2026-11-09: shared rules widened for an app without Docker (`MemoryMax=`, host services on 127.0.0.1); added rules 9–11 (disk, 02:00 window, kernel settings); pdf2phone notes on the shared Brevo account and pruning images (pdf2phone).
