#!/bin/sh
# Run as PUID:PGID (like linuxserver.io images) so created links/folders get the right owner.
set -e
PUID=${PUID:-1000}
PGID=${PGID:-1000}
umask "${UMASK:-002}"
mkdir -p /config
if [ "$(id -u)" = "0" ]; then
  chown "$PUID:$PGID" /config 2>/dev/null || true
  chown -R "$PUID:$PGID" /config/*.db* 2>/dev/null || true
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
fi
exec "$@"
