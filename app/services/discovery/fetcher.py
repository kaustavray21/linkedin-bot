"""
app/services/discovery/fetcher.py

The only place in this codebase that makes requests to linkedin.com for reading.

Everything protective lives here rather than in the egress strategies, because
these limits must hold no matter which strategy is selected. If the daily cap
were enforced per strategy, switching egress would reset it — turning a safety
limit into a formality.

What it enforces:
  - a minimum jittered gap between requests, so traffic is paced like a person
  - a hard daily ceiling on total fetches
  - a per-strategy circuit breaker that opens after repeated blocks
  - failover to the configured fallback strategy when the primary opens

The circuit breakers are per-strategy on purpose. A block from Jina says nothing
about whether a direct fetch would work, and vice versa — one shared breaker
would let either path disable both.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.logger import get_logger
from app.services.discovery.egress.base import EgressError, FetchResult
from app.services.discovery.egress.strategies import get_egress

log = get_logger(tag="discovery")


def _utcnow() -> datetime:
    """Naive UTC, matching how every datetime in this project is stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class CircuitState:
    consecutive_blocks: int = 0
    opened_at: datetime | None = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        cooldown = timedelta(hours=settings.discovery_circuit_cooldown_hours)
        if _utcnow() - self.opened_at >= cooldown:
            # Cooldown elapsed — close it and let the next request probe.
            self.opened_at = None
            self.consecutive_blocks = 0
            return False
        return True

    def record_block(self) -> None:
        self.consecutive_blocks += 1
        if self.consecutive_blocks >= settings.discovery_circuit_threshold:
            self.opened_at = _utcnow()

    def record_success(self) -> None:
        self.consecutive_blocks = 0
        self.opened_at = None


@dataclass
class FetchBudget:
    day: str = field(default_factory=lambda: _utcnow().strftime("%Y-%m-%d"))
    used: int = 0

    def _roll(self) -> None:
        today = _utcnow().strftime("%Y-%m-%d")
        if today != self.day:
            self.day = today
            self.used = 0

    def remaining(self) -> int:
        self._roll()
        return max(0, settings.discovery_daily_fetch_cap - self.used)

    def consume(self) -> None:
        self._roll()
        self.used += 1


class RateLimitedFetcher:
    """Paces and guards all outbound post-page fetches.

    Deliberately a single shared instance (see `fetcher` below) — per-request
    instances would each carry their own budget and breaker, which is the same
    as having neither.
    """

    def __init__(self) -> None:
        self._budget = FetchBudget()
        self._circuits: dict[str, CircuitState] = {}
        self._last_request_at: datetime | None = None
        self._lock = asyncio.Lock()

    def circuit(self, name: str) -> CircuitState:
        return self._circuits.setdefault(name, CircuitState())

    def status(self) -> dict:
        return {
            "daily_cap": settings.discovery_daily_fetch_cap,
            "remaining_today": self._budget.remaining(),
            "circuits": {
                name: {
                    "open": state.is_open(),
                    "consecutive_blocks": state.consecutive_blocks,
                    "opened_at": state.opened_at.isoformat() if state.opened_at else None,
                }
                for name, state in self._circuits.items()
            },
        }

    async def _wait_for_slot(self) -> None:
        """Hold the caller until the minimum gap since the last request elapses."""
        if self._last_request_at is None:
            return
        gap = settings.discovery_min_interval_seconds + random.uniform(
            0, settings.discovery_jitter_seconds
        )
        elapsed = (_utcnow() - self._last_request_at).total_seconds()
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)

    async def fetch(self, url: str, egress_name: str | None = None) -> FetchResult:
        """Fetch one page, or raise if a guard refuses.

        Serialised by a lock: concurrent callers would each see the gap as
        satisfied and fire simultaneously, which is exactly the burst the pacing
        exists to prevent.
        """
        async with self._lock:
            if self._budget.remaining() <= 0:
                raise EgressError(
                    f"Daily fetch cap reached ({settings.discovery_daily_fetch_cap}). "
                    "Resets at UTC midnight."
                )

            primary = (egress_name or settings.discovery_egress).lower()
            fallback = (settings.discovery_egress_fallback or "direct").lower()

            order = [primary]
            if fallback != primary:
                order.append(fallback)

            attempted_open: list[str] = []
            for name in order:
                state = self.circuit(name)
                if state.is_open():
                    attempted_open.append(name)
                    continue

                await self._wait_for_slot()
                strategy = get_egress(name)

                try:
                    result = await strategy.fetch(url)
                except EgressError as exc:
                    log.warning("Egress failed", egress=name, url=url, error=str(exc))
                    state.record_block()
                    self._last_request_at = _utcnow()
                    continue

                self._last_request_at = _utcnow()
                self._budget.consume()

                if result.is_blocked:
                    state.record_block()
                    log.warning(
                        "Fetch was blocked",
                        egress=name,
                        status=result.status_code,
                        consecutive=state.consecutive_blocks,
                        circuit_open=state.is_open(),
                    )
                    continue

                state.record_success()
                return result

            if attempted_open:
                raise EgressError(
                    f"Circuit open for: {', '.join(attempted_open)}. "
                    f"Cooling down for {settings.discovery_circuit_cooldown_hours}h."
                )
            raise EgressError(f"All egress strategies failed for {url}")


# One shared instance — the budget and breakers are only meaningful if every
# caller shares them.
fetcher = RateLimitedFetcher()
