# Shared server notes

**For any Claude (or person) working on an app that runs on this server.** Two apps share one machine. Read this before changing anything on the server that another app could notice: ports, nginx, Docker, firewall, memory/CPU limits, system packages or reboots.

The owner is dyslexic and reads on an iPhone. Keep instructions to them short, numbered, one command per line, and say which machine each command runs on.

This file lives in the pdf2phone repository (`deploy/SHARED-SERVER.md`, on the Mac at `/Users/dave/pdf2phone/deploy/SHARED-SERVER.md`, and on the server at `~/pdf2phone/deploy/SHARED-SERVER.md` after a `git pull`). The other app's section below is for that app to fill in. Change a section that isn't yours only after asking the owner.

---

## The server

| | |
|---|---|
| Provider | Hetzner dedicated server, Falkenstein (FSN1). Moving here in the week of 9 November 2026. |
| CPU | Intel Core i7-8700: 6 cores / 12 threads |
| Memory | 64 GB (4 × 16 GB DDR4, not ECC) |
| Disks | 2 × 1 TB NVMe, intended as software RAID 1 (check with `cat /proc/mdstat`) |
| Network | 1 Gbit |
| OS | Ubuntu 24.04 LTS (planned) |
| Web front | nginx on the host; HTTPS from certbot (Let's Encrypt), one certificate per domain |
| Apps | Docker Compose, one project folder per app in the owner's home directory |

---

## Rules every app follows

1. **Containers listen on `127.0.0.1` only.** Publish ports as `"127.0.0.1:PORT:..."`, never `"PORT:..."`. Docker's published ports skip `ufw`, so `0.0.0.0` would expose an app to the internet past the firewall.
2. **nginx is the only thing on ports 80 and 443.** Each app has its own file in `/etc/nginx/sites-available/`, linked from `sites-enabled/`. Don't edit another app's file, and don't put app settings in `nginx.conf` or `conf.d/` where they affect every site. Always run `sudo nginx -t` before `sudo systemctl reload nginx`. A broken file takes **every** site down.
3. **Pick a free port and add it to the table below.** Check with `sudo ss -tlnp | grep ':PORT '` first.
4. **Every container has a memory limit** (`mem_limit` in compose), and the totals in the table below fit in 64 GB with room to spare (aim for ≤ 48 GB).
5. **Never run `docker volume prune`, `docker system prune --volumes` or `docker compose down -v`.** Volumes hold user data. pdf2phone's documents and accounts exist nowhere else. `docker image prune` is fine.
6. **Never read, print, copy or commit another app's `.env`.** It holds passwords and keys.
7. **Ask the owner before a reboot**, a system-wide upgrade (`apt-get upgrade`), or changing `ufw`, Docker's own settings or nginx itself. These affect both apps.
8. **Code reaches the server through git.** Each app folder is a git checkout. Change code on the Mac, push, then `git pull` on the server. Don't edit files in an app folder on the server.

---

## What uses what

| App | Folder | Port (127.0.0.1) | Domain | nginx file | Memory limit | Heavy CPU |
|---|---|---|---|---|---|---|
| pdf2phone | `~/pdf2phone` | 8090 | the owner's `read.` subdomain | `/etc/nginx/sites-available/pdf2phone` | 14 GB | up to 3 conversions at once |
| *other app* | *fill in* | *fill in* | *fill in* | *fill in* | *fill in* | *fill in* |

Port 8091 is used only briefly by pdf2phone's upgrade rehearsal (`deploy/rehearsal.compose.yml`). Don't give it to anything permanent.

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
- Outgoing email: SMTP to Brevo (`smtp-relay.brevo.com:587`), at most 100 a day. If Brevo's *Authorised IPs* is on, the server's IP must be listed there.
- GitHub access: a read-only deploy key, `~/.ssh/github-pdf2phone`, with a `github-pdf2phone` host alias in `~/.ssh/config`. Use your own key and alias for your app; don't reuse this one.

**Resources:**

- Memory is capped at 14 GB (`PDF2PHONE_MEM_LIMIT` in `.env`). The app itself is small; each Docling conversion takes up to ~4 GB while it runs.
- CPU: when people convert with Docling, up to 3 conversions run at once (`PDF2PHONE_WORKERS=3`), and each one can keep several cores busy for a few minutes. If the other app needs steady CPU, tell the owner: pdf2phone can be set to `PDF2PHONE_WORKERS=1` or `2`.

**Its nginx file must keep these:**

- `proxy_set_header X-Forwarded-For $remote_addr;` (**overwrite** the header, never `$proxy_add_x_forwarded_for`). The app uses this address to block password guessing; appending would let a visitor fake it.
- `client_max_body_size 100m;` (uploads up to 100 MB), and `proxy_read_timeout 600s;`.
- HTTPS with a redirect from HTTP. Logins only work over HTTPS.

---

## Other app

*For the other app's Claude: fill this in, in the same shape as the pdf2phone section, and add your row to the table above. Include anything pdf2phone must not do to your app.*

---

## Change log

Add a line whenever something shared changes (dates as YYYY-MM-DD).

- 2026-11-09: file created while planning the move to the new FSN1 server (pdf2phone).
