"""Path helpers: library roots, safety checks, hardlink capability tests."""
from __future__ import annotations

import os
import re
import uuid

from . import settings

ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def library_roots() -> list[str]:
    roots = [settings.get("movies_dir"), settings.get("tv_dir"), settings.get("anime_dir")]
    return [os.path.normpath(r) for r in roots if r]


def download_roots() -> list[str]:
    return [os.path.normpath(r) for r in (settings.get("download_dirs") or []) if r]


def all_roots() -> list[str]:
    return list(dict.fromkeys(library_roots() + download_roots()))


def is_under(path: str, root: str) -> bool:
    path, root = os.path.normpath(path), os.path.normpath(root)
    return path != root and (path + os.sep).startswith(root.rstrip(os.sep) + os.sep)


def assert_managed(path: str) -> str:
    """Refuse to touch anything outside the configured roots (or a root itself)."""
    real = os.path.normpath(os.path.abspath(path))
    if not any(is_under(real, r) for r in all_roots()):
        raise PermissionError(f"Refusing to modify path outside managed folders: {path}")
    return real


def in_library(path: str) -> bool:
    return any(is_under(path, r) for r in library_roots())


def safe_name(s: str) -> str:
    s = ILLEGAL.sub("", s.replace(":", " -").replace("/", "-"))
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:200] or "Unknown"


def can_hardlink(src_dir: str, dst_dir: str) -> tuple[bool, str]:
    """Create a temp file in src_dir and try to hardlink it into dst_dir."""
    if not os.path.isdir(src_dir):
        return False, f"{src_dir} does not exist inside the container"
    if not os.path.isdir(dst_dir):
        # never create folders from a test: a typo would leave stray folders on the user's disk
        return False, f"{dst_dir} does not exist inside the container - check the path or use Browse"
    probe = os.path.join(src_dir, f".hardlinkarr-probe-{uuid.uuid4().hex[:8]}")
    target = os.path.join(dst_dir, os.path.basename(probe))
    try:
        with open(probe, "w") as fh:
            fh.write("probe")
        os.link(probe, target)
        ok = os.stat(probe).st_ino == os.stat(target).st_ino
        return ok, "hardlinks work" if ok else "link created but inodes differ"
    except OSError as e:
        if e.errno == 18:
            return False, ("different filesystems/mounts - mount ONE common parent folder (e.g. /data) "
                           "containing both torrents and media, instead of separate volumes")
        return False, f"{e.strerror} ({e.errno})"
    finally:
        for p in (target, probe):
            try:
                os.remove(p)
            except OSError:
                pass


def disk_usage(path: str) -> dict | None:
    try:
        st = os.statvfs(path)
        return {"path": path, "total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize}
    except (OSError, AttributeError):
        return None
