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
        assert "/api/v1/tenants/validate-credentials" in routes


class TestTenantCredentialValidation:
    def test_validate_credentials_endpoint_ok(self, monkeypatch):
        from api.main import create_app

        class Result:
            ok = True
            message = "Credenciales SRI válidas"

        async def fake_validate(**kwargs):
            return Result()

        monkeypatch.setattr(
            "api.routers.tenants.validar_credenciales_sri",
            fake_validate,
        )
        app = create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/tenants/validate-credentials",
            json={
                "ruc": "0916429921001",
                "sri_usuario": "demo",
                "sri_password": "secret",
            },
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_validate_credentials_endpoint_error(self, monkeypatch):
        from api.main import create_app

        class Result:
            ok = False
            message = "Credenciales inválidas"

        async def fake_validate(**kwargs):
            return Result()

        monkeypatch.setattr(
            "api.routers.tenants.validar_credenciales_sri",
            fake_validate,
        )
        app = create_app()
        client = TestClient(app)

        response = client.post(
            "/api/v1/tenants/validate-credentials",
            json={
                "ruc": "0916429921001",
                "sri_usuario": "demo",
                "sri_password": "secret",
            },
        )

        assert response.status_code == 400
        assert "Credenciales inválidas" in response.json()["detail"]
