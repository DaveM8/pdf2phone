# pdf2phone accounts: goals

Date: 2026-10-16

## Status

Signed off 2026-10-16. Working on milestone 1.

## Problem

pdf2phone has one shared password and one library. The owner wants to let a
few friends use it now, and possibly the public later, so it needs proper
accounts: a secure sign-up and login, owner approval of every new account, and
private libraries. It also needs real deletion of personal data.

## Goal

Friends can request an account through a sign-up form that stops bots. The
owner gets an email at their proton address and approves or denies them from an
admin page. Approved users log in securely with their own password and see only
their own private library. The owner can remove a user, and users can delete
their own account, which erases all their data and personal details. All that's
kept is a keyed hash of the email address they signed up with, the date their
data was removed, and whether the user or the admin removed it.

## Success criteria

- [ ] 1. A bot submitting the sign-up form is rejected and no email is sent (test submits like a bot).
- [ ] 2. A real sign-up shows "waiting for approval", and the owner receives exactly one email at davidjmorrisroe@proton.me with a link to the admin page.
- [ ] 3. The admin page opens only for the owner; a logged-in friend gets "not allowed".
- [ ] 4. Approve → the friend can log in. Deny → they can't, and their sign-up details are erased.
- [x] 5. Two users each upload a PDF; neither can open the other's document, even by typing its exact address (test).
- [ ] 6. "Delete my account" removes their documents, PDFs and every personal detail from disk and database; only one row remains (keyed hash + date + "deleted by user"). A test searches everything for their email and name.
- [ ] 7. Same as 6 when the owner removes them; the row says "removed by admin".
- [ ] 8. The owner's existing library and reading positions still work after the upgrade, now under their admin account.
- [ ] 9. An approved friend receives an email saying they can log in.

## Non-goals (not in this version)

- Two-factor login
- Storage limits per user
- Sign in with Apple/Google
- Sharing documents between users
- Open sign-up without approval
- More than one admin
- A paid email service (comes with a public launch)

In scope beyond the criteria above: a forgot-password email.

## Constraints

- Runs on the owner's Hetzner Ubuntu server, which also hosts other sites. Docker behind the existing nginx; app on 127.0.0.1:8090.
- One conversion at a time (Docling uses ~3 GB of memory); the queue is shared by all users.
- Email goes out through a new Gmail account made just for the app, using an app password (needs 2-Step Verification), with SMTP settings in .env so a paid service can replace it later without code changes. Free; Gmail allows ~500 emails a day.
- A few minutes of downtime during deployment is fine.
- No deadline: ship when it's right.
- Develop on the Mac, push to GitHub, pull and rebuild on the server. Docker can't be built on the Mac.

## Risks and irreversible steps

- Moving the existing library into the owner's account. Protection: the upgrade refuses to run without a backup file, and the move is rehearsed on a copy of the real library on the server and checked by the owner before it runs for real.
- Account deletion is irreversible by design. Deleted data stays in nightly backups for up to 14 days, and the sign-up page says so.
- The Gmail app password can read that account's mail; it's a dedicated empty account, kept only in .env.
- The keyed-hash key lives in .env, outside the database. If it's lost, old deletion records can't be checked against an email any more.

## Decisions

| Decision | Chosen | Alternatives | Why |
|---|---|---|---|
| Purpose of the deletion record | Proof that a deletion happened | — | Accountability without exposing who it was |
| Email hash | Keyed hash (HMAC-SHA256, key in .env) | Plain SHA-256 | Table access alone can't match a known email |
| Admin removal keeps | Same as self-deletion, plus who removed it | Nothing; hash + reason | One rule; no free-text personal data |
| Bot protection | Confirm-email step + hidden trap fields + timing check + per-address limit | + Cloudflare Turnstile; traps only | No third parties; fake addresses never reach the owner |
| Account storage | SQLite file in the existing Docker volume | PostgreSQL | No extra service; backed up with documents |
| Login identifier | Email address | Username + email | Less personal data to store and delete |
| Link in the owner's sign-up email | Opens the admin page (login required) | One-click approve links | Safe if forwarded or pre-opened by scanners |
| Password hashing | Argon2id (argon2-cffi) | bcrypt | Current best practice |
| Backups | Nightly, kept 14 days, stated on the sign-up page | 7 or 30 days; none; forever | Deleted data fully gone within two weeks |
| Admin account | Created from PDF2PHONE_ADMIN_EMAIL on first start; inherits the existing library; new personal password set at first login; shared password retired | One-off command | No extra steps on the server |
| Email sending | New dedicated Gmail account + app password, SMTP settings in .env | Personal Gmail; Proton Bridge; paid service | Free now, easy to swap later |
| Migration safety | Backup + rehearsal on a copy first | Backup only | Protects the owner's documents and positions |
| Password rule | At least 12 characters | 16 characters; + breached-password check | Passphrases; no outside calls |
| Session length | 30 days | 180 days | Shorter exposure if a phone is lost |

## Open questions

- [x] Password rules: at least 12 characters for every account; passphrases encouraged, no symbol/capital rules. (decided 2026-10-16)
- [x] Sessions last 30 days (was 180); a password change still logs out every device. (decided 2026-10-16)
- [ ] What the sign-up form asks for besides email and password, for example a name or a "who are you" note so the owner recognises friends. Every extra field is more personal data. (decide by: milestone 2)
- [ ] AGPL: PyMuPDF is AGPL, so once friends use the service, they must be offered the source code (a link to the repository, or giving them access). (decide by: milestone 2, before any friend signs up)
- [ ] Where nightly backups are stored: the same server, or also somewhere else. (decide by: milestone 4)
- [ ] Wording of the privacy note on the sign-up page. (decide by: milestone 2)

## Milestones

### Milestone 1: accounts and private libraries (first)

- [x] SQLite user database; log in by email with Argon2id passwords
- [x] Each user's documents stored and served separately
- [x] Admin account created from PDF2PHONE_ADMIN_EMAIL; `pdf2phone migrate-accounts` moves the current library into it after a checked backup (rehearsal steps in docs/UPGRADE-ACCOUNTS.md)
- [x] Old shared password retired after the owner sets a personal one
- [x] Added: `pdf2phone set-password` emergency reset (editing .env no longer resets a password)
- [ ] On the server: rehearsal, real upgrade, and the owner checks documents and reading positions on their phone (check 8)

Done when: checks 5 and 8 pass, and the owner logs in on their phone with their new password and sees all their documents and reading positions.

### Milestone 2: sign-up and approval

Checks 1–4 and 9.

### Milestone 3: deletion and password reset

Checks 6 and 7, plus forgot-password.

### Milestone 4: backups

Nightly backups kept 14 days; retention stated on the sign-up page.
