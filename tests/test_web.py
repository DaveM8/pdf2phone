from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pdf2phone import engines, library  # noqa: E402
from pdf2phone.web import migrate  # noqa: E402
from pdf2phone.web.app import create_app  # noqa: E402
from pdf2phone.web.settings import Settings  # noqa: E402

SAMPLE_PDF = Path(__file__).parent / "fixtures/sample.pdf"
ADMIN = "owner@example.com"
SHARED = "old shared password"          # bootstrap: the pre-accounts password
ADMIN_PW = "harbour pencil violet gravy"  # the admin's own password, set at first login
FRIEND = "friend@example.com"
FRIEND_PW = "friend's long password"


def make_client(tmp_path, secure=False, shared=SHARED, lib=None):
    settings = Settings(secret="s" * 40, library=lib or tmp_path / "lib", admin_email=ADMIN,
                        password=shared, default_engine="pymupdf", secure_cookies=secure, max_mb=1)
    app = create_app(settings)
    return TestClient(app, follow_redirects=False), app


def login(client, email=ADMIN, password=ADMIN_PW, next="/"):
    return client.post("/login", data={"email": email, "password": password, "next": next})


def set_password(client, new, current=None, confirm=None):
    data = {"new": new, "confirm": new if confirm is None else confirm}
    if current is not None:
        data["current"] = current
    return client.post("/account/password", data=data)


def ready_admin(client):
    """First login with the old shared password, then choose a personal one."""
    assert login(client, password=SHARED).headers["location"] == "/account?msg=setpw"
    assert set_password(client, ADMIN_PW).status_code == 303


def add_friend(app, email=FRIEND, password=FRIEND_PW):
    return app.state.users.create(email, password)


def upload(client, data=None, name="sample.pdf", engine="pymupdf"):
    data = SAMPLE_PDF.read_bytes() if data is None else data
    return client.post("/upload", files={"pdf": (name, data, "application/pdf")}, data={"engine": engine})


def converted_doc(client, app):
    assert upload(client).status_code == 303
    app.state.queue.wait()
    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs and jobs[0]["state"] == "done", jobs
    return jobs[0]["doc_id"]


@pytest.fixture
def logged_in(tmp_path):
    client, app = make_client(tmp_path)
    ready_admin(client)
    return client, app


# ------------------------------------------------------------ admin bootstrap


