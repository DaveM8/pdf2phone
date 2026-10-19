"""The conversion queue: several at once, users take turns, crashes are contained."""

import os
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")

from pdf2phone.web import jobs as jobs_mod  # noqa: E402
from pdf2phone.web.jobs import JobQueue  # noqa: E402
from test_web import SAMPLE_PDF  # noqa: E402


def crash(pdf, engine):
    """Stands in for a converter process killed for using too much memory."""
    os._exit(1)


def make_queue(tmp_path, workers):
    return JobQueue(tmp_path / "lib", lambda uid: tmp_path / "lib" / "_users" / str(uid), workers=workers)


def blocking(queue):
    """Replace conversion with something we control: records who started, then
    waits until released."""
    started, release = [], threading.Event()

    def fake(pdf, engine):
        started.append(pdf.parent.name)  # the doc id
        release.wait(10)
        raise RuntimeError("not really converting")
    queue._extract = fake
    return started, release


def until(cond, timeout=5):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        time.sleep(0.02)
    return cond()


def submit(queue, user, n):
    return queue.submit(user_id=user, doc_id=f"d{user}-{n}", source_name="x.pdf", engine="pymupdf", upload=None)


def test_conversions_run_side_by_side(tmp_path):
    queue = make_queue(tmp_path, workers=3)
    started, release = blocking(queue)
    for n in range(4):
        submit(queue, user=n + 1, n=n)
    assert until(lambda: len(started) == 3)
    time.sleep(0.2)
    assert len(started) == 3  # never more than the number of workers
    release.set()
    queue.wait(10)
    assert len(started) == 4 and not queue.busy()


def test_another_user_does_not_wait_behind_a_pile_of_uploads(tmp_path):
    queue = make_queue(tmp_path, workers=2)
    started, release = blocking(queue)
    for n in range(5):                                     # one user uploads five
        submit(queue, user=1, n=n)
    assert until(lambda: len(started) == 2)
    submit(queue, user=2, n=0)                             # then someone else uploads one
    release.set()                                          # both slots free up at once
    queue.wait(10)
    assert started[:2] == ["d1-0", "d1-1"]
    assert started[2] == "d2-0"  # the friend goes next, ahead of the three older uploads


def test_a_crashed_converter_fails_only_its_job_and_the_queue_recovers(tmp_path, monkeypatch):
    queue = make_queue(tmp_path, workers=1)
    real = jobs_mod.engines
    monkeypatch.setattr(jobs_mod, "engines", types.SimpleNamespace(extract=crash))
    pdf = tmp_path / "upload.pdf"
    pdf.write_bytes(SAMPLE_PDF.read_bytes())
    job = queue.submit(user_id=1, doc_id="boom", source_name="a.pdf", engine="pymupdf", upload=pdf)
    queue.wait(60)
    assert job.state == "failed" and "out of memory" in job.error

    monkeypatch.setattr(jobs_mod, "engines", real)
    pdf2 = tmp_path / "upload2.pdf"
    pdf2.write_bytes(SAMPLE_PDF.read_bytes())
    job = queue.submit(user_id=1, doc_id="fine", source_name="b.pdf", engine="pymupdf", upload=pdf2)
    queue.wait(60)
    assert job.state == "done", job.error

    workers = list(queue._procs._processes.values())
    assert workers and all(w.is_alive() for w in workers)
    queue.close()  # what the app does when it shuts down
    assert until(lambda: not any(w.is_alive() for w in workers))


def test_turn_taking_when_every_slot_frees_at_once(tmp_path):
    """Scheduler alone (no worker threads), so the tie is exact every time."""
    queue = make_queue(tmp_path, workers=0)
    pile = [submit(queue, user=1, n=n) for n in range(5)]
    with queue._lock:
        first, second = queue._next(), queue._next()
    assert (first, second) == (pile[0], pile[1])
    friend = submit(queue, user=2, n=0)
    first.state = second.state = "done"  # nobody has anything running now
    with queue._lock:
        assert queue._next() is friend   # user 1 had the last turn
        assert queue._next() is pile[2]


def test_workers_setting(monkeypatch):
    from pdf2phone.web.settings import Settings
    monkeypatch.setenv("PDF2PHONE_SECRET", "s" * 64)
    monkeypatch.setenv("PDF2PHONE_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("PDF2PHONE_RECORD_KEY", "r" * 40)
    monkeypatch.delenv("PDF2PHONE_WORKERS", raising=False)
    assert Settings.from_env().workers == jobs_mod.DEFAULT_WORKERS
    monkeypatch.setenv("PDF2PHONE_WORKERS", "4")
    assert Settings.from_env().workers == 4
    for bad in ("0", "9", "two", "-1"):
        monkeypatch.setenv("PDF2PHONE_WORKERS", bad)
        with pytest.raises(RuntimeError, match="WORKERS"):
            Settings.from_env()
