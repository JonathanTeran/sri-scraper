"""
Parser XML para comprobantes electrónicos del SRI Ecuador.

Parsea XMLs con wrapper <autorizacion> que contienen el comprobante
real dentro de un CDATA en <comprobante>.

Funciones públicas:
    parse_comprobante_sri(xml_bytes) -> ComprobanteParseado
    comprobante_to_dict(comprobante) -> dict
"""

from datetime import datetime, date
from decimal import Decimal, InvalidOperation

from lxml import etree

from parsers.types.base import (
    ComprobanteParseado,
    DetalleFactura,
    ImpuestoRetencionData,
    InfoAutorizacion,
    InfoTributaria,
    PagoData,
)

# ── Mapeo codDoc → tipo comprobante ─────────────────────────────────────────

COD_DOC_MAP: dict[str, str] = {
    "01": "factura",
    "03": "liquidacion",
    "04": "nota_credito",
    "05": "nota_debito",
    "06": "guia_remision",
    "07": "retencion",
}

TAG_RAIZ_MAP: dict[str, str] = {
    "factura": "factura",
    "liquidacionCompra": "liquidacion",
    "notaCredito": "nota_credito",
    "notaDebito": "nota_debito",
    "guiaRemision": "guia_remision",
    "comprobanteRetencion": "retencion",
}

# Tags de info específica según tipo
INFO_TAG_MAP: dict[str, str] = {
    "factura": "infoFactura",
    "liquidacion": "infoLiquidacionCompra",
    "nota_credito": "infoNotaCredito",
    "nota_debito": "infoNotaDebito",
    "guia_remision": "infoGuiaRemision",
    "retencion": "infoCompRetencion",
}

# ── Tablas de referencia SRI ────────────────────────────────────────────────

FORMA_PAGO_DESC: dict[str, str] = {
    "01": "Sin utilización del sistema financiero",
    "15": "Compensación de deudas",
    "16": "Tarjeta de débito",
    "17": "Dinero electrónico",
    "18": "Tarjeta prepago",
    "19": "Tarjeta de crédito",
    "20": "Otros con utilización del sistema financiero",
    "21": "Endoso de títulos",
}

