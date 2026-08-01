from __future__ import annotations

from cjdb_collectors.data_provider import (
    AwemeData,
    AwemeProviderMixin,
    BaseDataProvider,
    DataProviderType,
    FetchAwemeRequest,
    ResolvedMedia,
    ResolveVideoRequest,
)
from cjdb_collectors.data_provider.providers.tikhub import TikHubProvider
from cjdb_collectors.exceptions import InvalidOperationError
from cjdb_collectors.models import ContentType, Platform
from cjdb_collectors.services.data_providers import (
    DataProviderService,
    register_data_provider,
    registered_data_provider_classes,
)


class MemoryConfig:
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


class FakeProvider(BaseDataProvider, AwemeProviderMixin):
    def fetch_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        return AwemeData(
            platform_aweme_id=request.platform_aweme_id,
            content_type=ContentType.UNKNOWN,
        )

    def resolve_video(self, request: ResolveVideoRequest) -> ResolvedMedia | None:
        return None


class FakeDouyinProvider(FakeProvider):
    namespace = "local"
    name = "本地采集"
    supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)
    platforms_by_type = {DataProviderType.DOUYIN_AWEME_COLLECT: {Platform.DOUYIN}}


class FakeXhsProvider(FakeProvider):
    namespace = "tikhub"
    name = "TikHub"
    supported_types = (DataProviderType.XIAOHONGSHU_AWEME_COLLECT,)
    platforms_by_type = {
        DataProviderType.XIAOHONGSHU_AWEME_COLLECT: {Platform.XIAOHONGSHU}
    }


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


def test_real_provider_catalog_contains_namespace_labels_and_parameters() -> None:
    service = DataProviderService([TikHubProvider])

    catalog = service.catalog("xiaohongshu")
    provider = catalog["providers"][0]

    assert provider["namespace"] == "tikhub"
    assert provider["name"] == "TikHub"
    assert provider["parameters"][0]["key"] == "api_key"
    assert provider["parameters"][0]["type"] == "password"
    assert provider["parameters"][0]["label"] == "接口密钥"


def test_web_catalog_can_return_real_provider_configuration() -> None:
    config = MemoryConfig(
        {
            "api_key": "real-secret",
            "base_url": "https://api.tikhub.io",
            "timeout_seconds": 30,
        }
    )
    service = DataProviderService(
        [TikHubProvider],
        config=config,
    )

    catalog = service.catalog(
        "douyin",
        include_status=False,
        include_configuration=True,
    )

    assert config.refresh_count == 1
    assert catalog["providers"][0]["configuration"] == {
        "api_key": "real-secret",
        "base_url": "https://api.tikhub.io",
        "timeout_seconds": 30,
    }


def test_tikhub_setup_saves_once_and_logs_verified_account(monkeypatch) -> None:
    config = MemoryConfig()
    provider_type = DataProviderType.DOUYIN_AWEME_COLLECT
    service = DataProviderService(
        [TikHubProvider],
        selected={provider_type.value: "tikhub"},
        config=config,
    )
    calls = 0

    class FakeClient:
        def get_user_info(self) -> dict[str, object]:
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

    monkeypatch.setattr(TikHubProvider, "_tikhub", lambda self: FakeClient())

    result = service.setup(
        provider_type,
        {
            "api_key": "real-secret",
            "base_url": "https://api.tikhub.io",
            "timeout_seconds": 30,
        }
    )

    assert calls == 1
    assert config.values["api_key"] == "real-secret"
    assert result["status"]["ready"] is True
    assert result["status"]["details"]["account"]["api_key_name"] == "cjdb web"
    assert "TikHub 连接验证成功" in result["logs"]
    assert "用户：user@example.com" in result["logs"]


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
    class BrokenProvider(BaseDataProvider, AwemeProviderMixin):
        namespace = "broken"
        name = "未完整实现"
        supported_types = (DataProviderType.DOUYIN_AWEME_COLLECT,)

    try:
        BrokenProvider()
    except TypeError as exc:
        assert "fetch_aweme" in str(exc)
    else:
        raise AssertionError("abstract provider mixin should block initialization")


def test_builtin_providers_are_discovered_from_decorated_modules() -> None:
    providers = registered_data_provider_classes()

    assert {
        "faster_whisper",
        "http_collector",
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
