"""Constantes compartidas del flujo de scraping."""

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
