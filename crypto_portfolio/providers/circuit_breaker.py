"""Small in-process circuit breaker for repeated transient provider failures."""

from __future__ import annotations

from enum import Enum
import time
from typing import Callable


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Skip a provider briefly after bounded retryable failures."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if isinstance(failure_threshold, bool) or failure_threshold < 1:
            raise ValueError("failure_threshold must be a positive integer")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be non-negative")
        self.failure_threshold = int(failure_threshold)
        self.cooldown_seconds = float(cooldown_seconds)
        self.clock = clock
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.state is not CircuitState.OPEN:
            return True
        if self.opened_at is None or self.clock() - self.opened_at < self.cooldown_seconds:
            return False
        self.state = CircuitState.HALF_OPEN
        return True

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at = None

    def record_failure(self, *, retryable: bool) -> None:
        if not retryable:
            return
        if self.state is CircuitState.HALF_OPEN:
            self._open()
            return
        if self.state is CircuitState.OPEN:
            return
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self.state = CircuitState.OPEN
        self.opened_at = self.clock()


__all__ = ["CircuitBreaker", "CircuitState"]
