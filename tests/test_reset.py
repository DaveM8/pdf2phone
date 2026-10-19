"""Milestone 3: forgot-password email."""

import re
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from pdf2phone.web import app as app_mod  # noqa: E402
from test_signup import PUBLIC, link_in, site, visitor  # noqa: E402,F401
from test_delete import ZQ, member  # noqa: E402
from test_web import login  # noqa: E402

NEW_PW = "turquoise bicycle anthem"


def stamp(client):
    return re.search(r'name="stamp" value="([^"]+)"', client.get("/forgot").text).group(1)


def ask(client, email, **extra):
    return client.post("/forgot", data={"email": email, "stamp": stamp(client), "website": "", **extra})


def token_from(mailer):
    return link_in(mailer.outbox[-1], "/reset?t=").split("t=", 1)[1]


def test_reset_by_email_sets_a_new_password_and_logs_out_other_devices(site):
    app, admin, mailer = site
    other_device, _ = member(app, admin, mailer)
    assert "Forgot your password?" in visitor(app).get("/login").text

    v = visitor(app)
    before = len(mailer.outbox)
    r = ask(v, ZQ["email"].upper())
    assert r.status_code == 200 and "Check your email" in r.text
    assert len(mailer.outbox) == before + 1 and mailer.outbox[-1]["To"] == ZQ["email"]
    token = token_from(mailer)

    assert "Choose a new password" in v.get(f"/reset?t={token}").text  # opening the link uses nothing up
    assert "Choose a new password" in v.get(f"/reset?t={token}").text
    r = v.post("/reset", data={"t": token, "new": NEW_PW, "confirm": "different words entirely"})
    assert r.status_code == 400
    r = v.post("/reset", data={"t": token, "new": "short", "confirm": "short"})
    assert r.status_code == 400
    r = v.post("/reset", data={"t": token, "new": NEW_PW, "confirm": NEW_PW})
    assert r.headers["location"] == "/login?msg=reset"
    assert "Password saved" in v.get("/login?msg=reset").text

    assert login(visitor(app), ZQ["email"], ZQ["password"]).status_code == 401  # old one is gone
    assert login(v, ZQ["email"], NEW_PW).status_code == 303
    assert other_device.get("/").headers["location"].startswith("/login")    # logged out elsewhere
    # One use only.
    assert v.post("/reset", data={"t": token, "new": NEW_PW, "confirm": NEW_PW}).status_code == 404
    assert v.get(f"/reset?t={token}").status_code == 404


def test_unknown_or_waiting_emails_get_the_same_page_and_no_email(site):
    app, admin, mailer = site
    member(app, admin, mailer)
    real = ask(visitor(app), ZQ["email"])  # has an account: an email goes out
    before = len(mailer.outbox)
    app.state.users.create("waiting@example.org", "waiting waiting waiting", status="pending")
    for email in ["nobody@example.org", "waiting@example.org", "not an email"]:
        r = ask(visitor(app), email)
        assert r.status_code == 200 and r.text == real.text  # nothing tells them apart
    assert len(mailer.outbox) == before


def test_bots_get_no_email(site):
    app, admin, mailer = site
    member(app, admin, mailer)
    v = visitor(app)
    before = len(mailer.outbox)
    assert "Check your email" in ask(v, ZQ["email"], website="http://spam.example").text
    assert "Check your email" in v.post("/forgot", data={"email": ZQ["email"], "stamp": "1.forged"}).text
    assert len(mailer.outbox) == before


def test_one_inbox_cannot_be_flooded(site):
    app, admin, mailer = site
    member(app, admin, mailer)
    before = len(mailer.outbox)
    for i in range(app_mod.RESETS_PER_HOUR + 3):  # each from a different address
        c = TestClient(app, follow_redirects=False, client=(f"10.0.0.{i}", 5000))
        assert "Check your email" in ask(c, ZQ["email"]).text
    assert len(mailer.outbox) - before == app_mod.RESETS_PER_HOUR


def test_bad_or_expired_links(site, monkeypatch):
    app, admin, mailer = site
    member(app, admin, mailer)
    v = visitor(app)
    assert v.get("/reset?t=made-up").status_code == 404
    assert v.post("/reset", data={"t": "made-up", "new": NEW_PW, "confirm": NEW_PW}).status_code == 404
    ask(v, ZQ["email"])
    token = token_from(mailer)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3601)
    assert v.get(f"/reset?t={token}").status_code == 404


def test_a_signup_link_cannot_reset_a_password(site):
    app, admin, mailer = site
    member(app, admin, mailer)
    user = app.state.users.by_email(ZQ["email"])
    token = app.state.users.new_token(user.id, "confirm")
    assert visitor(app).post("/reset", data={"t": token, "new": NEW_PW, "confirm": NEW_PW}).status_code == 404
