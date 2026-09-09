"""Headless state guard for the Studio worker boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class StudioBusyError(RuntimeError):
    """Raised when a second bounded job is requested while one is active."""


@dataclass(frozen=True)
class JobToken:
    generation: int
    sequence: int


class StudioController:
    """Track one active job and reject stale worker results."""

    def __init__(self) -> None:
        self.generation = 0
        self._sequence = 0
        self._active: JobToken | None = None
        self.result: Any = None

    @property
    def busy(self) -> bool:
        return self._active is not None

    def begin_job(self) -> JobToken:
        if self.busy:
            raise StudioBusyError("Studio is busy")
        self._sequence += 1
        token = JobToken(self.generation, self._sequence)
        self._active = token
        return token

    def advance_generation(self) -> int:
        self.generation += 1
        self.result = None
        return self.generation

    def finish_job(self, token: JobToken, result: Any) -> bool:
        if self._active == token:
            self._active = None
        if token.generation != self.generation:
            return False
        self.result = result
        return True

    def fail_job(self, token: JobToken) -> bool:
        if self._active == token:
            self._active = None
        return token.generation == self.generation


__all__ = ["JobToken", "StudioBusyError", "StudioController"]
