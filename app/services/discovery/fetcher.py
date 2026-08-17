"""
app/services/discovery/fetcher.py

The only place in this codebase that makes requests to linkedin.com for reading.

Everything protective lives here rather than in the egress strategies, because
these limits must hold no matter which strategy is selected. If the daily cap
were enforced per strategy, switching egress would reset it — turning a safety
limit into a formality.

What it enforces:
  - a requests-per-second ceiling shared by every worker (token bucket)
  - a cap on how many fetches are in flight at once
  - a hard daily ceiling on total fetches
  - a per-strategy circuit breaker that opens after repeated blocks
  - failover to the configured fallback strategy when the primary opens

Concurrency notes, all of which came out of profiling rather than reasoning
(`scratchpad/worker_probe.py`, summarised in the plan's §2.1):

  - Requests/second is the real knob. In-flight demand is rate x latency, so
    at 2-3 req/s three workers is the knee; six measured exactly as fast and
    only sent more requests after a block.
  - The breaker must be re-checked AFTER queueing and immediately BEFORE the
    request. Callers are admitted together, so every one of them clears an
    entry-only check before the first response lands — with a wave of 30, all
    30 were still sent after the site started refusing at request 5.
  - Budget must be reserved before dispatch. N workers checking "is there
    budget?" against a cap of 1 all see yes.
  - Token burst is 1. A bucket that starts full fires `rate` requests
    simultaneously on the first tick, which is the worst possible opening
    move against a bot detector.
"""

from __future__ import annotations

import asyncio
import random
import time
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


