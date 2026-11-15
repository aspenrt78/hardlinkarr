"""Background scheduler: periodic scans of torrents + download folders, and cleanup reports."""
from __future__ import annotations

import os
import threading
import time
import traceback
from typing import Callable

from . import cleanup, db, linker, notify, organize, parser, settings
from .clients import auto_add_mappings, get_client
from .clients.base import ClientError
from .paths import download_roots

state: dict = {
    "running": False, "phase": "idle", "done": 0, "total": 0, "last_run": None,
    "last_result": None, "last_error": None, "next_run": None,
}
reports: dict[str, dict] = {
    k: {"running": False, "result": None, "error": None, "started": None}
    for k in ("empty", "duplicates", "orphans", "organize")
}
_scan_lock = threading.Lock()
_wake = threading.Event()


def _wanted(t: dict) -> bool:
    inc = [c.lower() for c in settings.get("include_categories") or []]
    exc = [c.lower() for c in settings.get("exclude_categories") or []]
    cat = (t.get("category") or "").lower()
    if inc and cat not in inc:
        return False
    return cat not in exc


def run_scan(full_rescan: bool = False) -> dict:
    if not _scan_lock.acquire(blocking=False):
        return {"skipped": "scan already running"}
    counts: dict[str, int] = {}
    new_links = 0
    try:
        state.update(running=True, phase="indexing library", done=0, total=0, last_error=None)
        index = linker.LibraryIndex().build()
        memo: dict = {}
        jobs: list[tuple[str, dict | None]] = []
        claimed: set[str] = set()      # files owned by a torrent (complete or not)

        client = get_client()
        if client:
            state["phase"] = "reading torrent client"
            try:
                with client:
                    torrents = client.torrents(with_files=True)
                    # the client may report paths this container sees elsewhere: detect and fix
                    added = auto_add_mappings(torrents)
                    if added:
                        db.log("info", "Added path mappings automatically: " + ", ".join(
                            f"{m['remote']} → {m['local']}" for m in added))
                        torrents = client.torrents(with_files=True)
                for t in torrents:
                    claimed.update(t["files"])
                    if not t["completed"] or not _wanted(t):
                        continue
                    for f in t["files"]:
                        if parser.is_video(f) and os.path.isfile(f):
                            jobs.append((f, t))
            except (ClientError, OSError, Exception) as e:  # noqa: BLE001
                state["last_error"] = f"Torrent client: {e}"
                db.log("error", f"Torrent client error: {e}")

        # loose files in the download folders (existing downloads / no client / manual copies)
        state["phase"] = "walking download folders"
        known = {r["source_path"] for r in db.query(
            "SELECT source_path FROM items WHERE status NOT IN ('error','pending','planned')")} if not full_rescan else set()
        queued = {j[0] for j in jobs}
        now = time.time()
        for root in download_roots():
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    p = os.path.join(dirpath, f)
                    if p in queued or p in claimed or not parser.is_video(p):
                        continue
                    if p in known:
                        continue
                    try:
                        if now - os.path.getmtime(p) < 120:
                            continue  # still being written
                    except OSError:
                        continue
                    jobs.append((p, None))

        # skip already-finished items quickly
        if not full_rescan:
            done_paths = {r["source_path"]: r["inode"] for r in db.query(
                "SELECT source_path, inode FROM items WHERE status NOT IN ('error','pending','planned')")}
            jobs = [j for j in jobs if j[0] not in done_paths]

        state.update(phase="processing", total=len(jobs))
        for n, (path, torrent) in enumerate(jobs, 1):
            state["done"] = n
            try:
                status = linker.process_file(path, torrent, index, memo, force=full_rescan)
            except Exception as e:  # noqa: BLE001
                status = "error"
                db.upsert_item({"source_path": path, "status": "error", "message": f"internal error: {e}"})
                traceback.print_exc()
            counts[status] = counts.get(status, 0) + 1
            if status == "linked":
                new_links += 1
        if new_links:
            notify.refresh()
        result = {"processed": len(jobs), **counts}
        if jobs:
            db.log("info", "Scan finished: " + ", ".join(f"{v} {k}" for k, v in result.items()))
        state["last_result"] = result
        return result
    except Exception as e:  # noqa: BLE001
        state["last_error"] = str(e)
        db.log("error", f"Scan failed: {e}")
        traceback.print_exc()
        return {"error": str(e)}
    finally:
        state.update(running=False, phase="idle", last_run=time.time())
        _scan_lock.release()


def trigger_scan(full: bool = False) -> None:
    threading.Thread(target=run_scan, args=(full,), daemon=True).start()


def run_report(kind: str) -> None:
    fn: Callable = {"empty": cleanup.find_empty_dirs, "duplicates": cleanup.find_duplicates,
                    "orphans": cleanup.find_orphans, "organize": organize.plan}[kind]
    r = reports[kind]
    if r["running"]:
        return

    def work() -> None:
        r.update(running=True, error=None, started=time.time())
        try:
            r["result"] = fn()
        except Exception as e:  # noqa: BLE001
            r["error"] = str(e)
            traceback.print_exc()
        finally:
            r["running"] = False

    threading.Thread(target=work, daemon=True).start()


def _loop() -> None:
    time.sleep(5)
    while True:
        interval = max(1, int(settings.get("scan_interval_minutes") or 5)) * 60
        if settings.get("setup_complete"):
            run_scan()
        state["next_run"] = time.time() + interval
        _wake.wait(interval)
        _wake.clear()


def start() -> None:
    threading.Thread(target=_loop, daemon=True, name="scheduler").start()


def wake() -> None:
    _wake.set()
