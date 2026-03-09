"""
Motor de scraping para el portal SRI en Línea de Ecuador usando nodriver.

Una instancia por ejecución (tenant + período + tipo).
Orquesta: login → navegar → consultar → iterar páginas → descargar XMLs.
"""

import enum
import re
import os
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

import structlog
import nodriver as uc
from bs4 import BeautifulSoup

from captcha.factory import crear_resolver
from config.settings import Settings
from scrapers.exceptions import (
    SRICaptchaError,
    SRILoginError,
    SRIMaintenanceError,
    SRISessionExpiredError,
    SRITimeoutError,
)
from utils.delays import simular_actividad_humana
from utils.time import utc_now

log = structlog.get_logger()

# ── URLs del portal SRI ────────────────────────────────────────────────────
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
    "comprobantes": (
        "https://srienlinea.sri.gob.ec/comprobantes-electronicos-internet/"
        "pages/consultas/recibidos/comprobantesRecibidos.jsf"
    )
}

MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

class EstadoPortal(enum.Enum):
    NORMAL = "normal"
    MANTENIMIENTO = "mantenimiento"
    SESION_EXPIRADA = "sesion_expirada"
    ERROR_SISTEMA = "error_sistema"

@dataclass
class EjecucionResult:
    total_encontrados: int = 0
    total_nuevos: int = 0
    total_errores: int = 0
    xmls_descargados: list[dict] = field(default_factory=list)
    duracion_seg: float = 0.0
    pagina_final: int = 1

