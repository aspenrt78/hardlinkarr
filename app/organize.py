"""Rename files already in the library (incl. existing hardlinks) to the configured naming format.

Renaming a hardlink only changes that one directory entry - the torrent's copy (the other link)
is untouched, so seeding continues and no extra space is used.
"""
from __future__ import annotations

import os
import shutil
import time

import httpx

from . import db, linker, metadata, parser, settings
from .paths import assert_managed, in_library, library_roots

_arr_cache: dict = {"ts": 0, "folders": set()}


def arr_managed_folders() -> set[str]:
    """Folder names (basename) of every movie/series Radarr/Sonarr manage.

    Compared by name because the arrs see the library through their own mount paths.
    """
    if time.time() - _arr_cache["ts"] < 600:
        return _arr_cache["folders"]
    names: set[str] = set()
    for kind, endpoint in (("radarr", "movie"), ("sonarr", "series")):
        url, key = settings.get(f"{kind}_url"), settings.get(f"{kind}_api_key")
        if not url or not key:
            continue
        try:
            r = httpx.get(f"{url.rstrip('/')}/api/v3/{endpoint}", headers={"X-Api-Key": key}, timeout=30)
            if r.status_code == 200:
                for x in r.json():
                    if x.get("path") and (x.get("hasFile") or x.get("statistics", {}).get("episodeFileCount")):
                        names.add(x["path"].replace("\\", "/").rstrip("/").rsplit("/", 1)[-1])
        except (httpx.HTTPError, ValueError):
            db.log("warning", f"Could not read {kind} library to protect arr-managed folders")
    _arr_cache.update(ts=time.time(), folders=names)
    return names


_torrent_cache: dict = {"ts": 0, "files": frozenset()}


def torrent_files(max_age: float = 120) -> frozenset[str]:
    """Every file the torrent client is seeding or downloading (paths as seen here).

    Those must never be renamed: the client would lose them and stop seeding. If the client is
    configured but can't be reached this raises, so nothing is renamed on a guess.
    """
    from .clients import get_client
    if time.time() - _torrent_cache["ts"] < max_age:
        return _torrent_cache["files"]
    client = get_client()
    files: set[str] = set()
    if client:
        try:
            with client:
                for t in client.torrents(with_files=True):
                    files.update(os.path.normpath(f) for f in t["files"])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"cannot read the torrent client, so files it seeds can't be protected: {e}") from e
    _torrent_cache.update(ts=time.time(), files=frozenset(files))
    return _torrent_cache["files"]


def _roots() -> list[tuple[str, str]]:
    s = settings.load()
    out = []
    if s.get("movies_dir"):
        out.append((os.path.normpath(s["movies_dir"]), "movie"))
    for k in ("tv_dir", "anime_dir"):
        if s.get(k):
            out.append((os.path.normpath(s[k]), "tv"))
    return list(dict.fromkeys(out))


def _top_folder(path: str, root: str) -> str | None:
    rel = os.path.relpath(path, root)
    first = rel.replace("\\", "/").split("/")[0]
    return os.path.join(root, first) if first != rel.replace("\\", "/") else None


def plan_file(path: str, root: str, kind: str, memo: dict | None = None) -> dict:
    """Work out where a library file should live. Returns a move proposal."""
    folder = _top_folder(path, root)
    fields = linker.identify_file(path, None, None, memo, type_hint=kind, folder=folder)
    entry = {"src": path, "dest": None, "confidence": fields.get("confidence", 0),
             "title": fields.get("title"), "year": fields.get("year"), "tmdb_id": fields.get("tmdb_id"),
             "kind": kind, "message": ""}
    if not fields.get("tmdb_id"):
        entry["message"] = "could not identify"
        return entry
    if fields["media_type"] == "episode" and not fields.get("episodes"):
        entry["message"] = "episode number not found"
        return entry
    m = {k: v for k, v in fields.items() if not k.startswith("_")}
    entry["dest"] = linker.build_dest({**m, "part": fields["_part"], "is_anime": fields["_is_anime"]},
                                      os.path.splitext(path)[1].lower())
    try:
        st = os.stat(path)
        entry.update(size=st.st_size, nlink=st.st_nlink)
    except OSError:
        pass
    if fields["media_type"] == "episode":
        entry["episode"] = f"S{(fields.get('season') or 0):02d}E" + "-E".join(f"{e:02d}" for e in fields["episodes"])
    return entry


