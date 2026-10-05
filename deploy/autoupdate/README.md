# Auto-update of yt-dlp / instaloader

Extractors break whenever sites change, and fixes ship more often than we deploy.
A systemd timer on the production server periodically rebuilds the running image
with the latest `yt-dlp` and `instaloader` and restarts the app only if versions changed.

Each run (`md-autoupdate.sh`):

1. Pulls `pasubi/media-downloader:latest` and builds a thin layer on top with
   `pip install --upgrade "yt-dlp[default,curl-cffi]" instaloader`.
2. Compares package versions with the running container — exits if nothing changed.
3. Smoke-tests the candidate (`import yt_dlp, instaloader, app.main`).
4. Waits up to 30 min while downloads are active (fresh `*.part` / `*.ytdl` files
   in `downloads/`), then restarts anyway.
5. Retags the candidate as `pasubi/media-downloader:latest` locally and runs
   `docker compose up -d --force-recreate`.
6. Waits for the container healthcheck; if it isn't `healthy`, rolls back to the previous image.

The CI deploy (`docker compose pull && up`) is unaffected: it replaces the local
image with the registry build, and the timer continues on top of it.

Restart side effects: in-progress tasks are lost (tasks live in memory).
Downloaded files survive (volume).

## First deploy on the server

```bash
sudo usermod -aG docker deploy
sudo mkdir -p /var/www/media-downloader/{downloads,cookies}
sudo chown deploy:deploy /var/www/media-downloader
sudo chown 1000:1000 /var/www/media-downloader/{downloads,cookies}
```
1. Copy `docker-compose.prod.yml` in `/var/www/media-downloader/docker-compose.yml`
2. Configure nginx config: copy `media-downloader.null-land.org` to `/etc/nginx/sites-available/media-downloader.null-land.org`

## Install (on the server)

```bash
sudo install -m 755 deploy/autoupdate/md-autoupdate.sh /usr/local/bin/md-autoupdate
sudo install -m 644 deploy/autoupdate/md-autoupdate.service deploy/autoupdate/md-autoupdate.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now md-autoupdate.timer
```

Run once by hand and check the log:

```bash
sudo systemctl start md-autoupdate.service
journalctl -u md-autoupdate -n 50
systemctl list-timers md-autoupdate.timer
```

## Configuration

Optional overrides in `/etc/default/md-autoupdate`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `APP_DIR` | `/var/www/media-downloader` | Directory with the server's `docker-compose.yml` |
| `IMAGE` | `pasubi/media-downloader:latest` | Image used by compose |
| `CONTAINER` | `media-downloader` | Container name |
| `PACKAGES` | `yt-dlp[default,curl-cffi] instaloader` | pip specs to upgrade |
| `DOWNLOADS_DIR` | `$APP_DIR/downloads` | Where to look for active downloads |
| `IDLE_WAIT_MAX` | `1800` | Max seconds to wait for active downloads |
| `HEALTH_TIMEOUT` | `180` | Max seconds to wait for `healthy` |
| `FORCE` | `0` | `1` = recreate even if versions are unchanged |

Schedule: every 6 hours + up to 30 min random delay (`md-autoupdate.timer`).
To change: `sudo systemctl edit md-autoupdate.timer`.
