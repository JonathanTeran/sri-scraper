"""
Exportación de comprobantes a Excel con 3 hojas:
- Comprobantes (cabecera)
- Detalles (líneas)
- Retenciones (impuestos retenidos)
"""

import io
from decimal import Decimal

import pandas as pd

from db.models.comprobante import Comprobante


def exportar_comprobantes_excel(
    comprobantes: list[Comprobante],
) -> bytes:
    """
    Genera un archivo Excel con los comprobantes proporcionados.
    Retorna bytes del archivo .xlsx.
    """
    # Hoja 1: Comprobantes
    comp_rows = []
    for c in comprobantes:
        comp_rows.append({
            "Clave Acceso": c.clave_acceso,
            "Tipo": c.tipo_comprobante.value
            if hasattr(c.tipo_comprobante, "value")
            else c.tipo_comprobante,
            "Número": c.numero_completo,
            "Fecha Emisión": c.fecha_emision,
            "RUC Emisor": c.ruc_emisor,
            "Razón Social Emisor": c.razon_social_emisor,
            "Receptor": c.razon_social_receptor or "",
            "ID Receptor": c.identificacion_receptor or "",
            "Subtotal": float(c.total_sin_impuestos),
            "IVA": float(c.total_iva),
            "Total": float(c.importe_total),
            "Descuento": float(c.total_descuento),
            "Estado": c.estado_autorizacion,
            "N° Autorización": c.numero_autorizacion,
            "Período Fiscal": c.periodo_fiscal or "",
        })

    # Hoja 2: Detalles
    det_rows = []
    for c in comprobantes:
        for d in c.detalles:
            det_rows.append({
                "Clave Acceso": c.clave_acceso,
                "N° Comprobante": c.numero_completo,
                "Orden": d.orden,
                "Código": d.codigo_principal or "",
                "Descripción": d.descripcion,
                "Cantidad": float(d.cantidad),
                "P. Unitario": float(d.precio_unitario),
                "Descuento": float(d.descuento),
                "Subtotal": float(d.precio_total_sin_impuesto),
                "IVA Base": float(d.iva_base_imponible),
                "IVA Valor": float(d.iva_valor),
                "IVA %": float(d.iva_tarifa),
            })

    # Hoja 3: Retenciones
    ret_rows = []
    for c in comprobantes:
        for r in c.retenciones:
            ret_rows.append({
                "Clave Acceso": c.clave_acceso,
                "N° Comprobante": c.numero_completo,
                "Tipo Tributo": r.tipo_tributo.value
                if hasattr(r.tipo_tributo, "value")
                else r.tipo_tributo,
                "Código Retención": r.codigo_retencion,
                "Base Imponible": float(r.base_imponible),
                "% Retención": float(r.porcentaje_retener),
                "Valor Retenido": float(r.valor_retenido),
                "Doc Sustento": r.num_doc_sustento or "",
                "Fecha Doc Sustento": r.fecha_emision_doc_sustento or "",
            })

    df_comp = pd.DataFrame(comp_rows) if comp_rows else pd.DataFrame()
    df_det = pd.DataFrame(det_rows) if det_rows else pd.DataFrame()
    df_ret = pd.DataFrame(ret_rows) if ret_rows else pd.DataFrame()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_comp.to_excel(writer, sheet_name="Comprobantes", index=False)
        df_det.to_excel(writer, sheet_name="Detalles", index=False)
        df_ret.to_excel(writer, sheet_name="Retenciones", index=False)

    return output.getvalue()
