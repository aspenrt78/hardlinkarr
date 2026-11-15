"""Hardlinkarr web API + static UI."""
from __future__ import annotations

import base64
import os
import secrets
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import cleanup, db, jobs, linker, metadata, notify, organize, settings
from .clients import get_client
from .paths import all_roots, can_hardlink, disk_usage, download_roots, library_roots

VERSION = "1.0.0"
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="Hardlinkarr", version=VERSION)


@app.on_event("startup")
def _startup() -> None:
    db.conn()
    db.log("info", f"Hardlinkarr {VERSION} started")
    jobs.start()


@app.middleware("http")
async def auth(request: Request, call_next):
    user, pw = settings.get("ui_username"), settings.get("ui_password")
    if user and pw and request.url.path != "/api/health":
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, pw)
            except ValueError:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Hardlinkarr"'})
    return await call_next(request)


@app.exception_handler(metadata.MetadataError)
async def _meta_err(_req: Request, exc: Exception):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# ---------------------------------------------------------------- status

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": VERSION}


@app.get("/api/status")
def status() -> dict:
    counts = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM items GROUP BY status")}
    saved = db.one("SELECT COALESCE(SUM(size),0) s FROM items WHERE status IN ('linked','exists')")
    disks, seen = [], set()
    for r in all_roots():
        d = disk_usage(r)
        if d and (d["total"], d["free"]) not in seen:
            seen.add((d["total"], d["free"]))
            disks.append(d)
    recent = [db.decode_item(r) for r in db.query(
        "SELECT * FROM items WHERE status IN ('linked','review','error','duplicate') ORDER BY updated DESC LIMIT 8")]
    return {"version": VERSION, "setup_complete": settings.get("setup_complete"), "job": jobs.state,
            "counts": counts, "space_saved": saved["s"] if saved else 0, "disks": disks,
            "dry_run": settings.get("dry_run"), "recent": recent,
            "reports": {k: {"running": v["running"], "has_result": v["result"] is not None}
                        for k, v in jobs.reports.items()}}


@app.post("/api/scan")
def scan(full: bool = False) -> dict:
    if jobs.state["running"]:
        return {"started": False, "detail": "scan already running"}
    jobs.trigger_scan(full)
    return {"started": True}


# ---------------------------------------------------------------- items

@app.get("/api/items")
def items(status: str = "", q: str = "", limit: int = 100, offset: int = 0) -> dict:
    where, args = [], []
    if status:
        sts = status.split(",")
        where.append(f"status IN ({','.join('?' * len(sts))})")
        args += sts
    if q:
        where.append("(source_path LIKE ? OR title LIKE ? OR dest_path LIKE ? OR torrent_name LIKE ?)")
        args += [f"%{q}%"] * 4
    w = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.one(f"SELECT COUNT(*) n FROM items {w}", args)["n"]
    rows = db.query(f"SELECT * FROM items {w} ORDER BY updated DESC LIMIT ? OFFSET ?", [*args, limit, offset])
    return {"total": total, "items": [db.decode_item(r) for r in rows]}


@app.get("/api/items/{item_id}/candidates")
def candidates(item_id: int) -> dict:
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(404)
    from . import parser
    p = parser.parse(item["source_path"])
    kind = "tv" if (item.get("media_type") == "episode" or p["kind"] == "episode") else "movie"
    results = metadata.search(kind, p["title"] or p.get("alt_title") or "", p.get("year")) if (p["title"] or p.get("alt_title")) else []
    return {"parsed": p, "kind": kind, "results": results[:10]}


