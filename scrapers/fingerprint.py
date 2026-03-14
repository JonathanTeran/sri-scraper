"""
Browser fingerprint rotation for anti-detection.

Generates randomized but consistent browser fingerprints per session
to prevent SRI from correlating blocked sessions via canvas, WebGL,
screen resolution, timezone, and other browser attributes.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

# ── Pools of realistic values ──────────────────────────────────────────

_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{chrome_ver}.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{chrome_ver}.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{chrome_ver}.0.0.0 Safari/537.36"
    ),
]

_CHROME_VERSIONS = list(range(120, 136))

_SCREEN_RESOLUTIONS = [
    (1366, 768),
    (1920, 1080),
    (1536, 864),
    (1440, 900),
    (1280, 720),
    (1600, 900),
    (1280, 800),
    (1280, 1024),
]

_GPU_PROFILES = [
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": (
            "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "vendor": "Google Inc. (AMD)",
        "renderer": (
            "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": (
            "ANGLE (Intel, Intel(R) HD Graphics 630 Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": (
            "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 "
            "vs_5_0 ps_5_0, D3D11)"
        ),
    },
]

_PLATFORM_SETS = [
    {"platform": "Win32", "oscpu": "Windows NT 10.0; Win64; x64"},
    {"platform": "MacIntel", "oscpu": "Intel Mac OS X 10_15_7"},
    {"platform": "Linux x86_64", "oscpu": "Linux x86_64"},
]

_LANGUAGE_SETS = [
    ["es-EC", "es", "en-US", "en"],
    ["es-EC", "es-419", "es", "en"],
    ["es", "es-EC", "en-US", "en"],
]

_TIMEZONE_IDS = [
    "America/Guayaquil",
]

_FONTS = [
    "Arial", "Verdana", "Times New Roman", "Georgia", "Courier New",
    "Trebuchet MS", "Comic Sans MS", "Impact", "Lucida Console",
    "Tahoma", "Palatino Linotype", "Segoe UI", "Calibri", "Cambria",
]


@dataclass
class BrowserFingerprint:
    """A complete browser fingerprint for a single session."""

    user_agent: str = ""
    viewport_width: int = 1366
    viewport_height: int = 768
    screen_width: int = 1366
    screen_height: int = 768
    color_depth: int = 24
    pixel_ratio: float = 1.0
    platform: str = "Win32"
    oscpu: str = "Windows NT 10.0; Win64; x64"
    languages: list[str] = field(default_factory=lambda: ["es-EC", "es", "en-US", "en"])
    timezone_id: str = "America/Guayaquil"
    gpu_vendor: str = "Google Inc. (Intel)"
    gpu_renderer: str = "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"
    canvas_noise_seed: int = 0
    webgl_noise_seed: int = 0
    audio_noise: float = 0.0
    hardware_concurrency: int = 4
    device_memory: int = 8
    max_touch_points: int = 0
    installed_fonts: list[str] = field(default_factory=list)

    @property
    def fingerprint_id(self) -> str:
        """Short hash identifying this fingerprint config."""
        data = f"{self.user_agent}:{self.gpu_renderer}:{self.canvas_noise_seed}"
        return hashlib.md5(data.encode()).hexdigest()[:12]


def generate_fingerprint(seed: str | None = None) -> BrowserFingerprint:
    """Generate a randomized but internally-consistent browser fingerprint.

    If *seed* is provided, the fingerprint is deterministic for that seed
    (useful for keeping a fingerprint stable within a single scrape session).
    """
    rng = random.Random(seed)

    # Platform determines user-agent template
    platform_set = rng.choice(_PLATFORM_SETS)
    chrome_ver = rng.choice(_CHROME_VERSIONS)

    if platform_set["platform"] == "Win32":
        ua_template = _USER_AGENTS[0]
    elif platform_set["platform"] == "MacIntel":
        ua_template = _USER_AGENTS[1]
    else:
        ua_template = _USER_AGENTS[2]

    user_agent = ua_template.format(chrome_ver=chrome_ver)
    screen_w, screen_h = rng.choice(_SCREEN_RESOLUTIONS)
    gpu = rng.choice(_GPU_PROFILES)
    languages = rng.choice(_LANGUAGE_SETS)

    # Select a random subset of fonts (8-12 fonts)
    font_count = rng.randint(8, 12)
    installed_fonts = rng.sample(_FONTS, min(font_count, len(_FONTS)))

    fp = BrowserFingerprint(
        user_agent=user_agent,
        viewport_width=screen_w,
        viewport_height=screen_h,
        screen_width=screen_w,
        screen_height=screen_h,
        color_depth=rng.choice([24, 32]),
        pixel_ratio=rng.choice([1.0, 1.25, 1.5]),
        platform=platform_set["platform"],
        oscpu=platform_set["oscpu"],
        languages=languages,
        timezone_id=rng.choice(_TIMEZONE_IDS),
        gpu_vendor=gpu["vendor"],
        gpu_renderer=gpu["renderer"],
        canvas_noise_seed=rng.randint(1, 2**31),
        webgl_noise_seed=rng.randint(1, 2**31),
        audio_noise=rng.uniform(0.0001, 0.01),
        hardware_concurrency=rng.choice([2, 4, 8, 12, 16]),
        device_memory=rng.choice([2, 4, 8, 16]),
        max_touch_points=0,
        installed_fonts=installed_fonts,
    )

    log.debug(
        "fingerprint_generated",
        fp_id=fp.fingerprint_id,
        platform=fp.platform,
        screen=f"{screen_w}x{screen_h}",
        gpu=gpu["vendor"][:20],
    )
    return fp


def build_stealth_script(fp: BrowserFingerprint) -> str:
    """Build a JavaScript init script that applies the fingerprint.

    Designed to run before any page navigation so that reCAPTCHA Enterprise
    sees consistent, realistic browser attributes.
    """
    fonts_js = json.dumps(fp.installed_fonts)
    languages_js = json.dumps(fp.languages)

    return f"""
    (() => {{
        // ── Navigator overrides ──────────────────────────────
        const overrides = {{
            webdriver: undefined,
            platform: {json.dumps(fp.platform)},
            hardwareConcurrency: {fp.hardware_concurrency},
            deviceMemory: {fp.device_memory},
            maxTouchPoints: {fp.max_touch_points},
        }};
        for (const [key, value] of Object.entries(overrides)) {{
            try {{
                Object.defineProperty(navigator, key, {{
                    get: () => value,
                    configurable: true,
                }});
            }} catch (e) {{}}
        }}

        // Languages
        try {{
            Object.defineProperty(navigator, 'languages', {{
                get: () => {languages_js},
                configurable: true,
            }});
            Object.defineProperty(navigator, 'language', {{
                get: () => {json.dumps(fp.languages[0])},
                configurable: true,
            }});
        }} catch (e) {{}}

        // Plugins (bots have empty plugins)
        try {{
            Object.defineProperty(navigator, 'plugins', {{
                get: () => [
                    {{ name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', length: 1 }},
                    {{ name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', length: 1 }},
                    {{ name: 'Native Client', filename: 'internal-nacl-plugin', length: 1 }},
                ],
                configurable: true,
            }});
        }} catch (e) {{}}

        // Chrome runtime
        if (!window.chrome) {{
            window.chrome = {{ runtime: {{}}, loadTimes: function(){{}}, csi: function(){{}} }};
        }}

        // Permissions API
        const origQuery = window.navigator.permissions?.query;
        if (origQuery) {{
            window.navigator.permissions.query = (params) => (
                params.name === 'notifications'
                    ? Promise.resolve({{ state: Notification.permission }})
                    : origQuery(params)
            );
        }}

        // ── Screen overrides ─────────────────────────────────
        const screenProps = {{
            width: {fp.screen_width},
            height: {fp.screen_height},
            availWidth: {fp.screen_width},
            availHeight: {fp.screen_height - 40},
            colorDepth: {fp.color_depth},
            pixelDepth: {fp.color_depth},
        }};
        for (const [key, value] of Object.entries(screenProps)) {{
            try {{
                Object.defineProperty(screen, key, {{
                    get: () => value,
                    configurable: true,
                }});
            }} catch (e) {{}}
        }}

        try {{
            Object.defineProperty(window, 'devicePixelRatio', {{
                get: () => {fp.pixel_ratio},
                configurable: true,
            }});
        }} catch (e) {{}}

        // ── Canvas fingerprint noise ─────────────────────────
        const canvasSeed = {fp.canvas_noise_seed};
        const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
        const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;

        HTMLCanvasElement.prototype.toDataURL = function(type) {{
            const ctx = this.getContext('2d');
            if (ctx && this.width > 0 && this.height > 0) {{
                const style = ctx.fillStyle;
                // Subtle noise: a single semi-transparent pixel
                ctx.fillStyle = 'rgba(' +
                    ((canvasSeed >> 16) & 0xFF) + ',' +
                    ((canvasSeed >> 8) & 0xFF) + ',' +
                    (canvasSeed & 0xFF) + ',0.003)';
                ctx.fillRect(0, 0, 1, 1);
                ctx.fillStyle = style;
            }}
            return origToDataURL.apply(this, arguments);
        }};

        // ── WebGL fingerprint spoof ──────────────────────────
        const webglSeed = {fp.webgl_noise_seed};
        const vendor = {json.dumps(fp.gpu_vendor)};
        const renderer = {json.dumps(fp.gpu_renderer)};
        const patchGL = (proto) => {{
            const origGetParam = proto.getParameter;
            proto.getParameter = function(param) {{
                if (param === 37445) return vendor;   // UNMASKED_VENDOR
                if (param === 37446) return renderer;  // UNMASKED_RENDERER
                return origGetParam.apply(this, arguments);
            }};
        }};
        try {{ patchGL(WebGLRenderingContext.prototype); }} catch (e) {{}}
        try {{ patchGL(WebGL2RenderingContext.prototype); }} catch (e) {{}}

        // ── AudioContext fingerprint noise ────────────────────
        try {{
            const origCreateOscillator = AudioContext.prototype.createOscillator;
            AudioContext.prototype.createOscillator = function() {{
                const osc = origCreateOscillator.apply(this, arguments);
                const origConnect = osc.connect.bind(osc);
                osc.connect = function(dest) {{
                    if (dest instanceof AnalyserNode) {{
                        // Add subtle frequency variation
                        osc.detune.value = {fp.audio_noise} * 100;
                    }}
                    return origConnect(dest);
                }};
                return osc;
            }};
        }} catch (e) {{}}

        // ── Font enumeration defense ─────────────────────────
        // Override fonts if document.fonts is available
        // (makes fingerprinting via font enumeration inconsistent)

        // ── Dialog blockers ──────────────────────────────────
        window.alert = function() {{ console.log('Interceptor: dismissed alert'); }};
        window.confirm = function() {{ return true; }};
    }})();
    """


def build_playwright_context_options(fp: BrowserFingerprint) -> dict:
    """Return kwargs suitable for playwright's browser.new_context()."""
    return {
        "user_agent": fp.user_agent,
        "viewport": {"width": fp.viewport_width, "height": fp.viewport_height},
        "screen": {"width": fp.screen_width, "height": fp.screen_height},
        "locale": fp.languages[0] if fp.languages else "es-EC",
        "timezone_id": fp.timezone_id,
        "device_scale_factor": fp.pixel_ratio,
        "color_scheme": "light",
    }


def build_nodriver_browser_args(fp: BrowserFingerprint) -> list[str]:
    """Return extra browser args for nodriver that match the fingerprint."""
    return [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--window-size={fp.viewport_width},{fp.viewport_height}",
        f"--lang={fp.languages[0] if fp.languages else 'es-EC'}",
    ]
