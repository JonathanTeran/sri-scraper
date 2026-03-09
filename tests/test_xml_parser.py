from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parsers.xml_parser import parse_comprobante_sri


INNER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<factura id="comprobante" version="1.1.0">
  <infoTributaria>
    <ambiente>2</ambiente>
    <tipoEmision>1</tipoEmision>
    <razonSocial>EMISOR PRUEBA S.A.</razonSocial>
    <nombreComercial>EMISOR PRUEBA</nombreComercial>
    <ruc>1790012345001</ruc>
    <claveAcceso>0103202601179001234500120010010000001231234567811</claveAcceso>
    <codDoc>01</codDoc>
    <estab>001</estab>
    <ptoEmi>001</ptoEmi>
    <secuencial>000000123</secuencial>
    <dirMatriz>Quito</dirMatriz>
  </infoTributaria>
  <infoFactura>
    <fechaEmision>01/03/2026</fechaEmision>
    <dirEstablecimiento>Quito</dirEstablecimiento>
    <obligadoContabilidad>SI</obligadoContabilidad>
    <tipoIdentificacionComprador>05</tipoIdentificacionComprador>
    <razonSocialComprador>RECEPTOR PRUEBA</razonSocialComprador>
    <identificacionComprador>1207481803001</identificacionComprador>
    <totalSinImpuestos>10.00</totalSinImpuestos>
    <totalDescuento>0.00</totalDescuento>
    <totalConImpuestos>
      <totalImpuesto>
        <codigo>2</codigo>
        <codigoPorcentaje>2</codigoPorcentaje>
        <baseImponible>10.00</baseImponible>
        <valor>1.20</valor>
      </totalImpuesto>
    </totalConImpuestos>
    <propina>0.00</propina>
    <importeTotal>11.20</importeTotal>
    <moneda>DOLAR</moneda>
  </infoFactura>
</factura>
"""

RETENCION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<comprobanteRetencion id="comprobante" version="2.0.0">
  <infoTributaria>
    <ambiente>2</ambiente>
    <tipoEmision>1</tipoEmision>
    <razonSocial>EMISOR RETENCION S.A.</razonSocial>
    <nombreComercial>EMISOR RETENCION</nombreComercial>
    <ruc>1790012345001</ruc>
    <claveAcceso>0102202601179001234500120010010000009991234567817</claveAcceso>
    <codDoc>07</codDoc>
    <estab>001</estab>
    <ptoEmi>001</ptoEmi>
    <secuencial>000000999</secuencial>
    <dirMatriz>Quito</dirMatriz>
  </infoTributaria>
  <infoCompRetencion>
    <fechaEmision>01/02/2026</fechaEmision>
    <dirEstablecimiento>Quito</dirEstablecimiento>
    <obligadoContabilidad>SI</obligadoContabilidad>
    <tipoIdentificacionSujetoRetenido>05</tipoIdentificacionSujetoRetenido>
    <razonSocialSujetoRetenido>RECEPTOR RETENCION</razonSocialSujetoRetenido>
    <identificacionSujetoRetenido>1207481803001</identificacionSujetoRetenido>
    <periodoFiscal>02/2026</periodoFiscal>
  </infoCompRetencion>
  <impuestos>
    <impuesto>
      <codigo>2</codigo>
      <codigoRetencion>1</codigoRetencion>
      <baseImponible>100.00</baseImponible>
      <porcentajeRetener>30.00</porcentajeRetener>
      <valorRetenido>30.00</valorRetenido>
      <codDocSustento>01</codDocSustento>
      <numDocSustento>001001000000123</numDocSustento>
      <fechaEmisionDocSustento>01/02/2026</fechaEmisionDocSustento>
    </impuesto>
  </impuestos>
</comprobanteRetencion>
"""


WRAPPED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<autorizacion>
  <estado>AUTORIZADO</estado>
  <numeroAutorizacion>0103202601179001234500120010010000001231234567811</numeroAutorizacion>
  <fechaAutorizacion>2026-03-01T10:15:30-05:00</fechaAutorizacion>
  <ambiente>PRODUCCION</ambiente>
  <comprobante><![CDATA[{INNER_XML}]]></comprobante>
</autorizacion>
"""


def test_parse_comprobante_sri_accepts_wrapper():
    comp = parse_comprobante_sri(WRAPPED_XML.encode("utf-8"))

    assert comp.info_tributaria.ruc == "1790012345001"
    assert comp.tipo_comprobante == "factura"
    assert comp.autorizacion.estado == "AUTORIZADO"
    assert comp.importe_total == Decimal("11.20")


def test_parse_comprobante_sri_accepts_inner_xml():
    comp = parse_comprobante_sri(INNER_XML.encode("utf-8"))

    assert comp.info_tributaria.ruc == "1790012345001"
    assert comp.tipo_comprobante == "factura"
    assert comp.autorizacion.estado == ""
    assert comp.importe_total == Decimal("11.20")


def test_parse_comprobante_sri_retencion_keeps_doc_sustento():
    comp = parse_comprobante_sri(RETENCION_XML.encode("utf-8"))

    assert comp.tipo_comprobante == "retencion"
    assert comp.periodo_fiscal == "02/2026"
    assert len(comp.retenciones) == 1
    ret = comp.retenciones[0]
    assert ret.tipo_tributo == "iva"
    assert ret.base_imponible == Decimal("100.00")
    assert ret.porcentaje_retener == Decimal("30.00")
    assert ret.valor_retenido == Decimal("30.00")
    assert ret.cod_doc_sustento == "01"
    assert ret.num_doc_sustento == "001001000000123"
    assert ret.fecha_emision_doc_sustento == "01/02/2026"
