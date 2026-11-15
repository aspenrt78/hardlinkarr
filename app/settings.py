"""User-editable settings stored in SQLite, with sensible defaults."""
from __future__ import annotations

import copy
import json
from typing import Any

from . import db

DEFAULTS: dict[str, Any] = {
    "setup_complete": False,
    # ---- paths (as seen *inside* this container)
    "download_dirs": ["/data/torrents"],
    "movies_dir": "/data/media/movies",
    "tv_dir": "/data/media/tv",
    "anime_dir": "",  # optional; empty = use tv_dir
    # ---- metadata
    "tmdb_api_key": "",          # v3 key or v4 read access token
    "tmdb_language": "en-US",
    "match_threshold": 0.80,     # auto-link at or above this confidence
    # ---- torrent client
    "client_type": "none",       # none | qbittorrent | transmission | deluge
    "client_url": "http://qbittorrent:8080",
    "client_username": "admin",
    "client_password": "",
    # client reports /downloads/x but we see /data/torrents/x
    "path_mappings": [],         # [{"remote": "/downloads", "local": "/data/torrents"}]
    "include_categories": [],    # empty = all
    "exclude_categories": [],    # e.g. radarr / tv-sonarr if the arrs already import them
    "category_types": {},        # {"movies": "movie", "tv": "tv"}
    # ---- *arr integration (optional, used as an extra identification source)
    "radarr_url": "", "radarr_api_key": "",
    "sonarr_url": "", "sonarr_api_key": "",
    # ---- media server refresh (optional)
    "mediaserver_type": "none",  # none | plex | jellyfin | emby
    "mediaserver_url": "",
    "mediaserver_token": "",
    # ---- naming
    "naming_preset": "standard",
    "movie_folder": "{title} ({year})",
    "movie_file": "{title} ({year}){edition_tag}{quality_tag}",
    "tv_folder": "{show} ({year})",
    "season_folder": "Season {season:02d}",
    "episode_file": "{show} - S{season:02d}{episode_tag} - {episode_title}{quality_tag}",
    "include_quality": True,
    "link_subtitles": True,
    "min_video_mb": 50,          # skip samples / extras below this size
    # ---- automation
    "auto_link": True,
    "dry_run": False,
    "scan_interval_minutes": 5,
    "upgrade_existing": False,   # replace library link when new release is higher resolution
    "rename_existing_links": False,  # rename library links found during scans to the naming format
    "skip_arr_managed": True,    # never rename files inside Radarr/Sonarr-managed folders
    # ---- cleanup
    "junk_extensions": [".nfo", ".txt", ".jpg", ".jpeg", ".png", ".url", ".exe", ".db", ".ds_store", ".sfv", ".md5"],
    "orphan_min_age_days": 2,
    # ---- web ui auth (optional)
    "ui_username": "",
    "ui_password": "",
}

SECRET_KEYS = {"tmdb_api_key", "client_password", "radarr_api_key", "sonarr_api_key",
               "mediaserver_token", "ui_password"}
MASK = "••••••••"

_cache: dict[str, Any] | None = None


# old defaults that were replaced - upgraded automatically if the user never changed them
_MIGRATE = {
    "movie_folder": ["{title} ({year}) {{tmdb-{tmdb_id}}}"],
    "tv_folder": ["{show} ({year}) {{tmdb-{tmdb_id}}}"],
}


def load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        data = copy.deepcopy(DEFAULTS)
        for row in db.query("SELECT key, value FROM settings"):
            try:
                data[row["key"]] = json.loads(row["value"])
            except ValueError:
                pass
        for k, olds in _MIGRATE.items():
            if data.get(k) in olds:
                data[k] = DEFAULTS[k]
        _cache = data
    return _cache


def get(key: str) -> Any:
    return load().get(key, DEFAULTS.get(key))


def save(values: dict[str, Any]) -> dict[str, Any]:
    global _cache
    current = load()
    for k, v in values.items():
        if k not in DEFAULTS:
            continue
        if k in SECRET_KEYS and v == MASK:
            continue  # unchanged secret
        current[k] = v
        db.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, json.dumps(v)),
        )
    _cache = current
    return current


def public() -> dict[str, Any]:
    """Settings for the UI with secrets masked."""
    out = copy.deepcopy(load())
    for k in SECRET_KEYS:
        if out.get(k):
            out[k] = MASK
    return out
