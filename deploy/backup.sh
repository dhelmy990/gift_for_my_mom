#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")/.."
exec python3 -m alias_server.storage data/aliases.sqlite3 backups
