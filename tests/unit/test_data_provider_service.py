from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import time

import pytest
from sqlmodel import Session, select

from cjdb_collectors.domains.data_provider import (
    AwemeData,
    BaseDataProvider,
    DataProviderType,
    DouyinAwemeProviderMixin,
    FetchAwemeRequest,
    SetupResult,
    ProviderStatus,
    XiaohongshuAwemeProviderMixin,
)
from cjdb_collectors.domains.data_provider.providers.tikhub import TikHubProvider
from cjdb_collectors.exceptions import InvalidOperationError
from cjdb_collectors.db import create_db_engine, init_db
from cjdb_collectors.models import ContentType, Project, Provider
from cjdb_collectors.services.data_providers import (
    DataProviderService,
    register_data_provider,
    registered_data_provider_classes,
)
from cjdb_collectors.services.logger import LoggerService, LogType
from cjdb_collectors.services.projects import ProjectService


class MemorySettings:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = dict(values or {})
        self.refresh_count = 0

    def refresh(self) -> None:
        self.refresh_count += 1

    def get(self, dotted_key: str) -> object:
        assert dotted_key == "providers.tikhub"
        return dict(self.values)

    def patch(self, changes: dict[str, object]) -> None:
        for dotted_key, value in changes.items():
            self.values[dotted_key.rsplit(".", 1)[-1]] = value

    def set(self, dotted_key: str, value: object) -> None:
        assert dotted_key == "providers.tikhub"
        assert isinstance(value, dict)
        self.values = dict(value)


class FakeProvider(BaseDataProvider):
    def refresh_status(self) -> ProviderStatus:
        return super().refresh_status()

    def setup(self, params: dict[str, object]) -> SetupResult:
        return SetupResult(success=True, setup_payload=params)

    def fetch_douyin_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        return AwemeData(
            platform_aweme_id=request.platform_aweme_id,
            content_type=ContentType.UNKNOWN,
        )

    def fetch_xiaohongshu_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        return AwemeData(
            platform_aweme_id=request.platform_aweme_id,
            content_type=ContentType.UNKNOWN,
        )


class FakeDouyinProvider(FakeProvider, DouyinAwemeProviderMixin):
    namespace = "local"
    name = "本地采集"
    supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)


def test_project_provider_selection_uses_single_and_multiple_cardinality(
    tmp_path,
) -> None:
    engine = create_db_engine(tmp_path / "project-provider-selection.sqlite")
    init_db(engine)

    @contextmanager
    def session_factory():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    with session_factory() as session:
        project = Project(name="选择关系")
        first = Provider(namespace="first", name="First")
        second = Provider(namespace="second", name="Second")
        session.add(project)
        session.add(first)
        session.add(second)
        session.flush()
        project_id = project.id
        first_id = first.id
        second_id = second.id

    service = ProjectService(session_factory)
    service.bind_provider(project_id, first_id)
    service.bind_provider(project_id, second_id)

    service.select_provider(
        project_id,
        DataProviderType.DOUYIN_AWEME_COLLECT,
        first_id,
    )
    service.select_provider(
        project_id,
        DataProviderType.DOUYIN_AWEME_COLLECT,
        second_id,
    )
    assert service.selected_provider_ids(
        project_id,
        DataProviderType.DOUYIN_AWEME_COLLECT,
    ) == [second_id]

    service.select_provider(project_id, "store_aweme", first_id)
    service.select_provider(project_id, "store_aweme", second_id)
    assert set(
        service.selected_provider_ids(project_id, "store_aweme")
    ) == {first_id, second_id}
    assert service.unselect_provider_type(project_id, "store_aweme") == []
    assert service.selected_provider_ids(project_id, "store_aweme") == []


class FakeXhsProvider(FakeProvider, XiaohongshuAwemeProviderMixin):
    namespace = "tikhub"
    name = "TikHub"
    supported_types = (DataProviderType.XIAOHONGSHU_AWEME_COLLECT,)


