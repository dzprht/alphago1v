#!/bin/sh
set -eu
umask 077

directory=${1:-/var/backups/alphago1v}
database=${2:-alphago1v}
mkdir -p "$directory"
backup=$(mktemp "$directory/$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
if ! sudo -u postgres pg_dump --format=custom "$database" > "$backup"; then
    rm -f "$backup"
    exit 1
fi
echo "$backup"
