# Turning on sign-up

This adds the **Ask for an account** page, so friends can ask for an account and you approve them.

You need **Brevo set up** first: your domain is authenticated, and the SMTP lines are in `.env`. There's about **5 minutes of downtime** while the app restarts.

Commands are typed **on the server**. Log in first with `ssh you@your-server`.

## Step 1: Add two lines to .env

Emails contain links back to the app. The app takes the address for those links from `PDF2PHONE_PUBLIC_URL`, never from the visitor's browser, so nobody can trick it into sending a link to their own website.

```
cd ~/pdf2phone
nano .env
```

Check that these lines are there, from the Brevo setup:

```
PDF2PHONE_SMTP_HOST=smtp-relay.brevo.com
PDF2PHONE_SMTP_PORT=587
PDF2PHONE_SMTP_USER=...
PDF2PHONE_SMTP_PASSWORD=...
PDF2PHONE_MAIL_FROM=biggerthandog <noreply@yourdomain.com>
```

Add this line, with **your** app's address. It must start with `https://`, with nothing after the domain:

```
PDF2PHONE_PUBLIC_URL=https://read.yourdomain.com
```

Then make a new random key:

```
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Add it on a line like this, pasting the long line it printed:

```
PDF2PHONE_RECORD_KEY=paste-the-long-line-here
```

This key is for when someone deletes their account. All that's kept is a one-way code made from their email address with this key. **The app won't start without it.** Don't change it later: old records would no longer match.

Save with **Ctrl+O**, **Enter**, then exit with **Ctrl+X**.

## Step 2: Update and restart

```
git pull
docker compose up -d --build
docker compose ps
```

Wait until it says **healthy**.

**If it keeps restarting,** run `docker compose logs --tail 20`. A message starting **"Email is half set up"**, **"Set PDF2PHONE_PUBLIC_URL"** or **"Set PDF2PHONE_RECORD_KEY"** says which line in `.env` to fix.

## Step 3: Try it yourself

Use a **second email address** of yours, not your Proton admin address. Signing up with an address that already has an account deliberately does nothing.

1. In a **private browser window**, open your app. The login page now says **"No account? Ask for one"**.
2. Fill in the form with the second address.
3. Wait at least **3 seconds** before pressing the button. Very fast forms are treated as bots.
4. You should see **"Check your email"**.
5. Open the email in the second inbox (look in spam too) and tap the link, then **Confirm my email**.
6. Your **Proton inbox** gets **"Someone asked for a pdf2phone account"**.
7. Log in as yourself. The library page shows **Admin (1 waiting)**. Open it and tap **Approve**.
8. The second inbox gets **"Your pdf2phone account is ready"**.
9. Log in with the second address in the private window. You see an empty library.

## Step 4: Try "Forgot your password?"

1. In the private window, tap **Log out** (on the Account page), then **Forgot your password?** on the login page.
2. Type the test account's email, wait 3 seconds, and tap **Email me a link**.
3. The second inbox gets **"Choose a new password"**. Tap the link, choose a new password, and save it.
4. You're back on the login page with **"Password saved"**. Log in with the new password.

The link works once, for one hour. Asking for a link with an address that has no account shows the same page, but sends nothing.

## Step 5: Try removing the test account

1. As yourself, open **Admin**. Under **People with an account**, the test account has a red **Remove** button.
2. Tap it and confirm. You see **"Removed. Their documents and details have been deleted."**
3. In the private window, reload: the test account is logged out and can't log in again.

Your friends can also delete their own account, from **Delete my account** at the bottom of their Account page. It erases exactly the same things.

## If an email doesn't arrive

- **Look in spam first.** New senders sometimes land there at the start.
- **See what the app says:**

```
docker compose logs --tail 50 | grep "sending email"
```

- **"Authentication"** in the message means the SMTP login or key in `.env` is wrong. Copy them again from Brevo's SMTP page, then run `docker compose up -d`.
- **No message at all** means the app sent it. Check Brevo's **Transactional** log to see what happened to it.
- **"daily email limit reached"** means the app's own safety limit of 100 emails a day was reached. It resets after 24 hours.

## Turning sign-up off again

Put a `#` at the start of the `PDF2PHONE_SMTP_HOST` line in `.env`, then run `docker compose up -d`. The sign-up page then says **"Sign-up is closed"**. Existing accounts keep working.
