"""Milestone 3: deleting your own account (GOALS.md check 6)."""

import hashlib
import hmac
import time

import pytest

pytest.importorskip("fastapi")

from test_signup import RECORD_KEY, confirm_by_email, csrf_of, sign_up, site, visitor  # noqa: F401
from test_web import ADMIN, ADMIN_PW, SAMPLE_PDF, login  # noqa: E402

ZQ = dict(first_name="Zebulon", last_name="Quixotic", email="zq-unique@example.org",
          password="periwinkle lighthouse oboe", note="hippopotamus marmalade")
PDF_NAME = "zq-private-diary.pdf"


def member(app, admin, mailer):
    """Zebulon signs up, is approved, logs in and converts a PDF."""
    v = visitor(app)
    sign_up(v, **ZQ, confirm=ZQ["password"])
    confirm_by_email(v, mailer)
    user = app.state.users.by_email(ZQ["email"])
    admin.post(f"/admin/users/{user.id}/approve", data={"csrf": csrf_of(admin)})
    assert login(v, ZQ["email"], ZQ["password"]).status_code == 303
    r = v.post("/upload", files={"pdf": (PDF_NAME, SAMPLE_PDF.read_bytes(), "application/pdf")},
               data={"engine": "pymupdf"})
    assert r.status_code == 303
    app.state.queue.wait()
    assert "zq-private-diary" in v.get("/").text
    return v, user


def everything_on_disk(app) -> bytes:
    return b"".join(p.read_bytes() for p in app.state.users.library.rglob("*") if p.is_file())


def test_deleting_your_account_leaves_only_the_record(site):
    app, admin, mailer = site
    v, user = member(app, admin, mailer)
    on_disk = everything_on_disk(app)
    traces = [ZQ["email"], "Zebulon", "Quixotic", "hippopotamus", "zq-private-diary"]
    assert all(t.encode() in on_disk for t in traces)  # they really were stored

    r = v.post("/account/delete", data={"password": "not my password"})
    assert r.status_code == 400 and app.state.users.get(user.id) is not None  # wrong password: nothing happens

    r = v.post("/account/delete", data={"password": ZQ["password"]})
    assert r.headers["location"] == "/goodbye"
    assert "has been deleted" in v.get("/goodbye").text
    assert v.get("/").headers["location"].startswith("/login")      # logged out
    assert login(v, ZQ["email"], ZQ["password"]).status_code == 401  # and can't log back in

    on_disk = everything_on_disk(app)
    for t in traces:
        assert t.encode() not in on_disk, f"{t!r} is still on disk"
    assert not app.state.users.root(user.id).exists()
    assert not [j for j in app.state.queue.jobs.values() if j.user_id == user.id]

    [record] = app.state.users.deletion_records()
    expected = hmac.new(RECORD_KEY.encode(), ZQ["email"].encode(), hashlib.sha256).hexdigest()
    assert record["email_hash"] == expected
    assert record["removed_by"] == "user"
    assert record["removed"] == time.strftime("%Y-%m-%d", time.gmtime())

    # Everyone else is untouched.
    assert login(visitor(app), ADMIN, ADMIN_PW).status_code == 303


def test_the_owner_cannot_delete_their_own_account(site):
    app, admin, _ = site
    r = admin.post("/account/delete", data={"password": ADMIN_PW})
    assert r.status_code == 400 and app.state.users.admin() is not None
    assert "Delete my account" not in admin.get("/account").text


def test_wrong_password_guesses_are_limited(site):
    app, admin, mailer = site
    v, user = member(app, admin, mailer)
    for _ in range(4):  # the login above used one attempt and was cleared on success
        assert v.post("/account/delete", data={"password": "guess guess guess"}).status_code == 400
    v.post("/account/delete", data={"password": "guess guess guess"})
    assert v.post("/account/delete", data={"password": ZQ["password"]}).status_code == 429
    assert app.state.users.get(user.id) is not None


def test_waits_for_a_running_conversion(site, monkeypatch):
    app, admin, mailer = site
    v, user = member(app, admin, mailer)
    monkeypatch.setattr(app.state.queue, "busy", lambda uid=None: True)
    r = v.post("/account/delete", data={"password": ZQ["password"]})
    assert r.status_code == 409 and app.state.users.get(user.id) is not None


def test_account_page_explains_what_is_deleted(site):
    app, admin, mailer = site
    v, _ = member(app, admin, mailer)
    page = v.get("/account").text
    assert "Delete my account" in page and "can't be undone" in page.replace("&#39;", "'")


# ---------------------------------------------------------------- check 7
def test_owner_removing_someone_leaves_only_the_record(site):
    app, admin, mailer = site
    v, user = member(app, admin, mailer)
    assert "Remove" in admin.get("/admin").text

    r = admin.post(f"/admin/users/{user.id}/remove", data={"csrf": "forged"})
    assert r.status_code == 400 and app.state.users.get(user.id) is not None

    r = admin.post(f"/admin/users/{user.id}/remove", data={"csrf": csrf_of(admin)})
    assert r.headers["location"] == "/admin?msg=removed"
    assert v.get("/").headers["location"].startswith("/login")      # their session ends at once
    assert login(v, ZQ["email"], ZQ["password"]).status_code == 401

    on_disk = everything_on_disk(app)
    for t in [ZQ["email"], "Zebulon", "Quixotic", "hippopotamus", "zq-private-diary"]:
        assert t.encode() not in on_disk, f"{t!r} is still on disk"
    assert not app.state.users.root(user.id).exists()
    [record] = app.state.users.deletion_records()
    assert record["removed_by"] == "admin"
    assert record["email_hash"] == hmac.new(RECORD_KEY.encode(), ZQ["email"].encode(), hashlib.sha256).hexdigest()


def test_only_the_owner_can_remove_and_never_themselves(site):
    app, admin, mailer = site
    v, user = member(app, admin, mailer)
    owner = app.state.users.admin()
    assert v.post(f"/admin/users/{owner.id}/remove", data={"csrf": ""}).status_code == 403
    assert app.state.users.admin() is not None
    r = admin.post(f"/admin/users/{owner.id}/remove", data={"csrf": csrf_of(admin)})
    assert r.headers["location"] == "/admin?msg=notactive" and app.state.users.admin() is not None
    assert not app.state.users.deletion_records()


def test_removal_waits_for_a_running_conversion(site, monkeypatch):
    app, admin, mailer = site
    _, user = member(app, admin, mailer)
    monkeypatch.setattr(app.state.queue, "busy", lambda uid=None: True)
    r = admin.post(f"/admin/users/{user.id}/remove", data={"csrf": csrf_of(admin)})
    assert r.headers["location"] == "/admin?msg=remove_busy" and app.state.users.get(user.id) is not None
