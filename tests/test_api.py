"""Tests de la API REST."""

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health(self, monkeypatch):
        """Test que el endpoint /health responde correctamente."""
        from api.main import create_app

        async def fake_report(_settings):
            return {
                "status": "ok",
                "checks": {},
                "timestamp": "2026-03-08T00:00:00+00:00",
            }

        monkeypatch.setattr("api.main.build_health_report", fake_report)
        app = create_app()
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_ready_retorna_503_si_hay_degradacion(self, monkeypatch):
        from api.main import create_app

        async def fake_report(_settings):
            return {
                "status": "degraded",
                "checks": {"database": {"status": "error"}},
                "timestamp": "2026-03-08T00:00:00+00:00",
            }

        monkeypatch.setattr("api.main.build_health_report", fake_report)
        app = create_app()
        client = TestClient(app)

        response = client.get("/ready")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"


class TestAPIStructure:
    def test_routers_registrados(self):
        from api.main import create_app
        app = create_app()
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/ready" in routes
