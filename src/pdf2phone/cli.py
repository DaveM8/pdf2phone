"""pdf2phone command line.

  pdf2phone convert paper.pdf [more.pdf ...] [--engine pymupdf] [--single-file] [-o DIR]
  pdf2phone compare paper.pdf [-o DIR]
  pdf2phone serve [--port 8000]
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import engines, library, page


def _progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def cmd_convert(args: argparse.Namespace) -> int:
    failures = 0
    for pdf in args.pdfs:
        pdf = Path(pdf).expanduser()
        if not pdf.is_file():
            _progress(f"✗ {pdf}: no such file")
            failures += 1
            continue
        _progress(f"… {pdf.name} ({args.engine})")
        t0 = time.time()
        try:
            doc = engines.extract(pdf, args.engine)
        except engines.EngineUnavailable as e:
            _progress(f"✗ {e}")
            return 2
        except Exception as e:  # keep going with the other PDFs
            _progress(f"✗ {pdf.name}: {type(e).__name__}: {e}")
            failures += 1
            continue

        if args.single_file:
            out_dir = Path(args.output or ".").expanduser()
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{pdf.stem}.html"
            out.write_text(page.build(doc, doc_id=library.slugify(pdf.stem), source_name=pdf.name, mode="single"))
        else:
            out = library.add(Path(args.output or library.DEFAULT_LIBRARY).expanduser(), doc, pdf)
        _progress(f"✓ {out}  ({time.time() - t0:.1f}s, {out.stat().st_size // 1024} KB)")
    return 1 if failures else 0


def cmd_compare(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf).expanduser()
    out_dir = Path(args.output or f"compare-{library.slugify(pdf.stem)}").expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    # docling-math takes minutes; only run it when asked for by name.
    names = args.engines or [n for n in engines.ENGINES if n != "docling-math"]
    rows = []
    for name in names:
        try:
            engines.get_engine(name)
        except engines.EngineUnavailable as e:
            _progress(f"– skipping {name}: {e}")
            continue
        _progress(f"… {name}")
        t0 = time.time()
        try:
            doc = engines.extract(pdf, name)
        except Exception as e:
            _progress(f"✗ {name}: {type(e).__name__}: {e}")
            continue
        secs = time.time() - t0
        (out_dir / f"{name}.md").write_text(doc.markdown)
        (out_dir / f"{name}.html").write_text(
            page.build(doc, doc_id=f"compare-{name}", source_name=pdf.name, mode="single")
        )
        headings = sum(1 for ln in doc.markdown.splitlines() if ln.startswith("#"))
        rows.append((name, secs, headings, len(doc.images), len(doc.markdown)))
        _progress(f"✓ {name}: {secs:.1f}s")

    print(f"\n{'engine':<12}{'time':>8}{'headings':>10}{'images':>8}{'chars':>9}")
    for name, secs, h, im, ch in rows:
        print(f"{name:<12}{secs:>7.1f}s{h:>10}{im:>8}{ch:>9}")
    print(f"\nOpen the .html files in {out_dir}/ to compare them.")
    return 0 if rows else 1


def _lan_addresses() -> list[str]:
    out = []
    try:
        name = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
        if name:
            out.append(f"{name}.local")
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # no packet is sent; just picks the LAN interface
        out.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return out


def cmd_serve(args: argparse.Namespace) -> int:
    lib = Path(args.library or library.DEFAULT_LIBRARY).expanduser()
    lib.mkdir(parents=True, exist_ok=True)
    library.write_index(lib)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            # Always fetch fresh pages/CSS while experimenting.
            self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def log_message(self, fmt, *a):
            if not args.quiet:
                super().log_message(fmt, *a)

    handler = functools.partial(Handler, directory=str(lib))
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {lib}", flush=True)
    print(f"  On this Mac:   http://localhost:{args.port}/")
    for host in _lan_addresses():
        print(f"  On your phone: http://{host}:{args.port}/   (same Wi-Fi)")
    print("Anyone on this Wi-Fi network can open these pages while this runs. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_migrate_accounts(args: argparse.Namespace) -> int:
    from .web import migrate  # needs the [web] extra

    lib = Path(args.library or os.environ.get("PDF2PHONE_LIBRARY", "/data/library")).expanduser()
    email = args.admin_email or os.environ.get("PDF2PHONE_ADMIN_EMAIL", "")
    if not email and not args.dry_run:
        _progress("✗ Set PDF2PHONE_ADMIN_EMAIL (or pass --admin-email).")
        return 2
    try:
        migrate.migrate(lib, email, dry_run=args.dry_run, say=lambda m: print(m, flush=True))
    except Exception as e:
        _progress(f"✗ {e}")
        return 1
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    """Emergency reset, run on the server: docker compose exec pdf2phone pdf2phone set-password EMAIL"""
    import getpass

    from .web.users import UserStore

    lib = Path(args.library or os.environ.get("PDF2PHONE_LIBRARY", "/data/library")).expanduser()
    store = UserStore(lib)
    try:
        user = store.by_email(args.email)
        if user is None:
            _progress(f"✗ No account for {args.email}.")
            return 1
        new = getpass.getpass("New password (at least 12 characters): ")
        if getpass.getpass("Same again: ") != new:
            _progress("✗ The two passwords don't match; nothing changed.")
            return 1
        try:
            store.set_password(user.id, new)
        except ValueError as e:
            _progress(f"✗ {e}")
            return 1
    finally:
        store.close()
    print(f"Password changed for {user.email}. Every device they were logged in on is now logged out.")
    return 0


def _library(args: argparse.Namespace) -> Path:
    return Path(args.library or os.environ.get("PDF2PHONE_LIBRARY", "/data/library")).expanduser()


def cmd_backup_db(args: argparse.Namespace) -> int:
    """Run by deploy/backup/pdf2phone-backup.sh inside the container."""
    from .web import backup

    try:
        backup.copy_database(_library(args), Path(args.dest), os.environ.get("PDF2PHONE_RECORD_KEY", ""))
    except (OSError, sqlite3.Error, RuntimeError) as e:
        _progress(f"✗ {e}")
        return 1
    return 0


def cmd_reapply_deletions(args: argparse.Namespace) -> int:
    """Run by deploy/backup/pdf2phone-restore.sh before the app starts again."""
    from .web import backup
    from .web.users import UserStore

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since):
        _progress("✗ --since must be a date like 2026-11-10.")
        return 2
    key = os.environ.get("PDF2PHONE_RECORD_KEY", "")
    try:
        backup.check_key(Path(args.newer), key)
    except (RuntimeError, sqlite3.Error) as e:
        _progress(f"✗ {e}")
        return 1
    newer = backup.deletion_records(Path(args.newer))
    store = UserStore(_library(args))
    try:
        erased = backup.reapply_deletions(store, newer, args.since, key)
    except RuntimeError as e:
        _progress(f"✗ {e}")
        return 1
    finally:
        store.close()
    # Only a count: logs shouldn't hold anything about the people.
    print(f"Deletion records checked: {len(newer)}. Accounts erased again: {erased}.")
    return 0


def cmd_email_admin(args: argparse.Namespace) -> int:
    """Email the owner (body from stdin), e.g. when a backup fails."""
    from .web.mail import Mailer, MailError
    from .web.settings import Settings

    try:
        settings = Settings.from_env()
    except RuntimeError as e:
        _progress(f"✗ {e}")
        return 1
    if not settings.smtp_host:
        _progress("✗ Email isn't set up in .env, so nothing was sent.")
        return 1
    mailer = Mailer(settings.smtp_host, settings.smtp_port, settings.smtp_user, settings.smtp_password,
                    settings.mail_from)
    try:
        mailer.send(settings.admin_email, args.subject, sys.stdin.read()[-20000:] or "(no details)")
    except MailError as e:
        _progress(f"✗ {e}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdf2phone", description="PDF → large-print reader HTML")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("convert", help="convert PDFs into the library (or single .html files)")
    c.add_argument("pdfs", nargs="+")
    c.add_argument("--engine", "-e", default=engines.DEFAULT_ENGINE, choices=list(engines.ENGINES))
    c.add_argument("--single-file", "-s", action="store_true",
                   help="write one self-contained .html per PDF (for AirDrop / Files app)")
    c.add_argument("--output", "-o", help=f"library folder (default {library.DEFAULT_LIBRARY}) "
                                          "or, with --single-file, the output folder (default .)")
    c.set_defaults(func=cmd_convert)

    k = sub.add_parser("compare", help="run every installed engine on one PDF")
    k.add_argument("pdf")
    k.add_argument("--engines", nargs="+", choices=list(engines.ENGINES))
    k.add_argument("--output", "-o")
    k.set_defaults(func=cmd_compare)

    s = sub.add_parser("serve", help="serve the library to your phone over Wi-Fi")
    s.add_argument("--library", "-l")
    s.add_argument("--port", "-p", type=int, default=8000)
    s.add_argument("--bind", default="0.0.0.0", help="use 127.0.0.1 for this Mac only")
    s.add_argument("--quiet", "-q", action="store_true")
    s.set_defaults(func=cmd_serve)

    m = sub.add_parser("migrate-accounts",
                       help="one-off: move the old shared web library into the admin's account (backs up first)")
    m.add_argument("--library", "-l", help="default: $PDF2PHONE_LIBRARY or /data/library")
    m.add_argument("--admin-email", help="default: $PDF2PHONE_ADMIN_EMAIL")
    m.add_argument("--dry-run", action="store_true", help="only list what would move")
    m.set_defaults(func=cmd_migrate_accounts)

    pw = sub.add_parser("set-password", help="set a new password for an account (asks for it, never shown)")
    pw.add_argument("email")
    pw.add_argument("--library", "-l", help="default: $PDF2PHONE_LIBRARY or /data/library")
    pw.set_defaults(func=cmd_set_password)

    b = sub.add_parser("backup-db", help="consistent copy of the account database (used by the backup script)")
    b.add_argument("dest")
    b.add_argument("--library", "-l", help="default: $PDF2PHONE_LIBRARY or /data/library")
    b.set_defaults(func=cmd_backup_db)

    r = sub.add_parser("reapply-deletions",
                       help="after a restore: erase accounts deleted since that backup (used by the restore script)")
    r.add_argument("--newer", required=True, help="account database from the newest backup")
    r.add_argument("--since", required=True, help="UTC date the restored backup was made, YYYY-MM-DD")
    r.add_argument("--library", "-l", help="default: $PDF2PHONE_LIBRARY or /data/library")
    r.set_defaults(func=cmd_reapply_deletions)

    e = sub.add_parser("email-admin", help="email the owner; the message is read from stdin")
    e.add_argument("subject", choices=["pdf2phone backup FAILED"],
                   help="fixed subjects only: nothing typed by anyone goes in an email header")
    e.set_defaults(func=cmd_email_admin)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
