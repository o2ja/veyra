#!/bin/sh
# Postgres archive_command target: copy ONE completed WAL segment into the archive directory, atomically.
# Usage (compose.prod.yml): archive_command = 'sh /opt/rebate-scripts/ops/wal-archive.sh %p %f'
# Env:   WAL_ARCHIVE_DIR  archive directory (default /wal_archive, the `wal_archive` volume)
# Exit 0 only when the segment is durably in place (an identical copy already there also counts: retries are safe).
# A DIFFERENT file with the same name is never overwritten: exit 1 and Postgres keeps retrying (alert on it).
set -eu
src="${1:?%p}"; name="${2:?%f}"
dir="${WAL_ARCHIVE_DIR:-/wal_archive}"
[ -d "$dir" ] && [ -w "$dir" ] || { echo "wal-archive: $dir missing or not writable by $(id -un)" >&2; exit 1; }
dst="$dir/$name"
if [ -f "$dst" ]; then
  cmp -s "$src" "$dst" && exit 0
  echo "wal-archive: $dst exists with different content, refusing to overwrite" >&2; exit 1
fi
tmp="$dir/.tmp.$name.$$"
trap 'rm -f "$tmp"' EXIT
cp "$src" "$tmp"
sync "$tmp"
mv "$tmp" "$dst"
