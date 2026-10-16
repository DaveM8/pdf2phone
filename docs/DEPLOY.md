# Putting pdf2phone on your Hetzner server

This guide assumes:

- your server runs **Ubuntu**,
- it **already hosts other websites** through **nginx** (step 2 checks this),
- you have a **domain**, and will use a subdomain such as `read.example.com`.

Everywhere you see `read.example.com`, use your own subdomain. Everywhere you see `you@your-server`, use the login you normally use for SSH.

Commands that start with `mac$` are typed on your Mac. Commands that start with `server$` are typed on the server, after logging in with `ssh you@your-server`.

---

## 1. Point the subdomain at the server

In your domain provider's DNS settings, add an **A record**:

| Name | Type | Value |
|---|---|---|
| `read` | A | your server's IP address |

If the server has an IPv6 address too, also add an **AAAA** record with it.

DNS changes can take a few minutes to an hour to work.

## 2. Check what the server is running

```
server$ sudo ss -tlnp | grep -E ':(80|443) '
```

- If the lines mention **nginx**, follow this guide as written.
- If they mention **apache2**, **caddy** or **traefik**, follow everything except step 7 and use the matching box in step 7b.

Check memory. Docling needs about **4 GB free**:

```
server$ free -h
```

Look at the **available** column on the `Mem:` line. If it's under 4 GB, the app still works, but set the default converter to Fast and use Docling only for single papers. See "Out of memory" at the end.

Check that port 8090 is free. Nothing should be printed:

```
server$ sudo ss -tlnp | grep ':8090 '
```

If something is printed, pick another number (for example 8091) and use it for `PDF2PHONE_PORT` in step 5 and in the nginx file in step 7.

## 3. Install Docker (skip if `docker --version` already works)

```
server$ sudo apt-get update
server$ sudo apt-get install -y ca-certificates curl
server$ sudo install -m 0755 -d /etc/apt/keyrings
server$ sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
server$ echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
server$ sudo apt-get update
server$ sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
server$ sudo usermod -aG docker $USER
```

Log out (`exit`) and log back in, so the last line takes effect. Then `docker --version` should work without `sudo`.

## 4. Copy the project to the server

On the Mac:

```
mac$ rsync -av --exclude .venv --exclude markitdown --exclude .git --exclude '*.html' ~/pdf2phone/ you@your-server:~/pdf2phone/
```

The `'*.html'` exclusion only skips generated pages such as the summary. The templates the app needs end in `.j2`, so they're still copied.

## 5. Set your password

```
server$ cd ~/pdf2phone
server$ cp .env.example .env
server$ python3 -c 'import secrets; print(secrets.token_hex(32))'
server$ nano .env
```

In `nano`:

- set `PDF2PHONE_PASSWORD` to the password you want to log in with,
- paste the long random line printed above after `PDF2PHONE_SECRET=`,
- save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

Make the file readable only by you:

```
server$ chmod 600 .env
```

## 6. Build and start the app

```
server$ docker compose up -d --build
```

The **first build takes 10–20 minutes**. It downloads PyTorch and Docling's models (a few GB) so later conversions start straight away.

Check that it's running:

```
server$ docker compose ps
server$ curl http://127.0.0.1:8090/healthz
```

`docker compose ps` should say **healthy** (it can say `starting` for the first minute). `curl` should print **ok**.

The app only listens on `127.0.0.1`, so it isn't reachable from the internet yet. The next step connects it.

## 7. Connect it to nginx and add HTTPS

```
server$ sudo cp ~/pdf2phone/deploy/nginx-pdf2phone.conf /etc/nginx/sites-available/pdf2phone
server$ sudo nano /etc/nginx/sites-available/pdf2phone
```

Change `read.example.com` to your subdomain. If you changed the port in step 2, change `8090` too. Save and exit.

```
server$ sudo ln -s /etc/nginx/sites-available/pdf2phone /etc/nginx/sites-enabled/
server$ sudo nginx -t
server$ sudo systemctl reload nginx
```

