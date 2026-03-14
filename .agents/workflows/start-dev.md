---
description: Start the local development environment using Docker Compose
---

# Start Local Development Environment

1. Make sure you are in the project root `/Users/jonathanteran/sri-scraper`.
2. Ensure Docker daemon is running on the host.
3. Use docker-compose to build and start the dev services.

// turbo
4. Run `docker-compose up -d --build`

5. Verify that the containers are running properly:

// turbo
6. Run `docker-compose ps`

7. To view logs for the active FastAPI application:

// turbo
8. Run `docker-compose logs -f app`
