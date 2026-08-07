from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from sqlmodel import Session, select

from cjdb_collectors.domains.store import (
    AccountStorePayload,
    AccountStoreProviderMixin,
    AwemeStorePayload,
    BaseStoreProvider,
    StoreResult,
    StoreProviderRegistry,
    StoreStatus,
    SetupResult,
    password_param,
    text_param,
)
from cjdb_collectors.exceptions import CJDBError
from cjdb_collectors.domains.provider import ProviderType
from cjdb_collectors.domains.store.providers import NotionStoreProvider
from cjdb_collectors.db import create_db_engine, init_db
from cjdb_collectors.models import (
    Account,
    Platform,
    Provider,
    ProviderSync,
    SyncObjectType,
    TaskStatus,
    WorkerSubject,
)
from cjdb_collectors.services.execution import ExecutionService
from cjdb_collectors.services.store_providers import StoreProviderService
from cjdb_collectors.services.stores import StoreService

DATA_SOURCE_ID = "d9824bdc-8445-4327-be8b-5b47500af6ce"
DATABASE_ID = "248104cd-477e-80fd-b757-e945d38000bd"
SECOND_DATA_SOURCE_ID = "c174b72c-d782-432f-8dc0-b647e1c96df6"


def test_store_provider_catalog_exposes_setup_contract() -> None:
    registry = StoreProviderRegistry([NotionStoreProvider])

    metadata = registry.list()[0]

    assert metadata["type"] == "notion"
    assert metadata["name"] == "Notion"
    assert metadata["capabilities"] == {
        "aweme": True,
        "account": True,
        "transcription": True,
        "attachments": True,
    }
    assert [item["type"] for item in metadata["parameters"]] == [
        "password",
        "text",
        "checkbox",
        "checkbox",
    ]
    assert [item["key"] for item in metadata["parameters"]] == [
        "token",
        "data_source_id",
        "upload_image_attachments",
        "upload_video_attachments",
    ]
    assert metadata["parameters"][1]["label"] == "数据源 ID"
    assert "data source ID" in metadata["parameters"][1]["help"]


def test_notion_store_provider_parses_attachment_switches() -> None:
    provider = NotionStoreProvider()

    parsed = provider.parse_setup_params(
        {"token": "token", "data_source_id": DATA_SOURCE_ID},
    )

    assert parsed == {
        "token": "token",
        "data_source_id": DATA_SOURCE_ID,
        "upload_image_attachments": False,
        "upload_video_attachments": False,
    }

    parsed = provider.parse_setup_params(
        {
            "token": "token",
            "data_source_id": DATA_SOURCE_ID,
            "upload_image_attachments": "true",
            "upload_video_attachments": "on",
        },
    )

    assert parsed["upload_image_attachments"] is True
    assert parsed["upload_video_attachments"] is True


def test_notion_extracts_ids_from_supported_input_formats() -> None:
    compact_data_source_id = DATA_SOURCE_ID.replace("-", "")
    compact_database_id = DATABASE_ID.replace("-", "")

    assert NotionStoreProvider._extract_notion_ids(compact_data_source_id) == [
        DATA_SOURCE_ID
    ]
    assert NotionStoreProvider._extract_notion_ids(DATA_SOURCE_ID) == [
        DATA_SOURCE_ID
    ]
    assert NotionStoreProvider._extract_notion_ids(
        f"https://www.notion.so/workspace/作品库-{compact_database_id}?v=148104cd477e80bb928f000ce197ddf2"
    ) == [DATABASE_ID]
    assert NotionStoreProvider._extract_notion_ids(
        f"https://example.notion.site/db?data_source_id={compact_data_source_id}"
    ) == [DATA_SOURCE_ID]


