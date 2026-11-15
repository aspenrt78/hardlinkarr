"""Torrent client adapters. Every adapter returns normalised torrent dicts:

{
  "hash": str, "name": str, "category": str, "save_path": str (local path),
  "files": [local absolute paths], "progress": 0..1, "completed": bool,
  "state": str, "ratio": float, "added_on": epoch, "error": bool
}
"""
from __future__ import annotations

import posixpath

from .. import settings
from .base import TorrentClient


def map_path(remote: str) -> str:
    """Translate a path reported by the torrent client into one visible here."""
    if not remote:
        return remote
    remote = remote.replace("\\", "/")
    best = None
    for m in settings.get("path_mappings") or []:
        r = (m.get("remote") or "").replace("\\", "/").rstrip("/")
        if r and (remote == r or remote.startswith(r + "/")):
            if best is None or len(r) > len(best[0]):
                best = (r, (m.get("local") or "").rstrip("/"))
    if best:
        remote = best[1] + remote[len(best[0]):]
    return posixpath.normpath(remote)


def content_name(t: dict) -> str:
    """The torrent's top-level file/folder on disk. It can differ from the display name."""
    save = (t.get("save_path") or "").rstrip("/")
    content = t.get("content_path") or ""
    if save and content.startswith(save + "/"):
        return content[len(save) + 1:].split("/")[0]
    return t.get("name") or ""


def suggest_mappings(torrents: list[dict], limit: int = 300) -> list[dict]:
    """Work out the path mappings needed for torrents whose data can't be found here.

    For every torrent whose ``save_path/name`` doesn't exist, try replacing a leading part of
    its save path with one of the folders this container can see, and keep the replacement
    that makes the torrent's data appear. Returns ``[{remote, local, count}]``.
    """
    import os
    from collections import Counter, defaultdict

    roots = [d for d in (settings.get("download_dirs") or []) if d]
    roots += [settings.get(k) for k in ("movies_dir", "tv_dir", "anime_dir") if settings.get(k)]
    cands: list[str] = []
    for r in roots:
        r = posixpath.normpath(r)
        cands.append(r)
        parent = posixpath.dirname(r)
        try:
            cands += [posixpath.join(parent, e.name) for e in os.scandir(parent) if e.is_dir()]
        except OSError:
            pass
    cands = list(dict.fromkeys(cands))

    votes: dict[str, Counter] = defaultdict(Counter)
    for t in torrents[:limit]:
        save, name = (t.get("save_path") or "").rstrip("/"), content_name(t)
        if not save.startswith("/") or not name or os.path.exists(posixpath.join(save, name)):
            continue
        parts = save.strip("/").split("/")
        for i in range(1, len(parts) + 1):  # shortest prefix first: /movies before /movies/sub
            prefix = "/" + "/".join(parts[:i])
            rest = "/".join(parts[i:])
            hit = next((c for c in cands if os.path.exists(posixpath.join(c, rest, name))), None)
            if hit:
                votes[prefix][hit] += 1
                break
    out = []
    for prefix, c in votes.items():
        local, n = c.most_common(1)[0]
        if local != prefix:
            out.append({"remote": prefix, "local": local, "count": n})
    return sorted(out, key=lambda m: -m["count"])


def auto_add_mappings(torrents: list[dict]) -> list[dict]:
    """Detect missing mappings and save them. Returns the mappings that were added."""
    found = suggest_mappings(torrents)
    current = list(settings.get("path_mappings") or [])
    known = {(m.get("remote") or "").rstrip("/") for m in current}
    added = [{"remote": m["remote"], "local": m["local"]} for m in found if m["remote"] not in known]
    if added:
        settings.save({"path_mappings": current + added})
    return added


def get_client() -> TorrentClient | None:
    kind = settings.get("client_type")
    url = settings.get("client_url")
    user = settings.get("client_username")
    pw = settings.get("client_password")
    if kind == "qbittorrent":
        from .qbittorrent import QBittorrent
        return QBittorrent(url, user, pw)
    if kind == "transmission":
        from .transmission import Transmission
        return Transmission(url, user, pw)
    if kind == "deluge":
        from .deluge import Deluge
        return Deluge(url, user, pw)
    return None
