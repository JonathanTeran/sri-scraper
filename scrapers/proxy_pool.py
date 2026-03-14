"""
Smart proxy pool management with health tracking.

Manages a pool of proxy servers, rotating them intelligently based on
success/failure history.  Blocked IPs are quarantined and rotated out.

Supports:
- Static proxy list from environment (comma-separated)
- Per-proxy health scoring
- Automatic quarantine of blocked proxies
- Geo-preference for Ecuador IPs
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger()

_REDIS_PREFIX = "sri:proxy_pool"
_PROXY_STATS_TTL = 24 * 3600  # 24 hours
_QUARANTINE_TTL = 30 * 60     # 30 minutes


@dataclass
class ProxyInfo:
    """Parsed proxy with metadata."""

    server: str          # host:port
    username: str = ""
    password: str = ""
    label: str = ""      # friendly name
    geo: str = ""        # country code (e.g., "EC")

    @property
    def url(self) -> str:
        if self.username and self.password:
            return f"http://{self.username}:{self.password}@{self.server}"
        return f"http://{self.server}"

    @property
    def playwright_proxy(self) -> dict:
        proxy = {"server": f"http://{self.server}"}
        if self.username:
            proxy["username"] = self.username
        if self.password:
            proxy["password"] = self.password
        return proxy

    @property
    def key(self) -> str:
        return self.server.replace(":", "_").replace(".", "_")


@dataclass
class ProxyHealth:
    """Health metrics for a proxy."""

    successes: int = 0
    failures: int = 0
    blocks: int = 0
    last_success_ts: float = 0.0
    last_failure_ts: float = 0.0
    quarantined_until: float = 0.0

    @property
    def total(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.5
        return self.successes / self.total

    @property
    def is_quarantined(self) -> bool:
        return time.time() < self.quarantined_until

    @property
    def score(self) -> float:
        """Weighted score for proxy selection (higher = better)."""
        if self.is_quarantined:
            return -1.0

        rate = self.success_rate
        now = time.time()

        # Recency bonus
        recency = 0.0
        if self.last_success_ts > 0:
            hours_ago = (now - self.last_success_ts) / 3600
            recency = max(0, 0.1 - hours_ago * 0.01)

        # Block penalty
        penalty = 0.0
        if self.blocks >= 3:
            penalty = min(self.blocks * 0.1, 0.5)

        return max(0.0, rate + recency - penalty)


class ProxyPool:
    """Manages a pool of proxies with intelligent rotation."""

    def __init__(self, redis_client, proxies: list[ProxyInfo] | None = None):
        self._redis = redis_client
        self._proxies: list[ProxyInfo] = proxies or []
        self._current_idx = 0

    @classmethod
    def from_config(
        cls,
        redis_client,
        proxy_urls: str,
    ) -> ProxyPool:
        """Create pool from comma-separated proxy URLs.

        Format: host:port:user:pass:label:geo,...
        Or simple: host:port,...
        """
        proxies = []
        if not proxy_urls.strip():
            return cls(redis_client, [])

        for entry in proxy_urls.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) >= 4:
                proxy = ProxyInfo(
                    server=f"{parts[0]}:{parts[1]}",
                    username=parts[2],
                    password=parts[3],
                    label=parts[4] if len(parts) > 4 else "",
                    geo=parts[5] if len(parts) > 5 else "",
                )
            elif len(parts) == 2:
                proxy = ProxyInfo(server=entry)
            else:
                log.warning("proxy_formato_invalido", entry=entry)
                continue
            proxies.append(proxy)

        log.info("proxy_pool_inicializado", total=len(proxies))
        return cls(redis_client, proxies)

    @property
    def size(self) -> int:
        return len(self._proxies)

    @property
    def is_empty(self) -> bool:
        return len(self._proxies) == 0

    async def get_best_proxy(self) -> ProxyInfo | None:
        """Return the proxy with the best health score, skipping quarantined ones."""
        if not self._proxies:
            return None

        scored: list[tuple[float, int, ProxyInfo]] = []
        for idx, proxy in enumerate(self._proxies):
            health = await self._get_health(proxy)
            if health.is_quarantined:
                continue
            scored.append((health.score, idx, proxy))

        if not scored:
            # All quarantined — return the one closest to un-quarantine
            healths = []
            for proxy in self._proxies:
                h = await self._get_health(proxy)
                healths.append((h.quarantined_until, proxy))
            healths.sort(key=lambda x: x[0])
            best = healths[0][1]
            log.warning("proxy_todos_en_cuarentena_usando_primero", proxy=best.server)
            return best

        scored.sort(key=lambda x: (-x[0], x[1]))
        best_proxy = scored[0][2]

        log.info(
            "proxy_seleccionado",
            proxy=best_proxy.server,
            score=round(scored[0][0], 3),
            disponibles=len(scored),
        )
        return best_proxy

    async def get_next_proxy(self) -> ProxyInfo | None:
        """Simple round-robin rotation, skipping quarantined proxies."""
        if not self._proxies:
            return None

        for _ in range(len(self._proxies)):
            proxy = self._proxies[self._current_idx % len(self._proxies)]
            self._current_idx += 1
            health = await self._get_health(proxy)
            if not health.is_quarantined:
                return proxy

        # All quarantined
        return self._proxies[0]

    async def record_success(self, proxy: ProxyInfo) -> None:
        """Record a successful request through this proxy."""
        key = f"{_REDIS_PREFIX}:{proxy.key}"
        health = await self._get_health(proxy)
        health.successes += 1
        health.last_success_ts = time.time()
        await self._save_health(key, health)
        log.debug("proxy_success", proxy=proxy.server, rate=round(health.success_rate, 3))

    async def record_failure(self, proxy: ProxyInfo, *, blocked: bool = False) -> None:
        """Record a failed request through this proxy."""
        key = f"{_REDIS_PREFIX}:{proxy.key}"
        health = await self._get_health(proxy)
        health.failures += 1
        health.last_failure_ts = time.time()

        if blocked:
            health.blocks += 1
            # Quarantine if too many blocks
            if health.blocks >= 3:
                quarantine_sec = min(health.blocks * 300, _QUARANTINE_TTL)
                health.quarantined_until = time.time() + quarantine_sec
                log.warning(
                    "proxy_en_cuarentena",
                    proxy=proxy.server,
                    blocks=health.blocks,
                    quarantine_sec=quarantine_sec,
                )

        await self._save_health(key, health)

    async def _get_health(self, proxy: ProxyInfo) -> ProxyHealth:
        key = f"{_REDIS_PREFIX}:{proxy.key}"
        raw = await self._redis.get(key)
        if not raw:
            return ProxyHealth()
        data = json.loads(raw)
        return ProxyHealth(
            successes=data.get("successes", 0),
            failures=data.get("failures", 0),
            blocks=data.get("blocks", 0),
            last_success_ts=data.get("last_success_ts", 0.0),
            last_failure_ts=data.get("last_failure_ts", 0.0),
            quarantined_until=data.get("quarantined_until", 0.0),
        )

    async def _save_health(self, key: str, health: ProxyHealth) -> None:
        data = {
            "successes": health.successes,
            "failures": health.failures,
            "blocks": health.blocks,
            "last_success_ts": health.last_success_ts,
            "last_failure_ts": health.last_failure_ts,
            "quarantined_until": health.quarantined_until,
        }
        await self._redis.setex(key, _PROXY_STATS_TTL, json.dumps(data))
