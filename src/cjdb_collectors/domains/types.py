from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SetupResult:
    success: bool
    message: str | None = None
    setup_payload: Mapping[str, Any] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "setup_payload": dict(self.setup_payload),
            "details": dict(self.details),
        }


__all__ = ["SetupResult"]