class TokenBucket:
    """Requests-per-second ceiling shared by every worker.

    Rate and capacity are read from settings on every acquire, not captured in
    __init__. The fetcher is a module-level singleton, so a snapshot would pin
    whatever the config said at import time — and the whole safety story here is
    "lower discovery_requests_per_second, don't touch code". A knob that needs a
    process restart to take effect is not that knob.

    Capacity defaults to one token, not `rate`: a full bucket lets the entire
    worker pool fire simultaneously on the first tick.
    """

    def __init__(self) -> None:
        self.tokens = 1.0
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                rate = max(settings.discovery_requests_per_second, 0.01)
                capacity = max(settings.discovery_token_burst, 1.0)
                now = time.monotonic()
                self.tokens = min(capacity, self.tokens + (now - self.updated) * rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / rate
            await asyncio.sleep(wait)


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

    def reserve(self) -> bool:
        """Claim one unit up front. Callers that bail must refund()."""
        self._roll()
        if self.used >= settings.discovery_daily_fetch_cap:
            return False
        self.used += 1
        return True

    def refund(self) -> None:
        self.used = max(0, self.used - 1)


class RateLimitedFetcher:
    """Paces and guards all outbound post-page fetches.

    Deliberately a single shared instance (see `fetcher` below) — per-request
    instances would each carry their own budget and breaker, which is the same
    as having neither.

    Concurrency lives here rather than in the caller so that every path through
    the app shares one pool. `service.py` simply calls `fetch()` from several
    tasks; this class decides how many actually run.
    """

    def __init__(self) -> None:
        self._budget = FetchBudget()
        self._circuits: dict[str, CircuitState] = {}
        self._last_request_at: datetime | None = None
        self._lock = asyncio.Lock()

        self._bucket = TokenBucket()
        # A hard ceiling, not the working limit. A semaphore cannot be resized
        # once tasks are waiting on it, so the live limit is applied in fetch()
        # instead and this only stops an unbounded pile-up.
        self._sem = asyncio.Semaphore(64)
        self._ramped_limit: int | None = None
        self._inflight = 0
        self._consecutive_ok = 0

    @property
    def active_limit(self) -> int:
        """Current worker limit, clamped to the configured range.

        Read live for the same reason as the token rate — see TokenBucket.
        """
        base = self._ramped_limit or settings.discovery_concurrency
        return max(1, min(base, settings.discovery_concurrency_max))

    # ------------------------------------------------------------- state --

    def circuit(self, name: str) -> CircuitState:
        return self._circuits.setdefault(name, CircuitState())

    def status(self) -> dict:
        return {
            "daily_cap": settings.discovery_daily_fetch_cap,
            "remaining_today": self._budget.remaining(),
            "requests_per_second": settings.discovery_requests_per_second,
            "concurrency": self.active_limit,
            "concurrency_max": settings.discovery_concurrency_max,
            "circuits": {
                name: {
                    "open": state.is_open(),
                    "consecutive_blocks": state.consecutive_blocks,
                    "opened_at": state.opened_at.isoformat() if state.opened_at else None,
                }
                for name, state in self._circuits.items()
            },
        }

    def any_circuit_open(self) -> bool:
        """True when every configured strategy is refusing.

        Callers use this to abandon a run rather than queue work that will be
        rejected one item at a time.
        """
        primary = (settings.discovery_egress or "direct").lower()
        fallback = (settings.discovery_egress_fallback or "direct").lower()
        names = {primary, fallback}
        return all(self.circuit(n).is_open() for n in names)

    # --------------------------------------------------------- internals --

    async def _record(self, blocked: bool) -> None:
        """Adaptive limit: ramp on sustained success, collapse on any block."""
        async with self._lock:
            if blocked:
                self._consecutive_ok = 0
                if settings.discovery_adaptive:
                    self._ramped_limit = None      # back to the configured floor
                return

            self._consecutive_ok += 1
            if (
                settings.discovery_adaptive
                and self._consecutive_ok >= settings.discovery_ramp_after
            ):
                self._ramped_limit = min(
                    settings.discovery_concurrency_max, self.active_limit + 1
                )
                self._consecutive_ok = 0

    async def _wait_for_slot(self) -> None:
        """Optional extra floor between requests, off by default."""
        if not settings.discovery_min_interval_seconds:
            return
        if self._last_request_at is None:
            return
        gap = settings.discovery_min_interval_seconds + random.uniform(
            0, settings.discovery_jitter_seconds
        )
        elapsed = (_utcnow() - self._last_request_at).total_seconds()
        if elapsed < gap:
            await asyncio.sleep(gap - elapsed)

    # -------------------------------------------------------------- fetch --

    async def fetch(self, url: str, egress_name: str | None = None) -> FetchResult:
        """Fetch one page, or raise if a guard refuses.

        Safe to call from many tasks at once — the pool, the rate ceiling and
        the budget are all shared instance state.
        """
        async with self._lock:
            if not self._budget.reserve():
                raise EgressError(
                    f"Daily fetch cap reached ({settings.discovery_daily_fetch_cap}). "
                    "Resets at UTC midnight."
                )

        spent = False
        try:
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

                async with self._sem:
                    # Re-check after queueing. Callers are admitted together, so
                    # every one of them cleared the check above before the first
                    # response landed. Without this, a tripped breaker still
                    # sends the entire wave.
                    if state.is_open():
                        attempted_open.append(name)
                        continue

                    # Adaptive throttle. The semaphore is sized to the maximum
                    # and cannot be resized mid-flight, so the live limit is
                    # applied here.
                    while self._inflight >= self.active_limit:
                        await asyncio.sleep(0.02)
                        if state.is_open():
                            attempted_open.append(name)
                            break
                    else:
                        async with self._lock:
                            self._inflight += 1
                        try:
                            await self._wait_for_slot()
                            await self._bucket.acquire()

                            try:
                                result = await strategy_fetch(name, url)
                            except EgressError as exc:
                                log.warning(
                                    "Egress failed", egress=name, url=url, error=str(exc)
                                )
                                state.record_block()
                                await self._record(blocked=True)
                                self._last_request_at = _utcnow()
                                continue

                            self._last_request_at = _utcnow()
                            spent = True

                            if result.is_blocked:
                                state.record_block()
                                await self._record(blocked=True)
                                log.warning(
                                    "Fetch was blocked",
                                    egress=name,
                                    status=result.status_code,
                                    consecutive=state.consecutive_blocks,
                                    circuit_open=state.is_open(),
                                )
                                continue

                            state.record_success()
                            await self._record(blocked=False)
                            return result
                        finally:
                            async with self._lock:
                                self._inflight -= 1

            if attempted_open:
                raise EgressError(
                    f"Circuit open for: {', '.join(sorted(set(attempted_open)))}. "
                    f"Cooling down for {settings.discovery_circuit_cooldown_hours}h."
                )
            raise EgressError(f"All egress strategies failed for {url}")
        finally:
            # A request that never reached the network must not cost budget —
            # otherwise a tripped breaker silently burns the day's allowance.
            if not spent:
                async with self._lock:
                    self._budget.refund()


async def strategy_fetch(name: str, url: str) -> FetchResult:
    """Indirection point so tests can substitute the network cheaply."""
    return await get_egress(name).fetch(url)


# One shared instance — the budget, pool and breakers are only meaningful if
# every caller shares them.
fetcher = RateLimitedFetcher()
