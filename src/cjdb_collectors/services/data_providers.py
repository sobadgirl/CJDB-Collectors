from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from importlib import import_module
from inspect import isabstract
from pkgutil import iter_modules
from threading import RLock
from typing import Any
from typing import TypeVar
from urllib.parse import urlparse

import psutil
from sqlmodel import select

from cjdb_collectors.domains.data_provider import (
    BaseDataProvider,
    DataProviderType,
    DouyinAccountProviderMixin,
    DouyinAwemeProviderMixin,
    DouyinCommentProviderMixin,
    ProviderMetadata,
    SetupResult,
    ProviderStatus,
    VideoTranscriptionProviderMixin,
    WeChatChannelsAccountProviderMixin,
    WeChatChannelsAwemeProviderMixin,
    WeChatChannelsCommentProviderMixin,
    WeChatMpAwemeProviderMixin,
    WeChatMpAccountProviderMixin,
    WeChatMpCommentProviderMixin,
    XiaohongshuAwemeProviderMixin,
    XiaohongshuAccountProviderMixin,
    XiaohongshuCommentProviderMixin,
)
from cjdb_collectors.models import Platform
from cjdb_collectors.models import ProjectProvider, ProjectProviderSelection, Provider
from cjdb_collectors.domains.provider import ProviderRegistry

from .base import InvalidOperationError, SessionFactory, as_uuid
from .logger import LoggerService, LogType


