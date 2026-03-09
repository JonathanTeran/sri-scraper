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

## 3. Primer deploy sin editar `.env.vps`

Si no existe `.env.vps`, `deploy.sh` lo genera automáticamente.

Ejemplo con CapSolver:

```bash
CAPSOLVER_API_KEY=tu_api_key bash deploy/deploy.sh
```

Ejemplo con 2captcha:

```bash
TWOCAPTCHA_API_KEY=tu_api_key CAPTCHA_PROVIDER=2captcha bash deploy/deploy.sh
```

Ejemplo con fallback real `capsolver -> 2captcha`:

```bash
CAPSOLVER_API_KEY=tu_capsolver \
TWOCAPTCHA_API_KEY=tu_2captcha \
CAPTCHA_PROVIDER=capsolver \
bash deploy/deploy.sh
```

Si quieres sobreescribir valores sin abrir `nano`, pásalos en el mismo comando:

```bash
API_DOMAIN=sri-process.amephia.com \
POSTGRES_PASSWORD=tu_password \
SECRET_KEY=tu_secret \
CAPSOLVER_API_KEY=tu_api_key \
bash deploy/deploy.sh
```

El script:

1. Construye las imágenes.
2. Genera `.env.vps` si aún no existe.
3. Guarda `API_DOMAIN=sri-process.amephia.com` por defecto.
4. Genera `POSTGRES_PASSWORD` y `SECRET_KEY` automáticamente si no los pasas.
5. Inserta en `.env.vps` las variables que le pases en el shell.
6. Levanta `postgres` y `redis`.
7. Ejecuta `alembic upgrade head`.
8. Levanta `api`, `worker`, `beat` y `caddy`.
9. Espera a que `/ready` responda correctamente.

Notas:

- Si `API_DOMAIN` es un dominio, Caddy emitirá HTTPS automáticamente.
- Si `API_DOMAIN` es la IP del Droplet, el servicio quedará por HTTP.

## 3.1. Modo asistido por VNC

Si quieres que el worker pase de automático a asistido cuando el SRI rechace todos
los tokens, habilita VNC local del contenedor y mantén browser visible:

```bash
CAPSOLVER_API_KEY=tu_capsolver \
TWOCAPTCHA_API_KEY=tu_2captcha \
CAPTCHA_PROVIDER=capsolver \
CAPTCHA_ASSISTED_MODE=fallback \
ENABLE_VNC=1 \
bash deploy/deploy.sh
```

El puerto VNC queda publicado sólo en `127.0.0.1:${WORKER_VNC_PORT:-5900}` del VPS.
Accede por túnel SSH:

```bash
ssh -L 5900:127.0.0.1:5900 root@161.35.141.92
```

Luego abre tu cliente VNC contra `127.0.0.1:5900`.

## 4. Flower opcional

Flower no se publica por Internet. Si lo necesitas:

```bash
ENABLE_FLOWER=true CAPSOLVER_API_KEY=tu_api_key bash deploy/deploy.sh
```

Queda disponible sólo en `127.0.0.1:5555`. Accede por túnel SSH:

```bash
ssh -L 5555:127.0.0.1:5555 usuario@tu-vps
```

Luego abre [http://127.0.0.1:5555](http://127.0.0.1:5555).

## 5. Autostart con systemd

```bash
sudo bash deploy/install-systemd.sh
sudo systemctl start sri-scraper
sudo systemctl status sri-scraper
```

## 6. Operación diaria

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

## 7. Rollback simple

Si una versión falla:

```bash
git checkout <commit-o-tag-anterior>
bash deploy/deploy.sh
```

## 8. Estructura de datos persistentes

Se conservan entre deploys:

- `xmls/`
- `screenshots/`
- `sessions/`
- `chrome_profile/`
- volumen Docker `pgdata`
- volumen Docker `redisdata`

## 9. Recomendaciones mínimas del VPS

- 2 vCPU / 4 GB RAM para un tenant con baja concurrencia
- 4 vCPU / 8 GB RAM si vas a subir `CELERY_WORKER_CONCURRENCY`
- activa backups del Droplet o snapshots además del dump SQL
