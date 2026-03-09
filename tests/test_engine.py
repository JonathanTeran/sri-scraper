"""Tests del motor de scraping con mocks de Playwright."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from captcha.factory import crear_resolvers
from scrapers.engine import SRIScraperEngine, EstadoPortal, URLS


class TestSRIScraperEngine:
    def _make_engine(self):
        settings = MagicMock()
        settings.playwright_headless = True
        settings.browser_timeout_ms = 5000
        settings.download_timeout_ms = 5000
        settings.delay_min_ms = 10
        settings.delay_max_ms = 20
        settings.delay_between_pages_ms = 10
        settings.captcha_provider = "capsolver"
        settings.capsolver_api_key = "capsolver-key"
        settings.twocaptcha_api_key = ""
        settings.captcha_assisted_mode = "off"
        settings.captcha_assisted_timeout_sec = 120
        settings.screenshot_on_error = False
        settings.screenshot_path = "/tmp/screenshots"
        settings.xml_storage_path = "/tmp/xmls"
        settings.browser_prefer_nodriver = True
        settings.browser_persistent_context = True
        settings.browser_executable_path = ""
        settings.browser_channel = ""
        settings.browser_profile_path = "/tmp/chrome_profile"
        settings.browser_proxy_server = ""
        settings.browser_proxy_username = ""
        settings.browser_proxy_password = ""
        settings.browser_proxy_bypass = ""

        return SRIScraperEngine(
            tenant_ruc="0916429921001",
            tenant_usuario="test_user",
            tenant_password="test_pass",
            periodo_anio=2026,
            periodo_mes=3,
            tipo_comprobante="Factura",
            settings=settings,
        )

    def test_urls_definidas(self):
        assert "login" in URLS
        assert "portal" in URLS
        assert "srienlinea.sri.gob.ec" in URLS["login"]

    def test_estado_portal_enum(self):
        assert EstadoPortal.NORMAL.value == "normal"
        assert EstadoPortal.MANTENIMIENTO.value == "mantenimiento"
        assert EstadoPortal.SESION_EXPIRADA.value == "sesion_expirada"

    def test_engine_inicializacion(self):
        engine = self._make_engine()
        assert engine._ruc == "0916429921001"
        assert engine._anio == 2026
        assert engine._mes == 3
        assert engine._tipo == "Factura"
        assert engine._browser is None
        assert engine._page is None

    def test_extraer_html_de_respuesta_jsf(self):
        engine = self._make_engine()
        xml = """
        <partial-response>
          <changes>
            <update id="javax.faces.ViewRoot"><![CDATA[
              <div id="frmPrincipal:panelListaComprobantes">
                <table><tbody><tr><td>ok</td></tr></tbody></table>
              </div>
            ]]></update>
          </changes>
        </partial-response>
        """

        html = engine._extraer_html_de_respuesta_jsf(xml)

        assert html is not None
        assert "panelListaComprobantes" in html

    def test_extraer_comprobantes_de_html(self):
        engine = self._make_engine()
        html = """
        <table id="frmPrincipal:tablaComprobantes">
          <tbody>
            <tr>
              <td>Factura</td>
              <td>001-001-000000123</td>
              <td>0103202601091642992100120030010000096761234567814</td>
              <td><a id="frmPrincipal:tablaCompRecibidos:0:lnkXml" title="XML" onclick="descargarXml()">Descargar</a></td>
            </tr>
          </tbody>
        </table>
        """

        comprobantes = engine._extraer_comprobantes_de_html(html)

        assert len(comprobantes) == 1
        assert (
            comprobantes[0]["clave_acceso"]
            == "0103202601091642992100120030010000096761234567814"
        )
        assert "onclick_xml" in comprobantes[0]
        assert "xml_link_id" in comprobantes[0]

    def test_extraer_error_de_soap(self):
        engine = self._make_engine()
        soap = """
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <RespuestaAutorizacionComprobante>
              <autorizaciones>
                <autorizacion>
                  <estado>RECHAZADA</estado>
                  <mensajes>
                    <mensaje>
                      <mensaje>ERROR EN LA ESTRUCTURA DE LA CLAVE DE ACCESO</mensaje>
                      <informacionAdicional>Fuera de rango permitido</informacionAdicional>
                    </mensaje>
                  </mensajes>
                </autorizacion>
              </autorizaciones>
            </RespuestaAutorizacionComprobante>
          </soap:Body>
        </soap:Envelope>
        """

        error = engine._extraer_error_de_soap(soap)

        assert "RECHAZADA" in error
        assert "FUERA DE RANGO" not in error
        assert "Fuera de rango permitido" in error

    def test_build_captcha_attempt_plan(self):
        engine = self._make_engine()

        attempts = engine._build_captcha_attempt_plan(5)

        assert [a["mode"] for a in attempts[:2]] == ["native", "native"]
        assert attempts[2]["provider"] == "capsolver"
        assert attempts[2]["variant"] == "enterprise_v3"
        assert attempts[3]["provider"] == "capsolver"
        assert attempts[3]["variant"] == "enterprise_v2"

    def test_build_captcha_attempt_plan_prioriza_proveedor_preferido(self):
        engine = self._make_engine()
        engine._captcha_resolvers = crear_resolvers(
            "capsolver",
            "2captcha-key",
            "capsolver-key",
        )

        attempts = engine._build_captcha_attempt_plan(6)

        assert [a["mode"] for a in attempts[:2]] == ["native", "native"]
        assert attempts[2]["provider"] == "capsolver"
        assert attempts[2]["variant"] == "enterprise_v3"
        assert attempts[3]["provider"] == "capsolver"
        assert attempts[3]["variant"] == "enterprise_v2"
        assert attempts[4]["provider"] == "2captcha"
        assert attempts[4]["variant"] == "enterprise_v3"
        assert attempts[5]["provider"] == "2captcha"
        assert attempts[5]["variant"] == "enterprise_v2"

    def test_build_captcha_attempt_plan_agrega_asistido_al_final(self):
        engine = self._make_engine()
        engine._settings.captcha_assisted_mode = "fallback"
        engine._settings.playwright_headless = False

        attempts = engine._build_captcha_attempt_plan(5)

        assert attempts[-1]["mode"] == "assisted"

    def test_build_profile_dir_aisla_por_tenant(self):
        engine = self._make_engine()

        assert engine._profile_dir.endswith("/0916429921001")

    def test_build_browser_launch_args_incluye_proxy(self):
        engine = self._make_engine()
        engine._settings.browser_proxy_server = "proxy.example.com:8080"
        engine._settings.browser_proxy_bypass = "localhost,127.0.0.1"

        args = engine._build_browser_launch_args()

        assert "--proxy-server=http://proxy.example.com:8080" in args
        assert "--proxy-bypass-list=localhost,127.0.0.1" in args

    def test_descargar_xml_con_fallbacks_usa_lnkxml_si_soap_rechaza(self):
        engine = self._make_engine()
        boton_xml = object()
        engine._descargar_xml_via_soap = AsyncMock(
            return_value=(None, "RECHAZADA")
        )
        engine._descargar_xml_de_fila = AsyncMock(return_value=b"<xml/>")

        xml_bytes, error, fuente = asyncio.run(
            engine._descargar_xml_con_fallbacks(
                client=object(),
                clave="123",
                boton_xml=boton_xml,
            )
        )

        assert xml_bytes == b"<xml/>"
        assert error is None
        assert fuente == "lnkXml"
        engine._descargar_xml_via_soap.assert_awaited_once()
        engine._descargar_xml_de_fila.assert_awaited_once_with(boton_xml)

    def test_descargar_xml_con_fallbacks_propagates_soap_error(self):
        engine = self._make_engine()
        engine._descargar_xml_via_soap = AsyncMock(
            return_value=(None, "RECHAZADA")
        )
        engine._descargar_xml_de_fila = AsyncMock(return_value=None)

        xml_bytes, error, fuente = asyncio.run(
            engine._descargar_xml_con_fallbacks(
                client=object(),
                clave="123",
                boton_xml=object(),
            )
        )

        assert xml_bytes is None
        assert error == "RECHAZADA"
        assert fuente is None


def test_crear_resolvers_respeta_orden_preferido():
    resolvers = crear_resolvers(
        "capsolver",
        "2captcha-key",
        "capsolver-key",
    )

    assert [item["provider"] for item in resolvers] == [
        "capsolver",
        "2captcha",
    ]
