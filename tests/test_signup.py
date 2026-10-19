"""Milestone 2: sign-up, email confirmation and owner approval (GOALS.md checks 1-4 and 9)."""

import re
import sqlite3
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pdf2phone.web import app as app_mod  # noqa: E402
from pdf2phone.web.app import create_app  # noqa: E402
from pdf2phone.web.mail import FakeMailer  # noqa: E402
from pdf2phone.web.settings import Settings  # noqa: E402
from test_web import ADMIN, ADMIN_PW, SHARED, add_friend, login, ready_admin  # noqa: E402

PUBLIC = "https://read.example.com"
RECORD_KEY = "r" * 40
PERSON = dict(first_name="Ada", last_name="Lovelace", email="ada@example.org",
              password="analytical engine notes", note="We met at the maths club.")


@pytest.fixture
def site(tmp_path, monkeypatch):
    """Admin ready, email captured, and no 3-second wait for the human check."""
    monkeypatch.setattr(app_mod, "MIN_FORM_SECONDS", 0)
    mailer = FakeMailer()
    settings = Settings(secret="s" * 40, library=tmp_path / "lib", admin_email=ADMIN, password=SHARED,
                        default_engine="pymupdf", secure_cookies=False, max_mb=1, public_url=PUBLIC,
                        record_key=RECORD_KEY)
    app = create_app(settings, mailer=mailer)
    admin = TestClient(app, follow_redirects=False)
    ready_admin(admin)
    return app, admin, mailer


def visitor(app):
    return TestClient(app, follow_redirects=False)


def stamp_of(client):
    page = client.get("/signup")
    assert page.status_code == 200
    return re.search(r'name="stamp" value="([^"]+)"', page.text).group(1)


def sign_up(client, **changes):
    data = {**PERSON, "confirm": PERSON["password"], "stamp": stamp_of(client), "website": "", **changes}
    if "password" in changes and "confirm" not in changes:
        data["confirm"] = changes["password"]
    return client.post("/signup", data=data)


def link_in(msg, path):
    body = msg.get_content()
    return re.search(re.escape(PUBLIC) + re.escape(path) + r"[^\s]*", body).group(0)


def confirm_by_email(client, mailer):
    url = link_in(mailer.outbox[-1], "/signup/confirm?t=")
    token = url.split("t=", 1)[1]
    assert "Confirm my email" in client.get(url[len(PUBLIC):]).text
    return client.post("/signup/confirm", data={"t": token})


def ada(app):
    return app.state.users.by_email(PERSON["email"])


def csrf_of(admin):
    return re.search(r'name="csrf" value="([^"]+)"', admin.get("/admin").text).group(1)


# ---------------------------------------------------------------- check 1
def test_bots_are_rejected_and_no_email_is_sent(site, monkeypatch):
    app, _, mailer = site
    v = visitor(app)
    attempts = [
        dict(website="http://spam.example"),          # filled in the hidden trap box
        dict(stamp=""),                               # never loaded the form
        dict(stamp=f"{int(time.time()) - 60}.forged"),  # made-up stamp
    ]
    for changes in attempts:
        r = sign_up(v, **changes)
        assert r.status_code == 200 and "Check your email" in r.text, changes  # looks like success...
    monkeypatch.setattr(app_mod, "MIN_FORM_SECONDS", 3)
    assert "Check your email" in sign_up(v).text  # ...and so does filling it in faster than a person
    assert mailer.outbox == []                     # but nothing was sent
    assert app.state.users.count() == 1            # and nothing stored (only the admin)


def test_too_many_signups_from_one_address(site):
    app, _, mailer = site
    v = visitor(app)
    for i in range(app_mod.SIGNUPS_PER_HOUR):
        assert sign_up(v, email=f"p{i}@example.org").status_code == 200
    assert sign_up(v, email="one-more@example.org").status_code == 429
    assert len(mailer.outbox) == app_mod.SIGNUPS_PER_HOUR


