from __future__ import annotations

import posixpath

from . import map_path
from .base import ClientError, TorrentClient


class Deluge(TorrentClient):
    name = "Deluge"
    _id = 0
    _ready = False

    def _rpc(self, method: str, params: list | None = None):
        self._id += 1
        r = self.http.post(f"{self.url}/json", json={"method": method, "params": params or [], "id": self._id})
        if r.status_code != 200:
            raise ClientError(f"Deluge {method} -> HTTP {r.status_code}")
        body = r.json()
        if body.get("error"):
            raise ClientError(f"Deluge {method}: {body['error'].get('message')}")
        return body.get("result")

    def _connect(self) -> None:
        if self._ready:
            return
        if not self._rpc("auth.login", [self.password]):
            raise ClientError("Deluge: invalid web UI password")
        if not self._rpc("web.connected"):
            hosts = self._rpc("web.get_hosts") or []
            if not hosts:
                raise ClientError("Deluge web UI has no daemon configured")
            self._rpc("web.connect", [hosts[0][0]])
        self._ready = True

    def test(self) -> str:
        self._connect()
        try:
            return "Deluge " + str(self._rpc("daemon.info"))
        except ClientError:
            return "Deluge (connected)"

    def torrents(self, with_files: bool = True) -> list[dict]:
        self._connect()
        fields = ["name", "save_path", "progress", "state", "label", "ratio", "time_added", "message"]
        if with_files:
            fields.append("files")
        result = self._rpc("core.get_torrents_status", [{}, fields]) or {}
        out = []
        for h, t in result.items():
            save = map_path(t.get("save_path", ""))
            files = [posixpath.join(save, f["path"]) for f in t.get("files", [])]
            state = t.get("state", "")
            out.append({
                "hash": h.lower(),
                "name": t.get("name", ""),
                "category": t.get("label", "") or "",
                "save_path": save,
                "content_path": posixpath.join(save, t.get("name", "")),
                "files": files,
                "progress": (t.get("progress", 0) or 0) / 100,
                "completed": (t.get("progress", 0) or 0) >= 100,
                "state": state,
                "ratio": round(max(t.get("ratio", 0) or 0, 0), 2),
                "added_on": t.get("time_added", 0),
                "error": state == "Error",
                "tracker": "",
            })
        return out

    def delete(self, hashes: list[str], delete_files: bool) -> None:
        self._connect()
        for h in hashes:
            self._rpc("core.remove_torrent", [h, delete_files])
