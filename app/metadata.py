"""Identification: TMDB (primary), plus Radarr/Sonarr parse APIs and .nfo ids as extra sources."""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

import httpx

from . import db, settings

TMDB = "https://api.themoviedb.org/3"
DAY = 86400


class MetadataError(Exception):
    pass


# ---------------------------------------------------------------- TMDB

def _tmdb(path: str, params: dict | None = None, ttl: float = 7 * DAY, key: str | None = None) -> Any:
    key = key or settings.get("tmdb_api_key")
    if not key:
        raise MetadataError("TMDB API key is not configured")
    params = dict(params or {})
    params.setdefault("language", settings.get("tmdb_language") or "en-US")
    ck = f"tmdb:{path}:{sorted(params.items())}"
    if ttl and (hit := db.cache_get(ck, ttl)) is not None:
        return hit
    headers = {"Accept": "application/json"}
    if key.startswith("eyJ"):  # v4 read access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    try:
        r = httpx.get(f"{TMDB}{path}", params=params, headers=headers, timeout=20)
    except httpx.HTTPError as e:
        raise MetadataError(f"TMDB unreachable: {e}") from e
    if r.status_code == 401:
        raise MetadataError("TMDB rejected the API key")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise MetadataError(f"TMDB {path} -> HTTP {r.status_code}")
    data = r.json()
    if ttl:
        db.cache_set(ck, data)
    return data


def test_tmdb(key: str) -> str:
    data = _tmdb("/configuration", ttl=0, key=key)
    if not data:
        raise MetadataError("Unexpected TMDB response")
    return "TMDB key OK"


