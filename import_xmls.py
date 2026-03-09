"""
Script para importar XMLs existentes del disco a la base de datos.
Uso: python import_xmls.py
"""

import asyncio
import glob
from datetime import datetime

from sqlalchemy import select

from config.settings import get_settings
from db.session import _build_session_factory
from db.models.comprobante import Comprobante, TipoComprobante
from db.models.detalle import DetalleComprobante
from db.models.retencion import ImpuestoRetencion
from db.models.pago import Pago
from db.models.tenant import Tenant
from parsers.xml_parser import parse_comprobante_sri, comprobante_to_dict


def _make_naive(dt):
    """Strip timezone info from datetime."""
    if dt and hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def importar_xmls():
    settings = get_settings()
    xml_dir = settings.xml_storage_path

    xml_files = glob.glob(f"{xml_dir}/**/*.xml", recursive=True)
    if not xml_files:
        xml_files = glob.glob(f"{xml_dir}/*.xml")

    print(f"Encontrados {len(xml_files)} archivos XML")

    session_factory = _build_session_factory()

    # Get tenant
    async with session_factory() as session:
        result = await session.execute(select(Tenant).where(Tenant.activo == True))
        tenants = result.scalars().all()
        if not tenants:
            print("ERROR: No hay tenants activos")
            return
        tenant_id = tenants[0].id
        tenant_ruc = tenants[0].ruc
        tenant_nombre = tenants[0].nombre
        print(f"Usando tenant: {tenant_nombre} (RUC: {tenant_ruc})")

    total_nuevos = 0
    total_existentes = 0
    total_errores = 0

    for xml_path in xml_files:
        filename = xml_path.split("/")[-1]
        async with session_factory() as session:
            try:
                with open(xml_path, "rb") as f:
                    xml_bytes = f.read()

                xml_str = xml_bytes.decode("utf-8", errors="replace")

                # Parsear
                try:
                    comp = parse_comprobante_sri(xml_bytes)
                    data = comprobante_to_dict(comp)
                except Exception as e:
                    print(f"  PARSE ERROR [{filename}]: {e}")
                    comp_obj = Comprobante(
                        tenant_id=tenant_id,
                        estado_autorizacion="DESCONOCIDO",
                        numero_autorizacion="",
                        ambiente_autorizacion="",
                        ruc_emisor="",
                        razon_social_emisor="",
                        cod_doc="",
                        tipo_comprobante=TipoComprobante.FACTURA,
                        clave_acceso=filename.replace(".xml", "")[:49],
                        estab="", pto_emi="", secuencial="",
                        serie="", numero_completo="",
                        fecha_emision=datetime.utcnow().date(),
                        xml_raw=xml_str,
                        parse_error=True,
                        parse_error_msg=str(e)[:500],
                    )
                    session.add(comp_obj)
                    await session.commit()
                    total_errores += 1
                    continue

                # Strip timezone from fecha_autorizacion
                if "fecha_autorizacion" in data:
                    data["fecha_autorizacion"] = _make_naive(data["fecha_autorizacion"])

                # Verificar duplicado
                clave = data["clave_acceso"]
                existing = await session.execute(
                    select(Comprobante.id).where(
                        Comprobante.tenant_id == tenant_id,
                        Comprobante.clave_acceso == clave,
                    )
                )
                if existing.scalar_one_or_none():
                    total_existentes += 1
                    continue

                # Crear comprobante
                comp_obj = Comprobante(tenant_id=tenant_id, **data)
                session.add(comp_obj)
                await session.flush()

                # Guardar detalles
                for det in comp.detalles:
                    session.add(DetalleComprobante(
                        comprobante_id=comp_obj.id,
                        tenant_id=tenant_id,
                        orden=det.orden,
                        codigo_principal=det.codigo_principal or None,
                        codigo_auxiliar=det.codigo_auxiliar or None,
                        descripcion=det.descripcion,
                        cantidad=det.cantidad,
                        precio_unitario=det.precio_unitario,
                        descuento=det.descuento,
                        precio_total_sin_impuesto=det.precio_total_sin_impuesto,
                        iva_codigo=det.iva_codigo or None,
                        iva_codigo_porcentaje=det.iva_codigo_porcentaje or None,
                        iva_tarifa=det.iva_tarifa,
                        iva_base_imponible=det.iva_base_imponible,
                        iva_valor=det.iva_valor,
                    ))

                # Guardar retenciones
                for ret in comp.retenciones:
                    session.add(ImpuestoRetencion(
                        comprobante_id=comp_obj.id,
                        tenant_id=tenant_id,
                        codigo=ret.codigo,
                        tipo_tributo=ret.tipo_tributo,
                        codigo_retencion=ret.codigo_retencion,
                        base_imponible=ret.base_imponible,
                        porcentaje_retener=ret.porcentaje_retener,
                        valor_retenido=ret.valor_retenido,
                        cod_doc_sustento=ret.cod_doc_sustento or None,
                        num_doc_sustento=ret.num_doc_sustento or None,
                        fecha_emision_doc_sustento=ret.fecha_emision_doc_sustento or None,
                    ))

                # Guardar pagos
                for pag in comp.pagos:
                    session.add(Pago(
                        comprobante_id=comp_obj.id,
                        tenant_id=tenant_id,
                        forma_pago=pag.forma_pago,
                        forma_pago_desc=pag.forma_pago_desc,
                        total=pag.total,
                        plazo=pag.plazo or None,
                        unidad_tiempo=pag.unidad_tiempo or None,
                    ))

                await session.commit()
                total_nuevos += 1
                print(f"  OK [{data['tipo_comprobante']}] {data['numero_completo']} - {data['razon_social_emisor'][:30]} - ${data['importe_total']}")

            except Exception as e:
                await session.rollback()
                print(f"  ERROR [{filename}]: {str(e)[:100]}")
                total_errores += 1

    print(f"\n{'='*50}")
    print(f"Importación completada:")
    print(f"  Nuevos:     {total_nuevos}")
    print(f"  Existentes: {total_existentes}")
    print(f"  Errores:    {total_errores}")
    print(f"  Total:      {len(xml_files)}")


if __name__ == "__main__":
    asyncio.run(importar_xmls())
