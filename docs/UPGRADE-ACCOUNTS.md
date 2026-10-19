# Upgrading the server to accounts (milestone 1)

This upgrade turns the single shared password into **accounts**. Your existing documents move into **your** admin account, keeping the same web addresses, so your phone remembers your place in each one.

It happens in three parts:

1. **Prepare.** Get the new code, add one setting and build the new version, while the old app keeps running.
2. **Rehearse.** Run the whole upgrade on a **copy** of your library, look at the result from your Mac, then throw the copy away. Your real library isn't touched.
3. **Upgrade for real.** About 5 minutes of downtime.

Commands starting `server$` are typed on the server, inside `~/pdf2phone`. Commands starting `mac$` are typed on your Mac.

---

## Part 1: prepare (the reader keeps working)

### Get the new code

If the server folder is connected to GitHub:

```
server$ cd ~/pdf2phone && git pull
```

Otherwise, from the Mac, run the same `rsync` command as in `DEPLOY.md` step 4.

### Add your admin email to .env

```
server$ nano .env
```

Add this line, with your own address:

```
PDF2PHONE_ADMIN_EMAIL=davidjmorrisroe@proton.me
```

**Leave `PDF2PHONE_PASSWORD` as it is.** It's the password for your **first** login after the upgrade. Straight after that login, you choose your own password and the old one stops working.

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

### Build the new version

The old app keeps running while this builds:

```
server$ docker compose build
```

---

## Part 2: rehearse on a copy

### Make a copy of your library

```
server$ docker volume create pdf2phone_rehearsal
server$ docker run --rm -v pdf2phone_library:/from:ro -v pdf2phone_rehearsal:/to alpine sh -c 'cp -a /from/. /to/'
```

`:ro` means the real library is opened **read-only**, so the copy can't change it.

### See what would move (changes nothing)

```
server$ docker compose -f deploy/rehearsal.compose.yml run --rm rehearsal pdf2phone migrate-accounts --dry-run
```

It lists every document it found. Check the number looks right.

### Run the upgrade on the copy

```
server$ docker compose -f deploy/rehearsal.compose.yml run --rm rehearsal pdf2phone migrate-accounts
```

You should see **"Backup written and checked"**, then **"Moved N documents into the admin account"**.

### Start the copy and look at it from your Mac

```
server$ docker compose -f deploy/rehearsal.compose.yml up -d
```

The rehearsal copy isn't on the internet. To reach it, open a **new Terminal window on the Mac** and run:

```
mac$ ssh -L 8091:127.0.0.1:8091 you@your-server
```

Leave that window open. In Safari on the Mac, open **http://localhost:8091**, then:

1. Log in with **your email** and the **old shared password**.
2. You're asked to choose your own password. Pick anything; this is only the copy.
3. Check that **all your documents** are listed, and open a few.

Reading positions won't show in the rehearsal, because they're saved per web address and this is a different one. They come back in the real upgrade, where the addresses are the same.

### Throw the copy away

```
server$ docker compose -f deploy/rehearsal.compose.yml down
server$ docker volume rm pdf2phone_rehearsal
```

Close the SSH window on the Mac.

If anything looked wrong, **stop here** and tell Claude what you saw. Nothing real has changed.

---

## Part 3: upgrade for real (about 5 minutes offline)

```
server$ docker compose down
server$ docker compose run --rm pdf2phone pdf2phone migrate-accounts
server$ docker compose up -d
```

The middle command prints **"Backup written and checked"** and **"Moved N documents"**, just like the rehearsal.

Keep a copy of that backup off the server too:

```
server$ docker run --rm -v pdf2phone_library:/data -v ~/:/out alpine sh -c 'cp /data/_backups/pre-accounts-*.tgz /out/'
mac$ scp you@your-server:~/pre-accounts-*.tgz ~/Desktop/
```

### On your phone

1. Open your reader site. You'll be on the login page, because everyone was logged out.
2. Log in with **your email** and the **old shared password**.
3. Choose **your own password**: at least 12 characters, and a few random words is ideal.
4. Check that your documents are there, open one, and check it opens where you left off.

### Tidy up

Once your own password works, delete the `PDF2PHONE_PASSWORD` line from `.env`:

```
server$ nano .env
server$ docker compose up -d
```

---

## If something goes wrong

**The app keeps restarting, and the logs say "old shared layout".** The migration hasn't run. Do Part 3 again.

**You forgot your new password.** Run this, then type a new password twice (nothing is shown as you type):

```
server$ docker compose exec pdf2phone pdf2phone set-password davidjmorrisroe@proton.me
```

**You want to go back to the old version.** The backup holds the library exactly as it was before:

```
server$ docker compose down
server$ docker run --rm -v pdf2phone_library:/data alpine sh -c 'cd /data && rm -rf _users _db && tar xzf _backups/pre-accounts-*.tgz'
server$ git checkout 33079c4
server$ docker compose up -d --build
```

`33079c4` is the last version before accounts. To come back to the latest version later, run `git checkout main`.
