#!/bin/sh
# Periodic Postgres backups for docker-compose.prod.yml's `backup` service.
# Writes pg_dump custom-format files to /backups, prunes old ones, and optionally copies
# each dump off the instance to S3 (BACKUP_S3_URI, e.g. s3://my-bucket/rental-mgmt).
set -u

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"
S3_URI="${BACKUP_S3_URI:-}"

if [ -n "$S3_URI" ] && ! command -v aws >/dev/null 2>&1; then
  apk add --no-cache aws-cli >/dev/null || echo "backup: could not install aws-cli; S3 upload disabled" >&2
fi

backup_once() {
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  target="/backups/rental_mgmt-${stamp}.dump"
  # Write to a temporary name so a failed dump never looks like a valid backup.
  if ! pg_dump --format=custom --file="${target}.partial"; then
    rm -f "${target}.partial"
    echo "backup: pg_dump failed at ${stamp}" >&2
    return 1
  fi
  mv "${target}.partial" "$target"
  echo "backup: wrote ${target}"
  if [ -n "$S3_URI" ] && command -v aws >/dev/null 2>&1; then
    if aws s3 cp --only-show-errors "$target" "${S3_URI%/}/"; then
      echo "backup: uploaded ${target} to ${S3_URI}"
    else
      echo "backup: S3 upload failed for ${target}" >&2
    fi
  fi
  find /backups -name 'rental_mgmt-*.dump' -mtime +"$RETENTION_DAYS" -delete
}

# As PID 1, sh ignores SIGTERM unless trapped; sleep in the background so `docker compose stop` is immediate.
trap 'exit 0' TERM INT

while true; do
  # A failed run is logged and retried next interval instead of crash-looping the container.
  backup_once || true
  sleep "$INTERVAL" &
  wait $!
done