def test_notion_setup_normalizes_url_input(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token"})
    compact_data_source_id = DATA_SOURCE_ID.replace("-", "")

    def request(method: str, path: str, **kwargs):
        if (method, path) == ("GET", f"/data_sources/{DATA_SOURCE_ID}"):
            return {
                "object": "data_source",
                "id": DATA_SOURCE_ID,
                "title": [{"plain_text": "作品库"}],
                "properties": {"名称": {"type": "title"}},
            }
        if (method, path) == ("POST", f"/data_sources/{DATA_SOURCE_ID}/query"):
            return {"results": [{"id": "page-1"}, {"id": "page-2"}], "has_more": False}
        raise AssertionError((method, path))

    monkeypatch.setattr(provider, "_request", request)

    result = provider.setup(
        {
            "token": "token",
            "data_source_id": (
                "https://example.notion.site/db"
                f"?data_source_id={compact_data_source_id}"
            ),
        }
    )

    assert result.success is True
    assert result.setup_payload["data_source_id"] == DATA_SOURCE_ID
    assert result.details["summary"] == "名称：作品库；字段数量：1；数据数量：2"


def test_notion_store_formats_persisted_payload_for_form_display() -> None:
    provider = NotionStoreProvider(
        {
            "token": "token",
            "data_source_id": DATA_SOURCE_ID,
            "provider_internal": "ignored",
        }
    )

    assert provider.clean_params_value(
        provider.parameters,
        provider.setup_payload,
        current=provider.setup_payload,
    ) == {
        "token": "token",
        "data_source_id": DATA_SOURCE_ID,
        "upload_image_attachments": False,
        "upload_video_attachments": False,
    }


def test_store_provider_catalog_filters_by_page_provider_type() -> None:
    class AccountOnlyStoreProvider(BaseStoreProvider):
        namespace = "account-only"
        type = "account-only"
        name = "Account Only"
        supported_types = (ProviderType.STORE_ACCOUNT,)

        def setup(self, params) -> SetupResult:
            return SetupResult(success=True, setup_payload=params)

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    class AwemeOnlyStoreProvider(BaseStoreProvider):
        namespace = "aweme-only"
        type = "aweme-only"
        name = "Aweme Only"
        supported_types = (ProviderType.STORE_AWEME,)

        def setup(self, params) -> SetupResult:
            return SetupResult(success=True, setup_payload=params)

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    registry = StoreProviderRegistry(
        [AccountOnlyStoreProvider, AwemeOnlyStoreProvider]
    )

    account_types = [ProviderType.STORE_ACCOUNT.value]
    aweme_types = [ProviderType.STORE_AWEME.value]
    assert [item["type"] for item in registry.list(account_types)] == [
        "account-only"
    ]
    assert [item["type"] for item in registry.list(aweme_types)] == ["aweme-only"]


def test_notion_store_provider_validates_data_source_schema() -> None:
    provider = NotionStoreProvider()

    properties = {
        name: {"type": expected_type}
        for name, expected_type in provider.required_properties.items()
    }
    provider._validate_data_source_schema({"properties": properties})

    try:
        provider._validate_data_source_schema({"properties": {}})
    except CJDBError as exc:
        assert exc.code == "store_schema_mismatch"
        assert "missing required properties" in str(exc)
    else:
        raise AssertionError("missing properties should fail")

    invalid = dict(properties)
    invalid["名称"] = {"type": "rich_text"}
    try:
        provider._validate_data_source_schema({"properties": invalid})
    except CJDBError as exc:
        assert exc.code == "store_schema_mismatch"
        assert "property types are invalid" in str(exc)
    else:
        raise AssertionError("invalid property types should fail")


def test_notion_setup_only_reports_its_own_result(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})

    def request(method: str, path: str, **kwargs):
        if (method, path) == ("GET", f"/data_sources/{DATA_SOURCE_ID}"):
            return {"object": "data_source", "id": DATA_SOURCE_ID, "properties": {}}
        if (method, path) == ("POST", f"/data_sources/{DATA_SOURCE_ID}/query"):
            return {"results": [], "has_more": False}
        raise AssertionError((method, path))

    monkeypatch.setattr(provider, "_request", request)

    result = provider.setup({"token": "token", "data_source_id": DATA_SOURCE_ID})

    assert result == SetupResult(
        success=True,
        message="Notion Store 配置已保存",
        setup_payload={"token": "token", "data_source_id": DATA_SOURCE_ID},
        details={
            "name": None,
            "property_count": 0,
            "row_count": 0,
            "row_count_limited": False,
            "summary": "名称：未命名；字段数量：0；数据数量：0",
        },
    )


def test_notion_setup_fails_when_initialization_query_fails(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})

    def request(method: str, path: str, **kwargs):
        if (method, path) == ("GET", f"/data_sources/{DATA_SOURCE_ID}"):
            return {"object": "data_source", "id": DATA_SOURCE_ID, "properties": {}}
        if (method, path) == ("POST", f"/data_sources/{DATA_SOURCE_ID}/query"):
            raise CJDBError("Notion returned HTTP 500", code="store_unavailable")
        raise AssertionError((method, path))

    monkeypatch.setattr(provider, "_request", request)

    result = provider.setup({"token": "token", "data_source_id": DATA_SOURCE_ID})

    assert result.success is False
    assert result.message == "Notion returned HTTP 500"


