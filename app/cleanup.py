"""Housekeeping reports: empty folders, duplicates and orphaned torrents/files.

Nothing here deletes automatically - reports are generated, then the user picks what to remove.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from collections import defaultdict

from . import db, parser, settings
from .clients import get_client
from .paths import assert_managed, download_roots, in_library, is_under, library_roots

TMDB_TAG = re.compile(r"[{\[](?:tmdb|tmdbid)[-=](\d+)[}\]]", re.I)
IMDB_TAG = re.compile(r"[{\[](?:imdb|imdbid)[-=](tt\d+)[}\]]", re.I)
PARTIAL_EXT = (".!qb", ".part", ".!ut", ".crdownload")


def _junk_exts() -> set[str]:
    return {e.lower() if e.startswith(".") else "." + e.lower() for e in settings.get("junk_extensions") or []}


# ---------------------------------------------------------------- empty folders

def find_empty_dirs() -> list[dict]:
    junk = _junk_exts()
    out = []
    for root in library_roots() + download_roots():
        if not os.path.isdir(root):
            continue
        empty: dict[str, bool] = {}
        for dirpath, dirs, files in os.walk(root, topdown=False):
            junk_files = [f for f in files if os.path.splitext(f)[1].lower() in junk or f.lower() in ("thumbs.db", ".ds_store")]
            real_files = [f for f in files if f not in junk_files]
            sub_ok = all(empty.get(os.path.join(dirpath, d), False) for d in dirs)
            is_empty = not real_files and sub_ok
            empty[dirpath] = is_empty
            if is_empty and dirpath != root:
                parent = os.path.dirname(dirpath)
                out.append({"path": dirpath, "junk_files": len(junk_files),
                            "root": root, "_parent": parent})
    # only report the top-most empty folder of each empty tree
    paths = {o["path"] for o in out}
    return [{k: v for k, v in o.items() if not k.startswith("_")} for o in out if o["_parent"] not in paths]


def remove_dirs(paths: list[str]) -> dict:
    junk = _junk_exts()
    removed, failed = [], []
    for p in paths:
        try:
            real = assert_managed(p)
            # re-verify it is still empty (only junk) before removing
            for dirpath, _d, files in os.walk(real):
                for f in files:
                    if os.path.splitext(f)[1].lower() not in junk and f.lower() not in ("thumbs.db", ".ds_store"):
                        raise RuntimeError(f"no longer empty: {os.path.join(dirpath, f)}")
            shutil.rmtree(real)
            removed.append(p)
        except Exception as e:  # noqa: BLE001
            failed.append({"path": p, "error": str(e)})
    if removed:
        db.log("info", f"Removed {len(removed)} empty folder(s)")
    return {"removed": removed, "failed": failed}


# ---------------------------------------------------------------- duplicates

def _quick_hash(path: str, size: int) -> str:
    """Hash of size + three 1 MiB samples - fast and good enough to spot identical copies."""
    h = hashlib.blake2b(str(size).encode(), digest_size=16)
    chunk = 1024 * 1024
    with open(path, "rb") as fh:
        for off in (0, max(0, size // 2 - chunk // 2), max(0, size - chunk)):
            fh.seek(off)
            h.update(fh.read(chunk))
    return h.hexdigest()


def _media_key(path: str, root: str, is_tv: bool) -> str | None:
    from .metadata import normalize
    rel = os.path.relpath(path, root).replace("\\", "/")
    top = rel.split("/")[0]
    clean_top = TMDB_TAG.sub("", IMDB_TAG.sub("", top)).strip()
    p = parser.parse(path, "episode" if is_tv else "movie")
    if is_tv:
        if not p["episodes"]:
            return None
        fp = parser.parse(clean_top, "episode")
        show = normalize(fp["title"] or clean_top)
        eps = "-".join(str(e) for e in p["episodes"])
        return f"tv:{show}:S{p['season'] or 0}E{eps}"
    fp = parser.parse(clean_top, "movie")
    key = f"{normalize(fp['title'] or p['title'])}:{fp['year'] or p['year']}"
    part = f":pt{p['part']}" if p.get("part") else ""
    edition = f":{(p.get('edition') or '').lower()}" if p.get("edition") else ""
    return f"movie:{key}{part}{edition}"


def _file_info(path: str, st: os.stat_result) -> dict:
    p = parser.parse(path)
    return {"path": path, "size": st.st_size, "inode": st.st_ino, "nlink": st.st_nlink,
            "resolution": p.get("resolution"), "in_library": in_library(path),
            "seeding": st.st_nlink > 1}


def find_duplicates() -> dict:
    min_size = (settings.get("min_video_mb") or 0) * 1024 * 1024
    s = settings.load()
    tv_roots = {os.path.normpath(r) for r in (s.get("tv_dir"), s.get("anime_dir")) if r}

    lib_files: list[tuple[str, str, os.stat_result]] = []
    for root in library_roots():
        for dirpath, _d, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if not parser.is_video(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size >= min_size:
                    lib_files.append((root, p, st))

    # 1) same movie / episode present more than once (different files)
    by_key: dict[str, list] = defaultdict(list)
    for root, p, st in lib_files:
        key = _media_key(p, root, os.path.normpath(root) in tv_roots)
        if key:
            by_key[key].append((p, st))
    media_dupes = []
    for key, files in by_key.items():
        inodes = {(st.st_dev, st.st_ino) for _p, st in files}
        if len(inodes) < 2:
            continue
        infos = [_file_info(p, st) for p, st in files]
        best = max(infos, key=lambda i: parser.quality_rank(i["resolution"], i["size"]))
        for i in infos:
            i["keep"] = i is best
        media_dupes.append({"key": key, "label": os.path.basename(os.path.dirname(best["path"])) if key.startswith("movie")
                            else key.split(":", 1)[1], "files": infos})

    # 2) redundant links: one inode reachable through several library paths
    by_inode: dict[tuple, list] = defaultdict(list)
    for _r, p, st in lib_files:
        by_inode[(st.st_dev, st.st_ino)].append((p, st))
    redundant = []
    for files in by_inode.values():
        if len(files) > 1:
            infos = [_file_info(p, st) for p, st in files]
            infos.sort(key=lambda i: len(i["path"]))
            for n, i in enumerate(infos):
                i["keep"] = n == 0
            redundant.append({"label": os.path.basename(infos[0]["path"]), "files": infos})

    # 3) identical content stored twice (copies, not hardlinks) - library vs downloads
    all_files = [(p, st) for _r, p, st in lib_files]
    seen_lib = {p for p, _ in all_files}
    for root in download_roots():
        for dirpath, _d, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if p in seen_lib or not parser.is_video(p):
                    continue
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size >= min_size:
                    all_files.append((p, st))
    by_size: dict[int, list] = defaultdict(list)
    for p, st in all_files:
        by_size[st.st_size].append((p, st))
    copies, wasted = [], 0
    for size, files in by_size.items():
        uniq = {}
        for p, st in files:
            uniq.setdefault((st.st_dev, st.st_ino), (p, st))
        if len(uniq) < 2:
            continue
        by_hash: dict[str, list] = defaultdict(list)
        for p, st in uniq.values():
            try:
                by_hash[_quick_hash(p, size)].append((p, st))
            except OSError:
                continue
        for files2 in by_hash.values():
            if len(files2) < 2:
                continue
            infos = [_file_info(p, st) for p, st in files2]
            # keep the copy inside the downloads (it is seeding); others can become hardlinks to it
            infos.sort(key=lambda i: (i["in_library"], -i["nlink"]))
            for n, i in enumerate(infos):
                i["keep"] = n == 0
            wasted += size * (len(infos) - 1)
            copies.append({"label": os.path.basename(infos[0]["path"]), "files": infos, "size": size})

    return {"media": media_dupes, "redundant_links": redundant, "copies": copies,
            "wasted_bytes": wasted, "generated": time.time()}


def delete_files(paths: list[str]) -> dict:
    removed, failed = [], []
    for p in paths:
        try:
            real = assert_managed(p)
            if os.path.isdir(real):
                shutil.rmtree(real)
            else:
                os.remove(real)
                _remove_sidecars(real)
            removed.append(p)
            db.execute("UPDATE items SET status='removed', message='library file deleted via cleanup' "
                       "WHERE dest_path=?", (real,))
            db.execute("DELETE FROM items WHERE source_path=? OR source_path LIKE ?", (real, real + os.sep + "%"))
        except Exception as e:  # noqa: BLE001
            failed.append({"path": p, "error": str(e)})
    if removed:
        db.log("info", f"Deleted {len(removed)} item(s): " + ", ".join(os.path.basename(r) for r in removed[:5]))
    return {"removed": removed, "failed": failed}


def _remove_sidecars(video: str) -> None:
    if not in_library(video):
        return
    stem = os.path.basename(os.path.splitext(video)[0])
    d = os.path.dirname(video)
    try:
        for f in os.listdir(d):
            if f.startswith(stem + ".") and parser.is_sub(f):
                os.remove(os.path.join(d, f))
    except OSError:
        pass


def relink_copies(keep: str, targets: list[str]) -> dict:
    """Replace identical copies with hardlinks to `keep`, reclaiming disk space."""
    keep = assert_managed(keep)
    done, failed = [], []
    for t in targets:
        try:
            real = assert_managed(t)
            if os.path.getsize(real) != os.path.getsize(keep):
                raise RuntimeError("sizes differ")
            if _quick_hash(real, os.path.getsize(real)) != _quick_hash(keep, os.path.getsize(keep)):
                raise RuntimeError("content differs")
            tmp = real + ".hlatmp"
            os.link(keep, tmp)
            os.replace(tmp, real)
            done.append(t)
        except Exception as e:  # noqa: BLE001
            failed.append({"path": t, "error": str(e)})
    if done:
        db.log("info", f"Converted {len(done)} duplicate copy(ies) into hardlinks")
    return {"relinked": done, "failed": failed}


# ---------------------------------------------------------------- orphans

def find_orphans() -> dict:
    client = get_client()
    if not client:
        return {"error": "No torrent client configured - orphan detection needs one.",
                "torrents": [], "files": [], "warnings": []}
    with client:
        torrents = client.torrents(with_files=True)

    min_age = (settings.get("orphan_min_age_days") or 0) * 86400
    now = time.time()
    lib_inodes = set()
    for root in library_roots():
        for dirpath, _d, files in os.walk(root):
            for f in files:
                try:
                    st = os.stat(os.path.join(dirpath, f))
                    lib_inodes.add((st.st_dev, st.st_ino))
                except OSError:
                    pass

    warnings = []
    roots = download_roots()
    outside = [t for t in torrents if t["save_path"] and not any(
        os.path.normpath(t["save_path"]) == r or is_under(t["save_path"], r) for r in roots)]
    if outside:
        warnings.append(f"{len(outside)} torrent(s) save outside your download folders "
                        f"(e.g. {outside[0]['save_path']}). Check the path mappings in Settings.")

    orphan_torrents = []
    torrent_files: set[str] = set()
    for t in torrents:
        torrent_files.update(t["files"])
        vids = [f for f in t["files"] if parser.is_video(f) and not parser.looks_like_extra(f)]
        existing = [f for f in t["files"] if os.path.exists(f)]
        size = 0
        reason = None
        if t["files"] and not existing:
            reason = "missing data - files no longer exist on disk"
        elif t["error"]:
            reason = f"client reports an error ({t['state']})"
        elif t["completed"] and vids and now - (t.get("added_on") or now) >= min_age:
            linked = False
            for f in vids:
                try:
                    st = os.stat(f)
                    size += st.st_size
                    if (st.st_dev, st.st_ino) in lib_inodes:
                        linked = True
                        break
                except OSError:
                    pass
            if not linked:
                reason = "not in library - no media file is hardlinked into your library"
        if reason:
            orphan_torrents.append({"hash": t["hash"], "name": t["name"], "category": t["category"],
                                    "reason": reason, "ratio": t["ratio"], "state": t["state"],
                                    "size": size, "added_on": t.get("added_on")})

    # files in download dirs that belong to no torrent
    orphan_files = []
    if torrents:
        ancestors: set[str] = set()
        for f in torrent_files:
            d = os.path.dirname(f)
            while d and d not in ancestors and d != "/":
                ancestors.add(d)
                d = os.path.dirname(d)
        units: dict[str, dict] = {}
        for root in roots:
            for dirpath, _d, files in os.walk(root):
                for f in files:
                    p = os.path.join(dirpath, f)
                    if p in torrent_files or f.lower().endswith(PARTIAL_EXT) or f.startswith(".hardlinkarr"):
                        continue
                    # group by the top-most folder that contains no torrent data at all
                    highest = None
                    d = os.path.dirname(p)
                    while d != root and len(d) > len(root) and d not in ancestors:
                        highest = d
                        d = os.path.dirname(d)
                    unit = highest or p
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if now - st.st_mtime < 3600:
                        continue  # probably still being written
                    u = units.setdefault(unit, {"path": unit, "size": 0, "files": 0, "linked": 0,
                                                "is_dir": unit != p})
                    u["size"] += st.st_size
                    u["files"] += 1
                    if (st.st_dev, st.st_ino) in lib_inodes:
                        u["linked"] += 1
        orphan_files = sorted(units.values(), key=lambda u: -u["size"])
    else:
        warnings.append("The torrent client returned no torrents, so orphaned files are not listed (safety).")

    return {"torrents": orphan_torrents, "files": orphan_files, "warnings": warnings,
            "torrent_count": len(torrents), "generated": time.time()}


def remove_torrents(hashes: list[str], delete_files: bool) -> dict:
    client = get_client()
    if not client:
        raise RuntimeError("No torrent client configured")
    with client:
        client.delete(hashes, delete_files)
    for h in hashes:
        db.execute("UPDATE items SET message='torrent removed from client' WHERE torrent_hash=?", (h,))
    db.log("info", f"Removed {len(hashes)} torrent(s) from client{' with data' if delete_files else ''}")
    return {"removed": hashes}
