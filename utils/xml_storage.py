"""Helpers para persistir XMLs por tenant/período."""

from pathlib import Path


def build_xml_storage_path(
    base_path: str,
    tenant_ruc: str,
    periodo_anio: int,
    periodo_mes: int,
    clave_acceso: str,
) -> Path:
    """Build a multi-tenant XML path: base/ruc/anio/mes/clave.xml."""
    return (
        Path(base_path)
        / tenant_ruc
        / str(periodo_anio)
        / f"{periodo_mes:02d}"
        / f"{clave_acceso}.xml"
    )
