"""Constantes compartidas del flujo de scraping."""

from __future__ import annotations

import re
import unicodedata

TIPOS_SCRAPING = [
    "Factura",
    "Liquidación de compra de bienes y prestación de servicios",
    "Notas de Crédito",
    "Notas de Débito",
    "Comprobante de Retención",
]

TIPO_MAP = {
    "Factura": "factura",
    "Liquidación de compra de bienes y prestación de servicios": "liquidacion",
    "Notas de Crédito": "nota_credito",
    "Notas de Débito": "nota_debito",
    "Comprobante de Retención": "retencion",
}


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower().strip()
    return re.sub(r"\s+", " ", ascii_text)


TIPO_ALIASES = {
    _normalize_key("Factura"): "Factura",
    _normalize_key("factura"): "Factura",
    _normalize_key("Liquidación de compra de bienes y prestación de servicios"): (
        "Liquidación de compra de bienes y prestación de servicios"
    ),
    _normalize_key("liquidacion"): (
        "Liquidación de compra de bienes y prestación de servicios"
    ),
    _normalize_key("liquidacion de compra"): (
        "Liquidación de compra de bienes y prestación de servicios"
    ),
    _normalize_key("Notas de Crédito"): "Notas de Crédito",
    _normalize_key("nota de credito"): "Notas de Crédito",
    _normalize_key("nota credito"): "Notas de Crédito",
    _normalize_key("nota_credito"): "Notas de Crédito",
    _normalize_key("Notas de Débito"): "Notas de Débito",
    _normalize_key("nota de debito"): "Notas de Débito",
    _normalize_key("nota debito"): "Notas de Débito",
    _normalize_key("nota_debito"): "Notas de Débito",
    _normalize_key("Comprobante de Retención"): "Comprobante de Retención",
    _normalize_key("comprobante de retencion"): "Comprobante de Retención",
    _normalize_key("retencion"): "Comprobante de Retención",
    _normalize_key("retención"): "Comprobante de Retención",
}


def normalize_tipo_comprobante(value: str) -> str:
    """Normaliza aliases del tipo a la etiqueta exacta usada por SRI."""
    normalized = TIPO_ALIASES.get(_normalize_key(value), "")
    if not normalized:
        raise ValueError(f"Tipo de comprobante no soportado: {value}")
    return normalized