def plan() -> dict:
    s = settings.load()
    threshold = float(s.get("match_threshold") or 0.8)
    managed = arr_managed_folders() if s.get("skip_arr_managed") else set()
    min_size = (s.get("min_video_mb") or 0) * 1024 * 1024
    memo: dict = {}
    moves, tidy, skipped, unknown, seeding = [], 0, [], [], []
    try:
        seeded = torrent_files(max_age=0)
    except RuntimeError as ex:
        return {"error": str(ex)}
    for root, kind in _roots():
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                if not parser.is_video(p) or parser.looks_like_extra(p) or f.endswith(".hlatmp"):
                    continue
                try:
                    if os.path.getsize(p) < min_size:
                        continue
                except OSError:
                    continue
                top = _top_folder(p, root)
                if top and os.path.basename(top) in managed:
                    skipped.append(p)
                    continue
                if os.path.normpath(p) in seeded:
                    seeding.append(p)
                    continue
                try:
                    e = plan_file(p, root, kind, memo)
                except metadata.MetadataError as ex:
                    return {"error": str(ex)}
                if not e["dest"]:
                    unknown.append(e)
                elif os.path.normcase(os.path.normpath(e["dest"])) == os.path.normcase(os.path.normpath(p)):
                    tidy += 1
                else:
                    e["confident"] = e["confidence"] >= threshold
                    e["conflict"] = os.path.lexists(e["dest"]) and not _same(p, e["dest"])
                    moves.append(e)
    # two files that want the same destination: only the best keeps it
    by_dest: dict[str, list] = {}
    for e in moves:
        by_dest.setdefault(os.path.normcase(e["dest"]), []).append(e)
    for group in by_dest.values():
        if len(group) > 1:
            for e in group:
                e["conflict"] = True
                e["message"] = "another file maps to the same name (duplicate?) - see Cleanup → Duplicates"
    moves.sort(key=lambda e: (not e["confident"], e["conflict"], e["src"]))
    return {"moves": moves, "tidy": tidy, "arr_managed": len(skipped), "unknown": unknown,
            "seeding": len(seeding), "seeding_examples": seeding[:5], "generated": time.time()}


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def move(src: str, dest: str) -> str:
    """Rename one library file (and its subtitles / artwork). Returns 'renamed' | 'merged'."""
    src, dest = assert_managed(src), assert_managed(dest)
    if not in_library(src) or not in_library(dest):
        raise PermissionError("both paths must be inside the library folders")
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    if os.path.normpath(src) in torrent_files():
        raise PermissionError("this file belongs to an active torrent - renaming it would stop it seeding")
    old_dir = os.path.dirname(src)
    if os.path.lexists(dest):
        if not _same(src, dest):
            raise FileExistsError(f"a different file already exists: {dest}")
        os.remove(src)  # dest is another link to the same data - just drop the extra name
        result = "merged"
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.rename(src, dest)
        result = "renamed"

    # subtitles that belong to this video
    sstem, dstem = os.path.splitext(os.path.basename(src))[0], os.path.splitext(dest)[0]
    for f in os.listdir(old_dir):
        if f.startswith(sstem + ".") and parser.is_sub(f):
            target = dstem + f[len(sstem):]
            if not os.path.exists(target):
                os.rename(os.path.join(old_dir, f), target)

    # a movie folder with no video left: carry artwork / nfo across, then remove it
    new_dir = os.path.dirname(dest)
    if old_dir != new_dir and old_dir not in library_roots() and not any(parser.is_video(f) for f in os.listdir(old_dir)) \
            and not any(os.path.isdir(os.path.join(old_dir, x)) for x in os.listdir(old_dir)):
        for f in os.listdir(old_dir):
            t = os.path.join(new_dir, f)
            if not os.path.exists(t):
                shutil.move(os.path.join(old_dir, f), t)
    linker._prune_empty(old_dir)

    db.execute("UPDATE items SET dest_path=?, updated=? WHERE dest_path=?", (dest, time.time(), src))
    return result


def apply(moves: list[dict]) -> dict:
    done, failed = [], []
    for m in moves:
        try:
            done.append({"src": m["src"], "dest": m["dest"], "result": move(m["src"], m["dest"])})
        except Exception as e:  # noqa: BLE001
            failed.append({"src": m["src"], "error": str(e)})
    if done:
        db.log("info", f"Renamed {len(done)} library file(s) to the naming format")
    return {"done": done, "failed": failed}


def auto_rename(lib_path: str, memo: dict | None = None) -> str | None:
    """During scans: rename an existing library link if confident. Returns the new path or None."""
    s = settings.load()
    root_kind = next(((r, k) for r, k in _roots() if lib_path.startswith(r + os.sep)), None)
    if not root_kind:
        return None
    top = _top_folder(lib_path, root_kind[0])
    if s.get("skip_arr_managed") and top and os.path.basename(top) in arr_managed_folders():
        return None
    try:
        if os.path.normpath(lib_path) in torrent_files():
            return None
    except RuntimeError:
        return None
    e = plan_file(lib_path, root_kind[0], root_kind[1], memo)
    if not e["dest"] or e["confidence"] < float(s.get("match_threshold") or 0.8):
        return None
    if os.path.normcase(e["dest"]) == os.path.normcase(lib_path):
        return lib_path
    if s.get("dry_run"):
        return None
    try:
        move(lib_path, e["dest"])
        db.log("info", f"Renamed existing link {os.path.basename(lib_path)} -> {e['dest']}")
        return e["dest"]
    except OSError as ex:
        db.log("warning", f"Could not rename {lib_path}: {ex}")
        return None