CODIGO_TRIBUTO_MAP: dict[str, str] = {
    "1": "renta",
    "2": "iva",
    "6": "isd",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _text(element: etree._Element | None, tag: str) -> str:
    """Extrae texto de un sub-elemento, retorna '' si no existe."""
    if element is None:
        return ""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _decimal(value: str) -> Decimal:
    """Convierte string a Decimal de forma segura."""
    if not value:
        return Decimal("0")
    try:
        return Decimal(value)
    except InvalidOperation:
        return Decimal("0")


def _parse_fecha_emision(fecha_str: str) -> date | None:
    """Parsea fecha en formato dd/MM/yyyy."""
    if not fecha_str:
        return None
    try:
        return datetime.strptime(fecha_str, "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_fecha_autorizacion(fecha_str: str) -> datetime | None:
    """Parsea fecha de autorización ISO con timezone."""
    if not fecha_str:
        return None
    try:
        return datetime.fromisoformat(fecha_str)
    except ValueError:
        try:
            return datetime.strptime(fecha_str[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


def _strip_namespace(root: etree._Element) -> None:
    """Elimina namespaces del XML para simplificar el parsing."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
        for key in list(elem.attrib.keys()):
            if "}" in key:
                new_key = key.split("}", 1)[1]
                elem.attrib[new_key] = elem.attrib.pop(key)


# ── Parseo de secciones ────────────────────────────────────────────────────

def _parse_autorizacion(root: etree._Element) -> InfoAutorizacion:
    """Parsea el wrapper <autorizacion>."""
    return InfoAutorizacion(
        estado=_text(root, "estado"),
        numero_autorizacion=_text(root, "numeroAutorizacion"),
        fecha_autorizacion=_parse_fecha_autorizacion(
            _text(root, "fechaAutorizacion")
        ),
        ambiente=_text(root, "ambiente"),
    )


def _parse_info_tributaria(comprobante: etree._Element) -> InfoTributaria:
    """Parsea <infoTributaria> común a todos los tipos."""
    info = comprobante.find("infoTributaria")
    if info is None:
        return InfoTributaria()
    return InfoTributaria(
        ambiente=_text(info, "ambiente"),
        tipo_emision=_text(info, "tipoEmision"),
        razon_social=_text(info, "razonSocial"),
        nombre_comercial=_text(info, "nombreComercial"),
        ruc=_text(info, "ruc"),
        clave_acceso=_text(info, "claveAcceso"),
        cod_doc=_text(info, "codDoc"),
        estab=_text(info, "estab"),
        pto_emi=_text(info, "ptoEmi"),
        secuencial=_text(info, "secuencial"),
        dir_matriz=_text(info, "dirMatriz"),
    )


def _parse_detalles(comprobante: etree._Element) -> list[DetalleFactura]:
    """Parsea <detalles> de factura, liquidación, NC, ND."""
    detalles_el = comprobante.find("detalles")
    if detalles_el is None:
        return []

    resultado: list[DetalleFactura] = []
    for idx, det in enumerate(detalles_el.findall("detalle")):
        # Buscar impuesto IVA del detalle
        iva_codigo = ""
        iva_codigo_porcentaje = ""
        iva_tarifa = Decimal("0")
        iva_base_imponible = Decimal("0")
        iva_valor = Decimal("0")

        impuestos_el = det.find("impuestos")
        if impuestos_el is not None:
            for imp in impuestos_el.findall("impuesto"):
                cod = _text(imp, "codigo")
                if cod == "2":  # IVA
                    iva_codigo = cod
                    iva_codigo_porcentaje = _text(imp, "codigoPorcentaje")
                    iva_tarifa = _decimal(_text(imp, "tarifa"))
                    iva_base_imponible = _decimal(_text(imp, "baseImponible"))
                    iva_valor = _decimal(_text(imp, "valor"))
                    break

        resultado.append(DetalleFactura(
            orden=idx + 1,
            codigo_principal=_text(det, "codigoPrincipal"),
            codigo_auxiliar=_text(det, "codigoAuxiliar"),
            descripcion=_text(det, "descripcion"),
            cantidad=_decimal(_text(det, "cantidad")),
            precio_unitario=_decimal(_text(det, "precioUnitario")),
            descuento=_decimal(_text(det, "descuento")),
            precio_total_sin_impuesto=_decimal(
                _text(det, "precioTotalSinImpuesto")
            ),
            iva_codigo=iva_codigo,
            iva_codigo_porcentaje=iva_codigo_porcentaje,
            iva_tarifa=iva_tarifa,
            iva_base_imponible=iva_base_imponible,
            iva_valor=iva_valor,
        ))

    return resultado


def _parse_retenciones(comprobante: etree._Element) -> list[ImpuestoRetencionData]:
    """Parsea <impuestos> de comprobanteRetencion."""
    impuestos_el = comprobante.find("impuestos")
    if impuestos_el is None:
        return []

    resultado: list[ImpuestoRetencionData] = []
    for imp in impuestos_el.findall("impuesto"):
        codigo = _text(imp, "codigo")
        resultado.append(ImpuestoRetencionData(
            codigo=codigo,
            tipo_tributo=CODIGO_TRIBUTO_MAP.get(codigo, "renta"),
            codigo_retencion=_text(imp, "codigoRetencion"),
            base_imponible=_decimal(_text(imp, "baseImponible")),
            porcentaje_retener=_decimal(_text(imp, "porcentajeRetener")),
            valor_retenido=_decimal(_text(imp, "valorRetenido")),
            cod_doc_sustento=_text(imp, "codDocSustento"),
            num_doc_sustento=_text(imp, "numDocSustento"),
            fecha_emision_doc_sustento=_text(imp, "fechaEmisionDocSustento"),
        ))

    return resultado


def _parse_pagos(info_element: etree._Element | None) -> list[PagoData]:
    """Parsea <pagos> de factura, liquidación, NC, ND."""
    if info_element is None:
        return []

    pagos_el = info_element.find("pagos")
    if pagos_el is None:
        return []

    resultado: list[PagoData] = []
    for pago in pagos_el.findall("pago"):
        forma = _text(pago, "formaPago")
        resultado.append(PagoData(
            forma_pago=forma,
            forma_pago_desc=FORMA_PAGO_DESC.get(forma, f"Código {forma}"),
            total=_decimal(_text(pago, "total")),
            plazo=_text(pago, "plazo"),
            unidad_tiempo=_text(pago, "unidadTiempo"),
        ))

    return resultado


def _calcular_total_iva(info_element: etree._Element | None) -> Decimal:
    """Suma el IVA total desde <totalConImpuestos>."""
    if info_element is None:
        return Decimal("0")

    total_impuestos = info_element.find("totalConImpuestos")
    if total_impuestos is None:
        return Decimal("0")

    total_iva = Decimal("0")
    for ti in total_impuestos.findall("totalImpuesto"):
        if _text(ti, "codigo") == "2":  # IVA
            total_iva += _decimal(_text(ti, "valor"))

    return total_iva


def _parse_info_factura(
    comprobante: etree._Element,
    tipo: str,
) -> dict:
    """Parsea la sección de info específica según el tipo de comprobante."""
    info_tag = INFO_TAG_MAP.get(tipo, "")
    info = comprobante.find(info_tag)
    if info is None:
        return {}

    result: dict = {
        "fecha_emision": _parse_fecha_emision(_text(info, "fechaEmision")),
        "dir_establecimiento": _text(info, "dirEstablecimiento"),
        "obligado_contabilidad": _text(info, "obligadoContabilidad"),
        "contribuyente_especial": _text(info, "contribuyenteEspecial"),
    }

    if tipo == "retencion":
        # Retención tiene campos diferentes
        result["tipo_id_receptor"] = _text(
            info, "tipoIdentificacionSujetoRetenido"
        )
        result["razon_social_receptor"] = _text(
            info, "razonSocialSujetoRetenido"
        )
        result["identificacion_receptor"] = _text(
            info, "identificacionSujetoRetenido"
        )
        result["periodo_fiscal"] = _text(info, "periodoFiscal")
        result["total_sin_impuestos"] = Decimal("0")
        result["total_descuento"] = Decimal("0")
        result["total_iva"] = Decimal("0")
        result["importe_total"] = Decimal("0")
        result["moneda"] = "DOLAR"
        result["propina"] = Decimal("0")
        result["pagos"] = []
    else:
        # Factura, Liquidación, NC, ND
        result["tipo_id_receptor"] = _text(
            info, "tipoIdentificacionComprador"
        )
        result["razon_social_receptor"] = _text(
            info, "razonSocialComprador"
        )
        result["identificacion_receptor"] = _text(
            info, "identificacionComprador"
        )
        result["periodo_fiscal"] = ""
        result["total_sin_impuestos"] = _decimal(
            _text(info, "totalSinImpuestos")
        )
        result["total_descuento"] = _decimal(
            _text(info, "totalDescuento")
        )
        result["total_iva"] = _calcular_total_iva(info)
        result["importe_total"] = _decimal(_text(info, "importeTotal"))
        result["moneda"] = _text(info, "moneda") or "DOLAR"
        result["propina"] = _decimal(_text(info, "propina"))
        result["pagos"] = _parse_pagos(info)

    return result


def _detectar_tipo(comprobante: etree._Element, cod_doc: str) -> str:
    """Detecta el tipo de comprobante por tag raíz o codDoc."""
    # Primero intentar por tag raíz
    tag = comprobante.tag
    if tag in TAG_RAIZ_MAP:
        return TAG_RAIZ_MAP[tag]

    # Fallback a codDoc
    return COD_DOC_MAP.get(cod_doc, "factura")


# ── Función pública principal ───────────────────────────────────────────────

def parse_comprobante_sri(xml_bytes: bytes) -> ComprobanteParseado:
    """
    Parsea un XML completo del SRI (con wrapper <autorizacion>).

    Args:
        xml_bytes: Contenido del XML como bytes.

    Returns:
        ComprobanteParseado con toda la información extraída.

    Raises:
        ValueError: Si el XML no tiene la estructura esperada.
    """
    xml_str = xml_bytes.decode("utf-8", errors="replace")

    # Parsear wrapper <autorizacion>
    root = etree.fromstring(xml_bytes)

    if root.tag != "autorizacion":
        raise ValueError(
            f"XML raíz esperado: <autorizacion>, encontrado: <{root.tag}>"
        )

    autorizacion = _parse_autorizacion(root)

    # Extraer CDATA del <comprobante>
    comprobante_el = root.find("comprobante")
    if comprobante_el is None or not comprobante_el.text:
        raise ValueError("No se encontró <comprobante> con CDATA")

    comprobante_xml = comprobante_el.text.strip()

    # Parsear el comprobante interior
    comprobante_root = etree.fromstring(comprobante_xml.encode("utf-8"))

    # Eliminar namespaces (ds:Signature, etc.)
    _strip_namespace(comprobante_root)

    # Remover ds:Signature si existe
    for sig in comprobante_root.findall(".//Signature"):
        sig.getparent().remove(sig)

    # Info tributaria
    info_tributaria = _parse_info_tributaria(comprobante_root)

    # Detectar tipo
    tipo = _detectar_tipo(comprobante_root, info_tributaria.cod_doc)

    # Parsear info específica
    info = _parse_info_factura(comprobante_root, tipo)

    # Detalles (no aplica a retenciones)
    detalles: list[DetalleFactura] = []
    if tipo != "retencion":
        detalles = _parse_detalles(comprobante_root)

    # Retenciones (solo para tipo retencion)
    retenciones: list[ImpuestoRetencionData] = []
    if tipo == "retencion":
        retenciones = _parse_retenciones(comprobante_root)

    # Pagos
    pagos = info.get("pagos", [])

    return ComprobanteParseado(
        autorizacion=autorizacion,
        info_tributaria=info_tributaria,
        tipo_comprobante=tipo,
        fecha_emision=info.get("fecha_emision"),
        dir_establecimiento=info.get("dir_establecimiento", ""),
        obligado_contabilidad=info.get("obligado_contabilidad", ""),
        contribuyente_especial=info.get("contribuyente_especial", ""),
        tipo_id_receptor=info.get("tipo_id_receptor", ""),
        razon_social_receptor=info.get("razon_social_receptor", ""),
        identificacion_receptor=info.get("identificacion_receptor", ""),
        periodo_fiscal=info.get("periodo_fiscal", ""),
        total_sin_impuestos=info.get("total_sin_impuestos", Decimal("0")),
        total_descuento=info.get("total_descuento", Decimal("0")),
        total_iva=info.get("total_iva", Decimal("0")),
        importe_total=info.get("importe_total", Decimal("0")),
        moneda=info.get("moneda", "DOLAR"),
        propina=info.get("propina", Decimal("0")),
        detalles=detalles,
        retenciones=retenciones,
        pagos=pagos,
        xml_raw=xml_str,
    )


def comprobante_to_dict(comprobante: ComprobanteParseado) -> dict:
    """
    Convierte un ComprobanteParseado a un dict plano para INSERT en BD.

    Returns:
        Dict con las claves correspondientes al modelo Comprobante de la BD.
    """
    it = comprobante.info_tributaria
    az = comprobante.autorizacion

    return {
        # Autorización
        "estado_autorizacion": az.estado,
        "numero_autorizacion": az.numero_autorizacion,
        "fecha_autorizacion": az.fecha_autorizacion,
        "ambiente_autorizacion": az.ambiente,
        # Info tributaria
        "ruc_emisor": it.ruc,
        "razon_social_emisor": it.razon_social,
        "nombre_comercial": it.nombre_comercial or None,
        "cod_doc": it.cod_doc,
        "tipo_comprobante": comprobante.tipo_comprobante,
        "clave_acceso": it.clave_acceso,
        "estab": it.estab,
        "pto_emi": it.pto_emi,
        "secuencial": it.secuencial,
        "serie": f"{it.estab}-{it.pto_emi}",
        "numero_completo": f"{it.estab}-{it.pto_emi}-{it.secuencial}",
        # Cabecera
        "fecha_emision": comprobante.fecha_emision,
        "dir_establecimiento": comprobante.dir_establecimiento or None,
        "obligado_contabilidad": comprobante.obligado_contabilidad or None,
        "contribuyente_especial": comprobante.contribuyente_especial or None,
        # Receptor
        "tipo_id_receptor": comprobante.tipo_id_receptor or None,
        "razon_social_receptor": comprobante.razon_social_receptor or None,
        "identificacion_receptor": comprobante.identificacion_receptor or None,
        # Período fiscal
        "periodo_fiscal": comprobante.periodo_fiscal or None,
        # Totales
        "total_sin_impuestos": comprobante.total_sin_impuestos,
        "total_descuento": comprobante.total_descuento,
        "total_iva": comprobante.total_iva,
        "importe_total": comprobante.importe_total,
        "moneda": comprobante.moneda,
        "propina": comprobante.propina,
        # XML
        "xml_raw": comprobante.xml_raw,
        # Metadata
        "parse_error": False,
        "parse_error_msg": None,
    }