def test_provider_selection_payload_and_status_are_persisted_in_database(tmp_path) -> None:
    class OtherDouyinProvider(FakeDouyinProvider):
        namespace = "other"
        name = "Other"

    engine = create_db_engine(tmp_path / "providers.sqlite")
    init_db(engine)

    @contextmanager
    def session_factory():
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    service = DataProviderService(
        [FakeDouyinProvider, OtherDouyinProvider],
        selected={provider_type.value: FakeDouyinProvider.namespace},
        session_factory=session_factory,
    )

    result = service.setup(provider_type, {})

    assert result["success"] is True
    assert service.is_ready(provider_type) is True
    with session_factory() as session:
        records = session.exec(select(Provider).order_by(Provider.namespace)).all()
        assert [(item.namespace, item.selected) for item in records] == [
            ("local", True),
            ("other", False),
        ]
        assert records[0].status == "ready"
        assert records[0].setup_payload_json == {}

    service.select(provider_type, "other")

    with session_factory() as session:
        records = session.exec(select(Provider).order_by(Provider.namespace)).all()
        assert [(item.namespace, item.selected) for item in records] == [
            ("local", False),
            ("other", True),
        ]
    engine.dispose()


def test_provider_catalog_exposes_supported_platforms_and_filters() -> None:
    service = DataProviderService([FakeDouyinProvider, FakeXhsProvider])

    catalog = service.catalog()

    assert catalog["selected"] == {}
    assert {provider["namespace"] for provider in catalog["providers"]} == {
        "local",
        "tikhub",
    }
    assert any(
        provider["namespace"] == "tikhub" and "xiaohongshu" in provider["platforms"]
        for provider in catalog["providers"]
    )

    xiaohongshu_catalog = service.catalog("xiaohongshu")

    assert xiaohongshu_catalog["type"] == "xiaohongshu_aweme_collect"
    assert [provider["namespace"] for provider in xiaohongshu_catalog["providers"]] == [
        "tikhub"
    ]

    page_catalog = service.catalog_for_types(
        [DataProviderType.XIAOHONGSHU_AWEME_COLLECT],
        include_status=False,
    )
    assert page_catalog["types"] == ["xiaohongshu_aweme_collect"]
    assert [item["namespace"] for item in page_catalog["providers"]] == ["tikhub"]


def test_real_provider_catalog_contains_namespace_labels_and_parameters() -> None:
    service = DataProviderService([TikHubProvider])

    catalog = service.catalog("xiaohongshu")
    provider = catalog["providers"][0]

    assert provider["namespace"] == "tikhub"
    assert provider["name"] == "TikHub"
    assert provider["parameters"][0]["key"] == "api_key"
    assert provider["parameters"][0]["type"] == "password"
    assert provider["parameters"][0]["label"] == "接口密钥"


def test_provider_instances_are_cached_by_namespace() -> None:
    service = DataProviderService(
        [TikHubProvider],
        selected={
            DataProviderType.DOUYIN_AWEME_COLLECT.value: "tikhub",
            DataProviderType.XIAOHONGSHU_AWEME_COLLECT.value: "tikhub",
        },
    )

    douyin = service.get_provider(DataProviderType.DOUYIN_AWEME_COLLECT)
    xiaohongshu = service.get_provider(DataProviderType.XIAOHONGSHU_AWEME_COLLECT)

    assert douyin is xiaohongshu
    with pytest.raises(InvalidOperationError):
        service.get_provider(DataProviderType.XIAOHONGSHU_COMMENT_COLLECT)


def test_provider_status_serializes_ready_flag_and_refreshes_once_concurrently() -> None:
    class SlowStatusProvider(FakeProvider):
        namespace = "slow"
        name = "慢状态"
        supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)
        calls = 0

        def refresh_status(self) -> ProviderStatus:
            type(self).calls += 1
            time.sleep(0.05)
            return ProviderStatus(status="ready")

    provider = SlowStatusProvider()

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(lambda _: provider.status(), range(5)))

    assert SlowStatusProvider.calls == 1
    assert all(result.status == "ready" for result in results)
    assert results[0].model_dump()["ready"] is True


