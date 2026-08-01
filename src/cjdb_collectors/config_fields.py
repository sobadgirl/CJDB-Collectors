from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ConfigParameterType(StrEnum):
    PASSWORD = "password"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    TEXT = "text"
    NUMBER = "number"
    LOCAL_PATH = "local_path"


@dataclass(frozen=True, slots=True)
class ConfigParameter:
    key: str
    type: ConfigParameterType
    label: str
    required: bool = False
    default: Any = None
    options: list[dict[str, str]] | None = None
    help: str | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type.value,
            "label": self.label,
            "required": self.required,
            "default": self.default,
            "options": self.options or [],
            "help": self.help,
        }


def clean_config_values(
    parameters: tuple[ConfigParameter, ...],
    values: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    unknown_message: str,
    required_message: str,
    error_type: type[Exception],
) -> dict[str, Any]:
    current = current or {}
    allowed = {parameter.key: parameter for parameter in parameters}
    unknown = sorted(set(values) - set(allowed))
    if unknown:
        raise error_type(unknown_message.format(keys=", ".join(unknown)))
    cleaned: dict[str, Any] = {}
    for key, parameter in allowed.items():
        value = values.get(key, current.get(key, parameter.default))
        if parameter.type == ConfigParameterType.PASSWORD and value == "":
            value = current.get(key, parameter.default)
        if parameter.required and not value:
            raise error_type(required_message.format(key=key))
        if value is not None:
            cleaned[key] = value
    return cleaned
