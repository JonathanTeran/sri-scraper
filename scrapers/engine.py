"""
Motor de scraping para el portal SRI en Línea de Ecuador.

Una instancia por ejecución (tenant + período + tipo).
Orquesta: login → navegar → consultar → iterar páginas → descargar XMLs.
"""

import enum
import re
import os
import asyncio
import hashlib
import json
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeout,
)
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

try:
    from playwright_stealth import stealth_async
except Exception:  # pragma: no cover - fallback for partial envs/tests
    async def stealth_async(page) -> None:
        """Allow the engine to load even if playwright-stealth is unavailable."""
        return None

from captcha.factory import crear_resolvers
from config.settings import Settings
from scrapers.captcha_strategy import (
    build_captcha_attempt_plan,
    resolve_provider_page_url,
)
from scrapers.portal import (
    MESES,
    RECAPTCHA_ACTION,
    SEL,
    TIPOS_COMPROBANTE,
    URLS,
    load_js_asset,
)
from scrapers.exceptions import (
    SRICaptchaError,
    SRIDownloadError,
    SRILoginError,
    SRIMaintenanceError,
    SRISessionExpiredError,
    SRITimeoutError,
    XMLInvalidError,
)
from scrapers.session_manager import SessionManager
from utils.browser_env import find_browser_executable
from utils.crypto import decrypt
from utils.delays import (
    delay_humano,
    escribir_como_humano,
    simular_actividad_humana,
)
from utils.screenshots import tomar_screenshot
from utils.time import utc_now

log = structlog.get_logger()