# ---------------------------------------------------------------- check 2
def test_real_signup_waits_for_approval_and_owner_gets_one_email(site):
    app, _, mailer = site
    v = visitor(app)
    r = sign_up(v)
    assert "Check your email" in r.text
    assert [m["To"] for m in mailer.outbox] == [PERSON["email"]]
    assert ada(app).status == "unconfirmed"
    r = login(v, PERSON["email"], PERSON["password"])
    assert r.status_code == 403 and "confirm your email" in r.text

    # Opening the link (or an email scanner doing it) changes nothing; the button does.
    url = link_in(mailer.outbox[-1], "/signup/confirm?t=")
    v.get(url[len(PUBLIC):])
    assert ada(app).status == "unconfirmed" and len(mailer.outbox) == 1
    r = confirm_by_email(v, mailer)
    assert "request has gone to the owner" in r.text
    assert ada(app).status == "pending"

    to_owner = [m for m in mailer.outbox if m["To"] == ADMIN]
    assert len(to_owner) == 1
    body = to_owner[0].get_content()
    assert f"{PUBLIC}/admin" in body and "Ada Lovelace" in body and "maths club" in body

    # The link is one use only: confirming again sends nothing more.
    token = url.split("t=", 1)[1]
    assert v.post("/signup/confirm", data={"t": token}).status_code == 404
    assert len([m for m in mailer.outbox if m["To"] == ADMIN]) == 1

    r = login(v, PERSON["email"], PERSON["password"])
    assert r.status_code == 403 and "waiting for the owner" in r.text


def test_signing_up_with_a_taken_email_reveals_nothing(site):
    app, _, mailer = site
    r = sign_up(visitor(app), email=ADMIN)
    assert "Check your email" in r.text and mailer.outbox == []


def test_links_in_emails_ignore_a_forged_host(site):
    app, _, mailer = site
    v = TestClient(app, follow_redirects=False, base_url="http://evil.example")
    sign_up(v)
    body = mailer.outbox[-1].get_content()
    assert f"{PUBLIC}/signup/confirm?t=" in body and "evil.example" not in body


def test_form_input_never_reaches_email_headers(site):
    app, _, mailer = site
    sign_up(visitor(app), first_name="Ada\nBcc: victim@example.net", note="Subject: hi\r\nBcc: x@y.z")
    msg = mailer.outbox[-1]
    assert msg["Subject"] == "Please confirm your email address"
    assert msg["Bcc"] is None and msg["To"] == PERSON["email"]


def test_unconfirmed_signups_are_deleted_after_a_day(site):
    app, _, _ = site
    sign_up(visitor(app))
    users = app.state.users
    with users._lock:
        users._db.execute("UPDATE users SET created = ? WHERE email = ?", (time.time() - 25 * 3600, PERSON["email"]))
        users._db.commit()
    assert users.erase_expired_signups() == 1 and ada(app) is None


def test_failed_email_keeps_nothing(site):
    app, _, mailer = site
    mailer.fail = True
    r = sign_up(visitor(app))
    assert r.status_code == 503 and "send the email just now" in r.text
    assert ada(app) is None


# ---------------------------------------------------------------- check 3
def test_admin_page_is_only_for_the_owner(site):
    app, admin, _ = site
    add_friend(app)
    friend = visitor(app)
    assert login(friend, "friend@example.com", "friend's long password").status_code == 303
    r = friend.get("/admin")
    assert r.status_code == 403 and "Not allowed" in r.text
    assert friend.post(f"/admin/users/{ada_id_or_admin(app)}/approve", data={"csrf": "x"}).status_code == 403
    assert visitor(app).get("/admin").headers["location"].startswith("/login")  # not logged in
    assert admin.get("/admin").status_code == 200


def ada_id_or_admin(app):
    user = ada(app) or app.state.users.admin()
    return user.id


def test_admin_actions_need_the_page_token(site):
    app, admin, mailer = site
    sign_up(visitor(app))
    confirm_by_email(visitor(app), mailer)
    assert admin.post(f"/admin/users/{ada(app).id}/approve", data={"csrf": "wrong"}).status_code == 400
    assert ada(app).status == "pending"


# ---------------------------------------------------------------- checks 4 and 9
def test_approve_lets_them_log_in_and_emails_them(site):
    app, admin, mailer = site
    v = visitor(app)
    sign_up(v)
    confirm_by_email(v, mailer)
    page = admin.get("/admin").text
    assert "Ada Lovelace" in page and "maths club" in page
    assert "1 waiting" in admin.get("/").text

    r = admin.post(f"/admin/users/{ada(app).id}/approve", data={"csrf": csrf_of(admin)})
    assert r.headers["location"] == "/admin?msg=approved"
    last = mailer.outbox[-1]
    assert last["To"] == PERSON["email"] and f"{PUBLIC}/login" in last.get_content()  # check 9
    assert login(v, PERSON["email"], PERSON["password"]).status_code == 303
    assert v.get("/").status_code == 200


