#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_FILE="${ROOT_DIR}/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.vps}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No existe el archivo de entorno: ${ENV_FILE}"
  echo "Copia .env.vps.example a .env.vps y complétalo."
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

mkdir -p \
  "${ROOT_DIR}/xmls" \
  "${ROOT_DIR}/screenshots" \
  "${ROOT_DIR}/sessions" \
  "${ROOT_DIR}/chrome_profile"

cd "${ROOT_DIR}"

echo "[1/5] Construyendo imágenes..."
compose build --pull api worker beat

echo "[2/5] Levantando dependencias..."
compose up -d postgres redis

echo "[3/5] Ejecutando migraciones..."
compose run --rm api alembic upgrade head

echo "[4/5] Levantando servicios principales..."
compose up -d --remove-orphans api worker beat caddy

if [[ "${ENABLE_FLOWER:-false}" == "true" ]]; then
  echo "[4b/5] Levantando Flower..."
  compose --profile ops up -d flower
fi

echo "[5/5] Esperando readiness del API..."
READY=0
for _ in $(seq 1 20); do
  if compose exec -T api python -c "import sys, urllib.request; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=5).status == 200 else sys.exit(1)"; then
    READY=1
    break
  fi
  sleep 3
done

compose ps

if [[ "${READY}" -ne 1 ]]; then
  echo "El API no quedó ready. Revisa logs:"
  compose logs --tail=100 api worker beat caddy
  exit 1
fi

echo "Deploy completado correctamente."
