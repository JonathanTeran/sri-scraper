---
description: Run the pytest suite inside the docker container
---

# Run Tests

1. Ensure the development environment is running (`docker-compose up -d`).
2. Run pytest inside the `app` container to run all tests.

// turbo
3. Run `docker-compose exec app pytest`

4. To run tests with verbose output:

// turbo
5. Run `docker-compose exec app pytest -v`
