"""Tests de las tareas Celery."""

from tasks.constants import TIPOS_SCRAPING, TIPO_MAP


class TestTasksConfig:
    def test_tipos_scraping(self):
        assert len(TIPOS_SCRAPING) == 5
        assert "Factura" in TIPOS_SCRAPING
        assert "Comprobante de Retención" in TIPOS_SCRAPING

    def test_tipo_map(self):
        assert TIPO_MAP["Factura"] == "factura"
        assert TIPO_MAP["Comprobante de Retención"] == "retencion"
        assert TIPO_MAP["Notas de Crédito"] == "nota_credito"
        assert TIPO_MAP["Notas de Débito"] == "nota_debito"
