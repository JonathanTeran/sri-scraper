# Deploy en DigitalOcean VPS

Esta guía deja el proyecto corriendo en un Droplet Ubuntu con Docker, Postgres, Redis, API, worker, beat y un reverse proxy Caddy.

## 1. Requisitos

- Droplet Ubuntu 22.04 o 24.04
- DNS apuntando al VPS si quieres HTTPS automático
- Acceso `sudo`
- API key de `capsolver` o `2captcha`

## 2. Bootstrap del VPS

Desde el repo ya clonado en el servidor:

```bash
sudo bash deploy/bootstrap-ubuntu-vps.sh
```

Ese script instala Docker Engine, Compose plugin y abre `22`, `80` y `443` en `ufw`.

## 3. Preparar variables

```bash
cp .env.vps.example .env.vps
```

Edita al menos:

- `API_DOMAIN`
- `POSTGRES_PASSWORD`
- `SECRET_KEY`
- `CAPTCHA_PROVIDER`
- `CAPSOLVER_API_KEY` o `TWOCAPTCHA_API_KEY`

Notas:

- Si `API_DOMAIN` es un dominio, Caddy emitirá HTTPS automáticamente.
- Si `API_DOMAIN` es la IP del Droplet, el servicio quedará por HTTP.

## 4. Primer deploy

```bash
bash deploy/deploy.sh
```

El script:

1. Construye las imágenes.
2. Levanta `postgres` y `redis`.
3. Ejecuta `alembic upgrade head`.
4. Levanta `api`, `worker`, `beat` y `caddy`.
5. Espera a que `/ready` responda correctamente.

## 5. Flower opcional

Flower no se publica por Internet. Si lo necesitas:

```bash
ENABLE_FLOWER=true bash deploy/deploy.sh
```

Queda disponible sólo en `127.0.0.1:5555`. Accede por túnel SSH:

```bash
ssh -L 5555:127.0.0.1:5555 usuario@tu-vps
```

Luego abre [http://127.0.0.1:5555](http://127.0.0.1:5555).

## 6. Autostart con systemd

```bash
sudo bash deploy/install-systemd.sh
sudo systemctl start sri-scraper
sudo systemctl status sri-scraper
```

## 7. Operación diaria

Ver estado:

```bash
docker compose --env-file .env.vps -f docker-compose.prod.yml ps
```

Ver logs:

```bash
docker compose --env-file .env.vps -f docker-compose.prod.yml logs -f api worker beat caddy
```

Actualizar a una versión nueva:

```bash
git pull
bash deploy/deploy.sh
```

Backup de Postgres:

```bash
bash deploy/backup-postgres.sh
```

## 8. Rollback simple

Si una versión falla:

```bash
git checkout <commit-o-tag-anterior>
bash deploy/deploy.sh
```

## 9. Estructura de datos persistentes

Se conservan entre deploys:

- `xmls/`
- `screenshots/`
- `sessions/`
- `chrome_profile/`
- volumen Docker `pgdata`
- volumen Docker `redisdata`

## 10. Recomendaciones mínimas del VPS

- 2 vCPU / 4 GB RAM para un tenant con baja concurrencia
- 4 vCPU / 8 GB RAM si vas a subir `CELERY_WORKER_CONCURRENCY`
- activa backups del Droplet o snapshots además del dump SQL
