from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from importlib import import_module
from inspect import isabstract
from pkgutil import iter_modules
from typing import Any
from typing import TypeVar
from urllib.parse import urlparse

import psutil

from cjdb_collectors.data_provider import (
    AccountProviderMixin,
    AwemeProviderMixin,
    BaseDataProvider,
    CommentProviderMixin,
    DataProviderType,
    ProviderMetadata,
    ProviderStatus,
    VideoTranscriptionProviderMixin,
)
from cjdb_collectors.models import Platform

from .base import InvalidOperationError


_REQUIRED_CAPABILITY = {
    DataProviderType.DOUYIN_AWEME_COLLECT: AwemeProviderMixin,
    DataProviderType.XIAOHONGSHU_AWEME_COLLECT: AwemeProviderMixin,
    DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT: AwemeProviderMixin,
    DataProviderType.WECHAT_MP_AWEME_COLLECT: AwemeProviderMixin,
    DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: CommentProviderMixin,
    DataProviderType.ACCOUNT_COLLECT: AccountProviderMixin,
    DataProviderType.VIDEO_TRANSCRIPTION: VideoTranscriptionProviderMixin,
}
_PROVIDER_PACKAGE = "cjdb_collectors.data_provider.providers"
_REGISTERED_PROVIDER_CLASSES: dict[str, type[BaseDataProvider]] = {}
_LEGACY_PROVIDER_NAMESPACES = {
    "http-collector": "http_collector",
    "faster-whisper": "faster_whisper",
}
ProviderClass = TypeVar("ProviderClass", bound=type[BaseDataProvider])


def register_data_provider(provider_class: ProviderClass) -> ProviderClass:
    if not isinstance(provider_class, type) or not issubclass(
        provider_class,
        BaseDataProvider,
    ):
        raise TypeError("registered provider must inherit BaseDataProvider")
    if isabstract(provider_class):
        raise InvalidOperationError(
            f"provider {provider_class.__name__} has unimplemented abstract methods"
        )

    for attribute in ("namespace", "name"):
        value = getattr(provider_class, attribute, None)
        if not isinstance(value, str) or not value.strip():
            raise InvalidOperationError(
                f"provider {provider_class.__name__} must declare {attribute}"
            )

    declared_types = tuple(getattr(provider_class, "supported_types", ()))
    supported_types: list[DataProviderType] = []
    if not declared_types:
        raise InvalidOperationError(
            f"provider {provider_class.namespace} must declare a supported type"
        )
    for provider_type in declared_types:
        try:
            selected_type = DataProviderType(provider_type)
        except (TypeError, ValueError) as exc:
            raise InvalidOperationError(
                f"provider {provider_class.namespace} declares unknown type: {provider_type}"
            ) from exc
        required = _REQUIRED_CAPABILITY[selected_type]
        if not issubclass(provider_class, required):
            raise InvalidOperationError(
                f"provider {provider_class.namespace} declares {selected_type.value} "
                f"but does not implement {required.__name__}"
            )
        supported_types.append(selected_type)
    provider_class.supported_types = tuple(supported_types)

    existing = _REGISTERED_PROVIDER_CLASSES.get(provider_class.namespace)
    if existing is provider_class:
        return provider_class
    if existing is not None:
        raise InvalidOperationError(
            f"provider namespace conflict: {provider_class.namespace}"
        )

    _REGISTERED_PROVIDER_CLASSES[provider_class.namespace] = provider_class
    return provider_class


def registered_data_provider_classes() -> tuple[type[BaseDataProvider], ...]:
    package = import_module(_PROVIDER_PACKAGE)
    module_names = sorted(
        module.name
        for module in iter_modules(
            package.__path__,
            prefix=f"{package.__name__}.",
        )
        if not module.name.rsplit(".", 1)[-1].startswith("_")
    )
    for module_name in module_names:
        import_module(module_name)
    return tuple(
        _REGISTERED_PROVIDER_CLASSES[namespace]
        for namespace in sorted(_REGISTERED_PROVIDER_CLASSES)
    )


