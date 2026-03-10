from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from tasks.constants import expand_tipo_comprobante, normalize_tipo_comprobante


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("Factura", "Factura"),
        ("factura", "Factura"),
        ("Retencion", "Comprobante de Retención"),
        ("retención", "Comprobante de Retención"),
        ("Comprobante de Retención", "Comprobante de Retención"),
        ("nota_credito", "Notas de Crédito"),
    ],
)
def test_normalize_tipo_comprobante_aliases(raw_value, expected):
    assert normalize_tipo_comprobante(raw_value) == expected


def test_normalize_tipo_comprobante_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_tipo_comprobante("Inventado")


@pytest.mark.parametrize("raw_value", ["todos", "todo", "all"])
def test_expand_tipo_comprobante_all_aliases(raw_value):
    assert expand_tipo_comprobante(raw_value) == [
        "Factura",
        "Liquidación de compra de bienes y prestación de servicios",
        "Notas de Crédito",
        "Notas de Débito",
        "Comprobante de Retención",
    ]
