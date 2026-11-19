# Automatic backups

Once set up, pdf2phone backs itself up with no help from you:

- **Every night at about 03:30**, every account and document is encrypted and copied to a Hetzner Storage Box.
- **Within a minute of someone deleting their account**, an extra backup runs, so the newest backup always knows about the deletion.
- **Copies older than 30 days are deleted**, as the privacy note on the sign-up page promises.
- **Hetzner snapshots the Storage Box** every day as well. Even someone who broke into your server couldn't delete those.
- **If a backup fails, you get an email.** No email means it's working.

**Restoring never brings back a deleted account.** The site stays offline until everyone who deleted their account after that backup has been erased again.

Commands that start with `new$` are typed on the server. Do this **after** the move to the new server (`docs/MOVE-SERVER.md`), not on the old one.

**Time needed:** about 30 minutes, once.

---

## Setting it up

### 1. Order the Storage Box

In Hetzner's console, order a **Storage Box BX11** (1 TB, about €3.20 a month). For **location**, choose **Helsinki**: your server is in Falkenstein, so a problem at one data centre can't affect both.

### 2. Make a sub-account for pdf2phone

In the Storage Box's settings:

1. Turn on **SSH support**.
2. Under **Sub-accounts**, create one with the folder `pdf2phone`, and tick **SSH support**. Note its **username** (like `u123456-sub1`) and the **password** it shows.

Yes Deputy can have its own sub-account in the same box. Neither app can then see or delete the other's backups.

### 3. Turn on automatic snapshots

In the Storage Box's settings, under **Snapshot plan**: **every day** at **05:00**, keep **7**.

These are made by Hetzner, not by your server. Together with the 30 days restic keeps, a deleted account is fully gone within about 6 weeks, which is what the privacy note says.

### 4. Give the server a key for the Storage Box

Install restic, then make a key that only root uses for backups:

```
new$ sudo apt-get install -y restic
new$ sudo ssh-keygen -t ed25519 -f /root/.ssh/storagebox-pdf2phone -N "" -C "pdf2phone backups"
```

Send the key to the sub-account. Use your username from step 2 in **both** places. It asks for the sub-account's password once:

```
new$ sudo ssh-copy-id -p 23 -s -i /root/.ssh/storagebox-pdf2phone.pub u123456-sub1@u123456-sub1.your-storagebox.de
```

Tell SSH how to reach it:

```
new$ sudo nano /root/.ssh/config
```

Add these lines (with your username), then save:

```
Host storagebox-pdf2phone
    HostName u123456-sub1.your-storagebox.de
    User u123456-sub1
    Port 23
    IdentityFile /root/.ssh/storagebox-pdf2phone
    IdentitiesOnly yes
```

Check it works. It should log in without a password. Type `exit` to leave:

```
new$ sudo sftp storagebox-pdf2phone
```

### 5. Make the encryption password and the settings file

Make a long random password that the server keeps:

```
new$ sudo install -d -m 700 /root/.config/restic
new$ sudo sh -c 'openssl rand -base64 32 > /root/.config/restic/pdf2phone-password'
new$ sudo cat /root/.config/restic/pdf2phone-password
```

**Copy what it prints into your password manager now**, named "pdf2phone backup password". The server needs its copy to make backups by itself. You need yours to open the backups if the server is ever lost. Without it, the backups can't be opened by anyone, you included.

Make the settings file:

```
new$ sudo nano /etc/pdf2phone-backup.env
```

Put in these three lines, then save:

```
RESTIC_REPOSITORY=sftp:storagebox-pdf2phone:restic
RESTIC_PASSWORD_FILE=/root/.config/restic/pdf2phone-password
RESTIC_CACHE_DIR=/var/cache/restic-pdf2phone
```

Set up the encrypted store on the Storage Box:

```
new$ sudo chmod 600 /etc/pdf2phone-backup.env
new$ sudo ~/pdf2phone/deploy/backup/restic.sh init
```

It should say **created restic repository**.

### 6. Switch on the automatic backups

```
new$ sudo ~/pdf2phone/deploy/backup/install.sh
```

It shows when the next nightly backup will run.

### 7. Run the first backup and check it

```
new$ sudo systemctl start pdf2phone-backup.service
new$ sudo ~/pdf2phone/deploy/backup/restic.sh snapshots
```

The list should show one backup, from just now. If `systemctl start` printed an error, see *If something goes wrong* below.

### 8. Check the failure email reaches you

```
new$ sudo systemctl start pdf2phone-backup-failed.service
```

Within a minute you should get an email called **pdf2phone backup FAILED**. That one is only a test, so you can delete it.

That's it. From now on everything runs by itself.

---

## Checking on it (now and then)

List the backups:

```
new$ sudo ~/pdf2phone/deploy/backup/restic.sh snapshots
```

There should be one for each recent day, and some weekly ones going back about a month.

Every Sunday the backup also reads back a sample of what's stored, to catch damage early. A problem there sends the failure email too.

## Restoring

Use this if documents or accounts are lost or damaged, or after a server has been rebuilt.

**The newest backup:**

```
new$ sudo ~/pdf2phone/deploy/backup/pdf2phone-restore.sh
```

**An older one:** list them, then give the start of its ID (the first column):

```
new$ sudo ~/pdf2phone/deploy/backup/restic.sh snapshots
new$ sudo ~/pdf2phone/deploy/backup/pdf2phone-restore.sh 1a2b3c4d
```

What the restore does, in order:

1. Stops the site.
2. Backs up what's there now, first, so a wrong restore can be undone by restoring that one.
3. Puts the chosen backup in place.
4. **Erases again everyone who deleted their account after that backup was made.** It prints how many, never who.
5. Only then starts the site again.

If any step fails, it stops and **leaves the site offline** on purpose. That way a deleted account can never come back online by accident. Read the message, fix the problem, and run it again.

After a restore, anything added since that backup is gone, and people who changed their password since then use their old one.

**Restoring on a brand-new server:** set up the server and pdf2phone (`docs/MOVE-SERVER.md`, steps 2–6, without starting the app). Then do steps 4–5 above, using the password from your password manager instead of making a new one (`sudo nano /root/.config/restic/pdf2phone-password` and paste it in). Skip `restic.sh init`, and run the restore.

## If something goes wrong

See what happened:

```
new$ sudo journalctl -u pdf2phone-backup.service -n 50
```

- **"Permission denied" or a password prompt:** the Storage Box key isn't set up. Repeat step 4.
- **"wrong password or no key found":** `/root/.config/restic/pdf2phone-password` isn't the password the backups were made with. Paste in the one from your password manager.
- **"repository does not exist":** the `init` in step 5 wasn't run, or `RESTIC_REPOSITORY` has a typo.
- **No failure email arrived:** check that email works for the app (sign-up emails), and that `docker compose ps` shows the app healthy.

## Notes

- **What's backed up:** every user's documents and PDFs, and a copy of the account database. Not included: the conversion job list (not needed) and `.env`. Keep your own copy of `.env` in your password manager; the backups can't be restored into a working site without its `PDF2PHONE_RECORD_KEY` and `PDF2PHONE_SECRET`.
- **Time:** backups avoid 02:00–02:15, when Yes Deputy's nightly update runs (see `deploy/SHARED-SERVER.md`).
- **Space:** pdf2phone's backups are a few GB. The BX11's 1 TB leaves plenty of room for Yes Deputy's backups too.
