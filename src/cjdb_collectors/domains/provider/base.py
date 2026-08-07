from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import Any, Mapping

from cjdb_collectors.config_fields import ConfigParameter, clean_config_values
from cjdb_collectors.domains.types import SetupResult

from .types import ProviderType


class BaseProvider(ABC):
    namespace: str
    name: str
    supported_types: tuple[ProviderType | str, ...]
    parameters: tuple[ConfigParameter, ...] = ()
    status_refresh_seconds: int = 30

    def __init__(
        self,
        setup_payload: Mapping[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.setup_payload = dict(setup_payload or {})
        self.logger = logger or logging.getLogger(
            f"cjdb_collectors.provider.{getattr(self, 'namespace', self.__class__.__name__)}.null"
        )
        if logger is None:
            self.logger.setLevel(logging.INFO)
            self.logger.propagate = False
            if not self.logger.handlers:
                self.logger.addHandler(logging.NullHandler())

    @staticmethod
    def clean_params_value(
        params: tuple[ConfigParameter, ...],
        values: Mapping[str, Any],
        *,
        current: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {parameter.key for parameter in params}
        return clean_config_values(
            params,
            {key: value for key, value in dict(values).items() if key in allowed},
            current=dict(current or {}),
            require_required=False,
            unknown_message="unknown provider parameters: {keys}",
            required_message="provider parameter is required: {key}",
            error_type=ValueError,
        )

    @abstractmethod
    def setup(self, params: Mapping[str, Any]) -> SetupResult:
        """Initialize from transient params and return the payload to persist."""

    @abstractmethod
    def status(self, *args: Any, **kwargs: Any) -> Any:
        """Return the Provider's current status."""


__all__ = ["BaseProvider"]
