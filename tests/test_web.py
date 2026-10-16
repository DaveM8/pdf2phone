from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from pdf2phone.web.app import create_app  # noqa: E402
from pdf2phone.web.settings import Settings  # noqa: E402

SAMPLE_PDF = Path(__file__).parent / "fixtures/sample.pdf"
PASSWORD = "correct horse battery"


def make_client(tmp_path, secure=False, password=PASSWORD):
    settings = Settings(password=password, secret="s" * 40, library=tmp_path / "lib",
                        default_engine="pymupdf", secure_cookies=secure, max_mb=1)
    app = create_app(settings)
    return TestClient(app, follow_redirects=False), app


def login(client, password=PASSWORD):
    return client.post("/login", data={"password": password, "next": "/"})


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
    assert login(client).status_code == 303
    return client, app


def test_everything_private_needs_login(tmp_path):
    client, app = make_client(tmp_path)
    login(client)
    doc_id = converted_doc(client, app)
    client.cookies.clear()

    for path in ("/", f"/{doc_id}/", f"/{doc_id}/source.md", f"/d/{doc_id}/source.pdf"):
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), path
    assert client.get("/api/jobs").status_code == 401
    assert upload(client).status_code == 401
    assert client.post(f"/d/{doc_id}/delete").status_code == 401
    # Shared assets are public (no private data; iOS fetches icons without cookies).
    assert client.get("/_static/reader.css").status_code == 200
    assert client.get("/apple-touch-icon.png").status_code == 200


def test_login_wrong_password_then_lockout(tmp_path):
    client, _ = make_client(tmp_path)
    for _ in range(5):
        assert login(client, "nope").status_code == 401
    r = login(client)  # even the right password is refused while locked out
    assert r.status_code == 429 and "Too many" in r.text


def test_login_sets_long_lived_secure_cookie(tmp_path):
    client, _ = make_client(tmp_path, secure=True)
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 303
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "max-age=15552000" in cookie  # 180 days


def test_next_cannot_redirect_off_site(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.post("/login", data={"password": PASSWORD, "next": "//evil.example/"})
    assert r.headers["location"] == "/"


def test_changing_password_logs_everyone_out(tmp_path):
    client, _ = make_client(tmp_path)
    login(client)
    cookie = client.cookies.get("pdf2phone")
    client2, _ = make_client(tmp_path, password="a different password")
    client2.cookies.set("pdf2phone", cookie)
    assert client2.get("/").status_code == 303


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
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/_jobs/", "/d/..%2f..%2fsecret.txt/source.pdf"):
        r = client.get(path)
        assert r.status_code in (404, 307, 308) or "nope" not in r.text, path
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



def test_rotating_ips_still_hit_global_limit():
    """A visitor who fakes a new address for every guess never trips the
    per-IP limit, but the global backstop still stops them."""
    from pdf2phone.web.auth import GLOBAL_MAX_FAILURES, MAX_FAILURES, Throttle

    t = Throttle()
    for i in range(GLOBAL_MAX_FAILURES):
        ip = f"10.0.{i // 250}.{i % 250}"
        assert t.locked_for(ip) == 0
        t.fail(ip)
    assert t.locked_for("192.0.2.99") > 0  # a brand-new address is refused too
    assert GLOBAL_MAX_FAILURES > MAX_FAILURES


def test_nginx_config_overwrites_forwarded_for():
    conf = (Path(__file__).parents[1] / "deploy/nginx-pdf2phone.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in conf
    assert "$proxy_add_x_forwarded_for" not in conf
