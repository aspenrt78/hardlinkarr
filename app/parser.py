"""Release-name parsing (guessit) plus extraction of IDs from .nfo files and folder names."""
from __future__ import annotations

import os
import re
from functools import lru_cache

from guessit import guessit

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".flv"}
SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}

IMDB_RE = re.compile(r"\b(tt\d{7,9})\b")
TMDB_RE = re.compile(r"(?:tmdb[-_ =:]?(?:id)?[-_ =:]?|themoviedb\.org/(?:movie|tv)/)(\d{2,8})", re.I)
TVDB_RE = re.compile(r"(?:tvdb[-_ =:]?(?:id)?[-_ =:]?|thetvdb\.com/.*?[?&]id=)(\d{2,8})", re.I)
EXTRAS_RE = re.compile(r"(^|[\W_])(sample|trailer|featurette|behind[ ._-]the[ ._-]scenes|deleted[ ._-]scenes|"
                       r"extras?|bonus|interview|making[ ._-]of)([\W_]|$)", re.I)


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def is_sub(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in SUB_EXT


def looks_like_extra(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")[-3:]
    return any(EXTRAS_RE.search(p) for p in parts)


@lru_cache(maxsize=4096)
def _guess(name: str, type_hint: str | None) -> dict:
    opts = {"type": type_hint} if type_hint in ("movie", "episode") else {}
    try:
        return dict(guessit(name, opts))
    except Exception:  # guessit can choke on odd strings
        return {}


def parse(path: str, type_hint: str | None = None) -> dict:
    """Parse a media file path into title/year/season/episode/etc.

    Combines guesses from the filename and the parent folder because releases often
    carry the useful information (year, show name) on only one of them.
    """
    path = path.replace("\\", "/")
    fname = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    grand = os.path.basename(os.path.dirname(os.path.dirname(path)))

    g = _guess(fname, type_hint)
    # "Season 1/01.mkv" style: use more of the path
    if not g.get("title") or len(str(g.get("title", ""))) < 2 or re.fullmatch(r"(?i)(e?p?\d+)", str(g.get("title"))):
        g = _guess(f"{grand}/{parent}/{fname}", type_hint)
    pg = _guess(parent, type_hint) if parent else {}

    kind = g.get("type") or pg.get("type") or "movie"
    if type_hint == "tv":
        kind = "episode"
    elif type_hint == "movie":
        kind = "movie"

    title = g.get("title") or pg.get("title") or ""
    year = g.get("year") or pg.get("year")
    season = g.get("season", pg.get("season"))
    episode = g.get("episode")
    if kind == "episode" and season is None:
        season = 1 if g.get("episode") is not None else None
    if isinstance(season, list):
        season = season[0]
    episodes = episode if isinstance(episode, list) else ([episode] if episode is not None else [])
    absolute = g.get("absolute_episode")
    if kind == "episode" and not episodes and absolute is not None:
        episodes = absolute if isinstance(absolute, list) else [absolute]

    edition = g.get("edition")
    if isinstance(edition, list):
        edition = " ".join(edition)
    other = g.get("other") or []
    if isinstance(other, str):
        other = [other]
    source = g.get("source")
    if isinstance(source, list):
        source = source[0]

    return {
        "kind": "episode" if kind == "episode" else "movie",
        "title": str(title).strip(),
        "alt_title": str(pg.get("title") or "").strip(),
        "year": int(year) if year else None,
        "season": int(season) if season is not None else None,
        "episodes": [int(e) for e in episodes if isinstance(e, int)],
        "episode_title": g.get("episode_title"),
        "resolution": g.get("screen_size"),
        "source": source,
        "hdr": "HDR" if any(o in ("HDR10", "HDR", "Dolby Vision", "HDR10+") for o in other) else None,
        "edition": edition,
        "part": g.get("cd") or g.get("part") if kind != "episode" else None,
        "country": getattr(g.get("country"), "alpha2", None),
        "is_date": bool(g.get("date")),
        "date": str(g.get("date")) if g.get("date") else None,
        "is_anime": bool(g.get("release_group")) and bool(re.search(r"\[[^\]]+\]", fname)) and absolute is not None,
    }


def scan_ids(directory: str, name_hint: str = "") -> dict:
    """Look for IMDb / TMDB / TVDB ids in .nfo files and folder names."""
    ids: dict[str, str] = {}
    texts = [name_hint, os.path.basename(directory)]
    try:
        for f in os.listdir(directory)[:200]:
            if f.lower().endswith(".nfo"):
                p = os.path.join(directory, f)
                if os.path.getsize(p) < 512_000:
                    with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                        texts.append(fh.read())
    except OSError:
        pass
    blob = "\n".join(texts)
    if m := IMDB_RE.search(blob):
        ids["imdb"] = m.group(1)
    if m := TMDB_RE.search(blob):
        ids["tmdb"] = m.group(1)
    if m := TVDB_RE.search(blob):
        ids["tvdb"] = m.group(1)
    return ids


RES_RANK = {"480p": 1, "576p": 2, "720p": 3, "1080i": 4, "1080p": 5, "1440p": 6, "2160p": 7, "4320p": 8}


def quality_rank(resolution: str | None, size: int = 0) -> tuple:
    return (RES_RANK.get(resolution or "", 0), size)
