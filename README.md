# Hardlinkarr

A companion for the *arr stack. Hardlinkarr watches your torrent client, identifies every movie and episode with TMDB, and **hardlinks** it into your library under a clean, Plex/Jellyfin/Emby-friendly name. Your torrents keep seeding the original files, and the library uses no extra disk space.

```
/data/torrents/Blade.Runner.2049.2017.2160p.UHD.BluRay.x265-TERMiNAL/…mkv     ← seeding, untouched
/data/media/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) - 2160p.mkv   ← same data, clean name
```

## Features

- **Automatic linking** of new torrents (qBittorrent, Transmission, Deluge), plus a one-time pass over everything already in your download folders.
- **Identification from several sources**, best first: IDs inside `.nfo` files → Radarr/Sonarr parse API (optional) → TMDB search with title/year/country scoring. Handles multi-episode files, season packs, date-based shows, anime absolute numbering, editions, multi-part (CD1/CD2) movies and subtitles.
- **Confidence gate**: uncertain matches wait in *Library → Needs review*, where you fix them with a TMDB search (posters included).
- **Never double-links**: files Radarr/Sonarr already hardlinked into the library are detected by inode and skipped.
- **Organize your existing library**: identifies files already in your movie/TV folders (including existing hardlinks) and renames them to your naming format, after a preview. Subtitles and artwork move with them. Renaming a hardlink never touches the torrent's copy. Folders Radarr/Sonarr manage are protected by default. Optionally, existing links found during scans are renamed automatically.
- **Media server check**: reads your Plex/Jellyfin/Emby library folders, warns if any still scan the torrent folder, and walks you through switching to the clean library without losing watch history.
- **Cleanup tools** (nothing is deleted without confirmation):
  - Duplicates: the same movie or episode stored more than once (keeps the best quality), identical copies that can be turned back into hardlinks to reclaim space, and duplicate links.
  - Empty folders (folders holding only junk such as `.nfo`, `.txt` or images count as empty).
  - Orphaned torrents: data missing, or media no longer in your library (e.g. you deleted it in Plex). Remove the torrent alone, or the torrent and its data.
  - Orphaned files: files in your download folders that belong to no torrent.
- **Setup wizard** with folder auto-detection, a folder browser, a hardlink test, connection tests and path-mapping hints.
- Dry-run mode, upgrade-to-better-release, media-server library refresh, and optional login.

## Quick start

1. **Lay out your folders on one filesystem** (the [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/) layout):
   ```
   /mnt/user/data
   ├── torrents/          ← torrent client saves here (category subfolders are fine)
   └── media/
       ├── movies/
       └── tv/
   ```
2. **Mount the common parent** (`/mnt/user/data:/data`) into Hardlinkarr. Hardlinks cannot cross mount points, so two separate volumes such as `/downloads` and `/movies` will **not** work, even when both are on the same disk.
3. Edit `docker-compose.yml` (volume path, PUID/PGID to match your other containers), then:
   ```sh
   docker compose up -d --build
   ```
4. Open `http://<host>:8686`. The setup wizard walks you through:
   - **Folders**: detected automatically. Click *Test hardlinks*.
   - **TMDB key**: free at <https://www.themoviedb.org/settings/api> (the v3 key or the v4 token both work).
   - **Torrent client**: URL and login. If the client reports paths differently (for example `/downloads` instead of `/data/torrents`), the test shows the mismatch and suggests a path mapping.
   - **Optional**: Radarr/Sonarr for better identification, Plex/Jellyfin/Emby for automatic library refresh.
   - **Naming**: with a live preview.
   - **First-run mode**: *Preview first* (dry run, recommended), *Link right away*, or *Ask me for everything*.

5. **Switch your media server to the clean library.** Hardlinkarr doesn't change your media server's settings, so you do this once. Point Plex/Jellyfin/Emby at `media/movies` and `media/tv`, **not** at the torrents folder; the torrent folder should only be used for seeding. If Plex/Jellyfin/Emby is connected under *Integrations*, the dashboard flags any library that still scans the torrent folder and shows step-by-step instructions. In short: add the new folder to the existing library, scan, then remove the torrent folder, and watch history carries over.
6. Optionally open **Organize** to rename everything already in your library to the same format.

## Working alongside Radarr/Sonarr

- If the arrs already import some categories, either list those categories under *Exclude categories*, or leave them in: Hardlinkarr spots the existing hardlinks and marks them *Already in library*.
- Hardlinkarr is ideal for content the arrs don't manage: manual downloads, packs, or anything you grab by hand.

## Settings reference

All settings are edited in the UI and stored in `/config/hardlinkarr.db`. Environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PUID` / `PGID` | 1000 | User/group that owns created links and folders |
| `UMASK` | 002 | Permissions for new folders |
| `TZ` | Etc/UTC | Timezone for logs |

### Naming

Choose a style in *Settings → Naming* (or during setup) and see a live preview:

| Style | Movie | Episode |
|---|---|---|
| Standard (default) | `Dune (2021)/Dune (2021) - 2160p.mkv` | `Severance (2022)/Season 01/Severance - S01E01 - Good News About Hell - 1080p.mkv` |
| Clean | `Dune (2021)/Dune (2021).mkv` | `Severance (2022)/Season 01/Severance - S01E01 - Good News About Hell.mkv` |
| Short episodes | `Dune (2021)/Dune (2021).mkv` | `Severance (2022)/Season 01/Severance - S01E01.mkv` |
| Kodi | `Dune (2021)/Dune (2021).mkv` | `Severance/Season 1/Severance S01E01.mkv` |
| With TMDB IDs | `Dune (2021) {tmdb-438631}/…` | `Severance (2022) {tmdb-95396}/…` |
| Custom | build your own from tokens | |

Custom tokens: `{title}` `{show}` `{year}` `{season}` `{season:02d}` `{episode_tag}` `{episode_title}` `{resolution}` `{quality_tag}` `{edition}` `{edition_tag}` `{imdb_id}` `{tmdb_id}`. Empty tokens and leftover separators are removed automatically. Literal braces are written `{{` `}}`. Changing the style only affects new links; use **Organize** to rename existing files to the new style.

## FAQ

**"Cross-device link" / the hardlink test fails.** Downloads and library are on different filesystems, or are mounted as separate Docker volumes. Mount one common parent folder instead.

**Does deleting the torrent delete my movie?** No. A hardlinked file only goes away when *every* link is removed. Removing the torrent with data deletes the download copy, and the library copy stays.

**Does deleting from Plex free the space?** Only once the torrent is also removed. Hardlinkarr lists these torrents under *Cleanup → Orphaned torrents* ("not in library").

**Unraid**: use `/mnt/user/data` (or a single pool path) for both the client and Hardlinkarr. Avoid mixing `/mnt/user` and `/mnt/cache` paths.

## Development

```sh
pip install -r requirements.txt
HLA_CONFIG_DIR=./config uvicorn app.main:app --reload --port 8686
```

Backend: FastAPI + SQLite (`app/`). Frontend: dependency-free HTML/CSS/JS (`app/static/`).
