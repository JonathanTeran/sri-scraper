#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Ejecuta este script como root: sudo bash deploy/install-systemd.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_TEMPLATE="${APP_DIR}/deploy/sri-scraper.service"
TARGET="/etc/systemd/system/sri-scraper.service"

sed "s|__APP_DIR__|${APP_DIR}|g" "${SERVICE_TEMPLATE}" > "${TARGET}"

systemctl daemon-reload
systemctl enable sri-scraper.service

echo "Servicio instalado en ${TARGET}"
echo "Para iniciarlo: sudo systemctl start sri-scraper"
