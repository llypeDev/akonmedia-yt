#!/bin/sh
# Saves the database to BACKUP_DIR (default ~/akonmedia-backups) and keeps the
# last KEEP_DAYS days of copies (default 14). Meant to run every night:
#
#   crontab -e
#   30 3 * * * sh $HOME/akonmedia-yt/deploy/backup.sh >> $HOME/akonmedia-backups/backup.log 2>&1
#
# A copy on the same disk does not survive the loss of the machine: bring these
# files and the media volume to another computer as well (deploy/oracle/README.md,
# "Backup"). Restoring is described in OPERATIONS.md.
set -eu

cd "$(dirname "$0")/.."

dir=${BACKUP_DIR:-$HOME/akonmedia-backups}
keep=${KEEP_DAYS:-14}
umask 077
mkdir -p "$dir"

target="$dir/akonmedia-$(date +%F-%H%M).dump"
if docker compose exec -T db pg_dump -U akonmedia -Fc akonmedia > "$target.part"; then
    mv "$target.part" "$target"
    echo "$(date '+%F %T') saved $target ($(du -h "$target" | cut -f1))"
else
    rm -f "$target.part"
    echo "$(date '+%F %T') backup failed: is the stack running? (docker compose ps)" >&2
    exit 1
fi

find "$dir" -name 'akonmedia-*.dump' -type f -mtime +"$keep" -delete
