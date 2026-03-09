"""Tests para el parser XML del SRI."""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from parsers.xml_parser import parse_comprobante_sri, comprobante_to_dict

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class TestParseFactura:
    def setup_method(self):
        self.xml = _load_fixture("factura_real.xml")
        self.comp = parse_comprobante_sri(self.xml)

    def test_autorizacion(self):
        az = self.comp.autorizacion
        assert az.estado == "AUTORIZADO"
        assert az.numero_autorizacion == (
            "0103202601091642992100120030010000096761234567814"
        )
        assert az.ambiente == "PRODUCCIÓN"
        assert az.fecha_autorizacion is not None

    def test_tipo_comprobante(self):
        assert self.comp.tipo_comprobante == "factura"

    def test_info_tributaria(self):
        it = self.comp.info_tributaria
        assert it.ruc == "0916429921001"
        assert it.razon_social == "EMPRESA DEMO S.A."
        assert it.nombre_comercial == "DEMO STORE"
        assert it.cod_doc == "01"
        assert it.estab == "003"
        assert it.pto_emi == "001"
        assert it.secuencial == "000009676"
        assert it.clave_acceso == (
            "0103202601091642992100120030010000096761234567814"
        )

    def test_cabecera(self):
        assert self.comp.fecha_emision is not None
        assert self.comp.fecha_emision.year == 2026
        assert self.comp.fecha_emision.month == 3
        assert self.comp.fecha_emision.day == 1
        assert self.comp.obligado_contabilidad == "NO"
        assert self.comp.dir_establecimiento == "SUCURSAL CENTRO"

    def test_receptor(self):
        assert self.comp.tipo_id_receptor == "05"
        assert self.comp.razon_social_receptor == "CLIENTE PRUEBA"
        assert self.comp.identificacion_receptor == "1207481803"

    def test_totales(self):
        assert self.comp.total_sin_impuestos == Decimal("27.25")
        assert self.comp.total_descuento == Decimal("0.00")
        assert self.comp.importe_total == Decimal("27.25")
        assert self.comp.moneda == "DOLAR"
        assert self.comp.propina == Decimal("0.00")

    def test_detalles(self):
        assert len(self.comp.detalles) == 2
        d1 = self.comp.detalles[0]
        assert d1.codigo_principal == "10"
        assert d1.descripcion == "Producto de prueba A"
        assert d1.cantidad == Decimal("1")
        assert d1.precio_unitario == Decimal("13.50")
        assert d1.precio_total_sin_impuesto == Decimal("13.50")
        assert d1.iva_codigo == "2"
        assert d1.iva_codigo_porcentaje == "7"

        d2 = self.comp.detalles[1]
        assert d2.codigo_principal == "20"
        assert d2.precio_unitario == Decimal("13.75")

    def test_pagos(self):
        assert len(self.comp.pagos) == 1
        p = self.comp.pagos[0]
        assert p.forma_pago == "19"
        assert p.forma_pago_desc == "Tarjeta de crédito"
        assert p.total == Decimal("27.25")
        assert p.unidad_tiempo == "DIAS"

    def test_sin_retenciones(self):
        assert len(self.comp.retenciones) == 0

    def test_xml_raw(self):
        assert "<autorizacion>" in self.comp.xml_raw
        assert "<factura" in self.comp.xml_raw


class TestParseRetencion:
    def setup_method(self):
        self.xml = _load_fixture("retencion_real.xml")
        self.comp = parse_comprobante_sri(self.xml)

    def test_autorizacion(self):
        az = self.comp.autorizacion
        assert az.estado == "AUTORIZADO"
        assert az.ambiente == "PRODUCCIÓN"

    def test_tipo_comprobante(self):
        assert self.comp.tipo_comprobante == "retencion"

    def test_info_tributaria(self):
        it = self.comp.info_tributaria
        assert it.ruc == "1790941450001"
        assert it.cod_doc == "07"
        assert it.estab == "001"
        assert it.pto_emi == "002"
        assert it.secuencial == "000000123"

    def test_cabecera_retencion(self):
        assert self.comp.fecha_emision is not None
        assert self.comp.fecha_emision.day == 13
        assert self.comp.fecha_emision.month == 2
        assert self.comp.obligado_contabilidad == "SI"
        assert self.comp.contribuyente_especial == "870"

    def test_sujeto_retenido(self):
        assert self.comp.tipo_id_receptor == "04"
        assert self.comp.razon_social_receptor == "PROVEEDOR DEMO"
        assert self.comp.identificacion_receptor == "1207481803001"

    def test_periodo_fiscal(self):
        assert self.comp.periodo_fiscal == "02/2026"

    def test_retenciones(self):
        assert len(self.comp.retenciones) == 2

        r1 = self.comp.retenciones[0]
        assert r1.codigo == "1"
        assert r1.tipo_tributo == "renta"
        assert r1.codigo_retencion == "3440"
        assert r1.base_imponible == Decimal("420.00")
        assert r1.porcentaje_retener == Decimal("2.75")
        assert r1.valor_retenido == Decimal("11.55")
        assert r1.cod_doc_sustento == "01"
        assert r1.num_doc_sustento == "002001000000017"

        r2 = self.comp.retenciones[1]
        assert r2.codigo == "2"
        assert r2.tipo_tributo == "iva"
        assert r2.codigo_retencion == "2"
        assert r2.porcentaje_retener == Decimal("70.00")
        assert r2.valor_retenido == Decimal("44.10")

    def test_sin_detalles(self):
        assert len(self.comp.detalles) == 0

    def test_sin_pagos(self):
        assert len(self.comp.pagos) == 0


class TestComprobanteToDict:
    def test_factura_to_dict(self):
        xml = _load_fixture("factura_real.xml")
        comp = parse_comprobante_sri(xml)
        d = comprobante_to_dict(comp)

        assert d["tipo_comprobante"] == "factura"
        assert d["ruc_emisor"] == "0916429921001"
        assert d["serie"] == "003-001"
        assert d["numero_completo"] == "003-001-000009676"
        assert d["importe_total"] == Decimal("27.25")
        assert d["parse_error"] is False
        assert d["xml_raw"] is not None

    def test_retencion_to_dict(self):
        xml = _load_fixture("retencion_real.xml")
        comp = parse_comprobante_sri(xml)
        d = comprobante_to_dict(comp)

        assert d["tipo_comprobante"] == "retencion"
        assert d["ruc_emisor"] == "1790941450001"
        assert d["periodo_fiscal"] == "02/2026"
        assert d["cod_doc"] == "07"


class TestParseErrors:
    def test_xml_sin_autorizacion(self):
        xml = b"<factura><infoTributaria/></factura>"
        with pytest.raises(ValueError, match="autorizacion"):
            parse_comprobante_sri(xml)

    def test_xml_sin_comprobante(self):
        xml = b"<autorizacion><estado>AUTORIZADO</estado></autorizacion>"
        with pytest.raises(ValueError, match="comprobante"):
            parse_comprobante_sri(xml)