class SRINodriverEngine:
    """
    Motor alternativo indetectable usando `nodriver`.
    """

    def __init__(
        self,
        tenant_ruc: str,
        tenant_usuario: str,
        tenant_password: str,
        periodo_anio: int,
        periodo_mes: int,
        tipo_comprobante: str,
        settings: Settings,
        pagina_inicio: int = 1,
    ):
        self._ruc = tenant_ruc
        self._usuario = tenant_usuario
        self._password = tenant_password
        self._anio = periodo_anio
        self._mes = periodo_mes
        self._tipo = tipo_comprobante
        self._settings = settings
        self._pagina_inicio = pagina_inicio

        self._browser = None
        self._page = None
        _captcha_key = (
            settings.capsolver_api_key
            if settings.captcha_provider == "capsolver"
            else settings.twocaptcha_api_key
        )
        self._captcha_resolver = crear_resolver(settings.captcha_provider, _captcha_key)

        self._comprobantes_html: list[dict] = []

        self._log = log.bind(
            tenant_ruc=tenant_ruc,
            periodo=f"{periodo_anio}-{periodo_mes:02d}",
            tipo=tipo_comprobante,
            engine="nodriver"
        )

    async def ejecutar(self) -> EjecucionResult:
        inicio = utc_now()
        result = EjecucionResult()

        try:
            await self._inicializar_browser()
            await self._login()
            await self._navegar_comprobantes()
            total = await self._seleccionar_periodo_y_consultar()

            result.total_encontrados = total
            if total == 0:
                self._log.info("sin_comprobantes")
                return result

            if self._comprobantes_html:
                self._log.info("comprobantes_encontrados", count=len(self._comprobantes_html))
                # For Phase 1 we just get the claves and HTML downloading will be implemented in the next step
                
        except Exception as e:
            self._log.error("error_inesperado", error=str(e))
            raise
        finally:
            duracion = (utc_now() - inicio).total_seconds()
            result.duracion_seg = duracion
            self._log.info("ejecucion_finalizada", duracion_seg=duracion)
            await self._cerrar_browser()

        return result

    async def _inicializar_browser(self):
        self._log.info("arrancando_nodriver")
        
        # In Docker, headless must be True unless xvfb is set up. 
        # But nodriver works best fully headed, so we require xvfb running.
        headless = self._settings.playwright_headless
        
        # Buscar el binario de Chromium instalado por Playwright (en Docker)
        chrome_path = None
        import glob
        candidates = glob.glob(
            "/root/.cache/ms-playwright/*/chrome-linux/chrome"
        )
        if candidates:
            chrome_path = candidates[0]

        kwargs = dict(
            headless=headless,
            browser_args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--window-size=1366,768",
            ],
        )
        if chrome_path:
            kwargs["browser_executable_path"] = chrome_path
            self._log.info("usando_chromium_playwright", path=chrome_path)

        self._browser = await uc.start(**kwargs)
        self._page = self._browser.main_tab

    async def _cerrar_browser(self):
        if self._browser:
            self._browser.stop()

    async def _login(self):
        self._log.info("iniciando_login")
        await self._page.get(URLS["login"])
        await asyncio.sleep(8)
        
        body = await self._page.evaluate("document.body.innerText")
        
        if "Clave" in body or "usuario" in await self._page.evaluate("document.body.innerHTML"):
            self._log.info("formulario_login_detectado")
            
            try:
                el = await self._page.select("input#usuario")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(self._usuario)
            except Exception as e:
                self._log.warning("error_input_usuario", error=str(e))

            await asyncio.sleep(0.5)

            try:
                el = await self._page.select("input#password")
                await el.click()
                await asyncio.sleep(0.3)
                await el.send_keys(self._password)
            except Exception as e:
                self._log.warning("error_input_password", error=str(e))

            await asyncio.sleep(1)

            try:
                btn = await self._page.select("input#kc-login")
                await btn.click()
            except Exception as e:
                self._log.warning("error_submit_login", error=str(e))

            # Esperar a que se procese el login y el Keycloak redirija
            await asyncio.sleep(15)
            
        url_actual = self._page.url
        self._log.info("estado_post_login", url=url_actual)
        
        if "auth/realms" in url_actual:
            raise SRILoginError("No se pudo completar el login (SSO falló).")

    async def _navegar_comprobantes(self):
        self._log.info("navegando_a_comprobantes")
        await self._page.get(URLS["comprobantes"])
        await asyncio.sleep(15)
        
        url_actual = self._page.url
        # If redirected to profile, try forcing navigation again
        if "perfil" in url_actual or "sri-en-linea" in url_actual:
            self._log.info("forzando_navegacion_comprobantes_nuevamente")
            await self._page.get(URLS["comprobantes"])
            await asyncio.sleep(15)
            
        body = await self._page.evaluate("document.body.innerHTML")
        if "Comprobantes electrónicos" not in body and "frmPrincipal" not in body:
            raise SRITimeoutError("No se pudo cargar el módulo de comprobantes recibidos.")

    async def _seleccionar_periodo_y_consultar(self) -> int:
        self._log.info("seleccionando_filtros")
        
        # Seleccionar Año
        await self._page.evaluate(f'''
        () => {{
            const a = document.getElementById('frmPrincipal:anio');
            if (a) {{
                for (let o of a.options)
                    if (o.text==='{self._anio}') {{ a.value=o.value; break; }}
                a.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        }}
        ''')
        await asyncio.sleep(2)
        
        # Seleccionar Mes
        mes_str = MESES[self._mes - 1]
        await self._page.evaluate(f'''
        () => {{
            const m = document.getElementById('frmPrincipal:mes');
            if (m) {{
                for (let o of m.options)
                    if (o.text==='{mes_str}') {{ m.value=o.value; break; }}
                m.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
        }}
        ''')
        await asyncio.sleep(2)

        # Solicitar resolución de Captcha
        self._log.info("solicitando_token_captcha", provider=self._settings.captcha_provider)
        try:
            await simular_actividad_humana(self._page)
        except Exception as exc:
            self._log.warning("actividad_humana_pre_captcha_error", error=str(exc))

        # Extraer el sitekey de la web
        site_key = await self._page.evaluate('''
            () => {
                const iframe = document.querySelector('iframe[src*="recaptcha"]');
                if (iframe) {
                    const match = iframe.src.match(/[?&]k=([^&]+)/);
                    return match ? match[1] : null;
                }
                return '6LdukTQsAAAAAIcciM4GZq4ibeyplUhmWvlScuQE'; // Default SRI
            }
        ''')

        token = await self._captcha_resolver.resolver_token_recaptcha(
            site_key=site_key,
            page_url=self._page.url,
            enterprise=True,
            action="consulta_cel_recibidos",
            score=0.9,
        )
        if not token:
            raise SRICaptchaError("Proveedor CAPTCHA no devolvió token")
        self._log.info("captcha_token_obtenido", len=len(token))

        # Wait for the token and execute the search using the exact same logic as test_nodriver_captcha
        self._log.info("inyectando_token_ejecutando_consulta")
        query_result = await self._page.evaluate(f'''
        () => new Promise((resolve) => {{
            const t = setTimeout(() => {{
                const ta = document.querySelector('[name="g-recaptcha-response"]');
                resolve({{error:'timeout', tokenLen: ta ? ta.value.length : -1}});
            }}, 30000);
            
            // Inyectar Token ahora mismo para que este listo
            document.querySelectorAll('[name="g-recaptcha-response"]').forEach(el => {{
                el.value = '{token}';
            }});

            const orig = window.rcBuscar;
            window.rcBuscar = function() {{
                const ta = document.querySelector('[name="g-recaptcha-response"]');
                if (ta && ta.value === '') ta.value = '{token}'; // re-inyectar si lo borraron
                
                orig.apply(this, arguments);
                window.rcBuscar = orig;
                setTimeout(() => {{
                    clearTimeout(t);
                    const m = document.getElementById('formMessages:messages');
                    const p = document.getElementById('frmPrincipal:panelListaComprobantes');
                    resolve({{
                        messages: m ? m.innerText.trim() : '',
                        panelLen: p ? p.innerHTML.length : 0,
                        tokenLen: ta ? ta.value.length : -1,
                    }});
                }}, 10000);
            }};
            
            if (typeof executeRecaptcha === 'function') {{
                // Dispara el flujo del JSF que eventualmente llama a rcBuscar
                executeRecaptcha('consulta_cel_recibidos');
            }} else {{
                clearTimeout(t);
                window.rcBuscar = orig;
                resolve({{error:'no_executeRecaptcha'}});
            }}
        }})
        ''')
        
        self._log.info("resultado_rcbuscar", qr=query_result)
        
        if query_result.get("error"):
            raise SRITimeoutError(f"Timeout al buscar: {query_result['error']}")
            
        if "captcha" in query_result.get("messages", "").lower():
            if query_result.get("panelLen", 0) < 50:
                raise SRICaptchaError("SRI rechazó el captcha.")
                
        # Extraer comprobantes del panel
        panel_html = await self._page.evaluate('''
            () => {
                const p = document.getElementById('frmPrincipal:panelListaComprobantes');
                return p ? p.innerHTML : '';
            }
        ''')
        
        if len(panel_html) < 50:
            return 0
            
        # Very simple parse for now
        claves = list(set(re.findall(r'\b(\d{49})\b', panel_html)))
        self._comprobantes_html = [{"clave_acceso": c} for c in claves]
        return len(claves)
