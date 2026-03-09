"""Configuración del portal SRI y assets reutilizables del scraper."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


URLS = {
    "login": (
        "https://srienlinea.sri.gob.ec/auth/realms/Internet"
        "/protocol/openid-connect/auth"
        "?client_id=app-sri-claves-angular"
        "&redirect_uri=https%3A%2F%2Fsrienlinea.sri.gob.ec"
        "%2Fsri-en-linea%2F%2Fcontribuyente%2Fperfil"
        "&response_mode=fragment&response_type=code&scope=openid"
    ),
    "portal": (
        "https://srienlinea.sri.gob.ec/tuportal-internet"
        "/accederAplicacion.jspa?redireccion=57&idGrupo=55"
    ),
}

TIPOS_COMPROBANTE = [
    "Factura",
    "Liquidación de compra de bienes y prestación de servicios",
    "Notas de Crédito",
    "Notas de Débito",
    "Comprobante de Retención",
]

SEL = {
    "anio": '[id="frmPrincipal:ano"]',
    "mes": '[id="frmPrincipal:mes"]',
    "dia": '[id="frmPrincipal:dia"]',
    "tipo": '[id="frmPrincipal:cmbTipoComprobante"]',
    "consultar": '[id="frmPrincipal:btnBuscar"]',
    "anterior": '[id="frmPrincipal:btnAnterior"]',
}

MESES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]

RECAPTCHA_ACTION = "consulta_cel_recibidos"

_JS_DIR = Path(__file__).with_name("js")


@lru_cache(maxsize=None)
def load_js_asset(name: str) -> str:
    return (_JS_DIR / name).read_text(encoding="utf-8")
