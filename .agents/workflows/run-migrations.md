---
description: Run Alembic database migrations in the Docker container
---

# Run Database Migrations

1. Ensure your Docker development environment is running (`docker-compose up -d`).
2. Run the Alembic upgrade command inside the `app` container.

// turbo
3. Run `docker-compose exec app alembic upgrade head`

4. To generate a new migration, you can use the following command (replace "message" with the actual migration description):

5. Run `docker-compose exec app alembic revision --autogenerate -m "message"`
