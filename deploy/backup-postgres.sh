#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_FILE="${ROOT_DIR}/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.vps}"
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_FILE="${BACKUP_DIR}/sri_scraper_${TIMESTAMP}.sql.gz"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No existe el archivo de entorno: ${ENV_FILE}"
  exit 1
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    APP_ENV_FILE="${ENV_FILE}" docker compose --env-file "${ENV_FILE}" -f "${STACK_FILE}" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
    APP_ENV_FILE="${ENV_FILE}" docker-compose -f "${STACK_FILE}" "$@"
  else
    echo "No se encontró docker compose ni docker-compose."
    exit 1
  fi
}

mkdir -p "${BACKUP_DIR}"

compose exec -T postgres pg_dump -U sri sri_scraper | gzip > "${OUTPUT_FILE}"

echo "Backup creado en ${OUTPUT_FILE}"