class DataProviderService:
    SERVICE_LABELS = {
        DataProviderType.DOUYIN_AWEME_COLLECT: "抖音数据采集",
        DataProviderType.XIAOHONGSHU_AWEME_COLLECT: "小红书数据采集",
        DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT: "视频号数据采集",
        DataProviderType.WECHAT_MP_AWEME_COLLECT: "公众号数据采集",
        DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: "小红书评论下载",
        DataProviderType.ACCOUNT_COLLECT: "账号数据采集",
        DataProviderType.VIDEO_TRANSCRIPTION: "视频转写",
    }
    TYPE_ALIASES = {
        "douyin": DataProviderType.DOUYIN_AWEME_COLLECT,
        "xiaohongshu": DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        "xhs": DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        "wechat_channels": DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
        "wechat_mp": DataProviderType.WECHAT_MP_AWEME_COLLECT,
        "comments": DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
        "account": DataProviderType.ACCOUNT_COLLECT,
        "transcription": DataProviderType.VIDEO_TRANSCRIPTION,
    }

    def __init__(
        self,
        providers: list[type[BaseDataProvider]],
        selected: dict[str, str] | None = None,
        config: Any = None,
    ) -> None:
        self._provider_classes = list(providers)
        self._provider_classes_by_type: dict[
            DataProviderType,
            list[type[BaseDataProvider]],
        ] = {provider_type: [] for provider_type in DataProviderType}
        namespaces: set[str] = set()
        for provider_class in self._provider_classes:
            if provider_class.namespace in namespaces:
                raise InvalidOperationError(
                    f"provider namespace conflict: {provider_class.namespace}"
                )
            namespaces.add(provider_class.namespace)
            for provider_type in provider_class.supported_types:
                selected_type = DataProviderType(provider_type)
                self._provider_classes_by_type[selected_type].append(provider_class)
        self._selected_provider_namespaces = {
            self.normalize_type(provider_type): self._normalize_namespace(namespace)
            for provider_type, namespace in (selected or {}).items()
        }
        self.config = config
        self._provider_statuses: dict[str, ProviderStatus] = {}

    def close(self) -> None:
        pass

    def selected(self) -> dict[str, str]:
        return {
            provider_type.value: namespace
            for provider_type, namespace in self._selected_provider_namespaces.items()
        }

    @staticmethod
    def _normalize_namespace(namespace: str) -> str:
        return _LEGACY_PROVIDER_NAMESPACES.get(namespace, namespace)

    def get_namespace(self, namespace: str) -> BaseDataProvider:
        namespace = self._normalize_namespace(namespace)
        for provider_class in self._provider_classes:
            if provider_class.namespace == namespace:
                provider = provider_class()
                provider.parameter_values = self.get_provider_parameters_values(
                    provider.namespace
                )
                return provider
        raise InvalidOperationError(f"unknown provider namespace: {namespace}")

    def get(
        self,
        provider_type: DataProviderType | str,
        namespace: str,
    ) -> BaseDataProvider:
        selected_type = DataProviderType(provider_type)
        namespace = self._normalize_namespace(namespace)
        for provider_class in self._provider_classes_by_type[selected_type]:
            if provider_class.namespace == namespace:
                provider = provider_class()
                provider.parameter_values = self.get_provider_parameters_values(
                    provider.namespace
                )
                return provider
        raise InvalidOperationError(
            f"unknown provider: {selected_type.value}/{namespace}"
        )

    def _metadata(
        self,
        provider_class: type[BaseDataProvider],
        provider_type: DataProviderType,
    ) -> dict[str, object]:
        if provider_type not in provider_class.supported_types:
            raise InvalidOperationError(
                f"provider {provider_class.namespace} does not support "
                f"{provider_type.value}"
            )
        platforms = provider_class.platforms_by_type.get(provider_type, set())
        return ProviderMetadata(
            namespace=provider_class.namespace,
            name=provider_class.name,
            type=provider_type,
            platforms=[
                platform.value
                for platform in sorted(platforms, key=lambda item: item.value)
            ],
            parameters=[
                parameter.model_dump()
                for parameter in provider_class.parameters
            ],
        ).model_dump()

    def _list_metadata(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> list[dict[str, object]]:
        selected_type = DataProviderType(provider_type) if provider_type else None
        provider_types = [selected_type] if selected_type else list(DataProviderType)
        values: list[dict[str, object]] = []
        for current_type in provider_types:
            for provider_class in self._provider_classes_by_type[current_type]:
                values.append(self._metadata(provider_class, current_type))
        return values

    def get_provider_parameters_values(self, namespace: str) -> dict[str, Any]:
        if self.config is None:
            return {}
        try:
            values = self.config.get(f"providers.{namespace}")
        except InvalidOperationError:
            return {}
        return dict(values) if isinstance(values, dict) else {}

    def set_provider_parameters(
        self,
        namespace: str,
        values: dict[str, Any],
    ) -> None:
        if self.config is None:
            raise InvalidOperationError("provider config is not bound")
        self.config.patch(
            {
                f"providers.{namespace}.{key}": value
                for key, value in values.items()
            }
        )

    def get_provider_status(self, namespace: str) -> ProviderStatus | None:
        namespace = self._normalize_namespace(namespace)
        settings = getattr(self.config, "settings", None)
        if settings is None:
            return self._provider_statuses.get(namespace)
        path = (
            Path(settings.app.data_dir)
            / "provider-status"
            / f"{namespace}.json"
        )
        if not path.is_file():
            return self._provider_statuses.get(namespace)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            checked_at_value = value.get("checked_at")
            checked_at = (
                datetime.fromisoformat(checked_at_value)
                if checked_at_value
                else None
            )
            details = value.get("details")
            status = ProviderStatus(
                status=str(value["status"]),
                message=value.get("message"),
                details=dict(details) if isinstance(details, dict) else {},
                checked_at=checked_at,
                setup_pid=value.get("setup_pid"),
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return self._provider_statuses.get(namespace)
        self._provider_statuses[namespace] = status
        return status

    def set_provider_status(
        self,
        namespace: str,
        status: ProviderStatus,
    ) -> ProviderStatus:
        namespace = self._normalize_namespace(namespace)
        if status.checked_at is None:
            status = replace(status, checked_at=datetime.now(timezone.utc))
        self._provider_statuses[namespace] = status
        settings = getattr(self.config, "settings", None)
        if settings is None:
            return status
        path = (
            Path(settings.app.data_dir)
            / "provider-status"
            / f"{namespace}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(status.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
        return status

    def refresh_provider_status(
        self,
        provider: BaseDataProvider,
    ) -> ProviderStatus:
        current = self.get_provider_status(provider.namespace)
        if current is not None:
            provider.set_status(current)
        status = provider.status()
        if status is current:
            return status
        return self.set_provider_status(provider.namespace, status)

    def normalize_type(
        self,
        provider_type: DataProviderType | str,
    ) -> DataProviderType:
        if isinstance(provider_type, DataProviderType):
            return provider_type
        value = provider_type.strip().lower().replace("-", "_")
        try:
            return DataProviderType(value)
        except ValueError:
            try:
                return self.TYPE_ALIASES[value]
            except KeyError as exc:
                choices = ", ".join(item.value for item in DataProviderType)
                raise InvalidOperationError(
                    f"unknown provider type: {provider_type}; choices: {choices}"
                ) from exc

    def get_provider(
        self,
        provider_type: DataProviderType | str,
    ) -> BaseDataProvider:
        selected_type = self.normalize_type(provider_type)
        namespace = self._selected_provider_namespaces.get(selected_type)
        if not namespace:
            raise InvalidOperationError(
                f"no provider selected for {selected_type.value}"
            )
        for provider_class in self._provider_classes_by_type[selected_type]:
            if provider_class.namespace == namespace:
                provider = provider_class()
                provider.parameter_values = self.get_provider_parameters_values(
                    provider.namespace
                )
                return provider
        raise InvalidOperationError(
            f"unknown provider: {selected_type.value}/{namespace}"
        )

    def get_aweme_provider(
        self,
        platform: Platform | str,
        source_url: str = "",
    ) -> BaseDataProvider:
        return self.get_provider(self.type_for_aweme(platform, source_url))

    def get_comment_provider(
        self,
        platform: Platform | str,
        source_url: str = "",
    ) -> BaseDataProvider:
        return self.get_provider(self.type_for_comments(platform, source_url))

    def providers(
        self,
        provider_type: DataProviderType | str | None = None,
        *,
        include_status: bool = True,
    ) -> list[dict[str, object]]:
        selected_type = self.normalize_type(provider_type) if provider_type else None
        providers = self._list_metadata(selected_type)
        if not include_status:
            return providers
        status_cache: dict[str, dict[str, Any]] = {}
        values: list[dict[str, Any]] = []
        for item in providers:
            namespace = str(item["namespace"])
            status = status_cache.get(namespace)
            if status is None:
                provider = self.get_namespace(namespace)
                status = self.refresh_provider_status(provider).model_dump()
                status_cache[namespace] = status
            values.append({**status, **item})
        return values

    def catalog(
        self,
        provider_type: DataProviderType | str | None = None,
        *,
        include_status: bool = True,
        include_configuration: bool = False,
    ) -> dict[str, object]:
        if include_configuration and self.config is not None:
            self.config.refresh()
        selected_type = self.normalize_type(provider_type) if provider_type else None
        selected = self.selected()
        providers = self.providers(
            selected_type,
            include_status=include_status,
        )
        if include_configuration:
            configuration_cache: dict[str, dict[str, Any]] = {}
            for provider in providers:
                namespace = str(provider["namespace"])
                if namespace not in configuration_cache:
                    configuration_cache[namespace] = (
                        self.get_provider_parameters_values(namespace)
                    )
                provider["configuration"] = dict(
                    configuration_cache[namespace]
                )
        return {
            "type": selected_type.value if selected_type else None,
            "selected": (
                selected.get(selected_type.value) if selected_type else selected
            ),
            "providers": providers,
        }

    def services(self) -> dict[str, object]:
        selected = self.selected()
        return {
            "services": [
                {
                    "type": provider_type.value,
                    "label": self.SERVICE_LABELS[provider_type],
                    "selected": selected.get(provider_type.value),
                    "providers": self.providers(
                        provider_type,
                        include_status=False,
                    ),
                }
                for provider_type in DataProviderType
            ]
        }

    def service_status(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> dict[str, object]:
        if self.config is not None:
            self.config.refresh()
        selected_type = self.normalize_type(provider_type) if provider_type else None
        selected = self.selected()
        status_cache: dict[str, dict[str, object]] = {}
        services: list[dict[str, object]] = []
        types = [selected_type] if selected_type else list(DataProviderType)
        for current_type in types:
            namespace = selected.get(current_type.value)
            item: dict[str, object] = {
                "type": current_type.value,
                "label": self.SERVICE_LABELS[current_type],
                "selected": namespace,
                "provider": None,
                "status": "unconfigured",
                "message": "未选择 Provider",
            }
            if namespace:
                try:
                    provider = self.get(current_type, namespace)
                    provider_status = status_cache.get(provider.namespace)
                    if provider_status is None:
                        provider_status = self.refresh_provider_status(
                            provider
                        ).model_dump()
                        status_cache[provider.namespace] = provider_status
                    item.update(
                        {
                            "provider": provider.metadata(current_type).model_dump(),
                            "status": provider_status["status"],
                            "message": provider_status.get("message"),
                            "details": provider_status.get("details", {}),
                            "checked_at": provider_status.get("checked_at"),
                            "setup_pid": provider_status.get("setup_pid"),
                        }
                    )
                except InvalidOperationError as exc:
                    item["message"] = str(exc)
            services.append(item)
        return {"services": services}

    def status(
        self,
        provider_type: DataProviderType | str,
    ) -> dict[str, object]:
        selected_type = self.normalize_type(provider_type)
        return self.service_status(selected_type)["services"][0]

    def setup(
        self,
        provider_type: DataProviderType | str,
        values: dict[str, Any],
    ) -> dict[str, object]:
        selected_type = self.normalize_type(provider_type)
        provider = self.get_provider(selected_type)
        current_status = self.get_provider_status(provider.namespace)
        if (
            current_status is not None
            and current_status.status == "setting_up"
            and current_status.setup_pid is not None
            and current_status.setup_pid != os.getpid()
            and self._pid_alive(
                current_status.setup_pid,
                current_status.checked_at,
            )
        ):
            raise InvalidOperationError(
                f"provider setup is already running: {provider.namespace}"
            )
        current = dict(provider.parameter_values)
        cleaned = provider.setup_values(values, current=current)
        self.set_provider_parameters(provider.namespace, cleaned)
        provider.parameter_values = dict(cleaned)
        setting_up_status = provider.set_status(
            ProviderStatus(
                status="setting_up",
                message="Provider setup 正在运行",
                setup_pid=os.getpid(),
            )
        )
        self.set_provider_status(provider.namespace, setting_up_status)
        try:
            result = provider.setup().model_dump()
        except Exception as exc:
            error_status = provider.set_status(
                ProviderStatus(
                    status="error",
                    message=str(exc),
                )
            )
            self.set_provider_status(provider.namespace, error_status)
            raise
        status = provider.set_status(provider.refresh_status())
        self.set_provider_status(provider.namespace, status)
        result["status"] = status.model_dump()
        result["type"] = selected_type.value
        result["provider"] = provider.metadata(selected_type).model_dump()
        return result

    def stop_setup(
        self,
        provider_type_or_namespace: DataProviderType | str,
    ) -> dict[str, object]:
        try:
            selected_type = self.normalize_type(provider_type_or_namespace)
        except InvalidOperationError:
            provider = self.get_namespace(str(provider_type_or_namespace))
            provider_type_value = next(
                iter(provider.supported_types),
                DataProviderType.VIDEO_TRANSCRIPTION,
            ).value
        else:
            provider = self.get_provider(selected_type)
            provider_type_value = selected_type.value
        provider_status = self.get_provider_status(provider.namespace)
        pid = provider_status.setup_pid if provider_status is not None else None
        checked_at = (
            provider_status.checked_at
            if provider_status is not None
            else None
        )
        log_path = self._provider_log_path(provider.namespace)
        if not pid or not self._pid_alive(pid, checked_at):
            if provider_status is not None and provider_status.status == "setting_up":
                refreshed = provider.set_status(provider.refresh_status())
                self.set_provider_status(provider.namespace, refreshed)
            return {
                "status": "stopped",
                "setup_pid": None,
                "type": provider_type_value,
            }

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("收到停止 setup 请求\n")
        status_value = self._terminate_setup_process(pid)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"setup 停止状态：{status_value}\n")
        if status_value in {"stopped", "killed"}:
            stopped_status = provider.set_status(
                ProviderStatus(
                    status="unavailable",
                    message="Provider setup 已停止",
                )
            )
            self.set_provider_status(provider.namespace, stopped_status)
        return {
            "status": status_value,
            "setup_pid": pid if status_value == "permission_denied" else None,
            "type": provider_type_value,
        }

    def _provider_log_path(self, namespace: str) -> Path:
        settings = getattr(self.config, "settings", None)
        if settings is None:
            raise InvalidOperationError("provider service config is not bound")
        return Path(settings.app.logs_dir) / f"provider-{namespace}.log"

    @staticmethod
    def _pid_alive(pid: int, checked_at: datetime | None = None) -> bool:
        try:
            process = psutil.Process(pid)
            if checked_at is not None and checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            if (
                checked_at is not None
                and process.create_time() > checked_at.timestamp() + 1
            ):
                return False
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    def _terminate_setup_process(self, pid: int) -> str:
        try:
            root = psutil.Process(pid)
            processes = root.children(recursive=True)
            processes.append(root)
        except psutil.NoSuchProcess:
            return "stopped"
        except psutil.AccessDenied:
            return "permission_denied"
        for process in processes:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                return "permission_denied"
        settings = getattr(self.config, "settings", None)
        grace_seconds = (
            settings.worker.terminate_grace_seconds
            if settings is not None
            else 10
        )
        _, alive = psutil.wait_procs(processes, timeout=grace_seconds)
        if not alive:
            return "stopped"
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                return "permission_denied"
        return "killed"

    def selected_namespace(
        self,
        provider_type: DataProviderType | str,
    ) -> str:
        return self.get_provider(provider_type).namespace

    def select(
        self,
        provider_type: DataProviderType | str,
        namespace: str,
    ) -> dict[str, object]:
        selected_type = self.normalize_type(provider_type)
        namespace = self._normalize_namespace(namespace)
        provider = self.get(selected_type, namespace)
        self._selected_provider_namespaces[selected_type] = provider.namespace
        if self.config is not None:
            self.config.set(
                f"providers.selected.{selected_type.value}",
                provider.namespace,
            )
        return self.catalog(selected_type, include_status=False)

    @staticmethod
    def normalize_platform(
        platform: Platform | str,
        source_url: str = "",
    ) -> Platform:
        if isinstance(platform, Platform):
            return platform
        value = platform.lower().strip()
        host = (urlparse(source_url).hostname or "").lower()
        if value in {"douyin", "www.douyin.com", "v.douyin.com"} or host.endswith(
            "douyin.com"
        ):
            return Platform.DOUYIN
        if value in {
            "xiaohongshu",
            "www.xiaohongshu.com",
            "xhslink.com",
        } or host.endswith(("xiaohongshu.com", "xhslink.com")):
            return Platform.XIAOHONGSHU
        if value in {"wechat_mp", "mp.weixin.qq.com"} or host == "mp.weixin.qq.com":
            return Platform.WECHAT_MP
        if (
            value in {"wechat_channels", "weixin_channels", "weixin.qq.com"}
            or host == "weixin.qq.com"
        ):
            return Platform.WECHAT_CHANNELS
        try:
            return Platform(value)
        except ValueError as exc:
            raise InvalidOperationError(f"unsupported platform: {platform}") from exc

    @classmethod
    def type_for_aweme(
        cls,
        platform: Platform | str,
        source_url: str = "",
    ) -> DataProviderType:
        selected = cls.normalize_platform(platform, source_url)
        mapping = {
            Platform.DOUYIN: DataProviderType.DOUYIN_AWEME_COLLECT,
            Platform.XIAOHONGSHU: DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
            Platform.WECHAT_CHANNELS: (
                DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT
            ),
            Platform.WECHAT_MP: DataProviderType.WECHAT_MP_AWEME_COLLECT,
        }
        return mapping[selected]

    @classmethod
    def type_for_comments(
        cls,
        platform: Platform | str,
        source_url: str = "",
    ) -> DataProviderType:
        selected = cls.normalize_platform(platform, source_url)
        if selected == Platform.XIAOHONGSHU:
            return DataProviderType.XIAOHONGSHU_COMMENT_COLLECT
        raise InvalidOperationError(
            f"unsupported comment provider platform: {selected}"
        )


def build_data_provider_service(
    *,
    config: Any = None,
    **_: Any,
) -> DataProviderService:
    selected: dict[str, str] = {}
    if config is not None:
        try:
            configured = config.get("providers.selected")
        except InvalidOperationError:
            configured = {}
        if isinstance(configured, dict):
            selected = {
                str(provider_type): str(namespace)
                for provider_type, namespace in configured.items()
            }
    return DataProviderService(
        list(registered_data_provider_classes()),
        selected=selected,
        config=config,
    )
