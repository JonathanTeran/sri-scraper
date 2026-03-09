# SRI Scraper — Sistema RPA Multi-Tenant

Sistema que automatiza la descarga masiva de comprobantes electrónicos recibidos del portal SRI en Línea de Ecuador para múltiples contribuyentes.

## Stack Tecnológico

- **Python 3.11+** — Lenguaje principal
- **Playwright** — Automatización de navegador (scraping)
- **PostgreSQL 15+** — Base de datos principal
- **SQLAlchemy 2.0 async** — ORM
- **Celery + Redis** — Cola de tareas y scheduler
- **FastAPI** — API REST de administración
- **lxml** — Parser XML
- **CapSolver / 2captcha** — Resolución automática de CAPTCHA
- **Docker** — Containerización

## Requisitos Previos

- Docker y Docker Compose
- Python 3.11+ (para desarrollo local)
- API key de CapSolver o 2captcha (para resolución de CAPTCHA)
- Credenciales de acceso al portal SRI

## Instalación Rápida (Docker)

```bash
# 1. Clonar el repositorio
git clone <repo-url>
cd sri-scraper

# 2. Copiar y configurar variables de entorno
cp .env.example .env
# Editar .env con tus valores (SECRET_KEY, POSTGRES_PASSWORD, etc.)

# 3. Levantar todos los servicios
docker-compose up -d --build

# 4. Ejecutar migraciones de BD
docker-compose exec api alembic upgrade head

# 5. Verificar que todo funciona
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## Deploy en VPS

Para despliegue en DigitalOcean VPS usa el stack de producción:

```bash
cp .env.vps.example .env.vps
bash deploy/deploy.sh
```

Documentación completa: [deploy/DIGITALOCEAN_VPS.md](/Users/jonathanteran/sri-scraper/deploy/DIGITALOCEAN_VPS.md)

## Instalación Local (Desarrollo)

```bash
# 1. Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Instalar navegador de Playwright
playwright install chromium

# 4. Configurar .env
cp .env.example .env
# Editar con valores locales

# 5. Iniciar PostgreSQL y Redis (via Docker)
docker-compose up -d postgres redis

# 6. Ejecutar migraciones
alembic upgrade head

# 7. Iniciar API
uvicorn api.main:app --reload --port 8000

# 8. Iniciar worker (en otra terminal)
celery -A tasks.celery_app worker --loglevel=info

# 9. Iniciar scheduler (en otra terminal)
celery -A tasks.celery_app beat --loglevel=info
```

## Servicios

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| API | 8000 | API REST FastAPI |
| Flower | 5555 | Monitor de Celery |
| PostgreSQL | 5432 | Base de datos |
| Redis | 6379 | Broker de tareas |

## API Endpoints

### Tenants
- `GET /api/v1/tenants` — Listar tenants
- `POST /api/v1/tenants` — Crear tenant
- `GET /api/v1/tenants/{id}` — Detalle
- `PATCH /api/v1/tenants/{id}` — Actualizar
- `DELETE /api/v1/tenants/{id}` — Desactivar (soft delete)
- `POST /api/v1/tenants/{id}/ejecutar` — Trigger manual

### Comprobantes
- `GET /api/v1/tenants/{id}/comprobantes` — Listar con filtros
- `GET /api/v1/comprobantes/{id}` — Detalle completo
- `GET /api/v1/comprobantes/{id}/xml` — Descargar XML

### Ejecuciones
- `GET /api/v1/tenants/{id}/ejecuciones` — Historial
- `GET /api/v1/ejecuciones/{id}` — Detalle

### Exportar
- `GET /api/v1/tenants/{id}/exportar` — Excel (3 hojas)

### Health
- `GET /health` — Estado operativo con checks de DB, Redis, storage y CAPTCHA
- `GET /ready` — Readiness para despliegue / balanceador

## Crear un Tenant

```bash
curl -X POST http://localhost:8000/api/v1/tenants \
  -H "Content-Type: application/json" \
  -d '{
    "nombre": "Mi Empresa",
    "ruc": "0916429921001",
    "sri_usuario": "usuario_sri",
    "sri_password": "password_sri"
  }'
```

## Ejecutar Scraping Manual

```bash
curl -X POST http://localhost:8000/api/v1/tenants/{tenant_id}/ejecutar \
  -H "Content-Type: application/json" \
  -d '{
    "anio": 2026,
    "mes": 3,
    "tipo_comprobante": "Factura"
  }'
```

## Estructura del Proyecto

```
sri-scraper/
├── api/            # FastAPI (endpoints REST)
├── captcha/        # Resolución de CAPTCHA (2captcha)
├── config/         # Settings y logging
├── db/             # SQLAlchemy models y session
├── exporters/      # Exportación Excel
├── parsers/        # Parser XML del SRI
├── scrapers/       # Motor Playwright (engine)
├── scheduler/      # APScheduler jobs
├── tasks/          # Celery tasks
└── utils/          # Crypto, delays, screenshots
```

## Notas Importantes

- El SRI tiene mantenimiento nocturno entre 00:00-06:00 Ecuador. Las ejecuciones automáticas están programadas a las 06:30.
- Mínimo 800ms de delay entre descargas individuales de XML para evitar bloqueos.
- Las credenciales de los tenants se almacenan encriptadas con Fernet.
- Los XMLs se guardan tanto en BD como en filesystem (`xmls/{ruc}/{año}/{mes}/`).
- El worker admite `CAPTCHA_PROVIDER=capsolver|2captcha` y puede encadenar ambos proveedores si ambos API keys están configurados.
- El browser usa perfil persistente por tenant en `chrome_profile/{ruc}` y puede preferir `nodriver` si está disponible.
- Se puede enrutar por proxy con `BROWSER_PROXY_SERVER`, `BROWSER_PROXY_USERNAME` y `BROWSER_PROXY_PASSWORD`.
- El modo asistido se activa con `CAPTCHA_ASSISTED_MODE=fallback|only` en ejecuciones con browser visible.
# sri-scraper