def search(kind: str, query: str, year: int | None = None) -> list[dict]:
    """kind: movie | tv. Returns normalised results."""
    params: dict[str, Any] = {"query": query, "include_adult": "false"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year
    data = _tmdb(f"/search/{kind}", params) or {}
    results = data.get("results", [])
    if year and not results:  # year in release name is sometimes wrong
        params.pop("year", None)
        params.pop("first_air_date_year", None)
        results = (_tmdb(f"/search/{kind}", params) or {}).get("results", [])
    return [_norm_result(kind, r) for r in results]


def _norm_result(kind: str, r: dict) -> dict:
    date = r.get("release_date" if kind == "movie" else "first_air_date") or ""
    return {
        "tmdb_id": r["id"],
        "kind": kind,
        "title": r.get("title" if kind == "movie" else "name") or "",
        "original_title": r.get("original_title" if kind == "movie" else "original_name") or "",
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "overview": (r.get("overview") or "")[:300],
        "poster": f"https://image.tmdb.org/t/p/w154{r['poster_path']}" if r.get("poster_path") else None,
        "popularity": r.get("popularity", 0),
        "genre_ids": r.get("genre_ids", []),
        "origin_country": r.get("origin_country", []),
        "original_language": r.get("original_language"),
    }


def details(kind: str, tmdb_id: int) -> dict | None:
    d = _tmdb(f"/{kind}/{tmdb_id}", {"append_to_response": "external_ids"})
    if not d:
        return None
    out = _norm_result(kind, d)
    out["genre_ids"] = [g["id"] for g in d.get("genres", [])]
    out["imdb_id"] = d.get("imdb_id") or (d.get("external_ids") or {}).get("imdb_id")
    out["seasons"] = [s.get("season_number") for s in d.get("seasons", [])] if kind == "tv" else []
    return out


def find_by_external(ext_id: str, source: str) -> dict | None:
    """source: imdb_id | tvdb_id"""
    d = _tmdb(f"/find/{ext_id}", {"external_source": source}) or {}
    if d.get("movie_results"):
        return _norm_result("movie", d["movie_results"][0])
    if d.get("tv_results"):
        return _norm_result("tv", d["tv_results"][0])
    if d.get("tv_episode_results"):
        ep = d["tv_episode_results"][0]
        return details("tv", ep["show_id"])
    return None


def season_episodes(tv_id: int, season: int) -> dict[int, dict]:
    d = _tmdb(f"/tv/{tv_id}/season/{season}", ttl=DAY) or {}
    return {e["episode_number"]: {"name": e.get("name"), "air_date": e.get("air_date")}
            for e in d.get("episodes", [])}


def episode_by_date(tv_id: int, date: str) -> tuple[int, int, str] | None:
    info = details("tv", tv_id) or {}
    for s in reversed(info.get("seasons") or []):
        for num, ep in season_episodes(tv_id, s).items():
            if ep.get("air_date") == date:
                return s, num, ep.get("name") or ""
    return None


# ---------------------------------------------------------------- *arr parse APIs

def _arr_parse(kind: str, title: str) -> dict | None:
    url = settings.get(f"{kind}_url")
    key = settings.get(f"{kind}_api_key")
    if not url or not key:
        return None
    try:
        r = httpx.get(f"{url.rstrip('/')}/api/v3/parse", params={"title": title},
                      headers={"X-Api-Key": key}, timeout=15)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def test_arr(url: str, key: str) -> str:
    r = httpx.get(f"{url.rstrip('/')}/api/v3/system/status", headers={"X-Api-Key": key}, timeout=10)
    if r.status_code != 200:
        raise MetadataError(f"HTTP {r.status_code}")
    d = r.json()
    return f"{d.get('appName', 'arr')} {d.get('version', '')}"


# ---------------------------------------------------------------- matching

def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _score(query: str, alt: str, year: int | None, r: dict, country: str | None = None) -> float:
    sim = max(similarity(q, t) for q in (query, alt) if q for t in (r["title"], r["original_title"]) if t) if query or alt else 0
    if country and country.upper() in (r.get("origin_country") or []):
        sim += 0.1  # "The.Office.US" -> prefer the US show
    if year and r["year"]:
        diff = abs(year - r["year"])
        sim = sim + 0.1 if diff == 0 else sim if diff == 1 else sim - 0.3
    elif not year:
        sim -= 0.05
    return max(0.0, min(1.0, sim))


ANIMATION_GENRE = 16


def identify(parsed: dict, release_name: str, ids: dict, type_hint: str | None = None) -> dict:
    """Return the best match for a parsed release.

    Result: {tmdb_id, kind (movie|tv), title, year, imdb_id, confidence, source, is_anime, candidates}
    """
    kind = "tv" if parsed["kind"] == "episode" else "movie"
    if type_hint in ("movie", "tv"):
        kind = type_hint

    # 1) explicit ids from .nfo / folder names
    if ids.get("tmdb"):
        d = details(kind, int(ids["tmdb"]))
        if d:
            return _result(d, 1.0, "nfo:tmdb")
    for src, key in (("imdb", "imdb_id"), ("tvdb", "tvdb_id")):
        if ids.get(src):
            d = find_by_external(ids[src], key)
            if d and d["kind"] == kind:
                return _result(d, 1.0, f"nfo:{src}")

    # 2) Radarr / Sonarr parsers know the user's library and scene naming quirks
    if kind == "movie":
        p = _arr_parse("radarr", release_name)
        mv = (p or {}).get("movie") or {}
        if mv.get("tmdbId"):
            d = details("movie", mv["tmdbId"])
            if d:
                return _result(d, 0.98, "radarr")
    else:
        p = _arr_parse("sonarr", release_name)
        sr = (p or {}).get("series") or {}
        d = None
        if sr.get("tmdbId"):
            d = details("tv", sr["tmdbId"])
        elif sr.get("tvdbId"):
            d = find_by_external(str(sr["tvdbId"]), "tvdb_id")
        if d:
            return _result(d, 0.98, "sonarr")

    # 3) TMDB title search
    title, alt, year = parsed["title"], parsed.get("alt_title") or "", parsed.get("year")
    if not title and not alt:
        return {"tmdb_id": None, "confidence": 0, "source": "none", "candidates": [], "kind": kind}
    results = search(kind, title or alt, year)
    if alt and normalize(alt) != normalize(title):
        seen = {r["tmdb_id"] for r in results}
        results += [r for r in search(kind, alt, year) if r["tmdb_id"] not in seen]
    if not results and kind == "movie" and parsed["kind"] != "episode":
        # might actually be a show packed oddly; try the other type at a penalty
        results = [dict(r, _penalty=0.2) for r in search("tv", title, year)]
    if not results:
        return {"tmdb_id": None, "confidence": 0, "source": "tmdb", "candidates": [], "kind": kind}

    scored = sorted(
        ((_score(title, alt, year, r, parsed.get("country")) - r.get("_penalty", 0), r) for r in results[:20]),
        key=lambda x: (x[0], x[1]["popularity"]), reverse=True,
    )
    best_score, best = scored[0]
    # ambiguity: two very close candidates with no year/country to separate them
    if len(scored) > 1 and scored[1][0] >= best_score - 0.02 and not year:
        best_score -= 0.1
    res = _result(best, best_score, "tmdb")
    res["candidates"] = [dict(r, score=round(s, 2)) for s, r in scored[:8]]
    return res


def _result(d: dict, conf: float, source: str) -> dict:
    is_anime = ANIMATION_GENRE in (d.get("genre_ids") or []) and (
        "JP" in (d.get("origin_country") or []) or d.get("original_language") == "ja")
    return {
        "tmdb_id": d["tmdb_id"], "kind": d["kind"], "title": d["title"], "year": d["year"],
        "imdb_id": d.get("imdb_id"), "confidence": round(conf, 3), "source": source,
        "is_anime": is_anime, "poster": d.get("poster"), "candidates": [],
    }
