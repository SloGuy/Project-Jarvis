#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="/home/sloguy/jarvis-core"
BACKUP_DIR="${PROJECT_DIR}/backups/postgres"
CONTAINER_NAME="jarvis-market-db"
DATABASE_NAME="jarvis_market"
DATABASE_USER="jarvis"
RETENTION_DAYS=14
TIMESTAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
BACKUP_FILE="${BACKUP_DIR}/jarvis_market_${TIMESTAMP}.dump"
TEMP_FILE="${BACKUP_FILE}.tmp"

mkdir -p "${BACKUP_DIR}"

docker exec "${CONTAINER_NAME}" \
    pg_dump \
    --username="${DATABASE_USER}" \
    --dbname="${DATABASE_NAME}" \
    --format=custom \
    --no-owner \
    --no-privileges \
    > "${TEMP_FILE}"

if [[ ! -s "${TEMP_FILE}" ]]; then
    rm -f "${TEMP_FILE}"
    echo "Backup failed: output file is empty." >&2
    exit 1
fi

mv "${TEMP_FILE}" "${BACKUP_FILE}"
chmod 600 "${BACKUP_FILE}"

find "${BACKUP_DIR}" \
    -type f \
    -name 'jarvis_market_*.dump' \
    -mtime "+${RETENTION_DAYS}" \
    -delete

echo "Backup created successfully: ${BACKUP_FILE}"
