"""Tests para almacenamiento multi-tenant de XMLs."""

from utils.xml_storage import build_xml_storage_path


def test_build_xml_storage_path_usa_tenant_y_periodo():
    path = build_xml_storage_path(
        "./xmls",
        "1207481803001",
        2026,
        2,
        "2602202601179071031900121260050001124375658032315",
    )

    assert str(path) == (
        "xmls/1207481803001/2026/02/"
        "2602202601179071031900121260050001124375658032315.xml"
    )
