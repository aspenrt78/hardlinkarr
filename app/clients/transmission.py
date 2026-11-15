from __future__ import annotations

import posixpath

from . import map_path
from .base import ClientError, TorrentClient

STATUS = {0: "stopped", 1: "check-wait", 2: "checking", 3: "download-wait",
          4: "downloading", 5: "seed-wait", 6: "seeding"}


class Transmission(TorrentClient):
    name = "Transmission"
    _sid = ""

    @property
    def rpc_url(self) -> str:
        return self.url if self.url.endswith("/rpc") else f"{self.url}/transmission/rpc"

    def _call(self, method: str, arguments: dict | None = None) -> dict:
        auth = (self.username, self.password) if self.username else None
        for _ in range(2):
            r = self.http.post(self.rpc_url, json={"method": method, "arguments": arguments or {}},
                               headers={"X-Transmission-Session-Id": self._sid}, auth=auth)
            if r.status_code == 409:
                self._sid = r.headers.get("X-Transmission-Session-Id", "")
                continue
            if r.status_code == 401:
                raise ClientError("Transmission: invalid username/password")
            if r.status_code != 200:
                raise ClientError(f"Transmission {method} -> HTTP {r.status_code}")
            body = r.json()
            if body.get("result") != "success":
                raise ClientError(f"Transmission {method}: {body.get('result')}")
            return body.get("arguments", {})
        raise ClientError("Transmission: could not obtain session id")

    def test(self) -> str:
        return "Transmission " + self._call("session-get", {"fields": ["version"]}).get("version", "?")

    def torrents(self, with_files: bool = True) -> list[dict]:
        fields = ["hashString", "name", "downloadDir", "percentDone", "status", "labels",
                  "uploadRatio", "addedDate", "error", "errorString"]
        if with_files:
            fields.append("files")
        out = []
        for t in self._call("torrent-get", {"fields": fields}).get("torrents", []):
            save = map_path(t.get("downloadDir", ""))
            files = [posixpath.join(save, f["name"]) for f in t.get("files", [])]
            labels = t.get("labels") or []
            err = t.get("error", 0) != 0
            out.append({
                "hash": t["hashString"].lower(),
                "name": t.get("name", ""),
                "category": labels[0] if labels else "",
                "save_path": save,
                "content_path": posixpath.join(save, t.get("name", "")),
                "files": files,
                "progress": t.get("percentDone", 0),
                "completed": t.get("percentDone", 0) >= 1,
                "state": t.get("errorString") if err else STATUS.get(t.get("status"), "?"),
                "ratio": round(max(t.get("uploadRatio", 0) or 0, 0), 2),
                "added_on": t.get("addedDate", 0),
                "error": err and "No data found" in (t.get("errorString") or ""),
                "tracker": "",
            })
        return out

    def delete(self, hashes: list[str], delete_files: bool) -> None:
        self._call("torrent-remove", {"ids": hashes, "delete-local-data": delete_files})