def test_provider_service_refresh_status_is_single_flight_per_namespace() -> None:
    class SlowStatusProvider(FakeProvider):
        namespace = "slow"
        name = "慢状态"
        supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)
        calls = 0

        def refresh_status(self) -> ProviderStatus:
            type(self).calls += 1
            time.sleep(0.05)
            return ProviderStatus(status="ready")

    service = DataProviderService([SlowStatusProvider])
    provider = service.get_namespace("slow")

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(
            executor.map(lambda _: service.refresh_provider_status(provider), range(5))
        )

    assert SlowStatusProvider.calls == 1
    assert all(result.status == "ready" for result in results)


def test_web_catalog_can_return_real_provider_configuration() -> None:
    settings = MemorySettings(
        {
            "api_key": "real-secret",
            "base_url": "https://api.tikhub.io",
            "timeout_seconds": 30,
        }
    )
    service = DataProviderService(
        [TikHubProvider],
        settings=settings,
    )

    catalog = service.catalog(
        "douyin",
        include_status=False,
        include_setup_payload=True,
    )

    assert settings.refresh_count == 1
    assert catalog["providers"][0]["setup_payload"] == {
        "api_key": "real-secret",
        "base_url": "https://api.tikhub.io",
        "timeout_seconds": 30,
    }


def test_tikhub_setup_saves_once_and_logs_verified_account(monkeypatch, tmp_path) -> None:
    settings = MemorySettings()
    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    logger_service = LoggerService(tmp_path / "logs")
    service = DataProviderService(
        [TikHubProvider],
        selected={provider_type.value: "tikhub"},
        settings=settings,
        logger_service=logger_service,
    )
    calls = 0

    def fake_get_user_info(self: TikHubProvider) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "api_key_data": {
                "api_key_name": "cjdb web",
                "api_key_status": 1,
            },
            "user_data": {
                "email": "user@example.com",
                "balance": 7.2,
                "free_credit": 1.2,
                "email_verified": True,
                "account_disabled": False,
                "is_active": True,
            },
        }

    monkeypatch.setattr(TikHubProvider, "get_user_info", fake_get_user_info)

    result = service.setup(
        provider_type,
        {
            "api_key": "real-secret",
            "base_url": "https://api.tikhub.io",
            "timeout_seconds": 30,
        }
    )

    assert calls == 1
    assert settings.values["api_key"] == "real-secret"
    assert result["success"] is True
    assert "logs" not in result
    log_path = logger_service.get_log_path(LogType.PROVIDER_SETUP, "tikhub")
    assert "已保存 TikHub Provider 配置" in log_path.read_text(encoding="utf-8")
    status = service.status(provider_type)
    assert status["ready"] is True
    assert status["message"] == "TikHub Provider 配置已保存"
    assert status["details"]["account"]["api_key_name"] == "cjdb web"


def test_provider_setup_rejects_config_changes_while_external_setup_is_running(
    monkeypatch,
) -> None:
    settings = MemorySettings()
    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    service = DataProviderService(
        [TikHubProvider],
        selected={provider_type.value: "tikhub"},
        settings=settings,
    )
    service.set_provider_status(
        "tikhub",
        ProviderStatus(status="setting_up", setup_pid=12345),
    )
    monkeypatch.setattr(DataProviderService, "_pid_alive", staticmethod(lambda *_: True))

    try:
        service.setup(provider_type, {"api_key": "real-secret"})
    except InvalidOperationError as exc:
        assert "provider setup is already running" in str(exc)
    else:
        raise AssertionError("running setup should block provider configuration changes")

    assert settings.values == {}