def test_deny_erases_everything_they_entered(site):
    app, admin, mailer = site
    v = visitor(app)
    sign_up(v)
    confirm_by_email(v, mailer)
    user = ada(app)
    folder = app.state.users.root(user.id)

    r = admin.post(f"/admin/users/{user.id}/deny", data={"csrf": csrf_of(admin)})
    assert r.headers["location"] == "/admin?msg=denied"
    assert ada(app) is None and not folder.exists()
    assert login(v, PERSON["email"], PERSON["password"]).status_code == 401

    # Nothing they typed is left anywhere in the database files.
    db_dir = app.state.users.library / "_db"
    raw = b"".join(p.read_bytes() for p in db_dir.iterdir() if p.is_file())
    for detail in (PERSON["email"], "Lovelace", "maths club"):
        assert detail.encode() not in raw, detail
    # Nobody was emailed about it, and the owner's own account is untouched.
    assert mailer.outbox[-1]["To"] == ADMIN
    assert login(visitor(app), ADMIN, ADMIN_PW).status_code == 303


def test_admin_cannot_approve_or_deny_themselves_or_active_users(site):
    app, admin, mailer = site
    friend = add_friend(app)
    sign_up(visitor(app))
    confirm_by_email(visitor(app), mailer)  # someone waiting, so the page has its form token
    token = csrf_of(admin)
    for uid in (app.state.users.admin().id, friend.id):
        assert admin.post(f"/admin/users/{uid}/deny", data={"csrf": token}).headers["location"] == "/admin?msg=gone"
    assert app.state.users.get(friend.id) is not None


# ---------------------------------------------------------------- without email
def test_signup_is_closed_without_email_settings(tmp_path):
    settings = Settings(secret="s" * 40, library=tmp_path / "lib", admin_email=ADMIN, password=SHARED)
    app = create_app(settings)
    v = visitor(app)
    assert "Sign-up is closed" in v.get("/signup").text
    assert v.post("/signup", data=PERSON).status_code == 404
    assert "Ask for one" not in v.get("/login").text
    assert "Source code" in v.get("/login").text


def test_email_settings_must_be_complete(monkeypatch):
    monkeypatch.setenv("PDF2PHONE_SECRET", "s" * 40)
    monkeypatch.setenv("PDF2PHONE_ADMIN_EMAIL", ADMIN)
    monkeypatch.setenv("PDF2PHONE_RECORD_KEY", RECORD_KEY)
    monkeypatch.setenv("PDF2PHONE_SMTP_HOST", "smtp-relay.brevo.com")
    with pytest.raises(RuntimeError, match="half set up"):
        Settings.from_env()
    for k, v in {"PDF2PHONE_SMTP_USER": "u", "PDF2PHONE_SMTP_PASSWORD": "p",
                 "PDF2PHONE_MAIL_FROM": "biggerthandog <noreply@example.com>"}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        Settings.from_env()
    monkeypatch.setenv("PDF2PHONE_PUBLIC_URL", "http://read.example.com")
    with pytest.raises(RuntimeError, match="PUBLIC_URL"):
        Settings.from_env()  # links in emails must be https
    monkeypatch.setenv("PDF2PHONE_PUBLIC_URL", "https://read.example.com/")
    s = Settings.from_env()
    assert s.public_url == "https://read.example.com" and s.smtp_port == 587


def test_old_database_gains_the_new_columns(tmp_path):
    db_dir = tmp_path / "lib" / "_db"
    db_dir.mkdir(parents=True)
    old = sqlite3.connect(db_dir / "pdf2phone.db")
    old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, pw_hash TEXT, "
                "is_admin INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL)")
    old.execute("INSERT INTO users (email, pw_hash, is_admin, created) VALUES (?, NULL, 1, 0)", (ADMIN,))
    old.commit()
    old.close()
    settings = Settings(secret="s" * 40, library=tmp_path / "lib", admin_email=ADMIN, password=SHARED)
    app = create_app(settings)
    assert app.state.users.admin().name == ""  # upgraded in place, admin kept
