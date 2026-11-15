from __future__ import annotations

import posixpath

from . import map_path
from .base import ClientError, TorrentClient

DONE_STATES = {"uploading", "stalledUP", "pausedUP", "stoppedUP", "queuedUP", "forcedUP", "checkingUP"}


class QBittorrent(TorrentClient):
    name = "qBittorrent"
    _logged_in = False

    def _login(self) -> None:
        if self._logged_in:
            return
        r = self.http.post(
            f"{self.url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
            headers={"Referer": self.url},
        )
        if r.status_code == 403:
            raise ClientError("qBittorrent banned this IP after too many failed logins")
        if r.status_code != 200 or r.text.strip() != "Ok.":
            # auth may be disabled for local networks: probe a protected endpoint
            probe = self.http.get(f"{self.url}/api/v2/app/version")
            if probe.status_code != 200:
                if r.text.strip() == "Fails.":
                    raise ClientError(
                        "qBittorrent rejected the username or password. If you never set a WebUI "
                        "password, qBittorrent makes a new temporary one at every restart (shown in its "
                        "log): set a permanent password under Options → Web UI, or add this app's "
                        "network to \"Bypass authentication for clients in whitelisted IP subnets\".")
                raise ClientError(f"qBittorrent login failed (HTTP {r.status_code}: {r.text.strip()[:80]})")
        self._logged_in = True

    def _get(self, path: str, **params):
        self._login()
        r = self.http.get(f"{self.url}/api/v2/{path}", params=params)
        if r.status_code != 200:
            raise ClientError(f"qBittorrent {path} -> HTTP {r.status_code}")
        return r

    def test(self) -> str:
        return "qBittorrent " + self._get("app/version").text.strip()

    def torrents(self, with_files: bool = True) -> list[dict]:
        out = []
        for t in self._get("torrents/info").json():
            save = map_path(t.get("save_path", ""))
            files: list[str] = []
            if with_files:
                for f in self._get("torrents/files", hash=t["hash"]).json():
                    files.append(posixpath.join(save, f["name"].replace("\\", "/")))
            state = t.get("state", "")
            out.append({
                "hash": t["hash"].lower(),
                "name": t.get("name", ""),
                "category": t.get("category", "") or "",
                "save_path": save,
                "content_path": map_path(t.get("content_path", "")),
                "files": files,
                "progress": t.get("progress", 0),
                "completed": t.get("progress", 0) >= 1 or state in DONE_STATES,
                "state": state,
                "ratio": round(t.get("ratio", 0) or 0, 2),
                "added_on": t.get("added_on", 0),
                "error": state in ("missingFiles", "error"),
                "tracker": t.get("tracker", ""),
            })
        return out

    def delete(self, hashes: list[str], delete_files: bool) -> None:
        self._login()
        r = self.http.post(
            f"{self.url}/api/v2/torrents/delete",
            data={"hashes": "|".join(hashes), "deleteFiles": "true" if delete_files else "false"},
        )
        if r.status_code != 200:
            raise ClientError(f"qBittorrent delete -> HTTP {r.status_code}")