def test_provider_service_does_not_refresh_status_after_failed_setup() -> None:
    class FailedSetupProvider(FakeProvider, DouyinAwemeProviderMixin):
        namespace = "tikhub"
        name = "失败 Setup"
        supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)

        def setup(self, params: dict[str, object]) -> SetupResult:
            return SetupResult(success=False, message="setup failed")

        def refresh_status(self) -> ProviderStatus:
            raise AssertionError("status must not run after failed setup")

    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    service = DataProviderService(
        [FailedSetupProvider],
        selected={provider_type.value: FailedSetupProvider.namespace},
        settings=MemorySettings(),
    )

    result = service.setup(provider_type, {})

    assert result["success"] is False
    assert result["message"] == "setup failed"
    assert service.status(provider_type)["status"] == "error"


def test_provider_selection_rejects_changes_while_selected_provider_setup_is_running(
    monkeypatch,
) -> None:
    class OtherDouyinProvider(FakeDouyinProvider):
        namespace = "other"
        name = "另一个采集"

    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    service = DataProviderService(
        [FakeDouyinProvider, OtherDouyinProvider],
        selected={provider_type.value: "local"},
    )
    service.set_provider_status(
        "local",
        ProviderStatus(status="setting_up", setup_pid=12345),
    )
    monkeypatch.setattr(DataProviderService, "_pid_alive", staticmethod(lambda *_: True))

    try:
        service.select(provider_type, "other")
    except InvalidOperationError as exc:
        assert "provider setup is already running" in str(exc)
    else:
        raise AssertionError("running setup should block provider selection changes")

    assert service.selected()[provider_type.value] == "local"


def test_provider_status_has_standard_shape() -> None:
    provider_type = DataProviderType.XIAOHONGSHU_AWEME_COLLECT
    service = DataProviderService(
        [TikHubProvider],
        selected={provider_type.value: "tikhub"},
    )

    status = service.status(provider_type)

    assert status["type"] == provider_type.value
    assert status["provider"]["namespace"] == "tikhub"
    assert status["status"] == "unconfigured"
    assert status["ready"] is False
    assert isinstance(status["message"], str)
    assert isinstance(status["details"], dict)
    assert status["details"]["configured_parameters"]["api_key"] is False
    assert status["details"]["values"]["api_key"] == ""


def test_provider_status_does_not_accept_namespace_as_provider_type() -> None:
    service = DataProviderService([TikHubProvider])

    try:
        service.status("tikhub")
    except InvalidOperationError as exc:
        assert "unknown provider type" in str(exc)
    else:
        raise AssertionError("namespace must not be accepted as a provider type")


def test_provider_service_rejects_namespace_conflicts() -> None:
    class FirstSharedProvider(FakeDouyinProvider):
        namespace = "shared"

    class SecondSharedProvider(FakeXhsProvider):
        namespace = "shared"

    try:
        DataProviderService([FirstSharedProvider, SecondSharedProvider])
    except InvalidOperationError as exc:
        assert "namespace conflict" in str(exc)
    else:
        raise AssertionError("namespace conflicts should fail")


def test_business_provider_mixin_requires_method_implementation() -> None:
    class BrokenProvider(BaseDataProvider, DouyinAwemeProviderMixin):
        namespace = "broken"
        name = "未完整实现"
        supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)

    try:
        BrokenProvider()
    except TypeError as exc:
        assert "fetch_douyin_aweme" in str(exc)
    else:
        raise AssertionError("abstract provider mixin should block initialization")


def test_builtin_providers_are_discovered_from_decorated_modules() -> None:
    providers = registered_data_provider_classes()

    assert {
        "faster_whisper",
        "funasr",
        "tikhub",
    } <= {provider.namespace for provider in providers}


def test_provider_decorator_rejects_namespace_conflicts() -> None:
    class ConflictingDecoratedProvider(TikHubProvider):
        pass

    try:
        register_data_provider(ConflictingDecoratedProvider)
    except InvalidOperationError as exc:
        assert "namespace conflict" in str(exc)
    else:
        raise AssertionError("decorator must reject namespace conflicts")
