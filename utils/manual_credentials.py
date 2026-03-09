"""Helpers para scripts manuales que necesitan credenciales SRI."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ManualTestCredentials:
    ruc: str
    usuario: str
    password: str


def get_manual_test_credentials() -> ManualTestCredentials:
    ruc = os.getenv("SRI_TEST_RUC", "").strip()
    usuario = os.getenv("SRI_TEST_USUARIO", ruc).strip()
    password = os.getenv("SRI_TEST_PASSWORD", "").strip()
    if not ruc or not usuario or not password:
        raise RuntimeError(
            "Define SRI_TEST_RUC, SRI_TEST_USUARIO opcional y SRI_TEST_PASSWORD"
        )
    return ManualTestCredentials(ruc=ruc, usuario=usuario, password=password)


def get_manual_test_period(
    default_year: int = 2025,
    default_month: int = 1,
) -> tuple[int, int]:
    year = int(os.getenv("SRI_TEST_ANIO", str(default_year)))
    month = int(os.getenv("SRI_TEST_MES", str(default_month)))
    return year, month
