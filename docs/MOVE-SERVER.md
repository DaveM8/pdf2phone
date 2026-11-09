# Moving pdf2phone to a new server

This guide moves pdf2phone, with every account and document, from your old server to a new one. Nobody loses anything, and everyone stays logged in.

Commands that start with `mac$` are typed on your Mac. `old$` means on the old server, and `new$` means on the new server. Everywhere you see `you@old-server` and `you@new-server`, use your SSH logins. Everywhere you see `read.example.com`, use your subdomain.

The server is shared with your other app. Rules that both apps follow are in `deploy/SHARED-SERVER.md`.

**Time needed:** about an hour, spread over two days. pdf2phone is offline for about 15 minutes on day 2.

---

## Day 1: get ready (pdf2phone keeps working)

### 1. Shorten the DNS wait

In your domain provider's DNS settings, find the **A record** for `read` (and **AAAA**, if there is one). Set its **TTL** to **300** (5 minutes). Leave the IP address as it is for now.

This makes the switch on day 2 take minutes, not hours.

### 2. Install Ubuntu on the new server (shared: do once, for both apps)

Hetzner's dedicated servers start in the **rescue system**. Log in with the rescue password from the Hetzner Robot email, then run:

```
new$ installimage
```

- Choose **Ubuntu 24.04 LTS**.
- In the settings file, check that `SWRAID 1` and `SWRAIDLEVEL 1` are set. This mirrors the two SSDs, so if one dies nothing is lost.
- Save with **F2**, exit with **F10**, then `reboot`.

Then make your normal login user, copy your SSH key to it, and install the basics:

```
new$ sudo apt-get update && sudo apt-get upgrade -y
new$ sudo apt-get install -y nginx certbot python3-certbot-nginx git ufw
new$ sudo ufw allow OpenSSH && sudo ufw allow 'Nginx Full' && sudo ufw enable
```

Install Docker with step 3 of `docs/DEPLOY.md`.

### 3. Connect the new server to GitHub

The server gets its own **read-only** key, so it can download the code but never change it.

```
new$ ssh-keygen -t ed25519 -f ~/.ssh/github-pdf2phone -N "" -C "pdf2phone deploy key (new server)"
new$ cat ~/.ssh/github-pdf2phone.pub
```

Copy the line it prints. On GitHub, open the `DaveM8/pdf2phone` repository, then **Settings → Deploy keys → Add deploy key**. Paste it, and leave **Allow write access** unticked.

Tell SSH to use that key for GitHub:

```
new$ nano ~/.ssh/config
```

Add these lines, then save:

```
Host github-pdf2phone
    HostName github.com
    User git
    IdentityFile ~/.ssh/github-pdf2phone
    IdentitiesOnly yes
```

Download the code. The folder **must** be called `pdf2phone`, because Docker names the documents volume after it:

```
new$ git clone git@github-pdf2phone:DaveM8/pdf2phone.git ~/pdf2phone
```

### 4. Copy the settings file

`.env` holds your passwords and keys, so it never goes on GitHub. This copies it straight from the old server to the new one through your Mac, without leaving a copy on the Mac:

```
mac$ scp -3 you@old-server:~/pdf2phone/.env you@new-server:~/pdf2phone/.env
new$ chmod 600 ~/pdf2phone/.env
```

Keep it exactly as it is:

- **`PDF2PHONE_SECRET`** must stay the same, or everyone is logged out.
- **`PDF2PHONE_RECORD_KEY`** must stay the same, or old account-deletion records stop matching.
- `PDF2PHONE_WORKERS=3` and `PDF2PHONE_MEM_LIMIT=14g` are right for the new server too.

### 5. Build the app (don't start it yet)

```
new$ cd ~/pdf2phone && docker compose build
```

This takes 10–20 minutes. It stops with a `warmup:` error if a converter won't load.

### 6. Add the nginx site

```
new$ sudo cp ~/pdf2phone/deploy/nginx-pdf2phone.conf /etc/nginx/sites-available/pdf2phone
new$ sudo nano /etc/nginx/sites-available/pdf2phone
```

Change `read.example.com` to your subdomain. Check that the port is the same as `PDF2PHONE_PORT` in `.env` (normally 8090). Then:

```
new$ sudo ln -s /etc/nginx/sites-available/pdf2phone /etc/nginx/sites-enabled/
new$ sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` must say **syntax is ok**. HTTPS is added on day 2, once the domain points at the new server.

### 7. Check Brevo

If you turned on **Authorised IPs** in Brevo (Settings → Security), add the new server's IP address there. Otherwise sign-up and password emails will fail after the move.

---

## Day 2: the move (about 15 minutes offline)

### 8. Stop the old app and back it up

Stopping first means nothing changes while it's copied.

```
old$ cd ~/pdf2phone && docker compose stop
old$ docker run --rm -v pdf2phone_library:/data -v ~/:/backup alpine tar czf /backup/pdf2phone-backup.tgz -C /data .
old$ ls -lh ~/pdf2phone-backup.tgz
```

### 9. Copy the backup to the new server

```
mac$ scp -3 you@old-server:~/pdf2phone-backup.tgz you@new-server:~/
```

### 10. Restore it and start the app

```
new$ cd ~/pdf2phone && docker compose create
new$ docker run --rm -v pdf2phone_library:/data -v ~/:/backup alpine sh -c "tar xzf /backup/pdf2phone-backup.tgz -C /data && chown -R 1000:1000 /data"
new$ docker compose up -d
new$ docker compose ps
new$ curl http://127.0.0.1:8090/healthz
```

The first line creates the empty documents volume, the second fills it, and the third starts the app. Wait for **healthy**. `curl` should print **ok**.

### 11. Point the domain at the new server

In your DNS settings, change the `read` **A record** to the new server's IP address (and **AAAA**, if you have one). Wait 5 minutes, then check it from the Mac:

```
mac$ dig +short read.example.com
```

It should print the **new** IP address.

### 12. Add HTTPS

```
new$ sudo certbot --nginx -d read.example.com
```

Choose to **redirect** HTTP to HTTPS when it asks.

### 13. Check it works

On your iPhone, open the home-screen icon. You should still be logged in, with all your documents and reading positions. Then check that:

- a document opens,
- uploading a small PDF with **Fast** converts it,
- `/admin` shows both users.

---

## Afterwards

### If something goes wrong

The old server still has everything. Point the DNS record back to the old IP address, then:

```
old$ cd ~/pdf2phone && docker compose start
```

### Once you're happy (within a week)

The backup file holds your friends' documents, so don't keep extra copies lying around:

```
old$ rm ~/pdf2phone-backup.tgz
new$ rm ~/pdf2phone-backup.tgz
```

Then:

1. On GitHub, under **Settings → Deploy keys**, delete the **old** server's key.
2. Cancel the old server in Hetzner Robot. This erases everything on it, including the old copy of the documents.
3. Set the DNS TTL back to its old value (e.g. 3600).

From now on, update the new server the usual way: see *Updating after changes* in `docs/DEPLOY.md`.
