"""One-off move from the single shared library to per-user libraries.

Before:  <lib>/<doc_id>/...                (one shared library)
After:   <lib>/_users/<admin_id>/<doc_id>/ (the admin's private library)

Document ids, and so their URLs (/<doc_id>/), don't change, which keeps the
reading positions stored in the phone's browser working.

Safety: a tar.gz of the whole library is written to <lib>/_backups/ and read
back to check it holds every document BEFORE anything moves; any mismatch
stops the migration with nothing changed.
"""

from __future__ import annotations

import json
import os
import tarfile
import time
from pathlib import Path
from typing import Callable

from .users import UserStore

BACKUP_DIR = "_backups"


def legacy_docs(library: Path) -> list[Path]:
    """Documents still in the old shared layout."""
    if not library.exists():
        return []
    return sorted(p for p in library.iterdir()
                  if p.is_dir() and not p.name.startswith("_") and (p / "meta.json").exists())


def _backup(library: Path, expected: list[Path], say: Callable[[str], None]) -> Path:
    dest_dir = library / BACKUP_DIR
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"pre-accounts-{time.strftime('%Y%m%d-%H%M%S')}.tgz"
    with tarfile.open(dest, "w:gz") as tar:
        for item in sorted(library.iterdir()):
            if item.name != BACKUP_DIR:
                tar.add(item, arcname=item.name)
    # Read it back: every document's meta.json and index.html must be inside.
    with tarfile.open(dest, "r:gz") as tar:
        names = set(tar.getnames())
    missing = [d.name for d in expected
               if f"{d.name}/meta.json" not in names or f"{d.name}/index.html" not in names]
    if missing:
        raise RuntimeError(f"Backup check failed (missing {missing[:5]}); nothing was moved. Backup: {dest}")
    say(f"Backup written and checked: {dest} ({dest.stat().st_size // 1024} KB, {len(expected)} documents)")
    return dest


def migrate(library: Path, admin_email: str, *, dry_run: bool = False,
            say: Callable[[str], None] = print) -> int:
    """Move legacy documents into the admin's library.  Returns how many moved."""
    docs = legacy_docs(library)
    if not docs:
        say("Nothing to migrate: no documents in the old shared layout.")
        return 0
    say(f"Found {len(docs)} documents in the old shared layout:")
    for d in docs:
        title = json.loads((d / "meta.json").read_text()).get("title", d.name)
        say(f"  {d.name}  ({title})")
    if dry_run:
        say(f"Dry run: they would move into the admin account for {admin_email}. Nothing changed.")
        return 0

    _backup(library, docs, say)

    users = UserStore(library)
    try:
        admin = users.ensure_admin(admin_email)
        root = users.root(admin.id)
    finally:
        users.close()
    root.mkdir(parents=True, exist_ok=True)

    for d in docs:
        target = root / d.name
        if target.exists():
            raise RuntimeError(f"{target} already exists; stopping. Earlier documents were moved; backup is in {BACKUP_DIR}/.")
        os.rename(d, target)  # same filesystem: each document moves in one step

    # Old CLI-style index page and job records belong to the admin now.
    (library / "index.html").unlink(missing_ok=True)
    for job_file in (library / "_jobs").glob("*.json"):
        try:
            job = json.loads(job_file.read_text())
        except Exception:
            continue
        if not job.get("user_id"):
            job["user_id"] = admin.id
            job_file.write_text(json.dumps(job))

    moved = [p for p in root.iterdir() if (p / "meta.json").exists() and (p / "index.html").exists()]
    if len(moved) < len(docs) or legacy_docs(library):
        raise RuntimeError("Check after moving failed; restore from the backup in _backups/.")
    say(f"Moved {len(docs)} documents into the admin account ({admin.email}).")
    return len(docs)
