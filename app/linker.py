"""Identify a downloaded file and hardlink it into the library under a clean name."""
from __future__ import annotations

import os
import re
from typing import Any

from . import db, metadata, parser, settings
from .paths import in_library, library_roots, safe_name

LANG_RE = re.compile(r"[._ -]((?:[a-z]{2,3})(?:[-_][A-Za-z]{2})?|english|spanish|french|german|italian|"
                     r"portuguese|dutch|swedish|norwegian|danish|finnish|polish|russian|japanese|korean|"
                     r"chinese|arabic|hebrew|turkish|greek|czech|hungarian|forced|sdh|cc)$", re.I)


# ---------------------------------------------------------------- library inode index

class LibraryIndex:
    """(dev, inode) -> library path, so we never double-link files the *arrs already imported."""

    def __init__(self) -> None:
        self.by_inode: dict[tuple[int, int], str] = {}
        self.paths: set[str] = set()

    def build(self) -> "LibraryIndex":
        for root in library_roots():
            for dirpath, _dirs, files in os.walk(root):
                for f in files:
                    p = os.path.join(dirpath, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    self.by_inode[(st.st_dev, st.st_ino)] = p
                    self.paths.add(p)
        return self

    def add(self, path: str) -> None:
        try:
            st = os.stat(path)
            self.by_inode[(st.st_dev, st.st_ino)] = path
            self.paths.add(path)
        except OSError:
            pass


# ---------------------------------------------------------------- naming

class _Fmt(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _render(template: str, values: dict) -> str:
    try:
        return template.format_map(_Fmt(values))
    except (ValueError, KeyError, IndexError):
        return template


def _tidy(s: str) -> str:
    s = re.sub(r"\(\s*\)|\{tmdb-(?:None|)\}|\{edition-\}", "", s)
    s = re.sub(r"\s+-\s*(?=\s*-|$)", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    return safe_name(s)


def build_dest(m: dict, ext: str) -> str:
    """m: item-like dict with media_type, title, year, tmdb_id, season, episodes, ..."""
    s = settings.load()
    quality = f" - {m['resolution']}" if s.get("include_quality") and m.get("resolution") else ""
    vals: dict[str, Any] = {
        "title": m.get("title") or "", "show": m.get("title") or "", "year": m.get("year") or "",
        "tmdb_id": m.get("tmdb_id") or "", "imdb_id": m.get("imdb_id") or "",
        "resolution": m.get("resolution") or "", "quality_tag": quality,
        "edition": m.get("edition") or "",
        "edition_tag": f" {{edition-{m['edition']}}}" if m.get("edition") else "",
    }
    if m["media_type"] == "movie":
        part = f" - pt{m['part']}" if m.get("part") else ""
        folder = _tidy(_render(s["movie_folder"], vals))
        name = _tidy(_render(s["movie_file"], vals) + part)
        return os.path.join(s["movies_dir"], folder, name + ext)

    eps = m.get("episodes") or []
    season = int(m.get("season") or 0)
    ep_tag = "".join(f"E{e:02d}" for e in eps[:1])
    if len(eps) > 1:
        ep_tag += f"-E{eps[-1]:02d}"
    vals.update(season=season, episode=eps[0] if eps else 0, episode_tag=ep_tag,
                episode_title=m.get("episode_title") or "")
    root = s["anime_dir"] if m.get("is_anime") and s.get("anime_dir") else s["tv_dir"]
    folder = _tidy(_render(s["tv_folder"], vals))
    sfolder = "Specials" if season == 0 else _tidy(_render(s["season_folder"], vals))
    name = _tidy(_render(s["episode_file"], vals))
    return os.path.join(root, folder, sfolder, name + ext)


def preview_examples() -> list[str]:
    movie = {"media_type": "movie", "title": "Blade Runner 2049", "year": 2017, "tmdb_id": 335984,
             "resolution": "2160p", "edition": None}
    ep = {"media_type": "episode", "title": "Severance", "year": 2022, "tmdb_id": 95396, "season": 1,
          "episodes": [1], "episode_title": "Good News About Hell", "resolution": "1080p"}
    multi = dict(ep, episodes=[2, 3], episode_title="Half Loop")
    return [build_dest(movie, ".mkv"), build_dest(ep, ".mkv"), build_dest(multi, ".mkv")]


# ---------------------------------------------------------------- linking primitives

class LinkConflict(Exception):
    def __init__(self, existing: str):
        super().__init__(existing)
        self.existing = existing


def hardlink(src: str, dest: str, replace: bool = False) -> str:
    """Returns 'linked' | 'already'. Raises LinkConflict if a different file is in the way."""
    if os.path.lexists(dest):
        if os.path.samefile(src, dest):
            return "already"
        if not replace:
            raise LinkConflict(dest)
        tmp = dest + ".hlatmp"
        os.link(src, tmp)
        os.replace(tmp, dest)
        return "linked"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.link(src, dest)
    except OSError as e:
        if e.errno == 18:
            raise OSError(18, "Cross-device link: downloads and library must be on the same filesystem "
                              "and mounted through the same volume") from e
        raise
    return "linked"


def _sub_lang(sub_path: str, video_stem: str) -> str:
    stem = os.path.splitext(os.path.basename(sub_path))[0]
    if stem.lower().startswith(video_stem.lower()):
        rest = stem[len(video_stem):].strip("._ -")
        if rest:
            return "." + rest.replace(" ", ".")
    tags = []
    s = stem
    while (m := LANG_RE.search(s)) and len(tags) < 3:
        tags.insert(0, m.group(1).lower())
        s = s[: m.start()]
    if not tags:
        # Subs/2_English.srt style
        m = re.search(r"(?i)(english|spanish|french|german|italian|portuguese|dutch|[a-z]{2,3})$", stem)
        if m:
            tags = [m.group(1).lower()]
    return "." + ".".join(tags) if tags else ""


def link_subtitles(video_src: str, video_dest: str, single_video: bool, dry: bool) -> int:
    vdir = os.path.dirname(video_src)
    vstem = os.path.splitext(os.path.basename(video_src))[0]
    dstem = os.path.splitext(video_dest)[0]
    candidates: list[str] = []
    for d in (vdir, os.path.join(vdir, "Subs"), os.path.join(vdir, "subs"), os.path.join(vdir, "Subs", vstem)):
        if os.path.isdir(d):
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if parser.is_sub(p) and os.path.isfile(p):
                    candidates.append(p)
    count, used = 0, set()
    for p in sorted(set(candidates)):
        stem = os.path.splitext(os.path.basename(p))[0]
        belongs = stem.lower().startswith(vstem.lower()) or os.path.dirname(p).endswith(vstem) or (
            single_video and not parser.is_video(stem))
        if not belongs:
            continue
        tag = _sub_lang(p, vstem)
        dest = f"{dstem}{tag}{os.path.splitext(p)[1].lower()}"
        n = 2
        while dest in used:
            dest = f"{dstem}{tag}.{n}{os.path.splitext(p)[1].lower()}"
            n += 1
        used.add(dest)
        if dry:
            count += 1
            continue
        try:
            if hardlink(p, dest) == "linked":
                count += 1
        except (LinkConflict, OSError):
            pass
    return count


# ---------------------------------------------------------------- pipeline

def _type_hint(category: str | None) -> str | None:
    if not category:
        return None
    mapping = settings.get("category_types") or {}
    if category in mapping:
        return mapping[category]
    c = category.lower()
    if any(k in c for k in ("movie", "film", "radarr")):
        return "movie"
    if any(k in c for k in ("tv", "show", "series", "sonarr", "anime")):
        return "tv"
    return None


def _abs_to_season(tv_id: int, absolute: int) -> tuple[int, int] | None:
    info = metadata.details("tv", tv_id) or {}
    total = 0
    for s in sorted(x for x in (info.get("seasons") or []) if x and x > 0):
        eps = metadata.season_episodes(tv_id, s)
        if absolute <= total + len(eps):
            return s, absolute - total
        total += len(eps)
    return None


def identify_file(path: str, torrent_name: str | None, category: str | None,
                  memo: dict | None = None, type_hint: str | None = None,
                  folder: str | None = None) -> dict:
    """Parse + identify a file; returns item fields (without status).

    `folder` is the movie/show folder for files already in the library: its name
    ("Show Name (2019) {tmdb-123}") and .nfo files are more reliable than the file name.
    """
    hint = type_hint or _type_hint(category)
    parsed = parser.parse(path, "episode" if hint == "tv" else hint)
    release = torrent_name or os.path.basename(os.path.dirname(path))
    ids = parser.scan_ids(os.path.dirname(path), torrent_name or "")
    if folder:
        fname = os.path.basename(folder)
        fp = parser.parse(fname + ".mkv", "episode" if hint == "tv" else "movie")
        if fp["title"]:
            parsed = dict(parsed, title=fp["title"], alt_title=parsed["title"],
                          year=fp["year"] or (parsed["year"] if hint != "tv" else None))
        if hint == "tv":
            release = fname
        folder_ids = parser.scan_ids(folder, fname)
        # episode .nfo files carry *episode* ids, so for TV trust only the show folder
        ids = folder_ids if hint == "tv" else {**ids, **folder_ids}
    key = (metadata.normalize(parsed["title"]), parsed.get("year"), parsed["kind"], tuple(sorted(ids.items())))
    if memo is not None and key in memo:
        match = memo[key]
    else:
        match = metadata.identify(parsed, release if torrent_name else os.path.basename(path), ids, hint)
        if memo is not None:
            memo[key] = match
    fields = {
        "media_type": "episode" if match.get("kind") == "tv" else "movie",
        "tmdb_id": match.get("tmdb_id"), "imdb_id": match.get("imdb_id"),
        "title": match.get("title") or parsed["title"], "year": match.get("year") or parsed.get("year"),
        "season": parsed.get("season"), "episodes": parsed.get("episodes") or [],
        "episode_title": None, "resolution": parsed.get("resolution"), "edition": parsed.get("edition"),
        "confidence": match.get("confidence", 0), "match_source": match.get("source"),
        "_part": parsed.get("part"), "_is_anime": match.get("is_anime") or parsed.get("is_anime"),
        "_candidates": match.get("candidates", []), "_parsed": parsed,
    }
    if fields["media_type"] == "episode" and fields["tmdb_id"]:
        _fill_episode(fields, parsed)
    return fields


def _fill_episode(fields: dict, parsed: dict) -> None:
    tv_id = fields["tmdb_id"]
    if parsed.get("is_date") and parsed.get("date") and not fields["episodes"]:
        hit = metadata.episode_by_date(tv_id, parsed["date"])
        if hit:
            fields["season"], ep, fields["episode_title"] = hit[0], hit[1], hit[2]
            fields["episodes"] = [ep]
            return
    if not fields["episodes"]:
        fields["confidence"] = min(fields["confidence"], 0.5)
        return
    season = fields["season"] if fields["season"] is not None else 1
    eps = metadata.season_episodes(tv_id, season)
    first = fields["episodes"][0]
    if first not in eps and (fields["season"] in (None, 1)):
        # absolute numbering (common for anime)
        mapped = _abs_to_season(tv_id, first)
        if mapped:
            season = mapped[0]
            offset = first - mapped[1]
            fields["episodes"] = [e - offset for e in fields["episodes"]]
            eps = metadata.season_episodes(tv_id, season)
    fields["season"] = season
    names = [eps[e]["name"] for e in fields["episodes"] if e in eps and eps[e].get("name")]
    if names:
        fields["episode_title"] = names[0] if len(set(names)) == 1 else " + ".join(dict.fromkeys(names))
        fields["episode_title"] = re.sub(r"\s*\(\d\)$", "", fields["episode_title"])[:120]
    elif eps:
        fields["confidence"] = min(fields["confidence"], 0.6)  # episode not on TMDB


def _item_from_fields(path: str, st: os.stat_result, fields: dict, torrent: dict | None) -> dict:
    return {
        "source_path": path, "size": st.st_size, "inode": st.st_ino, "dev": st.st_dev,
        "torrent_hash": (torrent or {}).get("hash"), "torrent_name": (torrent or {}).get("name"),
        "category": (torrent or {}).get("category"),
        **{k: v for k, v in fields.items() if not k.startswith("_")},
    }


def process_file(path: str, torrent: dict | None, index: LibraryIndex, memo: dict | None = None,
                 force: bool = False) -> str:
    """Process one video file. Returns the resulting status."""
    s = settings.load()
    try:
        st = os.stat(path)
    except OSError as e:
        return _err(path, torrent, f"cannot stat: {e.strerror}")

    existing = db.one("SELECT id, status, inode FROM items WHERE source_path=?", (path,))
    if existing and not force and existing["inode"] == st.st_ino:
        if existing["status"] == "planned" and not s.get("dry_run"):
            return apply_link(existing["id"], index=index)  # dry run was switched off
        if existing["status"] not in ("pending", "error"):
            return existing["status"]  # errors are retried every scan

    if st.st_size < (s.get("min_video_mb") or 0) * 1024 * 1024 or parser.looks_like_extra(path):
        db.upsert_item({"source_path": path, "size": st.st_size, "inode": st.st_ino, "dev": st.st_dev,
                        "torrent_hash": (torrent or {}).get("hash"), "torrent_name": (torrent or {}).get("name"),
                        "status": "ignored", "message": "sample / extra / below minimum size"})
        return "ignored"

    # Already in the library (e.g. imported by Radarr/Sonarr via hardlink)? Don't create another link.
    lib = index.by_inode.get((st.st_dev, st.st_ino)) if st.st_nlink > 1 else None
    if lib and not force:
        msg = "already hardlinked into library"
        if s.get("rename_existing_links"):
            from . import organize
            try:
                new = organize.auto_rename(lib, memo)
            except metadata.MetadataError:
                new = None
            if new and new != lib:
                index.add(new)
                lib, msg = new, "existing library link renamed to naming format"
        db.upsert_item({"source_path": path, "size": st.st_size, "inode": st.st_ino, "dev": st.st_dev,
                        "torrent_hash": (torrent or {}).get("hash"), "torrent_name": (torrent or {}).get("name"),
                        "category": (torrent or {}).get("category"), "dest_path": lib,
                        "status": "exists", "message": msg, "confidence": 1})
        return "exists"

    try:
        fields = identify_file(path, (torrent or {}).get("name"), (torrent or {}).get("category"), memo)
    except metadata.MetadataError as e:
        return _err(path, torrent, str(e), st)

    item = _item_from_fields(path, st, fields, torrent)
    if not item["tmdb_id"]:
        item.update(status="review", message="no TMDB match found - pick one manually")
        db.upsert_item(item)
        return "review"

    item["dest_path"] = build_dest({**item, "part": fields["_part"], "is_anime": fields["_is_anime"]},
                                   os.path.splitext(path)[1].lower())
    if item["confidence"] < float(s.get("match_threshold") or 0.8):
        item.update(status="review", message=f"low confidence ({item['confidence']:.0%}) - please confirm")
        db.upsert_item(item)
        return "review"
    if not s.get("auto_link") and not force:
        item.update(status="review", message="auto-link disabled - approve to link")
        db.upsert_item(item)
        return "review"

    item_id = db.upsert_item({**item, "status": "pending", "message": "linking"})
    return apply_link(item_id, index=index)


def apply_link(item_id: int, index: LibraryIndex | None = None, replace: bool | None = None) -> str:
    """Create the hardlink for an item whose dest_path is already set."""
    s = settings.load()
    item = db.get_item(item_id)
    if not item or not item.get("dest_path"):
        return "error"
    src, dest = item["source_path"], item["dest_path"]
    if not in_library(dest):
        db.update_item(item_id, status="error", message="destination is outside the library folders")
        return "error"
    if s.get("dry_run"):
        db.update_item(item_id, status="planned", message="dry run - would link here")
        return "planned"
    if replace is None:
        replace = False
        if os.path.lexists(dest) and not os.path.samefile(src, dest) and s.get("upgrade_existing"):
            old = db.one("SELECT resolution, size FROM items WHERE dest_path=? AND id<>?", (dest, item_id))
            old_rank = parser.quality_rank(old["resolution"], old["size"]) if old else (
                parser.quality_rank(parser.parse(dest).get("resolution"), os.path.getsize(dest)))
            replace = parser.quality_rank(item.get("resolution"), item.get("size") or 0) > old_rank
    try:
        result = hardlink(src, dest, replace=replace)
    except LinkConflict as c:
        db.update_item(item_id, status="duplicate",
                       message=f"a different file already exists at the destination: {c.existing}")
        return "duplicate"
    except OSError as e:
        db.update_item(item_id, status="error", message=f"link failed: {e.strerror or e}")
        db.log("error", f"Link failed for {src}: {e}")
        return "error"
    if replace:
        db.execute("UPDATE items SET status='replaced', message='upgraded by a better release' "
                   "WHERE dest_path=? AND id<>?", (dest, item_id))
    vids = db.one("SELECT COUNT(*) n FROM items WHERE torrent_hash=? AND media_type IS NOT NULL",
                  (item["torrent_hash"],)) if item.get("torrent_hash") else {"n": 1}
    subs = link_subtitles(src, dest, (vids or {}).get("n", 1) <= 1, False) if s.get("link_subtitles") else 0
    msg = "linked" if result == "linked" else "link already present"
    if subs:
        msg += f" (+{subs} subtitle{'s' if subs > 1 else ''})"
    db.update_item(item_id, status="linked", message=msg)
    if index:
        index.add(dest)
    if result == "linked":
        db.log("info", f"Linked {os.path.basename(src)} -> {dest}")
    return "linked"


def rematch(item_id: int, kind: str, tmdb_id: int, season: int | None = None,
            episodes: list[int] | None = None, link: bool = True) -> dict:
    """Manually assign a TMDB entry to an item (and optionally link it)."""
    item = db.get_item(item_id)
    if not item:
        raise ValueError("item not found")
    d = metadata.details(kind, tmdb_id)
    if not d:
        raise ValueError("TMDB entry not found")
    parsed = parser.parse(item["source_path"], "episode" if kind == "tv" else "movie")
    fields = {
        "media_type": "episode" if kind == "tv" else "movie", "tmdb_id": d["tmdb_id"], "imdb_id": d.get("imdb_id"),
        "title": d["title"], "year": d["year"], "season": season if season is not None else parsed.get("season"),
        "episodes": episodes if episodes else parsed.get("episodes") or [], "episode_title": None,
        "resolution": item.get("resolution") or parsed.get("resolution"), "edition": parsed.get("edition"),
        "confidence": 1.0, "match_source": "manual",
    }
    if kind == "tv":
        if season is not None or episodes:
            parsed = dict(parsed, season=fields["season"], episodes=fields["episodes"], is_date=False)
        _fill_episode(fields, parsed)
        fields["confidence"] = 1.0
        if not fields["episodes"]:
            raise ValueError("Could not determine episode number - enter season and episode")
    fields["dest_path"] = build_dest({**fields, "part": parsed.get("part"),
                                      "is_anime": metadata._result(d, 1, "")["is_anime"]},
                                     os.path.splitext(item["source_path"])[1].lower())
    old_dest = item.get("dest_path")
    db.update_item(item_id, **fields, status="review", message="manually matched")
    if old_dest and old_dest != fields["dest_path"] and item["status"] == "linked":
        _remove_link(old_dest, item["source_path"])
    status = apply_link(item_id) if link else "review"
    return db.get_item(item_id) | {"result": status}


def _remove_link(dest: str, src: str) -> None:
    try:
        if os.path.exists(dest) and os.path.samefile(dest, src) and in_library(dest):
            os.remove(dest)
            stem = os.path.splitext(dest)[0]
            d = os.path.dirname(dest)
            for f in os.listdir(d):
                if f.startswith(os.path.basename(stem) + ".") and parser.is_sub(f):
                    os.remove(os.path.join(d, f))
            _prune_empty(d)
    except OSError:
        pass


def _prune_empty(d: str) -> None:
    roots = set(library_roots())
    while d and d not in roots and in_library(d):
        try:
            os.rmdir(d)
        except OSError:
            return
        d = os.path.dirname(d)


def unlink_item(item_id: int) -> None:
    item = db.get_item(item_id)
    if item and item.get("dest_path") and item["status"] == "linked":
        _remove_link(item["dest_path"], item["source_path"])
    db.update_item(item_id, status="ignored", message="unlinked by user")


def _err(path: str, torrent: dict | None, msg: str, st: os.stat_result | None = None) -> str:
    d = {"source_path": path, "status": "error", "message": msg,
         "torrent_hash": (torrent or {}).get("hash"), "torrent_name": (torrent or {}).get("name")}
    if st:
        d.update(size=st.st_size, inode=st.st_ino, dev=st.st_dev)
    db.upsert_item(d)
    return "error"
