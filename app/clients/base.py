from __future__ import annotations

import httpx


class ClientError(Exception):
    pass


class TorrentClient:
    name = "base"

    def __init__(self, url: str, username: str = "", password: str = ""):
        self.url = (url or "").rstrip("/")
        self.username = username or ""
        self.password = password or ""
        self.http = httpx.Client(timeout=30, follow_redirects=True)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- interface
    def test(self) -> str:
        raise NotImplementedError

    def torrents(self, with_files: bool = True) -> list[dict]:
        raise NotImplementedError

    def delete(self, hashes: list[str], delete_files: bool) -> None:
        raise NotImplementedError