@app.post("/api/items/{item_id}/match")
def match(item_id: int, body: dict = Body(...)) -> dict:
    try:
        eps = body.get("episodes")
        if isinstance(eps, str):
            eps = [int(x) for x in eps.replace("-", ",").split(",") if x.strip().isdigit()]
        season = body.get("season")
        return linker.rematch(item_id, body["kind"], int(body["tmdb_id"]),
                              int(season) if season not in (None, "") else None, eps or None,
                              link=body.get("link", True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/items/bulk")
def bulk(body: dict = Body(...)) -> dict:
    action, ids = body.get("action"), body.get("ids") or []
    results: dict[str, int] = {}
    for i in ids:
        item = db.get_item(int(i))
        if not item:
            continue
        if action == "approve":
            r = linker.apply_link(item["id"]) if item.get("dest_path") else "review"
        elif action == "ignore":
            db.update_item(item["id"], status="ignored", message="ignored by user")
            r = "ignored"
        elif action == "unlink":
            linker.unlink_item(item["id"])
            r = "ignored"
        elif action == "retry":
            db.update_item(item["id"], status="pending")
            r = "pending"
        elif action == "forget":
            db.execute("DELETE FROM items WHERE id=?", (item["id"],))
            r = "forgotten"
        elif action == "replace":
            r = linker.apply_link(item["id"], replace=True)
        else:
            raise HTTPException(400, "unknown action")
        results[r] = results.get(r, 0) + 1
    if action == "retry":
        jobs.trigger_scan()
    if results.get("linked"):
        notify.refresh()
    return results


@app.get("/api/search")
def search(kind: str, q: str, year: int | None = None) -> list[dict]:
    return metadata.search("tv" if kind == "tv" else "movie", q, year)[:15]


# ---------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings() -> dict:
    return {"settings": settings.public(), "examples": _safe_examples()}


def _safe_examples() -> list[str]:
    try:
        return linker.preview_examples()
    except Exception as e:  # noqa: BLE001
        return [f"template error: {e}"]


@app.post("/api/settings")
def save_settings(body: dict = Body(...)) -> dict:
    settings.save(body)
    jobs.wake()
    return {"settings": settings.public(), "examples": _safe_examples()}


@app.post("/api/naming/preview")
def naming_preview(body: dict = Body(...)) -> dict:
    current = settings.load().copy()
    try:
        settings._cache = {**current, **{k: v for k, v in body.items() if k in settings.DEFAULTS}}
        return {"examples": _safe_examples()}
    finally:
        settings._cache = current


def _secret(body: dict, key: str) -> str:
    v = body.get(key) or ""
    return settings.get(key) if v == settings.MASK else v


@app.post("/api/test/{what}")
def test(what: str, body: dict = Body(default={})) -> dict:
    try:
        if what == "tmdb":
            return {"ok": True, "message": metadata.test_tmdb(_secret(body, "tmdb_api_key"))}
        if what == "client":
            old = settings.load().copy()
            try:
                settings._cache = {**old, **{k: v for k, v in body.items() if k in settings.DEFAULTS},
                                   "client_password": _secret(body, "client_password")}
                client = get_client()
                if not client:
                    return {"ok": False, "message": "choose a client type"}
                with client:
                    version = client.test()
                    ts = client.torrents(with_files=False)
                cats = sorted({t["category"] for t in ts if t["category"]})
                return {"ok": True, "message": f"{version} - {len(ts)} torrents", "categories": cats,
                        "save_paths": sorted({t["save_path"] for t in ts})[:20]}
            finally:
                settings._cache = old
        if what in ("radarr", "sonarr"):
            return {"ok": True, "message": metadata.test_arr(body.get(f"{what}_url", ""),
                                                             _secret(body, f"{what}_api_key"))}
        if what == "mediaserver":
            msg = notify.test(body.get("mediaserver_type"), body.get("mediaserver_url"),
                              _secret(body, "mediaserver_token"))
            old = settings.load().copy()
            try:
                settings._cache = {**old, **{k: v for k, v in body.items() if k in settings.DEFAULTS},
                                   "mediaserver_token": _secret(body, "mediaserver_token")}
                check = notify.check_libraries()
            finally:
                settings._cache = old
            return {"ok": True, "message": msg, "libraries": check.get("locations", [])}
        if what == "paths":
            return test_paths(body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": str(e)}
    raise HTTPException(404)


def test_paths(body: dict) -> dict:
    downloads = body.get("download_dirs") or settings.get("download_dirs")
    libs = [body.get(k, settings.get(k)) for k in ("movies_dir", "tv_dir", "anime_dir")]
    checks, ok = [], True
    for d in downloads:
        for lib in [x for x in libs if x]:
            good, msg = can_hardlink(d, lib)
            ok &= good
            checks.append({"from": d, "to": lib, "ok": good, "message": msg})
    return {"ok": ok, "checks": checks, "message": "all folder pairs support hardlinks" if ok else
            "some folders cannot be hardlinked"}


@app.get("/api/browse")
def browse(path: str = "/") -> dict:
    path = os.path.normpath(path or "/")
    try:
        entries = sorted(e.name for e in os.scandir(path) if e.is_dir() and not e.name.startswith("."))
    except OSError as e:
        raise HTTPException(400, str(e)) from e
    return {"path": path, "parent": os.path.dirname(path) if path != "/" else None, "dirs": entries[:500]}


@app.get("/api/detect")
def detect() -> dict:
    """Suggest folder settings by looking at common container mount points."""
    hints: dict[str, Any] = {}
    candidates = ["/data", "/mnt/data", "/media", "/mnt/media", "/downloads", "/movies", "/tv"]
    found = [c for c in candidates if os.path.isdir(c)]
    hints["mounts"] = found
    for base in found:
        for root, dirs, _f in os.walk(base):
            depth = root[len(base):].count(os.sep)
            if depth > 2:
                dirs[:] = []
                continue
            for d in dirs:
                low = d.lower()
                p = os.path.join(root, d)
                if low in ("torrents", "downloads", "download", "complete", "completed") and "downloads" not in hints:
                    hints["downloads"] = p
                elif low in ("movies", "films") and "movies" not in hints:
                    hints["movies"] = p
                elif low in ("tv", "shows", "tv shows", "series", "tvshows") and "tv" not in hints:
                    hints["tv"] = p
                elif low == "anime" and "anime" not in hints:
                    hints["anime"] = p
    return hints


# ---------------------------------------------------------------- torrents

@app.get("/api/torrents")
def torrents() -> dict:
    client = get_client()
    if not client:
        return {"torrents": [], "error": "no torrent client configured"}
    try:
        with client:
            ts = client.torrents(with_files=False)
    except Exception as e:  # noqa: BLE001
        return {"torrents": [], "error": str(e)}
    stats = {r["torrent_hash"]: r for r in db.query(
        "SELECT torrent_hash, SUM(status='linked') linked, SUM(status='exists') existing, "
        "SUM(status='review') review, SUM(status='error') errors, SUM(status='duplicate') dupes "
        "FROM items WHERE torrent_hash IS NOT NULL GROUP BY torrent_hash")}
    for t in ts:
        t["items"] = stats.get(t["hash"])
        t.pop("files", None)
    ts.sort(key=lambda t: -(t.get("added_on") or 0))
    return {"torrents": ts}


# ---------------------------------------------------------------- cleanup

@app.post("/api/cleanup/{kind}/scan")
def cleanup_scan(kind: str) -> dict:
    if kind not in jobs.reports:
        raise HTTPException(404)
    jobs.run_report(kind)
    return {"started": True}


@app.get("/api/cleanup/{kind}")
def cleanup_get(kind: str) -> dict:
    if kind not in jobs.reports:
        raise HTTPException(404)
    return jobs.reports[kind]


@app.post("/api/cleanup/empty/remove")
def cleanup_empty(body: dict = Body(...)) -> dict:
    return _after(cleanup.remove_dirs(body.get("paths") or []), "empty")


@app.post("/api/cleanup/files/delete")
def cleanup_delete(body: dict = Body(...)) -> dict:
    return _after(cleanup.delete_files(body.get("paths") or []), body.get("report"))


@app.post("/api/cleanup/duplicates/relink")
def cleanup_relink(body: dict = Body(...)) -> dict:
    return _after(cleanup.relink_copies(body["keep"], body.get("targets") or []), "duplicates")


@app.post("/api/cleanup/torrents/remove")
def cleanup_torrents(body: dict = Body(...)) -> dict:
    try:
        return _after(cleanup.remove_torrents(body.get("hashes") or [], bool(body.get("delete_files"))), "orphans")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


def _after(result: dict, report: str | None) -> dict:
    if report in jobs.reports:
        jobs.run_report(report)
    return result


@app.post("/api/organize/apply")
def organize_apply(body: dict = Body(...)) -> dict:
    result = organize.apply(body.get("moves") or [])
    if result["done"]:
        notify.refresh()
    return _after(result, "organize")


_ms_cache: dict = {"ts": 0, "data": None}


@app.get("/api/mediaserver/check")
def mediaserver_check(fresh: bool = False) -> dict:
    import time
    if fresh or not _ms_cache["data"] or time.time() - _ms_cache["ts"] > 600:
        _ms_cache.update(ts=time.time(), data=notify.check_libraries())
    return _ms_cache["data"]


# ---------------------------------------------------------------- logs

@app.get("/api/logs")
def logs(limit: int = 300, level: str = "") -> list[dict]:
    if level:
        return db.query("SELECT * FROM log WHERE level=? ORDER BY id DESC LIMIT ?", (level, limit))
    return db.query("SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/roots")
def roots() -> dict:
    return {"downloads": download_roots(), "library": library_roots()}


# ---------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> FileResponse:
    return FileResponse(os.path.join(STATIC, "index.html"))