`nginx -t` must say **syntax is ok** before you reload.

Now add HTTPS (the padlock):

```
server$ sudo certbot --nginx -d read.example.com
```

If `certbot` isn't installed: `sudo apt-get install -y certbot python3-certbot-nginx`.

When it asks, choose to **redirect** HTTP to HTTPS. The login cookie only works over HTTPS.

### 7b. If the server uses something other than nginx

**Apache.** Run `sudo a2enmod proxy proxy_http headers`, then add a site:

```
<VirtualHost *:80>
    ServerName read.example.com
    ProxyPreserveHost On
    ProxyPass / http://127.0.0.1:8090/
    ProxyPassReverse / http://127.0.0.1:8090/
    RequestHeader set X-Forwarded-Proto expr=%{REQUEST_SCHEME}
    # Drop any address the visitor made up; Apache then adds the real one.
    RequestHeader unset X-Forwarded-For early
    LimitRequestBody 104857600
    ProxyTimeout 600
</VirtualHost>
```

Then run `sudo certbot --apache -d read.example.com`.

**Caddy.** Add this to the Caddyfile and reload. Caddy adds HTTPS by itself:

```
read.example.com {
    request_body {
        max_size 100MB
    }
    reverse_proxy 127.0.0.1:8090
}
```

## 8. Use it from your phone

1. Open `https://read.example.com` in **Safari** and log in.
2. Tap **Share**, then **Add to Home Screen**. You get an "Aa" icon that opens the library like an app.
3. Read `docs/USING.md` (or `docs/USING.html`) for everyday use.

---

## Updating after changes

On the Mac, run the same `rsync` command as in step 4. Then:

```
server$ cd ~/pdf2phone && docker compose up -d --build
```

Your documents are kept, because they live in a Docker volume, not in the project folder. Later builds are much faster than the first.

## Backups

All documents (the converted pages and the original PDFs) live in the Docker volume `pdf2phone_library`. To copy them into a single file:

```
server$ docker run --rm -v pdf2phone_library:/data -v ~/:/backup alpine tar czf /backup/pdf2phone-backup.tgz -C /data .
mac$ scp you@your-server:~/pdf2phone-backup.tgz ~/Desktop/
```

## Looking at logs

```
server$ cd ~/pdf2phone && docker compose logs --tail 100 -f
```

Press **Ctrl+C** to stop watching. The app keeps running.

## Common problems

**502 Bad Gateway.** The app isn't running, or nginx points at the wrong port. Run `docker compose ps` and `curl http://127.0.0.1:8090/healthz`, and check that the port in the nginx file matches `PDF2PHONE_PORT`.

**"413 Request Entity Too Large" when uploading.** Raise `client_max_body_size` in the nginx file *and* `PDF2PHONE_MAX_MB` in `.env`. Then run `sudo systemctl reload nginx` and `docker compose up -d`.

**Login works but sends you straight back to the login page.** The site is being opened over plain `http://`. Use `https://`, and make sure certbot set up the redirect (step 7).

**A conversion says "Failed".** Open the ⋯ menu on the document and choose **Convert again** with a different converter. The logs show the full error.

**Out of memory, or the app restarts during a Docling conversion.** In `.env`, set `PDF2PHONE_DEFAULT_ENGINE=pymupdf`, then run `docker compose up -d`. Use Docling only when Fast gets a document wrong. You can also lower `mem_limit` in `docker-compose.yml` to protect your other sites. A conversion that hits the limit then fails on its own instead of slowing the whole server.

**The build fails while installing torch.** On some processor types the CPU-only PyTorch download can be missing. In the `Dockerfile`, delete the `--index-url https://download.pytorch.org/whl/cpu` part of the `torch` line and build again. The image will be bigger, but it will work.

**Forgot the password.** Edit `.env`, then run `docker compose up -d`. Changing the password logs out every device.
