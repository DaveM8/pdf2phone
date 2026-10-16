"""Background conversion queue.

One worker thread: Docling can use ~3 GB of memory, so conversions run one
at a time.  Job records are also written to <library>/_jobs/ so the page can
show what happened after an app restart.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import engines, library

ENGINE_LABELS = {
    "pymupdf": "Fast",
    "docling": "Careful (Docling)",
    "docling-math": "Careful + equations (slow)",
    "markitdown": "Plain text (markitdown)",
}
STATE_LABELS = {
    "queued": "Waiting",
    "running": "Converting…",
    "done": "Ready",
    "failed": "Failed",
    "interrupted": "Stopped by a restart – please convert again",
}


@dataclass
class Job:
    id: str
    doc_id: str
    source_name: str
    engine: str
    upload: str | None  # temp file for new uploads; None when re-converting
    state: str = "queued"
    error: str = ""
    created: float = 0.0
    finished: float = 0.0

    def view(self) -> dict:
        d = asdict(self)
        d["state_label"] = STATE_LABELS.get(self.state, self.state)
        d["engine_label"] = ENGINE_LABELS.get(self.engine, self.engine)
        return d


class JobQueue:
    def __init__(self, library_dir: Path):
        self.library = library_dir
        self.dir = library_dir / "_jobs"
        (self.dir / "uploads").mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="convert")
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        for p in self.dir.glob("*.json"):
            try:
                job = Job(**json.loads(p.read_text()))
            except Exception:
                continue
            if job.state in ("queued", "running"):
                job.state, job.finished = "interrupted", time.time()
                self._save(job)
            self.jobs[job.id] = job

    def _save(self, job: Job) -> None:
        (self.dir / f"{job.id}.json").write_text(json.dumps(asdict(job)))

    def _set(self, job: Job, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(job, k, v)
            self._save(job)

    # ------------------------------------------------------------ api
    def upload_path(self) -> Path:
        return self.dir / "uploads" / f"{secrets.token_hex(8)}.pdf"

    def submit(self, *, doc_id: str, source_name: str, engine: str, upload: Path | None) -> Job:
        job = Job(id=secrets.token_hex(6), doc_id=doc_id, source_name=source_name, engine=engine,
                  upload=str(upload) if upload else None, created=time.time())
        with self._lock:
            self.jobs[job.id] = job
            self._save(job)
        self._pool.submit(self._run, job)
        return job

    def busy(self) -> bool:
        return any(j.state in ("queued", "running") for j in self.jobs.values())

    def recent(self) -> list[Job]:
        """What the library page shows: anything unfinished, failures from the
        last day, and successes from the last 2 minutes."""
        now = time.time()
        keep = [
            j for j in self.jobs.values()
            if j.state in ("queued", "running")
            or (j.state in ("failed", "interrupted") and now - j.finished < 86400)
            or (j.state == "done" and now - j.finished < 120)
        ]
        return sorted(keep, key=lambda j: j.created, reverse=True)

    def wait(self, timeout: float = 600) -> None:
        """Block until the queue is idle (used by tests)."""
        end = time.time() + timeout
        while self.busy() and time.time() < end:
            time.sleep(0.1)

    # ------------------------------------------------------------ worker
    def _run(self, job: Job) -> None:
        self._set(job, state="running")
        pdf = Path(job.upload) if job.upload else self.library / job.doc_id / "source.pdf"
        try:
            doc = engines.extract(pdf, job.engine)
            library.add(self.library, doc, pdf, doc_id=job.doc_id,
                        source_name=job.source_name, write_idx=False)
            self._set(job, state="done", finished=time.time())
        except Exception as e:
            traceback.print_exc()
            self._set(job, state="failed", error=f"{type(e).__name__}: {e}"[:300], finished=time.time())
        finally:
            if job.upload:
                Path(job.upload).unlink(missing_ok=True)