def test_notion_setup_rejects_database_id_with_data_source_choices(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token"})
    requests: list[tuple[str, str]] = []

    def request(method: str, path: str, **kwargs):
        requests.append((method, path))
        if path == f"/data_sources/{DATABASE_ID}":
            raise CJDBError("not found", code="store_request_failed")
        if path == f"/databases/{DATABASE_ID}":
            return {
                "object": "database",
                "id": DATABASE_ID,
                "data_sources": [
                    {"id": DATA_SOURCE_ID, "name": "作品"},
                    {"id": SECOND_DATA_SOURCE_ID, "name": "归档"},
                ],
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(provider, "_request", request)

    result = provider.setup({"token": "token", "data_source_id": DATABASE_ID})

    assert result.success is False
    assert "你填入的是 database ID" in str(result.message)
    assert f"作品: {DATA_SOURCE_ID}" in str(result.message)
    assert f"归档: {SECOND_DATA_SOURCE_ID}" in str(result.message)
    assert requests == [
        ("GET", f"/data_sources/{DATABASE_ID}"),
        ("GET", f"/databases/{DATABASE_ID}"),
    ]


def test_notion_ensures_missing_schema_then_retries_once(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    upsert_calls = 0
    ensured_schemas: list[dict[str, str]] = []

    def upsert(values, last_store_result):
        nonlocal upsert_calls
        upsert_calls += 1
        if upsert_calls == 1:
            raise CJDBError("missing field", code="store_missing_field")
        return StoreResult(success=True, success_payload={"page_id": "page"})

    def ensure_schema(schema):
        ensured_schemas.append(dict(schema))

    monkeypatch.setattr(provider, "_upsert", upsert)
    monkeypatch.setattr(provider, "ensure_schema", ensure_schema)

    result = provider.store_account(
        AccountStorePayload(
            local_id="account-1",
            platform="douyin",
            platform_account_id="remote-account-1",
            profile_url="https://example.com/account",
            display_name="Example",
        ),
        None,
    )

    assert result.success is True
    assert upsert_calls == 2
    assert ensured_schemas == [
        {
            "CJDB ID": "rich_text",
            "平台": "rich_text",
            "账号 ID": "rich_text",
            "主页链接": "url",
            "名称": "title",
        }
    ]


def test_notion_ensure_schema_adds_fields_and_renames_title(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    requests: list[tuple[str, str, dict[str, Any]]] = []
    get_count = 0

    def request(method: str, path: str, **kwargs):
        nonlocal get_count
        requests.append((method, path, kwargs))
        if method == "GET":
            get_count += 1
            if get_count == 1:
                return {"properties": {"Name": {"type": "title"}}}
            return {
                "properties": {
                    "名称": {"type": "title"},
                    "CJDB ID": {"type": "rich_text"},
                }
            }
        return {}

    monkeypatch.setattr(provider, "_request", request)

    provider.ensure_schema(
        {
            "名称": "title",
            "CJDB ID": "rich_text",
        }
    )

    assert requests[1] == (
        "PATCH",
        f"/data_sources/{DATA_SOURCE_ID}",
        {
            "json": {
                "properties": {
                    "Name": {"name": "名称"},
                    "CJDB ID": {"rich_text": {}},
                }
            }
        },
    )


def test_notion_upsert_creates_page_under_data_source(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, path: str, **kwargs):
        requests.append((method, path, kwargs))
        return {"id": "page", "url": "https://notion.so/page"}

    monkeypatch.setattr(provider, "_request", request)

    result = provider._upsert(
        {
            "local_id": "aweme-1",
            "title": "作品",
        },
        None,
    )

    assert result.success is True
    assert requests == [
        (
            "POST",
            "/pages",
            {
                "notion_version": provider.notion_version,
                "json": {
                    "properties": {
                        "CJDB ID": {"rich_text": [{"text": {"content": "aweme-1"}}]},
                        "名称": {"title": [{"text": {"content": "作品"}}]},
                    },
                    "parent": {
                        "type": "data_source_id",
                        "data_source_id": DATA_SOURCE_ID,
                    },
                },
            },
        ),
    ]


def test_notion_upsert_uses_previous_store_payload_for_update(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, path: str, **kwargs):
        requests.append((method, path, kwargs))
        return {"id": "page-from-payload", "url": "https://notion.so/page"}

    monkeypatch.setattr(provider, "_request", request)

    result = provider._upsert(
        {"local_id": "aweme-1", "title": "作品"},
        StoreResult(
            success=True,
            success_payload={"remote_record_id": "page-from-payload"},
        ),
    )

    assert result.success is True
    assert requests == [
        (
            "PATCH",
            "/pages/page-from-payload",
            {
                "notion_version": provider.notion_version,
                "json": {
                    "properties": {
                        "CJDB ID": {"rich_text": [{"text": {"content": "aweme-1"}}]},
                        "名称": {"title": [{"text": {"content": "作品"}}]},
                    }
                },
            },
        )
    ]


def test_notion_rich_text_splits_long_content() -> None:
    value = "x" * 14450

    result = NotionStoreProvider._rich_text(value)
    chunks = result["rich_text"]

    assert len(chunks) == 8
    assert "".join(chunk["text"]["content"] for chunk in chunks) == value
    assert all(
        NotionStoreProvider._notion_text_units(chunk["text"]["content"])
        <= NotionStoreProvider.rich_text_content_limit
        for chunk in chunks
    )


def test_notion_rich_text_splits_by_utf16_units_for_emoji() -> None:
    value = "a" * 1990 + "🙂" * 20

    result = NotionStoreProvider._rich_text(value)
    chunks = result["rich_text"]

    assert len(value) == 2010
    assert NotionStoreProvider._notion_text_units(value) == 2030
    assert "".join(chunk["text"]["content"] for chunk in chunks) == value
    assert all(
        NotionStoreProvider._notion_text_units(chunk["text"]["content"])
        <= NotionStoreProvider.rich_text_content_limit
        for chunk in chunks
    )


def test_notion_transcription_uses_page_body_not_property(
    monkeypatch,
) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    requests: list[tuple[str, str, dict[str, Any]]] = []
    full_text = "短转写内容"

    def request(method: str, path: str, **kwargs):
        requests.append((method, path, kwargs))
        if method == "POST" and path == "/pages":
            return {"id": "page", "url": "https://notion.so/page"}
        if method == "GET" and path == "/blocks/page/children":
            return {"results": [], "has_more": False}
        if method == "PATCH" and path == "/blocks/page/children":
            return {}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(provider, "_request", request)

    result = provider._upsert(
        {
            "local_id": "aweme-1",
            "title": "作品",
            "transcription_text": full_text,
        },
        None,
    )

    assert result.success is True
    page_properties = requests[0][2]["json"]["properties"]
    assert "转写文本" not in page_properties

    append_children = requests[2][2]["json"]["children"]
    assert append_children[0]["type"] == "heading_2"
    assert (
        append_children[0]["heading_2"]["rich_text"][0]["text"]["content"]
        == "视频转写"
    )
    body_text = "".join(
        block["paragraph"]["rich_text"][0]["text"]["content"]
        for block in append_children[1:]
    )
    assert body_text == full_text
    assert all(
        provider._notion_text_units(
            block["paragraph"]["rich_text"][0]["text"]["content"]
        )
        <= provider.rich_text_content_limit
        for block in append_children[1:]
    )


def test_notion_store_aweme_maps_published_at_and_metrics(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    stored_values: dict[str, Any] = {}

    def store(values, last_store_result):
        stored_values.update(provider._properties(values))
        return StoreResult(success=True, success_payload={"remote_record_id": "page"})

    monkeypatch.setattr(provider, "_store", store)
    published_at = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)

    result = provider.store_aweme(
        AwemeStorePayload(
            local_id="aweme-1",
            platform="douyin",
            platform_aweme_id="remote-aweme-1",
            aweme_url="https://example.com/item",
            source_url="https://example.com/source",
            title="作品",
            published_at=published_at,
            metrics={
                "play_count": 100,
                "like_count": 20,
                "collect_count": 3,
                "share_count": 4,
                "comment_count": 5,
            },
        ),
        None,
    )

    assert result.success is True
    assert stored_values["发布时间"] == {"date": {"start": published_at.isoformat()}}
    assert stored_values["播放量"] == {"number": 100}
    assert stored_values["点赞量"] == {"number": 20}
    assert stored_values["收藏量"] == {"number": 3}
    assert stored_values["转发量"] == {"number": 4}
    assert stored_values["评论量"] == {"number": 5}


def test_notion_schema_retry_is_bounded(monkeypatch) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    upsert_calls = 0
    ensure_calls = 0

    def upsert(values, last_store_result):
        nonlocal upsert_calls
        upsert_calls += 1
        raise CJDBError("still missing", code="store_missing_field")

    def ensure_schema(schema):
        nonlocal ensure_calls
        ensure_calls += 1

    monkeypatch.setattr(provider, "_upsert", upsert)
    monkeypatch.setattr(provider, "ensure_schema", ensure_schema)

    result = provider._store({"local_id": "account-1"}, None)

    assert result == StoreResult(success=False, message="still missing")
    assert upsert_calls == 2
    assert ensure_calls == 1


def test_notion_store_aweme_uploads_enabled_local_attachments(
    monkeypatch,
    tmp_path,
) -> None:
    provider = NotionStoreProvider(
        {
            "token": "token",
            "data_source_id": DATA_SOURCE_ID,
            "upload_image_attachments": True,
            "upload_video_attachments": True,
        }
    )
    photo = tmp_path / "photo.jpg"
    video = tmp_path / "video.mp4"
    photo.write_bytes(b"photo")
    video.write_bytes(b"video")
    uploaded_paths: list[str] = []
    stored_values: dict[str, Any] = {}

    def upload(path: str) -> dict[str, Any]:
        uploaded_paths.append(path)
        return {
            "name": path.rsplit("/", 1)[-1],
            "type": "file_upload",
            "file_upload": {"id": f"upload-{len(uploaded_paths)}"},
        }

    def store(values, last_store_result):
        stored_values.update(values)
        return StoreResult(success=True, success_payload={"remote_record_id": "page"})

    monkeypatch.setattr(provider, "_upload_file_attachment", upload)
    monkeypatch.setattr(provider, "_store", store)

    result = provider.store_aweme(
        AwemeStorePayload(
            local_id="aweme-1",
            platform="xiaohongshu",
            platform_aweme_id="remote-aweme-1",
            aweme_url="https://example.com/item",
            source_url="https://example.com/source",
            title="作品",
            photo_paths=[{"url": "https://example.com/photo.jpg", "local_path": str(photo)}],
            video_path=str(video),
        ),
        None,
    )

    assert result.success is True
    assert uploaded_paths == [str(photo), str(video)]
    assert stored_values["image_attachments"] == [
        {
            "name": "photo.jpg",
            "type": "file_upload",
            "file_upload": {"id": "upload-1"},
        }
    ]
    assert stored_values["video_attachments"] == [
        {
            "name": "video.mp4",
            "type": "file_upload",
            "file_upload": {"id": "upload-2"},
        }
    ]


def test_notion_upload_uses_multipart_for_large_attachments(
    monkeypatch,
    tmp_path,
) -> None:
    provider = NotionStoreProvider({"token": "token", "data_source_id": DATA_SOURCE_ID})
    provider.single_part_upload_limit_bytes = 5
    provider.multipart_chunk_size_bytes = 4
    file_path = tmp_path / "large.mp4"
    file_path.write_bytes(b"abcdefghijkl")
    calls: list[tuple[str, str, dict[str, Any]]] = []
    sent_parts: list[tuple[str, bytes]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if path == "/file_uploads":
            return {"id": "upload-large"}
        if path == "/file_uploads/upload-large/send":
            file_name, file_obj, content_type = kwargs["files"]["file"]
            sent_parts.append(
                (
                    kwargs["data"]["part_number"],
                    file_obj.read(),
                )
            )
            assert file_name == "large.mp4"
            assert content_type == "video/mp4"
            return {"id": "upload-large", "status": "pending"}
        if path == "/file_uploads/upload-large/complete":
            return {"id": "upload-large", "status": "uploaded"}
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(provider, "_request", request)

    result = provider._upload_file_attachment(str(file_path))

    assert result == {
        "name": "large.mp4",
        "type": "file_upload",
        "file_upload": {"id": "upload-large"},
    }
    assert calls[0] == (
        "POST",
        "/file_uploads",
        {
            "notion_version": provider.file_upload_notion_version,
            "json": {
                "mode": "multi_part",
                "filename": "large.mp4",
                "content_type": "video/mp4",
                "number_of_parts": 3,
            },
        },
    )
    assert [call[1] for call in calls[1:]] == [
        "/file_uploads/upload-large/send",
        "/file_uploads/upload-large/send",
        "/file_uploads/upload-large/send",
        "/file_uploads/upload-large/complete",
    ]
    assert sent_parts == [
        ("1", b"abcd"),
        ("2", b"efgh"),
        ("3", b"ijkl"),
    ]


def test_notion_recognizes_missing_property_validation_error() -> None:
    assert NotionStoreProvider._is_missing_field_error(
        "validation_error",
        "名称 is not a property that exists.",
    )
    assert not NotionStoreProvider._is_missing_field_error(
        "validation_error",
        "名称 should be a title property.",
    )


def test_store_registry_rejects_declared_capability_without_mixin() -> None:
    class BrokenStoreProvider(BaseStoreProvider):
        type = "broken"
        name = "未完整实现"
        capabilities = {"aweme": True}

        def setup(self, params) -> SetupResult:
            return SetupResult(success=True)

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    try:
        StoreProviderRegistry([BrokenStoreProvider])
    except CJDBError as exc:
        assert "AwemeStoreProviderMixin" in str(exc)
    else:
        raise AssertionError("declared capabilities must have matching mixins")


def test_provider_get_visit_url_reads_success_payload() -> None:
    provider = NotionStoreProvider()

    assert provider.get_visit_url(
        StoreResult(
            success=True,
            success_payload={"visit_url": "https://example.com/record"},
        )
    ) == "https://example.com/record"


def test_store_service_persists_clean_config_and_calls_provider_setup(tmp_path) -> None:
    class FakeStoreProvider(BaseStoreProvider):
        type = "fake"
        name = "Fake"
        parameters = (
            password_param(
                "token",
                "Token",
                required=True,
            ),
            text_param(
                "database_id",
                "Database",
                required=True,
            ),
        )

        setup_configs: list[dict[str, Any]] = []

        def setup(self, params) -> SetupResult:
            self.setup_configs.append(dict(params))
            return SetupResult(
                success=True,
                setup_payload={**params, "prepared": True},
            )

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    FakeStoreProvider.setup_configs = []
    engine = create_db_engine(tmp_path / "stores.sqlite")
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

    provider_service = StoreProviderService(
        session_factory,
        StoreProviderRegistry([FakeStoreProvider]),
    )
    service = StoreService(session_factory, provider_service)

    item = service.create(
        "fake",
        name="主库",
        setup_values={"token": "secret", "database_id": "db"},
    )

    with session_factory() as session:
        persisted_provider = session.exec(
            select(Provider).where(Provider.namespace == "fake")
        ).first()
        assert persisted_provider is not None
        assert persisted_provider.setup_payload_json == {
            "token": "secret",
            "database_id": "db",
            "prepared": True,
        }
    assert FakeStoreProvider.setup_configs[-1] == {
        "token": "secret",
        "database_id": "db",
    }

    service.setup(item.id, {"token": "", "database_id": "db2"})

    with session_factory() as session:
        persisted_provider = session.exec(
            select(Provider).where(Provider.namespace == "fake")
        ).first()
        assert persisted_provider is not None
        assert persisted_provider.setup_payload_json == {
            "token": "secret",
            "database_id": "db2",
            "prepared": True,
        }
    assert FakeStoreProvider.setup_configs[-1] == {
        "token": "secret",
        "database_id": "db2",
    }
    engine.dispose()


def test_store_service_preserves_submitted_form_values_in_setup_payload(tmp_path) -> None:
    class NormalizingStoreProvider(BaseStoreProvider):
        type = "normalizing"
        name = "Normalizing"
        parameters = (
            password_param("token", "Token", required=True),
            text_param("target", "Target", required=True),
        )

        def setup(self, params) -> SetupResult:
            return SetupResult(
                success=True,
                message="Normalizing setup 完成",
                setup_payload={
                    **params,
                    "target": "normalized-target",
                    "provider_internal": "runtime",
                },
                details={"summary": "目标：submitted-target"},
            )

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    engine = create_db_engine(tmp_path / "stores-form-values.sqlite")
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

    provider_service = StoreProviderService(
        session_factory,
        StoreProviderRegistry([NormalizingStoreProvider]),
    )
    service = StoreService(session_factory, provider_service)

    item, setup_result = service.create_with_setup_result(
        "normalizing",
        name="主库",
        setup_values={"token": "secret", "target": "submitted-target"},
    )

    assert setup_result == {
        "success": True,
        "message": "Normalizing setup 完成",
        "setup_payload": {
            "token": "secret",
            "target": "normalized-target",
            "provider_internal": "runtime",
        },
        "details": {"summary": "目标：submitted-target"},
    }

    with session_factory() as session:
        persisted_provider = session.get(Provider, item.id)
        assert persisted_provider is not None
        assert persisted_provider.setup_payload_json == {
            "token": "secret",
            "target": "submitted-target",
            "provider_internal": "runtime",
        }
        assert persisted_provider.status == "ready"
        assert persisted_provider.status_message == "Normalizing setup 完成"
        provider = provider_service.registry.get(
            persisted_provider.namespace,
            persisted_provider.setup_payload_json,
        )
        assert provider.clean_params_value(
            provider.parameters,
            persisted_provider.setup_payload_json,
            current=persisted_provider.setup_payload_json,
        ) == {
            "token": "secret",
            "target": "submitted-target",
        }
    engine.dispose()


def test_store_service_marks_provider_error_when_setup_param_parsing_fails(
    tmp_path,
) -> None:
    class StrictStoreProvider(BaseStoreProvider):
        type = "strict"
        name = "Strict"
        parameters = (text_param("target", "Target", required=True),)

        def setup(self, params) -> SetupResult:
            raise AssertionError("setup should not run after parse failure")

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

    engine = create_db_engine(tmp_path / "stores-parse-failure.sqlite")
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

    provider_service = StoreProviderService(
        session_factory,
        StoreProviderRegistry([StrictStoreProvider]),
    )
    service = StoreService(session_factory, provider_service)
    item = service.create("strict", name="严格配置")
    service.update(item.id, status="ready")

    result = service.setup(item.id, {"unknown": "value"})

    assert result["success"] is False
    assert "unknown store parameters" in str(result["message"])
    with session_factory() as session:
        persisted_provider = session.get(Provider, item.id)
        assert persisted_provider is not None
        assert persisted_provider.status == "error"
        assert "unknown store parameters" in str(persisted_provider.status_message)
    engine.dispose()


def test_sync_only_replaces_success_payload_after_success(tmp_path) -> None:
    engine = create_db_engine(tmp_path / "sync-result.sqlite")
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
        account = Account(
            platform=Platform.DOUYIN,
            profile_url="https://example.com/account",
        )
        store = Provider(namespace="fake", name="同步测试", status="ready")
        session.add(account)
        session.add(store)
        session.flush()
        relation = ProviderSync(
            object_type=SyncObjectType.ACCOUNT,
            object_id=account.id,
            provider_id=store.id,
            status=TaskStatus.RUNNING,
            run_token="first-run",
        )
        session.add(relation)
        session.flush()
        sync_id = relation.id

    returned_results = [
        StoreResult(success=True, success_payload={"remote_record_id": "remote-1"}),
        StoreResult(success=False, message="remote rejected the update"),
    ]
    received_previous: list[StoreResult | None] = []

    def store_account(account, store_id, last_store_result=None):
        received_previous.append(last_store_result)
        return returned_results.pop(0)

    container = SimpleNamespace(
        stores=SimpleNamespace(store_account=store_account),
        runtime_settings=SimpleNamespace(
            worker_tasks=SimpleNamespace(
                data_sync=SimpleNamespace(retry_limit=0, retry_delay_seconds=0)
            )
        ),
    )
    service = ExecutionService(session_factory, container)

    service._sync(WorkerSubject.ACCOUNT_SYNC, sync_id, "first-run")

    with session_factory() as session:
        relation = session.get(ProviderSync, sync_id)
        assert relation is not None
        assert relation.status == TaskStatus.SUCCEEDED
        assert relation.success_payload_json == {"remote_record_id": "remote-1"}
        relation.status = TaskStatus.RUNNING
        relation.run_token = "second-run"
        session.add(relation)

    service._sync(WorkerSubject.ACCOUNT_SYNC, sync_id, "second-run")

    with session_factory() as session:
        relation = session.get(ProviderSync, sync_id)
        assert relation is not None
        assert relation.status == TaskStatus.FAILED
        assert relation.error_message == "remote rejected the update"
        assert relation.success_payload_json == {"remote_record_id": "remote-1"}

    assert received_previous[0] is None
    assert received_previous[1] == StoreResult(
        success=True,
        success_payload={"remote_record_id": "remote-1"},
    )
    engine.dispose()


def test_sync_passes_previous_success_payload_without_interpreting_it(tmp_path) -> None:
    engine = create_db_engine(tmp_path / "sync-previous-payload.sqlite")
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
        account = Account(
            platform=Platform.DOUYIN,
            profile_url="https://example.com/account",
        )
        store = Provider(namespace="fake", name="同步测试", status="ready")
        session.add(account)
        session.add(store)
        session.flush()
        relation = ProviderSync(
            object_type=SyncObjectType.ACCOUNT,
            object_id=account.id,
            provider_id=store.id,
            status=TaskStatus.RUNNING,
            run_token="retry-run",
            success_payload_json={"remote_record_id": "page-from-payload"},
            last_synced_at=datetime.now(timezone.utc),
        )
        session.add(relation)
        session.flush()
        sync_id = relation.id

    received_previous: list[StoreResult | None] = []

    def store_account(account, store_id, last_store_result=None):
        received_previous.append(last_store_result)
        return StoreResult(
            success=True,
            success_payload={"remote_record_id": "page-from-payload"},
        )

    container = SimpleNamespace(
        stores=SimpleNamespace(store_account=store_account),
        runtime_settings=SimpleNamespace(
            worker_tasks=SimpleNamespace(
                data_sync=SimpleNamespace(retry_limit=0, retry_delay_seconds=0)
            )
        ),
    )
    service = ExecutionService(session_factory, container)

    service._sync(WorkerSubject.ACCOUNT_SYNC, sync_id, "retry-run")

    with session_factory() as session:
        relation = session.get(ProviderSync, sync_id)
        assert relation is not None
        assert relation.success_payload_json == {"remote_record_id": "page-from-payload"}

    assert received_previous == [
        StoreResult(
            success=True,
            success_payload={"remote_record_id": "page-from-payload"},
        )
    ]
    engine.dispose()


def test_store_service_normalizes_unexpected_provider_exception(tmp_path) -> None:
    class BrokenAccountStoreProvider(BaseStoreProvider, AccountStoreProviderMixin):
        type = "broken-account"
        name = "Broken Account"
        capabilities = {"account": True}

        def setup(self, params) -> SetupResult:
            return SetupResult(success=True)

        def status(self) -> StoreStatus:
            return StoreStatus(status="ready", ready=True)

        def store_account(self, payload, last_store_result) -> StoreResult:
            raise RuntimeError("provider crashed")

    engine = create_db_engine(tmp_path / "provider-error.sqlite")
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

    provider_service = StoreProviderService(
        session_factory,
        StoreProviderRegistry([BrokenAccountStoreProvider]),
    )
    service = StoreService(session_factory, provider_service)
    store = service.create("broken-account", name="异常 Provider")
    account = Account(
        platform=Platform.DOUYIN,
        profile_url="https://example.com/account",
    )

    result = service.store_account(account, store.id)

    assert result == StoreResult(success=False, message="provider crashed")
    engine.dispose()


def test_store_service_does_not_check_status_after_failed_setup(tmp_path) -> None:
    class FailedSetupStoreProvider(BaseStoreProvider):
        type = "failed-setup"
        name = "Failed Setup"

        def setup(self, params) -> SetupResult:
            return SetupResult(success=False, message="setup failed")

        def status(self) -> StoreStatus:
            raise AssertionError("status must not run after failed setup")

    engine = create_db_engine(tmp_path / "failed-store-setup.sqlite")
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

    provider_service = StoreProviderService(
        session_factory,
        StoreProviderRegistry([FailedSetupStoreProvider]),
    )
    service = StoreService(session_factory, provider_service)
    store = service.create("failed-setup", name="失败 Store Setup")
    with session_factory() as session:
        persisted_provider = session.exec(
            select(Provider).where(Provider.namespace == "failed-setup")
        ).first()
        assert persisted_provider is not None
        persisted_provider.setup_payload_json = {"stable": "old-payload"}
        session.add(persisted_provider)

    result = service.setup(store.id, {})

    assert result == SetupResult(
        success=False,
        message="setup failed",
    ).model_dump()
    assert service.get(store.id).status == "error"
    with session_factory() as session:
        persisted_provider = session.exec(
            select(Provider).where(Provider.namespace == "failed-setup")
        ).first()
        assert persisted_provider is not None
        assert persisted_provider.setup_payload_json == {"stable": "old-payload"}
    engine.dispose()
