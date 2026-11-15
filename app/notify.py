"""Tell the media server to rescan after new links are created."""
from __future__ import annotations

import httpx

from . import db, settings


def _req(method: str, path: str, url: str | None = None, token: str | None = None,
         kind: str | None = None) -> httpx.Response:
    kind = kind or settings.get("mediaserver_type")
    url = (url or settings.get("mediaserver_url") or "").rstrip("/")
    token = token or settings.get("mediaserver_token")
    if kind == "plex":
        return httpx.request(method, f"{url}{path}", params={"X-Plex-Token": token},
                             headers={"Accept": "application/json"}, timeout=15)
    return httpx.request(method, f"{url}{path}", headers={"X-Emby-Token": token, "X-MediaBrowser-Token": token},
                         timeout=15)


def test(kind: str, url: str, token: str) -> str:
    if kind == "plex":
        r = _req("GET", "/identity", url, token, kind)
        r.raise_for_status()
        return "Plex " + r.json().get("MediaContainer", {}).get("version", "")
    r = _req("GET", "/System/Info", url, token, kind)
    r.raise_for_status()
    d = r.json()
    return f"{d.get('ProductName', kind.title())} {d.get('Version', '')}"


def library_locations() -> list[dict]:
    """[{library, type, path}] for every folder the media server scans."""
    kind = settings.get("mediaserver_type")
    out = []
    if kind == "plex":
        r = _req("GET", "/library/sections")
        r.raise_for_status()
        for d in r.json().get("MediaContainer", {}).get("Directory", []):
            if d.get("type") not in ("movie", "show"):
                continue
            for loc in d.get("Location", []):
                out.append({"library": d.get("title"), "type": d.get("type"), "path": loc.get("path", "")})
    elif kind in ("jellyfin", "emby"):
        r = _req("GET", "/Library/VirtualFolders")
        r.raise_for_status()
        for d in r.json():
            if (d.get("CollectionType") or "") not in ("movies", "tvshows", "mixed", ""):
                continue
            for loc in d.get("Locations", []):
                out.append({"library": d.get("Name"), "type": d.get("CollectionType"), "path": loc})
    return out


_DL_WORDS = ("torrent", "download", "complete", "seed", "incoming")


def _components(p: str) -> list[str]:
    return [c.lower() for c in p.replace("\\", "/").split("/") if c]


def check_libraries() -> dict:
    """Classify each media-server folder as pointing at downloads, the Hardlinkarr library, or unknown.

    The media server may see paths through different mounts, so folders are compared by their
    trailing path components rather than full paths.
    """
    from .paths import download_roots, library_roots

    kind = settings.get("mediaserver_type")
    if kind in (None, "", "none") or not settings.get("mediaserver_url"):
        return {"configured": False, "locations": []}
    try:
        locs = library_locations()
    except (httpx.HTTPError, ValueError) as e:
        return {"configured": True, "error": str(e), "locations": []}

    def tail_match(loc: str, root: str) -> bool:
        lc, rc = _components(loc), _components(root)
        if not rc:
            return False
        # a location equals or sits under the root if the root's last component(s) appear in it
        tail = rc[-2:] if len(rc) >= 2 else rc
        for i in range(len(lc) - len(tail) + 1):
            if lc[i:i + len(tail)] == tail:
                return True
        return len(tail) == 2 and rc[-1] in lc and rc[-1] not in ("movies", "tv", "media", "data")

    for l in locs:
        if any(tail_match(l["path"], r) for r in download_roots()) or any(
                w in c for c in _components(l["path"]) for w in _DL_WORDS):
            l["verdict"] = "downloads"
        elif any(tail_match(l["path"], r) for r in library_roots()):
            l["verdict"] = "library"
        else:
            l["verdict"] = "unknown"
    uses_library = any(l["verdict"] == "library" for l in locs)
    return {"configured": True, "server": kind, "locations": locs,
            "points_at_downloads": [l for l in locs if l["verdict"] == "downloads"],
            "uses_library": uses_library, "library_roots": library_roots()}


def refresh() -> None:
    kind = settings.get("mediaserver_type")
    if kind in (None, "", "none") or not settings.get("mediaserver_url"):
        return
    try:
        if kind == "plex":
            r = _req("GET", "/library/sections/all/refresh")
        else:
            r = _req("POST", "/Library/Refresh")
        r.raise_for_status()
        db.log("info", f"Asked {kind} to refresh its libraries")
    except httpx.HTTPError as e:
        db.log("warning", f"Media server refresh failed: {e}")
