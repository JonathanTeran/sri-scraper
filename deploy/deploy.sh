#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_FILE="${ROOT_DIR}/docker-compose.prod.yml"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env.vps}"
RENDER_ENV_SCRIPT="${ROOT_DIR}/deploy/render-env.sh"

upsert_env() {
  local key="${1}"
  local value="${2}"
  python3 - "${ENV_FILE}" "${key}" "${value}" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]

lines = env_path.read_text(encoding="utf-8").splitlines()
prefix = f"{key}="
updated = False
for index, line in enumerate(lines):
    if line.startswith(prefix):
        lines[index] = f"{key}={value}"
        updated = True
        break

if not updated:
    lines.append(f"{key}={value}")

env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No existe ${ENV_FILE}. Generándolo automáticamente..."
  ENV_FILE="${ENV_FILE}" bash "${RENDER_ENV_SCRIPT}"
fi

for key in \
  API_DOMAIN \
  POSTGRES_PASSWORD \
  SECRET_KEY \
  CAPTCHA_PROVIDER \
  TWOCAPTCHA_API_KEY \
  CAPSOLVER_API_KEY \
  CAPTCHA_ASSISTED_MODE \
  CAPTCHA_ASSISTED_TIMEOUT_SEC \
  ENABLE_VNC \
  WORKER_VNC_PORT
do
  value="${!key:-}"
  if [[ -n "${value}" ]]; then
    upsert_env "${key}" "${value}"
  fi
done

if ! grep -qE '^CAPSOLVER_API_KEY=.+' "${ENV_FILE}" \
  && ! grep -qE '^TWOCAPTCHA_API_KEY=.+' "${ENV_FILE}"; then
  echo "Falta configurar una API key de CAPTCHA en ${ENV_FILE}."
  echo "Ejemplo sin editar archivos:"
  echo "CAPSOLVER_API_KEY=tu_api_key bash deploy/deploy.sh"
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
