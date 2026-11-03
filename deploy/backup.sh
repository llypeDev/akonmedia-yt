#!/bin/sh
# Saves the database to BACKUP_DIR (default ~/qqplay-backups) and keeps the
# last KEEP_DAYS days of copies (default 14). Meant to run every night:
#
#   crontab -e
#   30 3 * * * sh $HOME/qqplay/deploy/backup.sh >> $HOME/qqplay-backups/backup.log 2>&1
#
# A copy on the same disk does not survive the loss of the machine: bring these
# files and the media volume to another computer as well (deploy/oracle/README.md,
# "Backup"). Restoring is described in OPERATIONS.md.
set -eu

cd "$(dirname "$0")/.."

dir=${BACKUP_DIR:-$HOME/qqplay-backups}
keep=${KEEP_DAYS:-14}
umask 077
mkdir -p "$dir"

target="$dir/qqplay-$(date +%F-%H%M).dump"
# The database's name and user as the database container knows them, so the
# script follows whatever the .env says.
# shellcheck disable=SC2016  # expanded by the shell inside the container
if docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$target.part"; then
    mv "$target.part" "$target"
    echo "$(date '+%F %T') saved $target ($(du -h "$target" | cut -f1))"
else
    rm -f "$target.part"
    echo "$(date '+%F %T') backup failed: is the stack running? (docker compose ps)" >&2
    exit 1
fi

find "$dir" -name 'qqplay-*.dump' -type f -mtime +"$keep" -delete
