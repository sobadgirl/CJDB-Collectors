from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ConfigParameterType(StrEnum):
    PASSWORD = "password"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    CHECKBOX = "checkbox"
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


def config_param(
    key: str,
    type: ConfigParameterType,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    options: list[dict[str, str]] | None = None,
    help: str | None = None,
) -> ConfigParameter:
    return ConfigParameter(
        key=key,
        type=type,
        label=label,
        required=required,
        default=default,
        options=options,
        help=help,
    )


def password_param(
    key: str,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.PASSWORD,
        label,
        required=required,
        default=default,
        help=help,
    )


def single_select_param(
    key: str,
    label: str,
    *,
    options: list[dict[str, str]],
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.SINGLE_SELECT,
        label,
        required=required,
        default=default,
        options=options,
        help=help,
    )


def multi_select_param(
    key: str,
    label: str,
    *,
    options: list[dict[str, str]],
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.MULTI_SELECT,
        label,
        required=required,
        default=default,
        options=options,
        help=help,
    )


def checkbox_param(
    key: str,
    label: str,
    *,
    required: bool = False,
    default: Any = False,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.CHECKBOX,
        label,
        required=required,
        default=default,
        help=help,
    )


def text_param(
    key: str,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.TEXT,
        label,
        required=required,
        default=default,
        help=help,
    )


def number_param(
    key: str,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.NUMBER,
        label,
        required=required,
        default=default,
        help=help,
    )


def local_path_param(
    key: str,
    label: str,
    *,
    required: bool = False,
    default: Any = None,
    help: str | None = None,
) -> ConfigParameter:
    return config_param(
        key,
        ConfigParameterType.LOCAL_PATH,
        label,
        required=required,
        default=default,
        help=help,
    )


def clean_config_values(
    parameters: tuple[ConfigParameter, ...],
    values: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    require_required: bool = True,
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
        if parameter.type == ConfigParameterType.CHECKBOX:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                value = bool(value)
        if require_required and parameter.required and not value:
            raise error_type(required_message.format(key=key))
        if value is not None:
            cleaned[key] = value
    return cleaned