DEFAULT_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
SRI_SOAP_URL = (
    "https://cel.sri.gob.ec/comprobantes-electronicos-ws/"
    "AutorizacionComprobantesOffline"
)
GPU_PROFILES = [
    {
        "webglVendor": "Google Inc. (Intel)",
        "webglRenderer": (
            "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
    },
    {
        "webglVendor": "Google Inc. (NVIDIA)",
        "webglRenderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
    },
    {
        "webglVendor": "Google Inc. (AMD)",
        "webglRenderer": (
            "ANGLE (AMD, AMD Radeon RX 580 2048SP Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
    },
    {
        "webglVendor": "Google Inc. (Intel)",
        "webglRenderer": (
            "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
    },
    {
        "webglVendor": "Google Inc. (NVIDIA)",
        "webglRenderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Laptop GPU "
            "Direct3D11 vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "webglVendor": "Google Inc. (AMD)",
        "webglRenderer": (
            "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 "
            "ps_5_0, D3D11)"
        ),
    },
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


PageProcessedCallback = Callable[[int, list[dict]], Awaitable[None]]
SkipDownloadCallback = Callable[[str], Awaitable[bool]]


class SRIScraperEngine:
    """
    Motor de scraping para el portal SRI en Línea.
    Una instancia por ejecución (tenant + período + tipo).
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
        on_page_processed: PageProcessedCallback | None = None,
        should_skip_download: SkipDownloadCallback | None = None,
        collect_results: bool = True,
    ):
        self._ruc = tenant_ruc
        self._usuario = tenant_usuario
        self._password = tenant_password
        self._anio = periodo_anio
        self._mes = periodo_mes
        self._tipo = tipo_comprobante
        self._settings = settings
        self._pagina_inicio = pagina_inicio
        self._on_page_processed = on_page_processed
        self._should_skip_download = should_skip_download
        self._collect_results = collect_results
        self._ultima_pagina_procesada = max(1, pagina_inicio)

        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._browser_executable_path: str | None = None
        self._persistent_context = False
        self._profile_dir = self._build_profile_dir()
        self._session_mgr = SessionManager(tenant_ruc)
        self._captcha_resolvers = crear_resolvers(
            settings.captcha_provider,
            settings.twocaptcha_api_key,
            settings.capsolver_api_key,
        )
        self._captcha_resolver = self._captcha_resolvers[0]["resolver"]

        self._route_handler = None  # Playwright route interception ref
        self._comprobantes_html: list[dict] = []

        self._log = log.bind(
            tenant_ruc=tenant_ruc,
            periodo=f"{periodo_anio}-{periodo_mes:02d}",
            tipo=tipo_comprobante,
        )

    async def ejecutar(self) -> EjecucionResult:
        """
        Punto de entrada principal. Orquesta todo el flujo:
        login → navegar → consultar → iterar páginas → descargar XMLs.
        """
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

            # Prefer direct XML downloads from each table row. This is the
            # authoritative multi-tenant flow from the portal itself.
            xmls = await self._procesar_todas_las_paginas()

            # Fallback to SOAP only if row downloads failed entirely and we
            # still have claves parsed from the result table.
            if (
                self._collect_results
                and
                not any(item.get("xml_bytes") for item in xmls)
                and self._comprobantes_html
            ):
                self._log.warning("fallback_descarga_soap")
                xmls = await self._descargar_xmls_por_clave(
                    self._comprobantes_html
                )

            result.xmls_descargados = xmls
            result.total_nuevos = len(
                [x for x in xmls if x.get("xml_bytes")]
            )
            result.total_errores = len(
                [x for x in xmls if x.get("error")]
            )
            result.pagina_final = self._ultima_pagina_procesada

        except SRILoginError:
            self._log.error("login_fallido")
            raise
        except SRIMaintenanceError:
            self._log.warning("sri_en_mantenimiento")
            raise
        except SRICaptchaError:
            self._log.warning("captcha_bloqueado")
            raise
        except SRISessionExpiredError:
            self._log.warning("sesion_expirada")
            raise
        except Exception as e:
            self._log.error("error_inesperado", error=str(e))
            if self._page and self._settings.screenshot_on_error:
                await tomar_screenshot(
                    self._page,
                    "error_inesperado",
                    self._ruc,
                    self._settings.screenshot_path,
                )
            raise
        finally:
            duracion = (utc_now() - inicio).total_seconds()
            result.duracion_seg = duracion
            self._log.info("ejecucion_finalizada", duracion_seg=duracion)
            await self._cerrar_browser()

        return result

    async def _inicializar_browser(self) -> None:
        """Launch browser using nodriver (undetected) + Playwright for API.

        nodriver patches Chromium to remove ALL automation indicators,
        making it invisible to reCAPTCHA Enterprise. We use nodriver
        to launch the browser, then connect Playwright via CDP for
        the convenience API (selectors, evaluate, etc.).
        """
        self._playwright = await async_playwright().start()
        self._nodriver_browser = None
        self._browser_executable_path = find_browser_executable(
            self._settings.browser_executable_path
        )
        os.makedirs(self._profile_dir, exist_ok=True)
        self._cleanup_profile_lock_files()

        chromium_path = self._browser_executable_path

        if (
            self._settings.browser_prefer_nodriver
            and chromium_path
            and self._can_use_nodriver()
        ):
            try:
                import nodriver as uc

                # nodriver patches the binary and launches undetected
                self._nodriver_browser = await uc.start(
                    user_data_dir=self._profile_dir,
                    browser_executable_path=chromium_path,
                    headless=self._settings.playwright_headless,
                    sandbox=False,
                    lang="es-EC",
                    browser_args=self._build_browser_launch_args(),
                )

                # Get the CDP endpoint from nodriver
                config = self._nodriver_browser.config
                cdp_port = config.port if hasattr(config, 'port') else None

                # Try to get the debugger URL
                ws_url = None
                if hasattr(self._nodriver_browser, 'connection'):
                    ws_url = getattr(
                        self._nodriver_browser.connection, 'url', None
                    )
                if not ws_url and hasattr(self._nodriver_browser, '_process_pid'):
                    # Find the debug port from the process
                    import aiohttp
                    for port in range(9222, 9322):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.get(
                                    f"http://127.0.0.1:{port}/json/version",
                                    timeout=aiohttp.ClientTimeout(total=2),
                                ) as resp:
                                    if resp.status == 200:
                                        data = await resp.json()
                                        ws_url = data.get(
                                            "webSocketDebuggerUrl"
                                        )
                                        cdp_port = port
                                        break
                        except Exception:
                            continue

                if ws_url or cdp_port:
                    # Connect Playwright to nodriver's browser
                    endpoint = (
                        f"http://127.0.0.1:{cdp_port}"
                        if cdp_port else ws_url
                    )
                    self._browser = await \
                        self._playwright.chromium.connect_over_cdp(
                            endpoint
                        )
                    contexts = self._browser.contexts
                    self._context = contexts[0] if contexts else \
                        await self._browser.new_context()
                    await self._configurar_contexto_browser()
                    pages = self._context.pages
                    self._page = pages[0] if pages else \
                        await self._context.new_page()

                    self._page.set_default_timeout(
                        self._settings.browser_timeout_ms
                    )
                    self._log.info("browser_nodriver_ok")
                    return
                else:
                    self._log.warning("nodriver_no_cdp_endpoint")
            except Exception as e:
                self._log.warning(
                    "nodriver_failed", error=str(e),
                )
                # Clean up nodriver if it started
                if self._nodriver_browser:
                    try:
                        self._nodriver_browser.stop()
                    except Exception:
                        pass
                    self._nodriver_browser = None
                self._cleanup_profile_lock_files()

        # Fallback: standard Playwright with stealth
        self._log.info("browser_fallback_playwright")
        launch_kwargs = self._build_playwright_launch_kwargs()
        if self._settings.browser_persistent_context:
            self._persistent_context = True
            try:
                self._context = (
                    await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=self._profile_dir,
                        **launch_kwargs,
                    )
                )
            except Exception as exc:
                if "ProcessSingleton" not in str(exc):
                    raise
                self._cleanup_profile_lock_files()
                self._context = (
                    await self._playwright.chromium.launch_persistent_context(
                        user_data_dir=self._profile_dir,
                        **launch_kwargs,
                    )
                )
            self._browser = self._context.browser
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=self._settings.playwright_headless,
                args=self._build_browser_launch_args(),
                executable_path=launch_kwargs.get("executable_path"),
                channel=launch_kwargs.get("channel"),
                proxy=launch_kwargs.get("proxy"),
            )
            self._context = await self._browser.new_context(
                user_agent=launch_kwargs["user_agent"],
                viewport=launch_kwargs["viewport"],
                locale=launch_kwargs["locale"],
                timezone_id=launch_kwargs["timezone_id"],
                accept_downloads=launch_kwargs["accept_downloads"],
                ignore_https_errors=launch_kwargs["ignore_https_errors"],
                extra_http_headers=launch_kwargs["extra_http_headers"],
            )
        await self._configurar_contexto_browser()
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        await stealth_async(self._page)
        self._page.set_default_timeout(self._settings.browser_timeout_ms)

    def _build_profile_dir(self) -> str:
        base = Path(self._settings.browser_profile_path).expanduser()
        return str(base / self._ruc)

    def _cleanup_profile_lock_files(self) -> None:
        profile_dir = Path(self._profile_dir)
        for name in (
            "SingletonLock",
            "SingletonCookie",
            "SingletonSocket",
            "DevToolsActivePort",
        ):
            target = profile_dir / name
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
            except Exception:
                pass

    def _can_use_nodriver(self) -> bool:
        if (
            self._settings.browser_proxy_server
            and (
                self._settings.browser_proxy_username
                or self._settings.browser_proxy_password
            )
        ):
            self._log.warning(
                "nodriver_proxy_auth_no_soportado",
                proxy_server=self._settings.browser_proxy_server,
            )
            return False
        return True

    def _normalize_proxy_server(self, server: str) -> str:
        server = server.strip()
        if not server:
            return ""
        if "://" not in server:
            return f"http://{server}"
        return server

    def _build_proxy_server_for_browser_args(self) -> str | None:
        server = self._normalize_proxy_server(
            self._settings.browser_proxy_server
        )
        if not server:
            return None
        username = self._settings.browser_proxy_username
        password = self._settings.browser_proxy_password
        if not username and not password:
            return server
        parts = urlsplit(server)
        netloc = parts.netloc
        if "@" not in netloc:
            credentials = username
            if password:
                credentials += f":{password}"
            netloc = f"{credentials}@{netloc}"
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )

    def _build_playwright_proxy_settings(self) -> dict | None:
        server = self._normalize_proxy_server(
            self._settings.browser_proxy_server
        )
        if not server:
            return None
        proxy = {"server": server}
        if self._settings.browser_proxy_username:
            proxy["username"] = self._settings.browser_proxy_username
        if self._settings.browser_proxy_password:
            proxy["password"] = self._settings.browser_proxy_password
        if self._settings.browser_proxy_bypass:
            proxy["bypass"] = self._settings.browser_proxy_bypass
        return proxy

    def _build_browser_launch_args(self) -> list[str]:
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1366,768",
            "--lang=es-EC",
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        proxy_server = self._build_proxy_server_for_browser_args()
        if proxy_server:
            args.append(f"--proxy-server={proxy_server}")
        if self._settings.browser_proxy_bypass:
            args.append(
                f"--proxy-bypass-list={self._settings.browser_proxy_bypass}"
            )
        return args

    def _build_playwright_launch_kwargs(self) -> dict:
        kwargs = {
            "headless": self._settings.playwright_headless,
            "args": self._build_browser_launch_args(),
            "user_agent": DEFAULT_BROWSER_USER_AGENT,
            "viewport": {"width": 1366, "height": 768},
            "locale": "es-EC",
            "timezone_id": "America/Guayaquil",
            "accept_downloads": True,
            "ignore_https_errors": True,
            "extra_http_headers": {
                "Accept-Language": "es-EC,es;q=0.9,en-US;q=0.8,en;q=0.7",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            },
        }
        proxy = self._build_playwright_proxy_settings()
        if proxy:
            kwargs["proxy"] = proxy
        if self._browser_executable_path:
            kwargs["executable_path"] = self._browser_executable_path
        elif self._settings.browser_channel:
            kwargs["channel"] = self._settings.browser_channel
        return kwargs

    def _build_fingerprint_profile(self) -> dict:
        digest = hashlib.sha256(self._ruc.encode("utf-8")).digest()
        gpu = GPU_PROFILES[digest[2] % len(GPU_PROFILES)]
        return {
            "hardwareConcurrency": [4, 8, 12, 16][digest[0] % 4],
            "deviceMemory": [4, 8, 16][digest[1] % 3],
            "maxTouchPoints": [0, 0, 1, 2][digest[3] % 4],
            "webglVendor": gpu["webglVendor"],
            "webglRenderer": gpu["webglRenderer"],
            "canvasNoise": {
                "r": (digest[4] % 5) - 2,
                "g": (digest[5] % 5) - 2,
                "b": (digest[6] % 5) - 2,
                "a": digest[7] % 2,
            },
        }

    def _build_fingerprint_init_script(self) -> str:
        profile = json.dumps(
            self._build_fingerprint_profile(),
            separators=(",", ":"),
        )
        return f"""
        (() => {{
            const profile = {profile};
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
            }});
            Object.defineProperty(navigator, 'languages', {{
                get: () => ['es-EC', 'es', 'en-US'],
            }});
            Object.defineProperty(navigator, 'language', {{
                get: () => 'es-EC',
            }});
            Object.defineProperty(navigator, 'platform', {{
                get: () => 'Win32',
            }});
            Object.defineProperty(navigator, 'hardwareConcurrency', {{
                get: () => profile.hardwareConcurrency,
            }});
            Object.defineProperty(navigator, 'deviceMemory', {{
                get: () => profile.deviceMemory,
            }});
            Object.defineProperty(navigator, 'maxTouchPoints', {{
                get: () => profile.maxTouchPoints,
            }});
            window.chrome = window.chrome || {{ runtime: {{}} }};
            if (navigator.permissions && navigator.permissions.query) {{
                const originalQuery = navigator.permissions.query.bind(navigator.permissions);
                navigator.permissions.query = (parameters) => (
                    parameters && parameters.name === 'notifications'
                        ? Promise.resolve({{ state: Notification.permission }})
                        : originalQuery(parameters)
                );
            }}

            const patchWebgl = (prototype) => {{
                if (!prototype || !prototype.getParameter) {{
                    return;
                }}
                const originalGetParameter = prototype.getParameter;
                prototype.getParameter = function(parameter) {{
                    if (parameter === 37445) return profile.webglVendor;
                    if (parameter === 37446) return profile.webglRenderer;
                    return originalGetParameter.call(this, parameter);
                }};
            }};

            patchWebgl(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
            patchWebgl(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);

            const mutateImageData = (imageData) => {{
                if (!imageData || !imageData.data || imageData.data.length < 4) {{
                    return imageData;
                }}
                for (let index = 0; index < Math.min(imageData.data.length, 24); index += 4) {{
                    imageData.data[index] = Math.max(0, Math.min(255, imageData.data[index] + profile.canvasNoise.r));
                    imageData.data[index + 1] = Math.max(0, Math.min(255, imageData.data[index + 1] + profile.canvasNoise.g));
                    imageData.data[index + 2] = Math.max(0, Math.min(255, imageData.data[index + 2] + profile.canvasNoise.b));
                    imageData.data[index + 3] = Math.max(0, Math.min(255, imageData.data[index + 3] + profile.canvasNoise.a));
                }}
                return imageData;
            }};

            if (window.CanvasRenderingContext2D && CanvasRenderingContext2D.prototype.getImageData) {{
                const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
                    return mutateImageData(originalGetImageData.apply(this, args));
                }};
            }}
        }})();
        """

    async def _resolve_browser_user_agent(self) -> str:
        page = self._page
        if page is not None:
            try:
                user_agent = await page.evaluate("() => navigator.userAgent")
                if isinstance(user_agent, str) and user_agent.strip():
                    return user_agent
            except Exception:
                pass
        return DEFAULT_BROWSER_USER_AGENT

    async def _get_browser_cookies(self) -> list[dict]:
        if self._context is None:
            return []
        try:
            return await self._context.cookies()
        except Exception as exc:
            self._log.warning("soap_cookies_no_disponibles", error=str(exc))
            return []

    def _build_soap_request_headers(self, user_agent: str) -> dict[str, str]:
        referer = URLS["portal"]
        if self._page is not None:
            try:
                referer = self._page.url or referer
            except Exception:
                referer = URLS["portal"]
        return {
            "Accept": "text/xml,application/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-EC,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "Connection": "keep-alive",
            "Content-Type": "text/xml; charset=utf-8",
            "Origin": "https://srienlinea.sri.gob.ec",
            "Referer": referer,
            "SOAPAction": "",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": user_agent,
        }

    @asynccontextmanager
    async def _crear_cliente_soap(self):
        from curl_cffi.requests import AsyncSession

        client_kwargs = {
            "headers": self._build_soap_request_headers(
                await self._resolve_browser_user_agent()
            ),
            "impersonate": "chrome131",
            "timeout": 30,
        }
        proxy_server = self._build_proxy_server_for_browser_args()
        if proxy_server:
            client_kwargs["proxy"] = proxy_server

        async with AsyncSession(**client_kwargs) as client:
            for cookie in await self._get_browser_cookies():
                name = cookie.get("name")
                value = cookie.get("value")
                if not name or value is None:
                    continue
                cookie_kwargs = {}
                if cookie.get("domain"):
                    cookie_kwargs["domain"] = cookie["domain"]
                if cookie.get("path"):
                    cookie_kwargs["path"] = cookie["path"]
                client.cookies.set(name, value, **cookie_kwargs)
            yield client

    async def _configurar_contexto_browser(self) -> None:
        context = self._context
        assert context is not None

        await context.set_extra_http_headers({
            "Accept-Language": "es-EC,es;q=0.9,en-US;q=0.8,en;q=0.7",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        })
        await context.add_init_script(self._build_fingerprint_init_script())

    async def _cerrar_browser(self) -> None:
        """Cierra el navegador y libera recursos."""
        if self._context:
            await self._session_mgr.guardar_cookies(self._context)
            if self._persistent_context:
                try:
                    await self._context.close()
                except Exception:
                    pass
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            await self._playwright.stop()
        # Stop nodriver browser if launched
        if hasattr(self, '_nodriver_browser') and self._nodriver_browser:
            try:
                self._nodriver_browser.stop()
            except Exception:
                pass

    async def _login(self) -> None:
        """
        Login con Keycloak SSO.
        Intenta cargar cookies guardadas primero.
        Si no, hace login completo con credenciales.
        """
        page = self._page
        assert page is not None

        # Intentar con cookies existentes
        cookies_ok = await self._session_mgr.cargar_cookies(self._context)
        if cookies_ok:
            await page.goto(URLS["portal"], timeout=30000)
            await page.wait_for_load_state(
                "domcontentloaded", timeout=15000
            )
            await delay_humano(1000, 2000)

            # Verificar si la sesión sigue activa
            estado = await self._detectar_estado_anomalo()
            if estado == EstadoPortal.NORMAL:
                self._log.info("login_con_cookies")
                return
            else:
                self._log.info("cookies_invalidas", estado=estado.value)
                self._session_mgr.limpiar_sesion()

        # Login completo
        max_intentos = 3
        for intento in range(1, max_intentos + 1):
            try:
                self._log.info("login_intento", intento=intento)
                await page.goto(URLS["login"],
                                wait_until="networkidle",
                                timeout=30000)

                # Wait for page to settle after redirects
                await delay_humano(2000, 3000)

                # Detectar si SRI está en mantenimiento
                try:
                    body_lower = (
                        await page.inner_text("body", timeout=10000)
                    ).lower()
                    if "ha ocurrido un error" in body_lower:
                        raise SRIMaintenanceError(
                            "SRI muestra página de error"
                        )
                except SRIMaintenanceError:
                    raise
                except Exception:
                    pass  # Navigation may still be in progress

                await page.wait_for_selector(
                    "input#usuario", timeout=15000
                )
                await delay_humano()

                # Campo RUC/CI (input#usuario) — NO llenar ciAdicional
                await escribir_como_humano(
                    page, "input#usuario", self._usuario
                )
                await delay_humano(500, 1000)

                # Campo contraseña (input#password)
                await escribir_como_humano(
                    page, "input#password", self._password
                )
                await delay_humano(500, 1000)

                # Submit (input#kc-login)
                await page.click("input#kc-login")

                # Detectar CAPTCHA inmediatamente
                await asyncio.sleep(2)
                captcha_detected = await self._detectar_captcha()
                if captcha_detected:
                    self._log.info("captcha_en_login_resuelto")

                # Esperar redirect exitoso
                await page.wait_for_url(
                    "**/sri-en-linea/**", timeout=30000
                )

                # Guardar cookies
                await self._session_mgr.guardar_cookies(self._context)
                self._log.info("login_exitoso", url=page.url)
                return

            except PlaywrightTimeout:
                self._log.warning(
                    "login_timeout", intento=intento
                )
                if self._settings.screenshot_on_error:
                    await tomar_screenshot(
                        page,
                        f"login_timeout_{intento}",
                        self._ruc,
                        self._settings.screenshot_path,
                    )
                # Check if SRI is down after timeout
                try:
                    body = (await page.inner_text(
                        "body", timeout=10000
                    )).lower()
                except Exception:
                    body = ""
                if "ha ocurrido un error" in body:
                    raise SRIMaintenanceError(
                        "SRI en mantenimiento"
                    )
            except (SRIMaintenanceError, SRICaptchaError):
                raise
            except Exception as e:
                self._log.warning(
                    "login_error",
                    intento=intento,
                    error=str(e),
                )

        raise SRILoginError(
            f"Login fallido después de {max_intentos} intentos "
            f"para RUC {self._ruc}"
        )

    async def _navegar_comprobantes(self) -> None:
        """Navega al módulo de comprobantes electrónicos recibidos."""
        page = self._page
        assert page is not None

        await page.goto(URLS["portal"], timeout=30000)
        await delay_humano(3000, 5000)

        # Esperar que el formulario JSF cargue (selector de año)
        try:
            await page.wait_for_selector(
                SEL["anio"], timeout=20000,
            )
        except PlaywrightTimeout:
            estado = await self._detectar_estado_anomalo()
            if estado == EstadoPortal.MANTENIMIENTO:
                raise SRIMaintenanceError("SRI en mantenimiento")
            if estado == EstadoPortal.SESION_EXPIRADA:
                raise SRISessionExpiredError("Sesión expirada")
            raise SRITimeoutError(
                "No se pudo cargar el módulo de comprobantes"
            )

        self._log.info(
            "modulo_comprobantes_cargado", url=page.url[:120]
        )

    async def _seleccionar_periodo_y_consultar(self) -> int:
        """
        Selecciona año, mes y tipo. Ejecuta consulta.
        Retorna total de registros encontrados.
        """
        page = self._page
        assert page is not None

        async def _snapshot_filtros() -> dict:
            return await page.evaluate(
                """
                () => {
                    const readSelect = (id) => {
                        const el = document.getElementById(id);
                        if (!el) return null;
                        const idx = el.selectedIndex;
                        const opt = idx >= 0 ? el.options[idx] : null;
                        return {
                            id,
                            value: el.value || '',
                            label: opt ? (opt.textContent || '').trim() : '',
                            options: el.options ? el.options.length : 0,
                        };
                    };
                    return {
                        anio: readSelect('frmPrincipal:ano'),
                        mes: readSelect('frmPrincipal:mes'),
                        dia: readSelect('frmPrincipal:dia'),
                        tipo: readSelect('frmPrincipal:cmbTipoComprobante'),
                    };
                }
                """
            )

        async def _configurar_criterio_ruc() -> dict:
            return await page.evaluate(
                """
                ({ ruc }) => {
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && el.offsetParent !== null;
                    };
                    const normalize = (value) => (value || '').trim().toLowerCase();
                    const form = document.getElementById('frmPrincipal')
                        || document.querySelector('form[id="frmPrincipal"]')
                        || document.querySelector('form');
                    const result = {
                        radioMatched: null,
                        textInput: null,
                        radios: [],
                        textInputs: [],
                    };
                    if (!form) {
                        return result;
                    }

                    const radios = Array.from(
                        form.querySelectorAll('input[type="radio"]')
                    );
                    for (const radio of radios) {
                        const label = document.querySelector(`label[for="${radio.id}"]`);
                        const labelText = normalize(
                            (label && label.textContent)
                            || radio.closest('label')?.textContent
                            || radio.parentElement?.textContent
                            || ''
                        );
                        result.radios.push({
                            id: radio.id || '',
                            name: radio.name || '',
                            checked: !!radio.checked,
                            label: labelText,
                        });
                        if (
                            !result.radioMatched
                            && (
                                labelText.includes('ruc')
                                || labelText.includes('cédula')
                                || labelText.includes('cedula')
                                || labelText.includes('pasaporte')
                            )
                        ) {
                            radio.checked = true;
                            radio.dispatchEvent(new Event('input', { bubbles: true }));
                            radio.dispatchEvent(new Event('change', { bubbles: true }));
                            radio.click();
                            result.radioMatched = radio.id || radio.name || 'matched';
                        }
                    }

                    const textInputs = Array.from(
                        form.querySelectorAll('input[type="text"]')
                    ).filter((el) => !el.disabled && !el.readOnly && isVisible(el));
                    for (const input of textInputs) {
                        const label = document.querySelector(`label[for="${input.id}"]`);
                        const meta = normalize(
                            `${label?.textContent || ''} ${input.placeholder || ''} ${input.name || ''} ${input.id || ''}`
                        );
                        result.textInputs.push({
                            id: input.id || '',
                            name: input.name || '',
                            value: input.value || '',
                            meta,
                        });
                    }
                    const target = textInputs.find((input) => {
                        const label = document.querySelector(`label[for="${input.id}"]`);
                        const meta = normalize(
                            `${label?.textContent || ''} ${input.placeholder || ''} ${input.name || ''} ${input.id || ''}`
                        );
                        return (
                            meta.includes('ruc')
                            || meta.includes('cedula')
                            || meta.includes('cédula')
                            || meta.includes('pasaporte')
                            || meta.includes('ident')
                        );
                    }) || (textInputs.length === 1 ? textInputs[0] : null);

                    if (target) {
                        target.focus();
                        target.value = ruc;
                        target.dispatchEvent(new Event('input', { bubbles: true }));
                        target.dispatchEvent(new Event('change', { bubbles: true }));
                        target.dispatchEvent(new Event('blur', { bubbles: true }));
                        result.textInput = {
                            id: target.id || '',
                            name: target.name || '',
                            value: target.value || '',
                        };
                    }

                    return result;
                }
                """,
                {"ruc": self._ruc},
            )

        async def _wait_ajax_idle(timeout_ms: int = 8000) -> None:
            try:
                await page.wait_for_function(
                    """
                    () => {
                        const q = window.PrimeFaces
                            && PrimeFaces.ajax
                            && PrimeFaces.ajax.Queue;
                        return !q || typeof q.isEmpty !== 'function' || q.isEmpty();
                    }
                    """,
                    timeout=timeout_ms,
                )
            except Exception:
                return None

        async def _set_select(
            selector: str,
            *,
            label: str | None = None,
            value: str | None = None,
        ) -> dict:
            result = await page.evaluate(
                """
                ({ selector, label, value }) => {
                    const el = document.querySelector(selector);
                    if (!el) {
                        return {
                            found: false,
                            matched: false,
                            value: '',
                            label: '',
                            options: 0,
                        };
                    }
                    let matched = false;
                    const desiredLabel = label == null ? null : String(label).trim();
                    const desiredValue = value == null ? null : String(value);
                    for (const opt of Array.from(el.options || [])) {
                        const optLabel = (opt.textContent || '').trim();
                        const optValue = String(opt.value || '');
                        if (
                            (desiredLabel !== null && optLabel === desiredLabel)
                            || (desiredValue !== null && optValue === desiredValue)
                        ) {
                            el.value = opt.value;
                            matched = true;
                            break;
                        }
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    const idx = el.selectedIndex;
                    const opt = idx >= 0 ? el.options[idx] : null;
                    return {
                        found: true,
                        matched,
                        value: el.value || '',
                        label: opt ? (opt.textContent || '').trim() : '',
                        options: el.options ? el.options.length : 0,
                    };
                }
                """,
                {
                    "selector": selector,
                    "label": label,
                    "value": value,
                },
            )
            await _wait_ajax_idle()
            return result

        criterio = await _configurar_criterio_ruc()
        self._log.info("criterio_busqueda_configurado", criterio=criterio)

        # Seleccionar año
        await _set_select(SEL["anio"], label=str(self._anio))
        await delay_humano(1000, 2000)

        # Esperar actualización AJAX del selector de mes
        await _wait_ajax_idle()
        await asyncio.sleep(1)

        # Seleccionar mes
        mes_label = MESES[self._mes - 1]
        await _set_select(SEL["mes"], label=mes_label)
        await delay_humano(1000, 2000)

        # Día → "Todos" (valor "0")
        await _set_select(SEL["dia"], value="0")
        await delay_humano(500, 1000)

        # Seleccionar tipo de comprobante
        await _set_select(SEL["tipo"], label=self._tipo)
        await delay_humano(500, 1000)

        self._log.info(
            "filtros_seleccionados",
            filtros=await _snapshot_filtros(),
            esperado={
                "anio": self._anio,
                "mes": MESES[self._mes - 1],
                "tipo": self._tipo,
            },
        )

        # Diagnose reCAPTCHA setup before attempting
        try:
            diag = await page.evaluate(
                load_js_asset("diagnose_recaptcha.js")
            )
            self._log.warning(
                "recaptcha_diagnostico",
                site_key=diag.get("siteKey"),
                site_key_source=diag.get("siteKeySource"),
                extracted_action=diag.get("extractedAction"),
                executeRecaptcha_exists=diag.get("executeRecaptchaExists"),
                executeRecaptcha_src=diag.get("executeRecaptchaSource", "")[:300],
                rcBuscar_exists=diag.get("rcBuscarExists"),
                rcBuscar_src=diag.get("rcBuscarSource", "")[:300],
                onSubmit_exists=diag.get("onSubmitExists"),
                onSubmit_src=diag.get("onSubmitSource", "")[:300],
                button_id=diag.get("buttonId"),
                button_onclick=diag.get("buttonOnclick"),
                iframe_count=diag.get("iframeCount"),
                iframe_srcs=diag.get("iframeSrcs"),
                action_patterns=diag.get("actionPatternsInScripts"),
                widget_ids=diag.get("widgetIds"),
                grecaptcha_cfg_keys=diag.get("grecaptchaCfgKeys"),
            )
        except Exception as exc:
            self._log.warning("recaptcha_diagnostico_error", error=str(exc))

        max_intentos = 12
        attempt_plan = self._build_captcha_attempt_plan(max_intentos)
        captcha_exitoso = False
        sin_datos_detectado = False
        html_content = ""
        msgs = ""

        # Track what the native reCAPTCHA generates
        intercepted_data = {}

        async def _intercept_post(route):
            """Intercept PrimeFaces POST to log/modify reCAPTCHA token."""
            request = route.request
            if request.method != "POST":
                await route.continue_()
                return

            post_body = request.post_data or ""
            intercepted_data["request_url"] = request.url
            is_consulta_post = (
                "frmPrincipal" in post_body
                or "javax.faces.ViewState" in post_body
                or "javax.faces.partial.ajax=true" in post_body
            )
            if is_consulta_post:
                # Extract the current g-recaptcha-response
                import urllib.parse
                params = urllib.parse.parse_qs(post_body)
                token = params.get("g-recaptcha-response", [""])[0]
                intercepted_data["native_token_len"] = len(token)
                intercepted_data["has_token"] = bool(token)
                intercepted_data["primefaces_partial"] = (
                    params.get("javax.faces.partial.ajax", [""])[0] == "true"
                )
                intercepted_data["posted_filters"] = {
                    "anio": params.get("frmPrincipal:ano", [""])[0],
                    "mes": params.get("frmPrincipal:mes", [""])[0],
                    "dia": params.get("frmPrincipal:dia", [""])[0],
                    "tipo": params.get(
                        "frmPrincipal:cmbTipoComprobante", [""]
                    )[0],
                    "source": params.get("javax.faces.source", [""])[0],
                    "event": params.get(
                        "javax.faces.behavior.event", [""]
                    )[0],
                }
                intercepted_data["posted_keys"] = sorted(
                    key for key in params.keys()
                    if key.startswith("frmPrincipal")
                    or key.startswith("javax.faces")
                )[:40]
                intercepted_data["ident_params"] = {
                    key: values[0]
                    for key, values in params.items()
                    if values
                    and (
                        self._ruc in values[0]
                        or "ident" in key.lower()
                        or "ruc" in key.lower()
                        or "cedula" in key.lower()
                        or "pasaporte" in key.lower()
                    )
                }

                self._log.info(
                    "post_interceptado",
                    url=request.url,
                    has_token=bool(token),
                    token_len=len(token),
                    token_prefix=token[:50] if token else "",
                    filtros=intercepted_data["posted_filters"],
                    ident_params=intercepted_data["ident_params"],
                    keys=intercepted_data["posted_keys"],
                )

                # If we have a replacement token, swap it
                replacement = intercepted_data.get("replacement_token")
                if replacement and token != replacement:
                    encoded_replacement = urllib.parse.quote_plus(
                        replacement
                    )
                    if "g-recaptcha-response=" in post_body:
                        post_body = re.sub(
                            r'g-recaptcha-response=[^&]*',
                            f'g-recaptcha-response={encoded_replacement}',
                            post_body,
                        )
                    else:
                        post_body += (
                            f"&g-recaptcha-response={encoded_replacement}"
                        )
                    intercepted_data["post_token_replaced"] = True
                    intercepted_data["native_token_len"] = len(replacement)
                    intercepted_data["has_token"] = True
                    self._log.info("token_reemplazado_en_post")

                await route.continue_(post_data=post_body)
            else:
                await route.continue_()

        # Intercept all POSTs during the consulta because the JSF endpoint can
        # vary while still carrying the same frmPrincipal/ViewState payload.
        await self._limpiar_route_handler()
        self._route_handler = _intercept_post
        await page.route("**/*", self._route_handler)

        for intento, attempt in enumerate(attempt_plan, start=1):
            self._log.info(
                "consulta_intento",
                intento=intento,
                modo=attempt["mode"],
                provider=attempt.get("provider"),
                variant=attempt.get("variant"),
            )
            intercepted_data.pop("native_token_len", None)
            intercepted_data.pop("has_token", None)
            intercepted_data.pop("replacement_token", None)
            intercepted_data.pop("post_token_replaced", None)
            intercepted_data.pop("primefaces_partial", None)
            intercepted_data.pop("request_url", None)

            try:
                await page.screenshot(
                    path=f"/app/screenshots/pre_consulta_{intento}.png"
                )
            except Exception:
                pass

            if attempt["mode"] == "native":
                result = await self._ejecutar_recaptcha_nativo()
            elif attempt["mode"] == "assisted":
                result = await self._ejecutar_consulta_asistida()
            else:
                try:
                    await simular_actividad_humana(page)
                except Exception as exc:
                    self._log.warning(
                        "actividad_humana_pre_provider_error",
                        error=str(exc),
                    )
                site_key = await self._obtener_site_key_consulta()
                token = await self._resolver_token_con_proveedor(
                    attempt["resolver"],
                    attempt["provider"],
                    site_key,
                    attempt,
                )
                if not token:
                    self._log.warning(
                        "captcha_provider_sin_token",
                        intento=intento,
                        provider=attempt["provider"],
                        variant=attempt.get("variant"),
                    )
                    await self._resetear_recaptcha()
                    continue

                intercepted_data["replacement_token"] = token
                result = await self._ejecutar_consulta_controlada(
                    token=token,
                    source=attempt["provider"],
                )

            # Check what happened
            try:
                await page.screenshot(
                    path=f"/app/screenshots/post_consulta_{intento}.png"
                )
            except Exception:
                pass

            self._log.info(
                "consulta_result",
                intento=intento,
                result=result,
                intercepted=dict(intercepted_data),
            )

            msgs = result.get("messages", "").lower()
            panel_len = result.get("panelLen", 0)
            table_container_html_len = result.get(
                "tableContainerHtmlLen", 0
            )
            table_rows = result.get("tableRows", 0)
            table_html_len = result.get("tableHtmlLen", 0)
            error = result.get("error")
            empty_partial = bool(result.get("emptyPartialResponse"))
            has_token = bool(intercepted_data.get("has_token"))

            if (
                panel_len > 50
                or table_container_html_len > 100
                or table_rows > 0
                or table_html_len > 100
            ):
                html_content = (
                    result.get("panelHtml")
                    or result.get("documentsPanelHtml")
                    or result.get("tableContainerHtml", "")
                    or result.get("tableHtml", "")
                )
                captcha_exitoso = True
                self._log.info(
                    "consulta_exitosa",
                    intento=intento,
                    table_container_id=result.get("tableContainerId"),
                    table_rows=table_rows,
                    table_id=result.get("tableId"),
                )
                break
            elif "captcha" in msgs:
                self._log.warning(
                    "captcha_rechazado", intento=intento,
                    native_token=intercepted_data.get("has_token"),
                    native_token_len=intercepted_data.get(
                        "native_token_len"
                    ),
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                )
                if attempt["mode"] == "provider":
                    await attempt["resolver"].reportar_token_malo()
                await self._resetear_recaptcha()
                await delay_humano(5000, 10000)
                continue
            elif empty_partial:
                self._log.warning(
                    "respuesta_parcial_vacia",
                    intento=intento,
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                    has_token=has_token,
                    request_url=intercepted_data.get("request_url"),
                    posted_filters=intercepted_data.get("posted_filters"),
                )
                await self._resetear_recaptcha()
                await delay_humano(3000, 5000)
                continue
            elif error:
                self._log.warning(
                    "consulta_controlada_error",
                    intento=intento,
                    provider=attempt.get("provider"),
                    variant=attempt.get("variant"),
                    error=error,
                )
                await self._resetear_recaptcha()
                await delay_humano(3000, 5000)
                continue
            else:
                sin_datos_detectado = True
                self._log.info(
                    "sin_datos",
                    intento=intento,
                    empty_partial=empty_partial,
                    request_url=intercepted_data.get("request_url"),
                    posted_filters=intercepted_data.get("posted_filters"),
                )
                break

        await self._limpiar_route_handler()

        if sin_datos_detectado:
            return 0

        if not captcha_exitoso:
            if "no se encontraron" in msgs:
                return 0
            body_text = await page.inner_text("body")
            if "no se encontraron" in body_text.lower():
                return 0
            raise SRICaptchaError(
                "No se pudo consultar comprobantes"
            )

        # Parse comprobantes from HTML
        comprobantes = self._extraer_comprobantes_de_html(html_content)
        self._log.info("comprobantes_extraidos", total=len(comprobantes))

        if not comprobantes:
            if "no se encontraron" in html_content.lower():
                return 0
            self._log.warning(
                "sin_comprobantes_en_html",
                html_len=len(html_content),
            )
            return 0

        self._comprobantes_html = comprobantes
        return len(comprobantes)

    def _build_captcha_attempt_plan(self, max_intentos: int) -> list[dict]:
        assist_mode = self._settings.captcha_assisted_mode.lower().strip()
        return build_captcha_attempt_plan(
            assist_mode=assist_mode,
            assisted_available=self._captcha_assisted_available(),
            captcha_resolvers=self._captcha_resolvers,
            max_attempts=max_intentos,
        )

    def _captcha_assisted_available(self) -> bool:
        return (
            self._settings.captcha_assisted_enabled()
            and not self._settings.playwright_headless
        )

    async def _extraer_site_key_recaptcha(self) -> str | None:
        page = self._page
        assert page is not None

        return await page.evaluate(load_js_asset("extract_site_key.js"))

    async def _obtener_site_key_consulta(self) -> str:
        site_key = await self._extraer_site_key_recaptcha()
        if site_key:
            return site_key
        fallback = self._settings.recaptcha_sitekey_fallback.strip()
        if fallback:
            self._log.warning("captcha_sitekey_fallback_en_uso")
            return fallback
        raise SRICaptchaError(
            "No se pudo detectar la sitekey reCAPTCHA del portal"
        )

    async def _resolver_token_con_proveedor(
        self,
        resolver,
        provider: str,
        site_key: str,
        attempt: dict,
    ) -> str | None:
        page = self._page
        assert page is not None

        page_url = resolve_provider_page_url(
            page.url,
            attempt.get("page_url_mode", "canonical"),
        )
        self._log.info(
            "captcha_provider_intento",
            provider=provider,
            site_key=site_key,
            variant=attempt.get("variant"),
            enterprise=attempt.get("enterprise"),
            invisible=attempt.get("invisible"),
            action=attempt.get("action"),
            score=attempt.get("score"),
            page_url_mode=attempt.get("page_url_mode"),
            page_url=page_url,
        )
        token = await resolver.resolver_token_recaptcha(
            site_key=site_key,
            page_url=page_url,
            enterprise=attempt.get("enterprise", False),
            action=attempt.get("action"),
            score=attempt.get("score"),
            invisible=attempt.get("invisible", False),
        )
        if token:
            self._log.info(
                "captcha_provider_token_obtenido",
                provider=provider,
                token_len=len(token),
            )
        return token

    async def _ejecutar_consulta_controlada(
        self,
        *,
        token: str | None,
        source: str,
    ) -> dict:
        """Genera/inserta token y dispara rcBuscar sin depender del onclick."""
        page = self._page
        assert page is not None

        result = await page.evaluate(
            load_js_asset("controlled_query.js"),
            {
                "token": token,
                "source": source,
                "action": RECAPTCHA_ACTION,
                "timeoutMs": max(self._settings.browser_timeout_ms, 45_000),
                "pollIntervalMs": 500,
            },
        )
        return result

    async def _resetear_recaptcha(self) -> None:
        page = self._page
        assert page is not None

        await page.evaluate(load_js_asset("reset_recaptcha.js"))

    async def _ejecutar_consulta_asistida(self) -> dict:
        """Permite que un operador complete el captcha manualmente."""
        page = self._page
        assert page is not None

        timeout_sec = self._settings.captcha_assisted_timeout_sec
        self._log.warning(
            "captcha_modo_asistido",
            timeout_sec=timeout_sec,
            url=page.url,
        )
        await page.evaluate(
            """
            ({ timeoutSec }) => {
                const existing = document.getElementById('codex-captcha-assist');
                if (existing) existing.remove();
                const banner = document.createElement('div');
                banner.id = 'codex-captcha-assist';
                banner.innerText =
                    'Modo asistido activo. Complete el captcha y use el boton Consultar del portal. '
                    + 'La espera termina en ' + timeoutSec + ' segundos.';
                Object.assign(banner.style, {
                    position: 'fixed',
                    top: '12px',
                    right: '12px',
                    zIndex: '99999',
                    background: '#111827',
                    color: '#ffffff',
                    padding: '12px 14px',
                    borderRadius: '10px',
                    fontSize: '14px',
                    boxShadow: '0 10px 25px rgba(0,0,0,0.25)',
                    maxWidth: '360px',
                });
                document.body.appendChild(banner);
            }
            """,
            {"timeoutSec": timeout_sec},
        )

        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            result = await page.evaluate(
                """
                () => {
                    const msgs = document.getElementById('formMessages:messages');
                    const panel = document.getElementById(
                        'frmPrincipal:panelListaComprobantes'
                    );
                    const documentsPanel = document.getElementById(
                        'frmPrincipal:pnldocumentosrecibidos'
                    );
                    const tableContainer =
                        document.getElementById('frmPrincipal:tablaCompRecibidos')
                        || document.getElementById('frmPrincipal:pnldocumentosrecibidos');
                    const tableBody =
                        document.getElementById('frmPrincipal:tablaCompRecibidos_data')
                        || document.querySelector('[id="frmPrincipal:tablaCompRecibidos_data"]');
                    const table =
                        (tableBody && tableBody.closest('table'))
                        || (tableContainer && tableContainer.querySelector('table'))
                        || null;
                    const tas = document.querySelectorAll(
                        '[name="g-recaptcha-response"]'
                    );
                    const rowCount = tableBody
                        ? Array.from(tableBody.querySelectorAll('tr')).filter(
                            (row) => (row.textContent || '').trim().length > 0
                        ).length
                        : (table
                            ? Array.from(table.querySelectorAll('tbody tr')).filter(
                                (row) => (row.textContent || '').trim().length > 0
                            ).length
                            : 0);
                    return {
                        source: 'assisted',
                        messages: msgs ? msgs.innerText.trim() : '',
                        panelLen: panel ? panel.innerHTML.length : 0,
                        panelHtml: panel ? panel.innerHTML : '',
                        documentsPanelLen: documentsPanel ? documentsPanel.innerHTML.length : 0,
                        documentsPanelHtml: documentsPanel ? documentsPanel.innerHTML : '',
                        tableContainerId: tableContainer ? (tableContainer.id || '') : '',
                        tableContainerHtmlLen: tableContainer ? tableContainer.innerHTML.length : 0,
                        tableContainerHtml: tableContainer ? tableContainer.innerHTML : '',
                        tableId: table ? (table.id || '') : '',
                        tableBodyId: tableBody ? (tableBody.id || '') : '',
                        tableRows: rowCount,
                        tableHtmlLen: table ? table.outerHTML.length : 0,
                        tableHtml: table ? table.outerHTML : '',
                        textareas: Array.from(tas).map((t, i) => ({
                            index: i,
                            len: t.value.length,
                            form: (t.closest('form') || {}).id || 'none',
                        })),
                    };
                }
                """
            )
            if (
                result.get("panelLen", 0) > 50
                or result.get("documentsPanelLen", 0) > 100
                or result.get("tableContainerHtmlLen", 0) > 100
                or result.get("tableRows", 0) > 0
                or result.get("tableHtmlLen", 0) > 100
                or "captcha" in result.get("messages", "").lower()
            ):
                return result

            await asyncio.sleep(2)

        return {
            "source": "assisted",
            "messages": "",
            "panelLen": 0,
            "panelHtml": "",
            "error": f"assist_timeout_{timeout_sec}s",
        }

    def _extraer_html_de_respuesta_jsf(
        self, xml_text: str
    ) -> str | None:
        """Extract HTML content from JSF partial-update XML response."""
        try:
            # The response is XML with <partial-response><changes>
            # <update id="..."><![CDATA[...HTML...]]></update>
            from lxml import etree
            root = etree.fromstring(xml_text.encode("utf-8"))
            # Find <update> elements (no namespace or JSF namespace)
            for update in root.iter():
                if update.tag.endswith("update") or update.tag == "update":
                    uid = update.get("id", "")
                    if uid == "javax.faces.ViewRoot":
                        return update.text or ""
                    if uid and uid != "javax.faces.ViewState":
                        return update.text or ""
        except Exception as e:
            self._log.warning(
                "jsf_xml_parse_error", error=str(e)
            )
            # Fallback: extract CDATA via regex
            match = re.search(
                r'<update\s+id="javax\.faces\.ViewRoot">'
                r'\s*<!\[CDATA\[(.*?)\]\]>\s*</update>',
                xml_text,
                re.DOTALL,
            )
            if match:
                return match.group(1)
        return None

    def _extraer_comprobantes_de_html(
        self, html_content: str
    ) -> list[dict]:
        """Parse comprobante data from the HTML response.
        Returns list of dicts with clave_acceso and metadata."""
        from lxml import html as lxml_html
        comprobantes = []

        try:
            doc = lxml_html.fromstring(html_content)
        except Exception as e:
            self._log.warning(
                "html_parse_error", error=str(e)
            )
            return []

        # Find RichFaces DataTable or any table with comprobante data
        # Try known table structures
        tables = doc.xpath(
            "//table[contains(@class, 'rf-dt')]"
            " | //table[contains(@id, 'tablaCompRecibidos')]"
            " | //table[contains(@id, 'tablaComprobantes')]"
            " | //table[contains(@id, 'tblComprobantes')]"
        )

        if not tables:
            # Look for any table that has rows with many columns
            for table in doc.xpath("//table"):
                rows = table.xpath(".//tbody/tr")
                if rows and len(rows) > 0:
                    first_row_cells = rows[0].xpath(".//td")
                    if len(first_row_cells) >= 5:
                        tables.append(table)

        self._log.info(
            "tablas_candidatas",
            count=len(tables),
            ids=[t.get("id", "")[:50] for t in tables[:5]],
        )

        for table in tables:
            rows = table.xpath(".//tbody/tr")
            for row in rows:
                cells = row.xpath(".//td")
                if len(cells) < 3:
                    continue

                cell_texts = [
                    (c.text_content() or "").strip() for c in cells
                ]

                # Look for clave de acceso (49-digit number)
                clave = None
                for text in cell_texts:
                    match = re.search(r'\b(\d{49})\b', text)
                    if match:
                        clave = match.group(1)
                        break

                if not clave:
                    # Also check hidden inputs or data attributes
                    for inp in row.xpath(
                        ".//input[@type='hidden']"
                    ):
                        val = inp.get("value", "")
                        if re.match(r'^\d{49}$', val):
                            clave = val
                            break

                if clave:
                    comprobante = {
                        "clave_acceso": clave,
                        "columnas": cell_texts,
                    }
                    # Extract download link if present
                    links = row.xpath(
                        ".//a[contains(@onclick, 'xml')"
                        " or contains(@onclick, 'descargar')"
                        " or contains(@title, 'XML')]"
                    )
                    if links:
                        onclick = links[0].get("onclick", "")
                        comprobante["onclick_xml"] = onclick
                        comprobante["xml_link_id"] = links[0].get("id", "")
                    comprobantes.append(comprobante)

        # If no claves found via table, search entire HTML
        if not comprobantes:
            claves = re.findall(r'\b(\d{49})\b', html_content)
            claves = list(dict.fromkeys(claves))  # dedupe
            self._log.info(
                "claves_regex_fallback",
                total=len(claves),
                sample=claves[:3] if claves else [],
            )
            for clave in claves:
                comprobantes.append({
                    "clave_acceso": clave,
                    "columnas": [],
                })

        return comprobantes

    async def _descargar_xml_via_soap(
        self,
        client,
        clave: str,
    ) -> tuple[bytes | None, str | None]:
        SOAP_TEMPLATE = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<soapenv:Envelope '
            'xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:ec="http://ec.gob.sri.ws.autorizacion">'
            '<soapenv:Body>'
            '<ec:autorizacionComprobante>'
            '<claveAccesoComprobante>{clave}</claveAccesoComprobante>'
            '</ec:autorizacionComprobante>'
            '</soapenv:Body>'
            '</soapenv:Envelope>'
        )

        soap_body = SOAP_TEMPLATE.format(clave=clave)
        resp = await client.post(
            SRI_SOAP_URL,
            data=soap_body,
        )

        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"

        xml_text = resp.text
        xml_bytes = self._extraer_xml_de_soap(xml_text)
        if xml_bytes:
            return xml_bytes, None

        return None, self._extraer_error_de_soap(xml_text)

    def _extraer_error_de_soap(self, soap_response: str) -> str:
        from lxml import etree

        try:
            root = etree.fromstring(soap_response.encode("utf-8"))
            estado = None
            mensaje = None
            info = None
            for elem in root.iter():
                tag = elem.tag.split("}")[-1]
                text = (elem.text or "").strip()
                if tag == "estado" and text:
                    estado = text
                elif tag == "mensaje" and text and mensaje is None:
                    mensaje = text
                elif tag == "informacionAdicional" and text and info is None:
                    info = text

            partes = [p for p in [estado, mensaje, info] if p]
            return " | ".join(partes) if partes else "XML no encontrado en SOAP"
        except Exception as e:
            self._log.warning("soap_error_parse", error=str(e))
            return "XML no encontrado en SOAP"

    async def _descargar_xml_via_link_id(
        self,
        xml_link_id: str,
    ) -> bytes | None:
        page = self._page
        assert page is not None

        if not xml_link_id:
            return None

        boton_xml = await page.query_selector(f'[id="{xml_link_id}"]')
        if not boton_xml:
            return None
        return await self._descargar_xml_de_fila(boton_xml)

    async def _descargar_xml_con_fallbacks(
        self,
        *,
        client,
        clave: str | None,
        boton_xml=None,
        xml_link_id: str | None = None,
    ) -> tuple[bytes | None, str | None, str | None]:
        soap_error = None

        if clave:
            try:
                xml_bytes, soap_error = await self._descargar_xml_via_soap(
                    client,
                    clave,
                )
                if xml_bytes:
                    return xml_bytes, None, "soap"
                self._log.warning(
                    "soap_rechazado_fallback_lnkxml",
                    clave=clave[:20] + "...",
                    error=soap_error,
                )
            except Exception as e:
                soap_error = str(e)
                self._log.warning(
                    "soap_download_exception",
                    clave=clave[:20] + "...",
                    error=soap_error,
                )

        try:
            xml_bytes = None
            if boton_xml is not None:
                xml_bytes = await self._descargar_xml_de_fila(boton_xml)
            elif xml_link_id:
                xml_bytes = await self._descargar_xml_via_link_id(xml_link_id)

            if xml_bytes:
                return xml_bytes, None, "lnkXml"
        except Exception as e:
            fallback_error = str(e)
            self._log.warning(
                "lnkxml_download_exception",
                clave=(clave or "")[:20] + "...",
                error=fallback_error,
            )
            return None, fallback_error, None

        return None, soap_error or "No se pudo descargar XML", None

    async def _descargar_xmls_por_clave(
        self, comprobantes: list[dict]
    ) -> list[dict]:
        """Descarga XMLs por clave usando SOAP y fallback por lnkXml."""
        resultados = []

        async with self._crear_cliente_soap() as client:
            for idx, comp in enumerate(comprobantes):
                clave = comp.get("clave_acceso")
                if not clave:
                    continue

                self._log.info(
                    "descargando_xml",
                    idx=idx + 1,
                    total=len(comprobantes),
                    clave=clave[:20] + "...",
                )

                item = {
                    "clave_acceso": clave,
                    "columnas": comp.get("columnas", []),
                }

                try:
                    xml_bytes, error, fuente = (
                        await self._descargar_xml_con_fallbacks(
                            client=client,
                            clave=clave,
                            xml_link_id=comp.get("xml_link_id"),
                        )
                    )
                    if xml_bytes:
                        item["xml_bytes"] = xml_bytes
                        item["fuente_descarga"] = fuente
                        self._log.info(
                            "xml_descargado",
                            clave=clave[:20] + "...",
                            size=len(xml_bytes),
                            fuente=fuente,
                        )
                    else:
                        item["error"] = error

                except Exception as e:
                    item["error"] = str(e)
                    self._log.warning(
                        "xml_download_exception",
                        error=str(e),
                    )

                resultados.append(item)
                await delay_humano(
                    self._settings.delay_min_ms,
                    self._settings.delay_max_ms,
                )

        return resultados

    def _extraer_xml_de_soap(
        self, soap_response: str
    ) -> bytes | None:
        """Extract the comprobante XML from SRI SOAP response."""
        from lxml import etree
        import base64

        try:
            root = etree.fromstring(soap_response.encode("utf-8"))
            # Look for <comprobante> element which contains the XML
            # The SOAP structure is:
            # <autorizaciones><autorizacion>
            #   <comprobante><![CDATA[...XML...]]></comprobante>
            # </autorizacion></autorizaciones>
            for elem in root.iter():
                if elem.tag.endswith("comprobante") \
                        or elem.tag == "comprobante":
                    text = elem.text
                    if text and text.strip():
                        return text.strip().encode("utf-8")
        except Exception as e:
            self._log.warning(
                "soap_xml_parse_error", error=str(e)
            )
        return None

    async def _procesar_todas_las_paginas(self) -> list[dict]:
        """Itera por todas las páginas de la tabla."""
        page = self._page
        assert page is not None
        resultados: list[dict] = []
        pagina_actual = 1

        # Si debemos reanudar desde una página específica
        while pagina_actual < self._pagina_inicio:
            next_btn = await page.query_selector(
                "a.ui-paginator-next:not(.ui-state-disabled)"
            )
            if not next_btn:
                break
            await next_btn.click()
            await delay_humano(
                self._settings.delay_between_pages_ms,
                self._settings.delay_between_pages_ms + 1000,
            )
            pagina_actual += 1

        async with self._crear_cliente_soap() as client:
            while True:
                self._log.info("procesando_pagina", pagina=pagina_actual)
                resultados_pagina: list[dict] = []

                # Detectar estado anómalo
                estado = await self._detectar_estado_anomalo()
                if estado == EstadoPortal.SESION_EXPIRADA:
                    raise SRISessionExpiredError("Sesión expirada en paginación")
                if estado == EstadoPortal.MANTENIMIENTO:
                    raise SRIMaintenanceError("SRI en mantenimiento")

                # Extraer filas de la tabla principal
                data_table = await page.query_selector(
                    "table[id*='tablaCompRecibidos'], "
                    "table[id*='tablaComprobantes'], "
                    "table[id*='tblComprobantes'], "
                    "table[id*='dataTable']"
                )
                if data_table:
                    filas = await data_table.query_selector_all(
                        "tbody tr"
                    )
                else:
                    filas = await page.query_selector_all(
                        "table tbody tr"
                    )

                self._log.info(
                    "filas_en_pagina",
                    total=len(filas),
                    pagina=pagina_actual,
                )

                for idx, fila in enumerate(filas):
                    celdas = await fila.query_selector_all("td")
                    if len(celdas) < 3:
                        continue

                    fila_data = {
                        "numero_fila": idx + 1,
                        "pagina": pagina_actual,
                    }

                    # Extraer datos visibles de la tabla
                    for i, celda in enumerate(celdas):
                        texto = await celda.inner_text()
                        fila_data[f"col_{i}"] = texto.strip()

                    # Capturar clave de acceso visible en la fila
                    for texto in fila_data.values():
                        if not isinstance(texto, str):
                            continue
                        match = re.search(r"\b(\d{49})\b", texto)
                        if match:
                            fila_data["clave_acceso"] = match.group(1)
                            break

                    clave = fila_data.get("clave_acceso")
                    if (
                        clave
                        and self._should_skip_download is not None
                        and await self._should_skip_download(clave)
                    ):
                        fila_data["omitido_existente"] = True
                        fila_data["fuente_descarga"] = "cache"
                        resultados_pagina.append(fila_data)
                        if self._collect_results:
                            resultados.append(fila_data)
                        continue

                    # Log primera fila para diagnóstico
                    if idx == 0:
                        self._log.info(
                            "primera_fila",
                            cols=len(celdas),
                            datos={
                                k: v[:40]
                                for k, v in fila_data.items()
                                if k.startswith("col_")
                            },
                        )

                    # Buscar botón de descarga XML
                    # PrimeFaces/SRI usa links con iconos o títulos XML
                    boton_xml = await fila.query_selector(
                        "a[id$=':lnkXml'], "
                        "a[title*='XML' i], "
                        "a[title*='xml' i], "
                        "a[onclick*='xml' i], "
                        "a[onclick*='descargar' i], "
                        "a:has(img[src*='xml' i]), "
                        "a:has(img[alt*='xml' i])"
                    )
                    if not boton_xml:
                        # Intentar links genéricos en la última columna
                        if celdas:
                            boton_xml = await celdas[-1].query_selector(
                                "a, button"
                            )

                    xml_bytes, error, fuente = (
                        await self._descargar_xml_con_fallbacks(
                            client=client,
                            clave=fila_data.get("clave_acceso"),
                            boton_xml=boton_xml,
                        )
                    )
                    if xml_bytes:
                        fila_data["xml_bytes"] = xml_bytes
                        fila_data["fuente_descarga"] = fuente
                    else:
                        fila_data["error"] = error or "No se pudo descargar XML"

                    resultados_pagina.append(fila_data)
                    if self._collect_results:
                        resultados.append(fila_data)
                    await delay_humano(
                        self._settings.delay_min_ms,
                        self._settings.delay_max_ms,
                    )

                self._ultima_pagina_procesada = pagina_actual
                if self._on_page_processed and resultados_pagina:
                    await self._on_page_processed(
                        pagina_actual,
                        resultados_pagina,
                    )

                # Intentar siguiente página
                next_btn = await page.query_selector(
                    "a.ui-paginator-next:not(.ui-state-disabled)"
                )
                if not next_btn:
                    self._log.info(
                        "ultima_pagina", pagina=pagina_actual
                    )
                    break

                await next_btn.click()
                await delay_humano(
                    self._settings.delay_between_pages_ms,
                    self._settings.delay_between_pages_ms + 1000,
                )
                pagina_actual += 1

                # Esperar que la tabla se actualice
                await asyncio.sleep(1)

        return resultados

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(SRIDownloadError),
        reraise=True,
    )
    async def _descargar_xml_de_fila(
        self, boton_xml
    ) -> bytes | None:
        """Descarga el XML del botón de una fila."""
        page = self._page
        assert page is not None

        if not boton_xml:
            return None

        try:
            async with page.expect_download(
                timeout=self._settings.download_timeout_ms
            ) as dl_info:
                await boton_xml.click()

            download = await dl_info.value
            path = await download.path()
            if path:
                with open(path, "rb") as f:
                    xml_bytes = f.read()

                # Validar que sea XML real
                contenido = xml_bytes.decode("utf-8", errors="replace")
                if (
                    contenido.strip().startswith("<?xml")
                    or "<autorizacion" in contenido[:200]
                ):
                    # Wait until the table is interactive again before
                    # moving to the next row or page.
                    try:
                        await page.wait_for_selector(
                            "table[id*='tablaCompRecibidos'], "
                            "table[id*='tablaComprobantes'], "
                            "table[id*='tblComprobantes'], "
                            "table[id*='dataTable']",
                            timeout=10_000,
                        )
                    except Exception:
                        pass
                    return xml_bytes
                else:
                    self._log.warning(
                        "descarga_no_es_xml",
                        preview=contenido[:100],
                    )
                    raise XMLInvalidError("Contenido descargado no es XML")

        except PlaywrightTimeout:
            self._log.warning("descarga_timeout")
            raise SRIDownloadError("Timeout descargando XML")
        except XMLInvalidError:
            return None
        except Exception as e:
            self._log.warning("descarga_error", error=str(e))
            raise SRIDownloadError(f"Error descargando: {e}")

    async def _ejecutar_recaptcha_nativo(self) -> dict:
        """
        Ejecuta la consulta usando el token nativo del browser.
        """
        self._log.info("recaptcha_nativo_iniciando")
        try:
            page = self._page
            assert page is not None
            try:
                await simular_actividad_humana(page)
            except Exception as exc:
                self._log.warning(
                    "actividad_humana_pre_captcha_error",
                    error=str(exc),
                )
            result = await self._ejecutar_consulta_controlada(
                token=None,
                source="native",
            )
            self._log.info("recaptcha_nativo_resultado", result=result)
            return result
        except Exception as e:
            self._log.error("recaptcha_nativo_error", error=str(e))
            return {
                "messages": "",
                "panelLen": 0,
                "panelHtml": "",
                "error": str(e),
            }

    async def _resolver_recaptcha_pre_consulta(
        self, score: float | None = None
    ) -> None:
        """Resuelve reCAPTCHA Enterprise antes de enviar la consulta.

        Strategy (expert-level fix):
        1. Get token from external solver (2captcha/CapSolver)
        2. Set window.__captchaToken so our addInitScript override
           returns it when rcBuscar calls grecaptcha.enterprise.execute
        3. Also inject into g-recaptcha-response textareas as fallback
        4. Set up route interception to replace token in AJAX POST body
        """
        page = self._page
        assert page is not None

        site_key = await self._obtener_site_key_consulta()

        self._log.info(
            "recaptcha_resolviendo",
            site_key=site_key[:10],
            enterprise=True,
            action=RECAPTCHA_ACTION,
            score=score,
        )

        # Use canonical URL without query params for solver
        page_url = page.url.split("?")[0].split("#")[0]
        self._log.info("captcha_page_url", url=page_url)

        # Try external solver first, fall back to native
        token = await self._captcha_resolver.resolver_token_recaptcha(
            site_key=site_key,
            page_url=page_url,
            enterprise=True,
            action=RECAPTCHA_ACTION,
            score=score if score is not None else 0.9,
        )
        if not token:
            raise SRICaptchaError(
                "No se pudo resolver reCAPTCHA Enterprise"
            )

        self._log.info(
            "recaptcha_token_obtenido",
            token_len=len(token),
        )

        # 1) Set global token so addInitScript override returns it
        await page.evaluate(f"""
        () => {{
            window.__captchaToken = '{token}';
            console.log('[captcha] Token set in window.__captchaToken (' + window.__captchaToken.length + ' chars)');

            // Also inject into textareas
            document.querySelectorAll('[name="g-recaptcha-response"]')
                .forEach(el => {{ el.value = window.__captchaToken; }});

            // Re-patch grecaptcha.enterprise.execute in case it was
            // re-defined after page load (e.g. by lazy-loaded scripts)
            if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) {{
                const token = window.__captchaToken;
                grecaptcha.enterprise.execute = function(sk, opts) {{
                    console.log('[captcha] execute intercepted, returning our token');
                    return Promise.resolve(token);
                }};
                grecaptcha.enterprise.reset = function() {{}};
            }}

            // Also override executeRecaptcha if it exists
            if (typeof window.executeRecaptcha === 'function') {{
                const token = window.__captchaToken;
                window.executeRecaptcha = function(action) {{
                    document.querySelectorAll('[name="g-recaptcha-response"]')
                        .forEach(el => {{ el.value = token; }});
                    if (typeof rcBuscar === 'function') {{
                        rcBuscar({{ 'g-recaptcha-response': token }});
                    }} else if (typeof onSubmit === 'function') {{
                        onSubmit();
                    }}
                }};
            }}
        }}
        """)

        # No route interception needed — we pass the token directly
        # as params to rcBuscar(), which sends it via PrimeFaces.ab()
        await self._limpiar_route_handler()

    async def _limpiar_route_handler(self) -> None:
        """Remove route interception after query to avoid interfering
        with pagination and other requests."""
        if self._route_handler and self._page:
            try:
                await self._page.unroute("**/*", self._route_handler)
            except Exception:
                pass
            self._route_handler = None

    async def _detectar_captcha(self) -> bool:
        """Detecta si hay CAPTCHA visible y lo resuelve."""
        page = self._page
        assert page is not None

        selectors = [
            "iframe[src*='recaptcha/enterprise']",
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "div.g-recaptcha",
            "img[src*='captcha']",
            "img[alt*='captcha' i]",
        ]

        for selector in selectors:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                self._log.warning("captcha_detectado", selector=selector)
                for resolver_info in self._captcha_resolvers:
                    resuelto = await resolver_info["resolver"].resolver(
                        page,
                        enterprise="enterprise" in selector,
                    )
                    if resuelto:
                        self._log.info(
                            "captcha_resuelto",
                            provider=resolver_info["provider"],
                        )
                        return True
                if self._captcha_assisted_available():
                    result = await self._esperar_captcha_login_asistido()
                    if result:
                        return True
                raise SRICaptchaError("No se pudo resolver CAPTCHA")

        return False

    async def _esperar_captcha_login_asistido(self) -> bool:
        page = self._page
        assert page is not None

        timeout_sec = self._settings.captcha_assisted_timeout_sec
        self._log.warning(
            "captcha_login_asistido",
            timeout_sec=timeout_sec,
        )
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while asyncio.get_running_loop().time() < deadline:
            if "auth/realms" not in page.url:
                return True
            await asyncio.sleep(2)
        return False

    async def _detectar_estado_anomalo(self) -> EstadoPortal:
        """Detecta estados especiales del portal."""
        page = self._page
        assert page is not None

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass

        body_text = await page.inner_text("body", timeout=15000)
        body_lower = body_text.lower()

        if "no está disponible" in body_lower or "mantenimiento" in body_lower:
            return EstadoPortal.MANTENIMIENTO

        # Redirect a login page = sesión expirada
        url = page.url
        if "auth/realms" in url:
            return EstadoPortal.SESION_EXPIRADA

        if "sesión ha expirado" in body_lower or "session" in body_lower:
            return EstadoPortal.SESION_EXPIRADA

        if "ha ocurrido un error" in body_lower:
            return EstadoPortal.ERROR_SISTEMA

        # Verificar redirect fuera de dominio
        if "srienlinea.sri.gob.ec" not in page.url:
            return EstadoPortal.ERROR_SISTEMA

        return EstadoPortal.NORMAL
