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