_REQUIRED_CAPABILITY = {
    DataProviderType.DOUYIN_AWEME_COLLECT: DouyinAwemeProviderMixin,
    DataProviderType.XIAOHONGSHU_AWEME_COLLECT: XiaohongshuAwemeProviderMixin,
    DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT: WeChatChannelsAwemeProviderMixin,
    DataProviderType.WECHAT_MP_AWEME_COLLECT: WeChatMpAwemeProviderMixin,
    DataProviderType.DOUYIN_COMMENT_COLLECT: DouyinCommentProviderMixin,
    DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: XiaohongshuCommentProviderMixin,
    DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT: WeChatChannelsCommentProviderMixin,
    DataProviderType.WECHAT_MP_COMMENT_COLLECT: WeChatMpCommentProviderMixin,
    DataProviderType.DOUYIN_ACCOUNT_COLLECT: DouyinAccountProviderMixin,
    DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT: XiaohongshuAccountProviderMixin,
    DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT: WeChatChannelsAccountProviderMixin,
    DataProviderType.WECHAT_MP_ACCOUNT_COLLECT: WeChatMpAccountProviderMixin,
    DataProviderType.VIDEO_TRANSCRIPTION: VideoTranscriptionProviderMixin,
}
_PLATFORM_BY_TYPE = {
    DataProviderType.DOUYIN_AWEME_COLLECT: Platform.DOUYIN,
    DataProviderType.XIAOHONGSHU_AWEME_COLLECT: Platform.XIAOHONGSHU,
    DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT: Platform.WECHAT_CHANNELS,
    DataProviderType.WECHAT_MP_AWEME_COLLECT: Platform.WECHAT_MP,
    DataProviderType.DOUYIN_COMMENT_COLLECT: Platform.DOUYIN,
    DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: Platform.XIAOHONGSHU,
    DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT: Platform.WECHAT_CHANNELS,
    DataProviderType.WECHAT_MP_COMMENT_COLLECT: Platform.WECHAT_MP,
    DataProviderType.DOUYIN_ACCOUNT_COLLECT: Platform.DOUYIN,
    DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT: Platform.XIAOHONGSHU,
    DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT: Platform.WECHAT_CHANNELS,
    DataProviderType.WECHAT_MP_ACCOUNT_COLLECT: Platform.WECHAT_MP,
}
_PROVIDER_PACKAGE = "cjdb_collectors.domains.data_provider.providers"
_REGISTERED_PROVIDER_CLASSES: dict[str, type[BaseDataProvider]] = {}
_LEGACY_PROVIDER_NAMESPACES = {
    "faster-whisper": "faster_whisper",
}
ProviderClass = TypeVar("ProviderClass", bound=type[BaseDataProvider])
_V1_HIDDEN_DATA_PROVIDER_TYPES = {
    # V1.0 发布隐藏：评论采集和账号/作者采集不参与 Provider 列表/状态选择。
    DataProviderType.DOUYIN_COMMENT_COLLECT,
    DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
    DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT,
    DataProviderType.WECHAT_MP_COMMENT_COLLECT,
    DataProviderType.DOUYIN_ACCOUNT_COLLECT,
    DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
    DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
    DataProviderType.WECHAT_MP_ACCOUNT_COLLECT,
}
_DATA_PROVIDER_TYPES = tuple(
    provider_type
    for provider_type in DataProviderType
    if not provider_type.value.startswith("store_")
    and provider_type not in _V1_HIDDEN_DATA_PROVIDER_TYPES
)


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
        DataProviderType.DOUYIN_COMMENT_COLLECT: "抖音评论采集",
        DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: "小红书评论下载",
        DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT: "视频号评论采集",
        DataProviderType.WECHAT_MP_COMMENT_COLLECT: "公众号评论采集",
        DataProviderType.DOUYIN_ACCOUNT_COLLECT: "抖音账号采集",
        DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT: "小红书账号采集",
        DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT: "视频号账号采集",
        DataProviderType.WECHAT_MP_ACCOUNT_COLLECT: "公众号账号采集",
        DataProviderType.VIDEO_TRANSCRIPTION: "视频文字转写",
    }
    TYPE_ALIASES = {
        "douyin": DataProviderType.DOUYIN_AWEME_COLLECT,
        "xiaohongshu": DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        "xhs": DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        "wechat_channels": DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
        "wechat_mp": DataProviderType.WECHAT_MP_AWEME_COLLECT,
        "comments": DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
        "douyin_comments": DataProviderType.DOUYIN_COMMENT_COLLECT,
        "xiaohongshu_comments": DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
        "wechat_channels_comments": DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT,
        "wechat_mp_comments": DataProviderType.WECHAT_MP_COMMENT_COLLECT,
        "douyin_account": DataProviderType.DOUYIN_ACCOUNT_COLLECT,
        "xiaohongshu_account": DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
        "xhs_account": DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
        "wechat_channels_account": DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
        "wechat_mp_account": DataProviderType.WECHAT_MP_ACCOUNT_COLLECT,
        "transcription": DataProviderType.VIDEO_TRANSCRIPTION,
    }

    def __init__(
        self,
        providers: list[type[BaseDataProvider]],
        selected: dict[str, str] | None = None,
        settings: Any = None,
        session_factory: SessionFactory | None = None,
        logger_service: LoggerService | None = None,
    ) -> None:
        self._provider_classes = list(providers)
        self._provider_classes_by_type: dict[
            DataProviderType,
            list[type[BaseDataProvider]],
        ] = {provider_type: [] for provider_type in _DATA_PROVIDER_TYPES}
        namespaces: set[str] = set()
        for provider_class in self._provider_classes:
            if provider_class.namespace in namespaces:
                raise InvalidOperationError(
                    f"provider namespace conflict: {provider_class.namespace}"
                )
            namespaces.add(provider_class.namespace)
            for provider_type in provider_class.supported_types:
                selected_type = DataProviderType(provider_type)
                if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
                    continue
                self._provider_classes_by_type[selected_type].append(provider_class)
        self.registry = ProviderRegistry(self._provider_classes)
        self._selected_provider_namespaces = {
            self.normalize_type(provider_type): self._normalize_namespace(namespace)
            for provider_type, namespace in (selected or {}).items()
        }
        self.settings = settings
        self._session = session_factory
        self.logger_service = logger_service
        self._provider_statuses: dict[str, ProviderStatus] = {}
        self._provider_instances: dict[str, BaseDataProvider] = {}
        self._provider_instance_lock = RLock()
        self._ensure_provider_records()

    def _ensure_provider_records(self) -> None:
        if self._session is None:
            return
        selected_namespaces = set(self._selected_provider_namespaces.values())
        with self._session() as session:
            existing = {
                item.namespace: item
                for item in session.exec(select(Provider).order_by(Provider.created_at)).all()
            }
            for provider_class in self._provider_classes:
                namespace = self._normalize_namespace(provider_class.namespace)
                if namespace in existing:
                    record = existing[namespace]
                    if not record.name:
                        record.name = provider_class.name
                        session.add(record)
                    continue
                payload: dict[str, Any] = {}
                if self.settings is not None:
                    try:
                        legacy = self.settings.get(f"providers.{namespace}")
                    except InvalidOperationError:
                        legacy = {}
                    if isinstance(legacy, dict):
                        payload = dict(legacy)
                session.add(
                    Provider(
                        namespace=namespace,
                        name=provider_class.name,
                        setup_payload_json=payload,
                        selected=namespace in selected_namespaces,
                    )
                )

    def create_instance(
        self,
        namespace: str,
        *,
        name: str,
        project_id: str,
        values: dict[str, Any],
    ) -> Provider:
        record, _setup_result = self.create_instance_with_setup_result(
            namespace,
            name=name,
            project_id=project_id,
            values=values,
        )
        return record

    def create_instance_with_setup_result(
        self,
        namespace: str,
        *,
        name: str,
        project_id: str,
        values: dict[str, Any],
    ) -> tuple[Provider, dict[str, Any]]:
        normalized = self._normalize_namespace(namespace)
        provider_class = next(
            (
                item
                for item in self._provider_classes
                if item.namespace == normalized
            ),
            None,
        )
        if provider_class is None:
            raise InvalidOperationError(f"unknown provider namespace: {namespace}")
        with self._session() as session:
            record = Provider(
                namespace=normalized,
                name=name.strip() or provider_class.name,
                status="setting_up",
                status_message="Provider setup 正在运行",
            )
            session.add(record)
            session.flush()
            session.add(
                ProjectProvider(
                    project_id=as_uuid(project_id),
                    provider_id=record.id,
                )
            )
            session.flush()
            session.refresh(record)
            provider_id = record.id
        provider = provider_class(
            setup_payload={},
            logger=(
                self.logger_service.get_logger(LogType.PROVIDER_SETUP, record)
                if self.logger_service is not None
                else None
            ),
        )
        log_path = self._provider_instance_setup_log_path(record)
        self._write_setup_log(log_path, f"开始设置 Provider：{record.name or normalized}")
        try:
            with self._setup_log_redirect(log_path):
                params = provider.parse_setup_params(values, current={})
                provider.logger.info("开始执行 setup")
                result = provider.setup(params)
        except Exception as exc:
            result = SetupResult(success=False, message=str(exc))
        finally:
            provider.close()
        self._write_setup_result_log(log_path, result)
        if not isinstance(result, SetupResult) or not result.success:
            message = getattr(result, "message", None) or "Provider setup failed"
            self._discard_provider_record(provider_id)
            raise InvalidOperationError(message)
        runtime = provider_class(
            setup_payload=dict(result.setup_payload),
            logger=(
                self.logger_service.get_logger(LogType.PROVIDER_RUNTIME, record)
                if self.logger_service is not None
                else None
            ),
        )
        try:
            status = runtime.refresh_status()
        except Exception as exc:
            status = ProviderStatus(status="error", message=str(exc))
        finally:
            runtime.close()
        checked_at = status.checked_at or datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(Provider, provider_id)
            if record is None:
                raise InvalidOperationError("provider not found")
            record.setup_payload_json = dict(result.setup_payload)
            record.status = status.status
            record.status_message = self._setup_status_message(result, status)
            record.status_payload_json = dict(status.details)
            record.last_checked_at = checked_at
            record.next_check_at = checked_at + timedelta(
                seconds=provider_class.status_refresh_seconds
            )
            session.add(record)
            session.flush()
            session.refresh(record)
            return record, result.model_dump()

    def setup_instance(
        self,
        provider_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        if self._session is None:
            raise InvalidOperationError("provider persistence is not bound")
        with self._session() as session:
            record = session.get(Provider, as_uuid(provider_id))
            if record is None:
                raise InvalidOperationError("provider not found")
            current_payload = dict(record.setup_payload_json or {})
            namespace = record.namespace
        provider_class = next(
            (item for item in self._provider_classes if item.namespace == namespace),
            None,
        )
        if provider_class is None:
            raise InvalidOperationError(f"unknown provider namespace: {namespace}")
        provider = provider_class(
            setup_payload=current_payload,
            logger=(
                self.logger_service.get_logger(LogType.PROVIDER_SETUP, record)
                if self.logger_service is not None
                else None
            ),
        )
        log_path = self._provider_instance_setup_log_path(record)
        self._write_setup_log(log_path, f"开始设置 Provider：{record.name or namespace}")
        try:
            with self._setup_log_redirect(log_path):
                params = provider.parse_setup_params(values, current=current_payload)
                provider.logger.info("开始执行 setup")
                result = provider.setup(params)
        except Exception as exc:
            result = SetupResult(success=False, message=str(exc))
        finally:
            provider.close()
        self._write_setup_result_log(log_path, result)
        checked_at = datetime.now(timezone.utc)
        with self._session() as session:
            record = session.get(Provider, as_uuid(provider_id))
            if record is None:
                raise InvalidOperationError("provider not found")
            if result.success:
                persisted_payload = {**dict(result.setup_payload), **params}
                record.setup_payload_json = persisted_payload
                runtime = provider_class(
                    setup_payload=record.setup_payload_json,
                    logger=(
                        self.logger_service.get_logger(LogType.PROVIDER_RUNTIME, record)
                        if self.logger_service is not None
                        else None
                    ),
                )
                try:
                    status = runtime.refresh_status()
                except Exception as exc:
                    status = ProviderStatus(status="error", message=str(exc))
                finally:
                    runtime.close()
                record.status = status.status
                record.status_message = self._setup_status_message(result, status)
                record.status_payload_json = dict(status.details)
            else:
                record.status = "error"
                record.status_message = result.message or "Provider setup failed"
            record.last_checked_at = checked_at
            record.next_check_at = checked_at + timedelta(
                seconds=provider_class.status_refresh_seconds
            )
            session.add(record)
        return result.model_dump()

    def status_instance(self, provider_id: str, *, refresh: bool = False) -> dict[str, Any]:
        if self._session is None:
            raise InvalidOperationError("provider persistence is not bound")
        with self._session() as session:
            record = session.get(Provider, as_uuid(provider_id))
            if record is None:
                raise InvalidOperationError("provider not found")
            namespace = record.namespace
            payload = dict(record.setup_payload_json or {})
        provider_class = next(
            (item for item in self._provider_classes if item.namespace == namespace),
            None,
        )
        if provider_class is None:
            raise InvalidOperationError(f"unknown provider namespace: {namespace}")
        if refresh:
            runtime = provider_class(
                setup_payload=payload,
                logger=(
                    self.logger_service.get_logger(LogType.PROVIDER_RUNTIME, record)
                    if self.logger_service is not None
                    else None
                ),
            )
            try:
                state = runtime.refresh_status()
            except Exception as exc:
                state = ProviderStatus(status="error", message=str(exc))
            finally:
                runtime.close()
            checked_at = state.checked_at or datetime.now(timezone.utc)
            with self._session() as session:
                current = session.get(Provider, as_uuid(provider_id))
                if current is not None:
                    current.status = state.status
                    current.status_message = state.message or current.status_message
                    current.status_payload_json = dict(state.details)
                    current.last_checked_at = checked_at
                    current.next_check_at = checked_at + timedelta(
                        seconds=provider_class.status_refresh_seconds
                    )
                    session.add(current)
            record = self._provider_record_by_id(provider_id)
        return {
            "provider": record.model_dump(mode="json"),
            "status": record.status,
            "ready": record.status == "ready",
            "message": record.status_message,
            "details": dict(record.status_payload_json or {}),
            "checked_at": record.last_checked_at,
        }

    def _provider_record_by_id(self, provider_id: str) -> Provider:
        with self._session() as session:
            record = session.get(Provider, as_uuid(provider_id))
            if record is None:
                raise InvalidOperationError("provider not found")
            return record

    def _discard_provider_record(self, provider_id: str) -> None:
        with self._session() as session:
            selected_id = as_uuid(provider_id)
            for relation in session.exec(
                select(ProjectProvider).where(ProjectProvider.provider_id == selected_id)
            ).all():
                session.delete(relation)
            record = session.get(Provider, selected_id)
            if record is not None:
                session.delete(record)

    def _provider_record(
        self,
        namespace: str,
        *,
        selected_first: bool = True,
    ) -> Provider | None:
        if self._session is None:
            return None
        namespace = self._normalize_namespace(namespace)
        with self._session() as session:
            statement = select(Provider).where(Provider.namespace == namespace)
            if selected_first:
                statement = statement.order_by(
                    Provider.selected.desc(),
                    Provider.created_at,
                )
            else:
                statement = statement.order_by(Provider.created_at)
            return session.exec(statement.limit(1)).first()

    def close(self) -> None:
        with self._provider_instance_lock:
            providers = list(self._provider_instances.values())
            self._provider_instances.clear()
        for provider in providers:
            provider.close()

    def selected(self) -> dict[str, str]:
        if self._session is not None:
            with self._session() as session:
                rows = session.exec(
                    select(ProjectProviderSelection.provider_type, Provider.namespace)
                    .join(
                        Provider,
                        Provider.id == ProjectProviderSelection.provider_id,
                    )
                    .order_by(ProjectProviderSelection.created_at)
                ).all()
            values: dict[str, str] = {}
            for provider_type, namespace in rows:
                values.setdefault(provider_type, namespace)
            for provider_type, namespace in self._selected_provider_namespaces.items():
                values.setdefault(provider_type.value, namespace)
            return values
        return {
            provider_type.value: namespace
            for provider_type, namespace in self._selected_provider_namespaces.items()
        }

    @staticmethod
    def _normalize_namespace(namespace: str) -> str:
        return _LEGACY_PROVIDER_NAMESPACES.get(namespace, namespace)

    def _create_provider(
        self,
        provider_class: type[BaseDataProvider],
        setup_payload: dict[str, Any] | None = None,
        *,
        cache_key: str | None = None,
        logger_instance: Any = None,
    ) -> BaseDataProvider:
        namespace = self._normalize_namespace(provider_class.namespace)
        instance_key = cache_key or namespace
        values = (
            setup_payload
            if setup_payload is not None
            else self.get_provider_setup_payload(namespace)
        )
        provider = provider_class(
            setup_payload=values,
            logger=(
                self.logger_service.get_logger(
                    LogType.PROVIDER_RUNTIME,
                    logger_instance or provider_class.namespace,
                )
                if self.logger_service is not None
                else None
            ),
        )
        with self._provider_instance_lock:
            cached = self._provider_instances.get(instance_key)
            if cached is not None and cached.setup_payload == provider.setup_payload:
                return cached
            if cached is not None:
                cached.close()
            self._provider_instances[instance_key] = provider
            return provider

    def _replace_provider_instance(
        self,
        provider_class: type[BaseDataProvider],
        setup_payload: dict[str, Any],
    ) -> BaseDataProvider:
        namespace = self._normalize_namespace(provider_class.namespace)
        with self._provider_instance_lock:
            cached = self._provider_instances.pop(namespace, None)
            if cached is not None:
                cached.close()
            provider = provider_class(
                setup_payload=setup_payload,
                logger=(
                    self.logger_service.get_logger(
                        LogType.PROVIDER_RUNTIME,
                        namespace,
                    )
                    if self.logger_service is not None
                    else None
                ),
            )
            self._provider_instances[namespace] = provider
            return provider

    def get_namespace(self, namespace: str) -> BaseDataProvider:
        namespace = self._normalize_namespace(namespace)
        for provider_class in self._provider_classes:
            if provider_class.namespace == namespace:
                return self._create_provider(provider_class)
        raise InvalidOperationError(f"unknown provider namespace: {namespace}")

    def get(
        self,
        provider_type: DataProviderType | str,
        namespace: str,
    ) -> BaseDataProvider:
        selected_type = DataProviderType(provider_type)
        if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
            raise InvalidOperationError(
                f"provider type hidden for V1.0 release: {selected_type.value}"
            )
        namespace = self._normalize_namespace(namespace)
        for provider_class in self._provider_classes_by_type.get(selected_type, []):
            if provider_class.namespace == namespace:
                return self._create_provider(provider_class)
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
        platform = _PLATFORM_BY_TYPE.get(provider_type)
        return ProviderMetadata(
            namespace=provider_class.namespace,
            name=provider_class.name,
            type=provider_type,
            platforms=[platform.value] if platform else [],
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
        if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
            return []
        provider_types = [selected_type] if selected_type else list(_DATA_PROVIDER_TYPES)
        values: list[dict[str, object]] = []
        for current_type in provider_types:
            for provider_class in self._provider_classes_by_type.get(current_type, []):
                values.append(self._metadata(provider_class, current_type))
        return values

    def get_provider_setup_payload(self, namespace: str) -> dict[str, Any]:
        record = self._provider_record(namespace)
        if record is not None:
            return dict(record.setup_payload_json or {})
        if self.settings is None:
            return {}
        try:
            values = self.settings.get(f"providers.{namespace}")
        except InvalidOperationError:
            return {}
        return dict(values) if isinstance(values, dict) else {}

    def set_provider_setup_payload(
        self,
        namespace: str,
        values: dict[str, Any],
    ) -> None:
        if self._session is not None:
            record = self._provider_record(namespace)
            if record is None:
                raise InvalidOperationError(f"unknown provider namespace: {namespace}")
            with self._session() as session:
                current = session.get(Provider, record.id)
                if current is None:
                    raise InvalidOperationError(f"unknown provider namespace: {namespace}")
                current.setup_payload_json = dict(values)
                session.add(current)
            return
        if self.settings is None:
            raise InvalidOperationError("provider settings is not bound")
        self.settings.set(f"providers.{namespace}", dict(values))

    def get_provider_status(self, namespace: str) -> ProviderStatus | None:
        namespace = self._normalize_namespace(namespace)
        record = self._provider_record(namespace)
        if record is not None:
            return ProviderStatus(
                status=record.status,
                message=record.status_message,
                details=dict(record.status_payload_json or {}),
                checked_at=record.last_checked_at,
                setup_pid=record.setup_pid,
            )
        settings = getattr(self.settings, "settings", None)
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
        record = self._provider_record(namespace)
        if record is not None and self._session is not None:
            refresh_seconds = next(
                (
                    provider_class.status_refresh_seconds
                    for provider_class in self._provider_classes
                    if provider_class.namespace == namespace
                ),
                30,
            )
            with self._session() as session:
                current = session.get(Provider, record.id)
                if current is not None:
                    current.status = status.status
                    current.status_message = status.message or current.status_message
                    current.status_payload_json = dict(status.details)
                    current.setup_pid = status.setup_pid
                    current.last_checked_at = status.checked_at
                    current.next_check_at = status.checked_at + timedelta(
                        seconds=refresh_seconds
                    )
                    session.add(current)
            return status
        settings = getattr(self.settings, "settings", None)
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
        *,
        refresh: bool = False,
    ) -> ProviderStatus:
        current = None if refresh else self.get_provider_status(provider.namespace)
        if refresh:
            provider.set_status(
                ProviderStatus(
                    status="unknown",
                    checked_at=datetime.fromtimestamp(0, tz=timezone.utc),
                )
            )
        status = provider.status(current)
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
                choices = ", ".join(item.value for item in _DATA_PROVIDER_TYPES)
                raise InvalidOperationError(
                    f"unknown provider type: {provider_type}; choices: {choices}"
                ) from exc

    def get_provider(
        self,
        provider_type: DataProviderType | str,
        project_id: str | None = None,
    ) -> BaseDataProvider:
        selected_type = self.normalize_type(provider_type)
        if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
            raise InvalidOperationError(
                f"provider type hidden for V1.0 release: {selected_type.value}"
            )
        if self._session is not None:
            with self._session() as session:
                statement = (
                    select(Provider)
                    .join(
                        ProjectProviderSelection,
                        ProjectProviderSelection.provider_id == Provider.id,
                    )
                    .where(
                        ProjectProviderSelection.provider_type
                        == selected_type.value,
                    )
                    .order_by(
                        ProjectProviderSelection.created_at,
                        Provider.created_at,
                    )
                    .limit(1)
                )
                if project_id is not None:
                    statement = statement.where(
                        ProjectProviderSelection.project_id == as_uuid(project_id)
                    )
                record = session.exec(statement).first()
            if record is not None:
                provider_class = self.registry.get(record.namespace)
                if selected_type not in provider_class.supported_types:
                    raise InvalidOperationError(
                        f"provider {record.namespace} does not support "
                        f"{selected_type.value}"
                    )
                return self._create_provider(
                    provider_class,
                    dict(record.setup_payload_json or {}),
                    cache_key=str(record.id),
                    logger_instance=record,
                )
            if project_id is not None:
                raise InvalidOperationError(
                    f"no provider selected for {selected_type.value}"
                )
        namespace = self._selected_provider_namespaces.get(selected_type)
        if not namespace:
            raise InvalidOperationError(
                f"no provider selected for {selected_type.value}"
            )
        for provider_class in self._provider_classes_by_type.get(selected_type, []):
            if provider_class.namespace == namespace:
                return self._create_provider(provider_class)
        raise InvalidOperationError(
            f"unknown provider: {selected_type.value}/{namespace}"
        )

    def is_ready(
        self,
        provider_type: DataProviderType | str,
        project_id: str | None = None,
    ) -> bool:
        """Read the selected Provider's persisted status without refreshing it."""
        selected_type = self.normalize_type(provider_type)
        if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
            return False
        if self._session is not None:
            with self._session() as session:
                statement = (
                    select(Provider)
                    .join(
                        ProjectProviderSelection,
                        ProjectProviderSelection.provider_id == Provider.id,
                    )
                    .where(
                        ProjectProviderSelection.provider_type
                        == selected_type.value,
                    )
                    .order_by(ProjectProviderSelection.created_at)
                    .limit(1)
                )
                if project_id is not None:
                    statement = statement.where(
                        ProjectProviderSelection.project_id == as_uuid(project_id)
                    )
                record = session.exec(statement).first()
            if record is not None:
                return record.status == "ready"
            if project_id is not None:
                return False
        namespace = self._selected_provider_namespaces.get(selected_type)
        if not namespace:
            return False
        status = self.get_provider_status(namespace)
        return status is not None and status.status == "ready"

    def get_aweme_provider(
        self,
        platform: Platform | str,
        source_url: str = "",
        project_id: str | None = None,
    ) -> BaseDataProvider:
        return self.get_provider(
            self.type_for_aweme(platform, source_url),
            project_id,
        )

    def get_comment_provider(
        self,
        platform: Platform | str,
        source_url: str = "",
        project_id: str | None = None,
    ) -> BaseDataProvider:
        return self.get_provider(
            self.type_for_comments(platform, source_url),
            project_id,
        )

    def providers(
        self,
        provider_type: DataProviderType | str | None = None,
        *,
        include_status: bool = True,
        refresh: bool = False,
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
                status = self.refresh_provider_status(
                    provider,
                    refresh=refresh,
                ).model_dump()
                status_cache[namespace] = status
            values.append({**status, **item})
        return values

    def catalog(
        self,
        provider_type: DataProviderType | str | None = None,
        *,
        include_status: bool = True,
        include_setup_payload: bool = False,
    ) -> dict[str, object]:
        if include_setup_payload and self.settings is not None:
            self.settings.refresh()
        selected_type = self.normalize_type(provider_type) if provider_type else None
        selected = self.selected()
        providers = self.providers(
            selected_type,
            include_status=include_status,
        )
        if include_setup_payload:
            setup_payload_cache: dict[str, dict[str, Any]] = {}
            for provider in providers:
                namespace = str(provider["namespace"])
                if namespace not in setup_payload_cache:
                    raw_payload = self.get_provider_setup_payload(namespace)
                    provider_class = self.registry.get(namespace)
                    setup_payload_cache[namespace] = provider_class.clean_params_value(
                        provider_class.parameters,
                        raw_payload,
                        current=raw_payload,
                    )
                provider["setup_payload"] = dict(
                    setup_payload_cache[namespace]
                )
        return {
            "type": selected_type.value if selected_type else None,
            "selected": (
                selected.get(selected_type.value) if selected_type else selected
            ),
            "providers": providers,
        }

    def catalog_for_types(
        self,
        provider_types: list[DataProviderType | str]
        | tuple[DataProviderType | str, ...],
        *,
        include_status: bool = True,
    ) -> dict[str, object]:
        selected_types = [self.normalize_type(value) for value in provider_types]
        providers: list[dict[str, object]] = []
        for provider_type in selected_types:
            providers.extend(
                self.providers(
                    provider_type,
                    include_status=include_status,
                )
            )
        selected = self.selected()
        return {
            "type": None,
            "types": [item.value for item in selected_types],
            "selected": {
                item.value: selected.get(item.value) for item in selected_types
            },
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
                for provider_type in _DATA_PROVIDER_TYPES
            ]
        }

    def service_status(
        self,
        provider_type: DataProviderType | str | None = None,
        *,
        refresh: bool = False,
    ) -> dict[str, object]:
        if self.settings is not None:
            self.settings.refresh()
        selected_type = self.normalize_type(provider_type) if provider_type else None
        selected = self.selected()
        status_cache: dict[str, dict[str, object]] = {}
        services: list[dict[str, object]] = []
        types = [selected_type] if selected_type else list(_DATA_PROVIDER_TYPES)
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
                            provider,
                            refresh=refresh,
                        ).model_dump()
                        status_cache[provider.namespace] = provider_status
                    item.update(
                        {
                            "provider": provider.metadata(current_type).model_dump(),
                            "status": provider_status["status"],
                            "ready": provider_status.get("ready", False),
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
        *,
        refresh: bool = False,
    ) -> dict[str, object]:
        selected_type = self.normalize_type(provider_type)
        return self.service_status(selected_type, refresh=refresh)["services"][0]

    def setup(
        self,
        provider_type: DataProviderType | str,
        values: dict[str, Any],
    ) -> dict[str, object]:
        selected_type = self.normalize_type(provider_type)
        provider = self.get_provider(selected_type)
        self.assert_provider_config_mutable(provider.namespace, current_pid=os.getpid())
        current_payload = dict(provider.setup_payload)
        setup_params = provider.parse_setup_params(values, current=current_payload)
        provider = type(provider)(
            setup_payload=current_payload,
            logger=(
                self.logger_service.get_logger(
                    LogType.PROVIDER_SETUP,
                    provider.namespace,
                )
                if self.logger_service is not None
                else None
            ),
        )
        setting_up_status = provider.set_status(
            ProviderStatus(
                status="setting_up",
                message="Provider setup 正在运行",
                setup_pid=os.getpid(),
            )
        )
        self.set_provider_status(provider.namespace, setting_up_status)
        try:
            setup_result = provider.setup(setup_params)
        except Exception as exc:
            setup_result = SetupResult(
                success=False,
                message=str(exc),
            )
        if not isinstance(setup_result, SetupResult):
            setup_result = SetupResult(
                success=False,
                message="Provider setup did not return SetupResult",
            )
        if setup_result.success:
            persisted_payload = {**dict(setup_result.setup_payload), **setup_params}
            self.set_provider_setup_payload(
                provider.namespace,
                persisted_payload,
            )
            provider.close()
            provider = self._replace_provider_instance(
                type(provider),
                persisted_payload,
            )
            try:
                status = provider.set_status(provider.refresh_status())
            except Exception as exc:
                status = provider.set_status(
                    ProviderStatus(status="error", message=str(exc))
                )
            status = replace(
                status,
                message=self._setup_status_message(setup_result, status),
            )
            provider.set_status(status)
        else:
            status = provider.set_status(
                ProviderStatus(
                    status="error",
                    message=setup_result.message or "Provider setup failed",
                )
            )
            provider.close()
        self.set_provider_status(provider.namespace, status)
        return setup_result.model_dump()

    def provider_setup_running(
        self,
        provider_type_or_namespace: DataProviderType | str,
        *,
        current_pid: int | None = None,
    ) -> bool:
        try:
            provider = self.get_provider(self.normalize_type(provider_type_or_namespace))
        except InvalidOperationError:
            provider = self.get_namespace(str(provider_type_or_namespace))
        current_status = self.get_provider_status(provider.namespace)
        if (
            current_status is None
            or current_status.status != "setting_up"
            or current_status.setup_pid is None
        ):
            return False
        if current_pid is not None and current_status.setup_pid == current_pid:
            return False
        return self._pid_alive(
            current_status.setup_pid,
            current_status.checked_at,
        )

    def assert_provider_config_mutable(
        self,
        provider_type_or_namespace: DataProviderType | str,
        *,
        current_pid: int | None = None,
    ) -> None:
        if self.provider_setup_running(
            provider_type_or_namespace,
            current_pid=current_pid,
        ):
            raise InvalidOperationError(
                f"provider setup is already running: {provider_type_or_namespace}"
            )

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
        log_path = self._provider_setup_log_path(provider.namespace)
        if not pid or not self._pid_alive(pid, checked_at):
            if provider_status is not None and provider_status.status == "setting_up":
                refreshed = provider.set_status(provider.refresh_status())
                self.set_provider_status(provider.namespace, refreshed)
            return {
                "status": "stopped",
                "setup_pid": None,
                "type": provider_type_value,
            }

        self.logger_service.append_line(log_path, "收到停止 setup 请求")
        status_value = self._terminate_setup_process(pid)
        self.logger_service.append_line(log_path, f"setup 停止状态：{status_value}")
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
        if self.logger_service is not None:
            return self.logger_service.get_log_path(
                LogType.PROVIDER_RUNTIME,
                namespace,
            )
        raise InvalidOperationError("logger service is not bound")

    def _provider_setup_log_path(self, namespace: str) -> Path:
        if self.logger_service is not None:
            return self.logger_service.get_log_path(LogType.PROVIDER_SETUP, namespace)
        raise InvalidOperationError("logger service is not bound")

    def _provider_instance_log_path(self, provider: Provider | str) -> Path | None:
        if self.logger_service is not None:
            return self.logger_service.get_log_path(
                LogType.PROVIDER_RUNTIME,
                provider,
            )
        return None

    def _provider_instance_setup_log_path(
        self,
        provider: Provider | str,
    ) -> Path | None:
        if self.logger_service is not None:
            return self.logger_service.get_log_path(LogType.PROVIDER_SETUP, provider)
        return None

    def _write_setup_log(self, log_path: Path | None, message: str) -> None:
        if log_path is None or self.logger_service is None:
            return
        self.logger_service.append_line(log_path, message)

    def _write_setup_result_log(
        self,
        log_path: Path | None,
        result: SetupResult,
    ) -> None:
        status = "成功" if result.success else "失败"
        self._write_setup_log(
            log_path,
            f"设置{status}：{result.message or '无返回消息'}",
        )

    @staticmethod
    def _setup_status_message(
        result: SetupResult,
        status: ProviderStatus,
    ) -> str | None:
        if status.status == "ready":
            return result.message or status.message or "Provider 初始化完成"
        return status.message or result.message

    @contextmanager
    def _setup_log_redirect(self, log_path: Path | None):
        if log_path is None or self.logger_service is None:
            yield
            return
        with self.logger_service.open_text_append(log_path) as handle:
            with redirect_stdout(handle), redirect_stderr(handle):
                yield

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
        settings = getattr(self.settings, "settings", None)
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
        if selected_type in _V1_HIDDEN_DATA_PROVIDER_TYPES:
            raise InvalidOperationError(
                f"provider type hidden for V1.0 release: {selected_type.value}"
            )
        namespace = self._normalize_namespace(namespace)
        current_namespace = self._selected_provider_namespaces.get(selected_type)
        if current_namespace:
            self.assert_provider_config_mutable(current_namespace)
        self.assert_provider_config_mutable(namespace)
        provider = self.get(selected_type, namespace)
        self._selected_provider_namespaces[selected_type] = provider.namespace
        if self._session is not None:
            candidate_namespaces = {
                item.namespace
                for item in self._provider_classes_by_type.get(selected_type, [])
            }
            with self._session() as session:
                records = session.exec(
                    select(Provider).where(
                        Provider.namespace.in_(candidate_namespaces)
                    )
                ).all()
                target_found = False
                for record in records:
                    record.selected = record.namespace == provider.namespace
                    target_found = target_found or record.selected
                    session.add(record)
                if not target_found:
                    raise InvalidOperationError(
                        f"provider is not persisted: {provider.namespace}"
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
        mapping = {
            Platform.DOUYIN: DataProviderType.DOUYIN_COMMENT_COLLECT,
            Platform.XIAOHONGSHU: DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
            Platform.WECHAT_CHANNELS: DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT,
            Platform.WECHAT_MP: DataProviderType.WECHAT_MP_COMMENT_COLLECT,
        }
        return mapping[selected]

    @classmethod
    def type_for_account(
        cls,
        platform: Platform | str,
        source_url: str = "",
    ) -> DataProviderType:
        selected = cls.normalize_platform(platform, source_url)
        mapping = {
            Platform.DOUYIN: DataProviderType.DOUYIN_ACCOUNT_COLLECT,
            Platform.XIAOHONGSHU: DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
            Platform.WECHAT_CHANNELS: DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
            Platform.WECHAT_MP: DataProviderType.WECHAT_MP_ACCOUNT_COLLECT,
        }
        return mapping[selected]


def build_data_provider_service(
    *,
    settings: Any = None,
    session_factory: SessionFactory | None = None,
    logger_service: LoggerService | None = None,
    **_: Any,
) -> DataProviderService:
    return DataProviderService(
        list(registered_data_provider_classes()),
        selected={},
        settings=settings,
        session_factory=session_factory,
        logger_service=logger_service,
    )