def test_first_login_forces_admin_to_choose_password(tmp_path):
    client, app = make_client(tmp_path)
    assert login(client, password=SHARED).headers["location"] == "/account?msg=setpw"
    # Until a password is chosen, everything else sends them back to the account page.
    for path in ("/", "/api/jobs"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/account?msg=setpw"
    assert "Choose your password" in client.get("/account").text
    assert set_password(client, "too short").status_code == 400
    assert set_password(client, ADMIN_PW, confirm="something else entirely").status_code == 400
    assert set_password(client, ADMIN_PW).headers["location"] == "/account?msg=pwset"
    assert client.get("/").status_code == 200  # same device stays logged in

    # The old shared password is retired; the new one works.
    other = TestClient(app, follow_redirects=False)
    assert login(other, password=SHARED).status_code == 401
    assert login(other).status_code == 303


def test_shared_password_required_only_until_admin_has_one(tmp_path):
    with pytest.raises(RuntimeError, match="PDF2PHONE_PASSWORD"):
        make_client(tmp_path, shared="")
    client, _ = make_client(tmp_path)
    ready_admin(client)
    make_client(tmp_path, shared="")  # starts fine once the admin has a password


# ------------------------------------------------------------ login and sessions


def test_everything_private_needs_login(tmp_path):
    client, app = make_client(tmp_path)
    ready_admin(client)
    doc_id = converted_doc(client, app)
    client.cookies.clear()

    for path in ("/", f"/{doc_id}/", f"/{doc_id}/source.md", f"/d/{doc_id}/source.pdf", "/account"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert client.get("/api/jobs").status_code == 401
    assert upload(client).status_code == 401
    assert client.post(f"/d/{doc_id}/delete").status_code == 401
    assert client.post("/account/password", data={"new": "x" * 20, "confirm": "x" * 20}).status_code == 401
    # Shared assets are public (no private data; iOS fetches icons without cookies).
    assert client.get("/_static/reader.css").status_code == 200
    assert client.get("/apple-touch-icon.png").status_code == 200


def test_wrong_email_and_wrong_password_look_the_same(logged_in, tmp_path):
    _, app = logged_in
    client = TestClient(app, follow_redirects=False)
    a = login(client, email="nobody@example.com")
    b = login(client, password="not the password at all")
    assert a.status_code == b.status_code == 401
    assert "email or password" in a.text and "email or password" in b.text


def test_login_wrong_password_then_lockout(tmp_path):
    client, _ = make_client(tmp_path)
    ready_admin(client)
    client.cookies.clear()
    for _ in range(5):
        assert login(client, password="nope").status_code == 401
    r = login(client)  # even the right password is refused while locked out
    assert r.status_code == 429 and "Too many" in r.text


def test_session_cookie_is_secure_and_lasts_30_days(tmp_path):
    client, _ = make_client(tmp_path, secure=True)
    r = login(client, password=SHARED)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "max-age=2592000" in cookie  # 30 days


def test_next_cannot_redirect_off_site(logged_in):
    _, app = logged_in
    client = TestClient(app, follow_redirects=False)
    assert login(client, next="//evil.example/").headers["location"] == "/"


def test_changing_password_logs_out_other_devices(logged_in):
    phone, app = logged_in
    laptop = TestClient(app, follow_redirects=False)
    login(laptop)
    assert laptop.get("/").status_code == 200
    assert set_password(phone, "a brand new long password", current=ADMIN_PW).status_code == 303
    assert phone.get("/").status_code == 200       # the device that changed it stays in
    assert laptop.get("/").status_code == 303      # every other device is logged out


def test_change_password_needs_current_password(logged_in):
    client, _ = logged_in
    r = set_password(client, "a brand new long password", current="wrong current password")
    assert r.status_code == 400 and "current password" in r.text


def test_forged_session_user_id_is_rejected(logged_in):
    """Swapping uid in the cookie is impossible (it's signed), and even a
    cookie from another user only works for that user."""
    client, app = logged_in
    add_friend(app)
    friend = TestClient(app, follow_redirects=False)
    login(friend, FRIEND, FRIEND_PW)
    client.cookies.set("pdf2phone", client.cookies.get("pdf2phone")[:-4] + "AAAA")
    assert client.get("/").status_code == 303


# ------------------------------------------------------------ check 5: private libraries


def test_users_cannot_see_each_others_documents(logged_in):
    owner, app = logged_in
    add_friend(app)
    friend = TestClient(app, follow_redirects=False)
    assert login(friend, FRIEND, FRIEND_PW).status_code == 303

    owner_doc = converted_doc(owner, app)
    friend_doc = converted_doc(friend, app)

    # Each library lists only its own document.
    assert owner_doc in owner.get("/").text and friend_doc not in owner.get("/").text
    assert friend_doc in friend.get("/").text and owner_doc not in friend.get("/").text
    # Jobs too.
    assert {j["doc_id"] for j in friend.get("/api/jobs").json()["jobs"]} == {friend_doc}

    # Typing the exact address of the other person's document gets nothing.
    owner_id = app.state.users.by_email(ADMIN).id
    for path in (f"/{owner_doc}/", f"/{owner_doc}/index.html", f"/{owner_doc}/source.md",
                 f"/{owner_doc}/meta.json", f"/d/{owner_doc}/source.pdf",
                 f"/../{owner_id}/{owner_doc}/", f"/%2e%2e/{owner_id}/{owner_doc}/index.html",
                 f"/_users/{owner_id}/{owner_doc}/", "/_db/pdf2phone.db"):
        r = friend.get(path)
        assert r.status_code == 404, (path, r.status_code)
    assert friend.post(f"/d/{owner_doc}/delete").status_code == 404
    assert friend.post(f"/d/{owner_doc}/reconvert", data={"engine": "pymupdf"}).status_code == 404
    assert owner.get(f"/{owner_doc}/").status_code == 200  # still there for its owner


# ------------------------------------------------------------ documents


def test_upload_convert_and_read(logged_in):
    client, app = logged_in
    doc_id = converted_doc(client, app)
    home = client.get("/")
    assert doc_id in home.text and "Introduction" in home.text
    # No trailing slash -> redirect, so the reader's relative links work.
    assert client.get(f"/{doc_id}").headers["location"] == f"/{doc_id}/"
    page = client.get(f"/{doc_id}/")
    assert page.status_code == 200 and "../_static/reader.css" in page.text
    pdf = client.get(f"/d/{doc_id}/source.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    # Shared assets live once, not copied into each user's folder.
    uid = app.state.users.by_email(ADMIN).id
    assert not (app.state.users.root(uid) / "_static").exists()


def test_rejects_non_pdf_and_too_big(logged_in):
    client, app = logged_in
    assert upload(client, b"hello", name="x.pdf").headers["location"] == "/?msg=notpdf"
    big = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
    assert upload(client, big).headers["location"] == "/?msg=toobig"
    assert not app.state.queue.jobs
    assert not list((app.state.queue.dir / "uploads").iterdir())  # temp files cleaned up


def test_paths_cannot_escape_library(logged_in, tmp_path):
    client, _ = logged_in
    (tmp_path / "secret.txt").write_text("nope")
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/_jobs/", "/../../secret.txt",
                 "/_static/../../secret.txt", "/d/..%2f..%2fsecret.txt/source.pdf"):
        r = client.get(path)
        assert "nope" not in r.text, path


def test_reconvert_and_delete(logged_in):
    client, app = logged_in
    doc_id = converted_doc(client, app)
    added = client.get(f"/{doc_id}/meta.json").json()["added"]

    assert client.post(f"/d/{doc_id}/reconvert", data={"engine": "pymupdf"}).status_code == 303
    app.state.queue.wait()
    assert client.get(f"/{doc_id}/meta.json").json()["added"] == added  # keeps its place in the list
    assert client.get(f"/{doc_id}/").status_code == 200

    assert client.post(f"/d/{doc_id}/delete").headers["location"] == "/?msg=deleted"
    assert client.get(f"/{doc_id}/").status_code == 404
    assert client.post("/d/_static/delete").status_code == 404


def test_security_headers_and_reader_csp(logged_in):
    client, app = logged_in
    doc_id = converted_doc(client, app)
    page = client.get(f"/{doc_id}/")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    for r in (page, client.get("/"), client.get("/login")):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
    # The library page has its own inline script, so it must not get the reader CSP.
    assert "content-security-policy" not in client.get("/").headers


# ------------------------------------------------------------ check 8: migration


def _legacy_library(lib: Path) -> list[str]:
    """A library in the old shared layout, as the single-password app made it."""
    doc = engines.extract(SAMPLE_PDF, "pymupdf")
    ids = []
    for name in ("paper-one-abc123", "paper-two-def456"):
        library.add(lib, doc, SAMPLE_PDF, doc_id=name, source_name=f"{name}.pdf")
        ids.append(name)
    return ids


def test_app_refuses_to_start_on_old_layout(tmp_path):
    _legacy_library(tmp_path / "lib")
    with pytest.raises(RuntimeError, match="migrate-accounts"):
        make_client(tmp_path)


def test_migration_keeps_documents_and_addresses(tmp_path):
    lib = tmp_path / "lib"
    ids = _legacy_library(lib)
    before = {i: (lib / i / "index.html").read_bytes() for i in ids}

    # Dry run changes nothing.
    assert migrate.migrate(lib, ADMIN, dry_run=True, say=lambda m: None) == 0
    assert migrate.legacy_docs(lib) and not (lib / "_backups").exists()

    assert migrate.migrate(lib, ADMIN, say=lambda m: None) == 2
    backups = list((lib / "_backups").glob("pre-accounts-*.tgz"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0
    assert not migrate.legacy_docs(lib)
    assert migrate.migrate(lib, ADMIN, say=lambda m: None) == 0  # running it again is harmless

    # The owner logs in and finds every document at the SAME address, so the
    # reading positions saved on the phone (keyed by doc id) still apply.
    client, app = make_client(tmp_path)
    ready_admin(client)
    home = client.get("/").text
    for i in ids:
        assert i in home
        r = client.get(f"/{i}/")
        assert r.status_code == 200 and r.content == before[i]
        assert f'name="pdf2phone-doc" content="{i}"' in r.text


def test_migration_stops_if_backup_check_fails(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    _legacy_library(lib)
    import tarfile

    real_open = tarfile.open

    def broken_open(path, mode="r", *a, **k):
        if mode.startswith("r"):
            raise tarfile.ReadError("simulated broken backup")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr(tarfile, "open", broken_open)
    with pytest.raises(tarfile.ReadError):
        migrate.migrate(lib, ADMIN, say=lambda m: None)
    assert len(migrate.legacy_docs(lib)) == 2  # nothing moved


# ------------------------------------------------------------ brute-force protection


def _flood(t, n):
    from pdf2phone.web import auth
    for i in range(n):
        assert t.begin(f"10.0.{i // 250}.{i % 250}") == 0
    return auth


def test_throttle_counts_attempts_up_front():
    """Parallel requests from one address: the 6th is refused even if none of
    the first five has finished yet."""
    from pdf2phone.web.auth import MAX_FAILURES, Throttle

    t = Throttle()
    for _ in range(MAX_FAILURES):
        assert t.begin("198.51.100.7") == 0
    assert t.begin("198.51.100.7") > 0
    t2 = Throttle()
    t2.begin("198.51.100.8")
    t2.succeed("198.51.100.8")  # a success clears the address
    assert t2.locked_for("198.51.100.8") == 0


def test_throttle_memory_is_bounded(monkeypatch):
    from pdf2phone.web import auth

    monkeypatch.setattr(auth, "MAX_TRACKED_IPS", 50)
    t = auth.Throttle()
    _flood(t, 500)
    assert len(t._per_ip) <= 50
    assert len(t._global) <= auth.GLOBAL_MAX_FAILURES


def test_global_flood_slows_unknown_devices_but_not_known_ones(logged_in, monkeypatch):
    from pdf2phone.web import app as app_module, auth

    client, app = logged_in
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(app_module.asyncio, "sleep", fake_sleep)

    # The owner's phone logged in before, then logged out: it's a known device.
    client.get("/logout")
    assert client.get("/").status_code == 303

    _flood(app.state.throttle, auth.GLOBAL_MAX_FAILURES)
    assert app.state.throttle.under_attack()

    assert login(client).status_code == 303 and not slept  # straight in

    # A stranger's device is queued and delayed, but still not refused.
    stranger = TestClient(app, follow_redirects=False)
    assert login(stranger).status_code == 303 and slept == [auth.GLOBAL_DELAY_SECONDS]


def test_known_device_mark_is_per_user_and_revoked_by_password_change(logged_in, monkeypatch):
    from pdf2phone.web import app as app_module, auth

    client, app = logged_in
    add_friend(app)
    client.get("/logout")  # owner's device is now "known" for the owner only
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(app_module.asyncio, "sleep", fake_sleep)
    _flood(app.state.throttle, auth.GLOBAL_MAX_FAILURES)

    # The owner's mark doesn't help someone guessing the friend's password.
    login(client, FRIEND, "guessing the friend's password")
    assert slept == [auth.GLOBAL_DELAY_SECONDS]

    # After the owner's password changes elsewhere, the old mark stops working.
    app.state.users.set_password(app.state.users.by_email(ADMIN).id, "a brand new long password")
    slept.clear()
    login(client, password="a brand new long password")
    assert slept == [auth.GLOBAL_DELAY_SECONDS]


def test_logout_requires_login(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.get("/logout")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert "pdf2phone" not in client.cookies  # no known-device mark handed out


def test_nginx_config_overwrites_forwarded_for():
    conf = (Path(__file__).parents[1] / "deploy/nginx-pdf2phone.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert "$proxy_add_x_forwarded_for" not in conf


# ------------------------------------------------------------ settings


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("PDF2PHONE_SECRET", "s" * 64)
    monkeypatch.setenv("PDF2PHONE_ADMIN_EMAIL", " Owner@Example.com ")
    for bad in ("short", "change-me-to-something-long"):
        monkeypatch.setenv("PDF2PHONE_PASSWORD", bad)
        with pytest.raises(RuntimeError):
            Settings.from_env()
    monkeypatch.setenv("PDF2PHONE_PASSWORD", "harbour pencil violet gravy")
    s = Settings.from_env()
    assert s.admin_email == "owner@example.com" and s.session_days == 30
    monkeypatch.delenv("PDF2PHONE_PASSWORD")
    assert Settings.from_env().password == ""  # optional once the admin has their own
    monkeypatch.setenv("PDF2PHONE_ADMIN_EMAIL", "not-an-email")
    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        Settings.from_env()


def test_set_password_command(logged_in, monkeypatch):
    from pdf2phone import cli

    client, app = logged_in
    answers = iter(["recovered long password"] * 2)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    assert cli.main(["set-password", "OWNER@example.com", "-l", str(app.state.users.library)]) == 0
    assert client.get("/").status_code == 303  # logged out everywhere
    assert login(client, password="recovered long password").status_code == 303
    assert cli.main(["set-password", "nobody@example.com", "-l", str(app.state.users.library)]) == 1


def test_guessing_current_password_is_throttled(logged_in):
    # A stolen session must not allow unlimited guesses at the current password.
    client, _ = logged_in
    for _ in range(5):
        assert set_password(client, "n" * 20, current="wrong guess here").status_code == 400
    r = set_password(client, "n" * 20, current=ADMIN_PW)
    assert r.status_code == 429 and "Too many" in r.text  # locked, even with the right one


def test_password_checks_run_at_most_two_at_a_time(tmp_path, monkeypatch):
    # Each Argon2 check briefly takes ~64 MB; a login flood mustn't run dozens at once.
    import asyncio
    import threading
    import time as _time

    import httpx

    from pdf2phone.web import users as users_mod

    client, app = make_client(tmp_path)
    ready_admin(client)
    running, peak, lock = 0, 0, threading.Lock()
    real_verify = users_mod.UserStore.verify

    def slow_verify(self, email, password):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        _time.sleep(0.2)
        with lock:
            running -= 1
        return real_verify(self, email, password)

    monkeypatch.setattr(users_mod.UserStore, "verify", slow_verify)

    async def flood():  # one event loop, like the real server
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            await asyncio.gather(*(c.post("/login", data={"email": ADMIN, "password": f"wrong guess {i:04}"})
                                   for i in range(4)))

    asyncio.run(flood())
    assert peak == 2
