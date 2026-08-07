from __future__ import annotations

import logging
import mimetypes
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

import httpx
from cjdb_collectors.exceptions import CJDBError
from cjdb_collectors.domains.provider import ProviderType

from ..base import (
    AccountStoreProviderMixin,
    AwemeStoreProviderMixin,
    BaseStoreProvider,
    TranscriptionStoreProviderMixin,
)
from ..types import (
    AccountStorePayload,
    AwemeStorePayload,
    StoreResult,
    StoreStatus,
    SetupResult,
    TranscriptionStorePayload,
    checkbox_param,
    password_param,
    text_param,
)


class NotionStoreProvider(
    BaseStoreProvider,
    AwemeStoreProviderMixin,
    AccountStoreProviderMixin,
    TranscriptionStoreProviderMixin,
):
    type = "notion"
    namespace = "notion"
    name = "Notion"
    supported_types = (
        ProviderType.STORE_AWEME,
        ProviderType.STORE_ACCOUNT,
        ProviderType.STORE_VIDEO_TRANSCRIPTION,
    )
    api_base_url = "https://api.notion.com/v1"
    notion_version = "2026-03-11"
    file_upload_notion_version = "2026-03-11"
    rich_text_content_limit = 2000
    transcription_property_limit = 1950
    transcription_body_heading = "视频转写"
    transcription_truncation_suffix = "...（未完结，详见正文中的完整转写内容）"
    single_part_upload_limit_bytes = 20 * 1024 * 1024
    multipart_chunk_size_bytes = 20 * 1024 * 1024
    multipart_max_parts = 1000
    notion_id_pattern = re.compile(
        r"(?<![0-9a-fA-F])"
        r"("
        r"[0-9a-fA-F]{32}|"
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        r")"
        r"(?![0-9a-fA-F])"
    )
    capabilities = {
        "aweme": True,
        "account": True,
        "transcription": True,
        "attachments": True,
    }
    property_mapping = {
        "local_id": "CJDB ID",
        "platform": "平台",
        "platform_aweme_id": "作品 ID",
        "aweme_url": "作品链接",
        "source_url": "来源链接",
        "title": "名称",
        "description": "描述",
        "published_at": "发布时间",
        "play_count": "播放量",
        "like_count": "点赞量",
        "collect_count": "收藏量",
        "share_count": "转发量",
        "comment_count": "评论量",
        "image_attachments": "图片附件",
        "video_attachments": "视频附件",
        "platform_account_id": "账号 ID",
        "profile_url": "主页链接",
        "display_name": "名称",
        "aweme_id": "关联作品 ID",
        "video_path": "视频路径",
        "status": "状态",
        "text_summary": "摘要",
        "duration_seconds": "时长",
    }
    required_properties = {
        "CJDB ID": "rich_text",
        "平台": "rich_text",
        "作品 ID": "rich_text",
        "作品链接": "url",
        "来源链接": "url",
        "名称": "title",
        "描述": "rich_text",
        "发布时间": "date",
        "播放量": "number",
        "点赞量": "number",
        "收藏量": "number",
        "转发量": "number",
        "评论量": "number",
        "图片附件": "files",
        "视频附件": "files",
        "账号 ID": "rich_text",
        "主页链接": "url",
        "关联作品 ID": "rich_text",
        "视频路径": "rich_text",
        "状态": "rich_text",
        "摘要": "rich_text",
        "时长": "number",
    }
    parameters = (
        password_param(
            "token",
            "集成 Token",
            required=True,
        ),
        text_param(
            "data_source_id",
            "数据源 ID",
            required=True,
            help=(
                "请输入 Notion data source ID，不是 database ID；可粘贴 ID 或 Notion 链接。"
                "如果填入 database ID，保存时会列出可用 data source ID。"
            ),
        ),
        checkbox_param(
            "upload_image_attachments",
            "上传图片附件",
            default=False,
        ),
        checkbox_param(
            "upload_video_attachments",
            "上传视频作为附件",
            default=False,
        ),
    )

    def __init__(
        self,
        setup_payload: Mapping[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(setup_payload, logger=logger)

    def setup(self, params: Mapping[str, Any]) -> SetupResult:
        configured = dict(params)
        candidates = self._extract_notion_ids(configured.get("data_source_id"))
        if not candidates:
            return SetupResult(
                success=False,
                message="未识别到 Notion data source ID，请填写 ID 或 Notion 链接。",
            )
        original_payload = self.setup_payload
        self.setup_payload = configured
        data_source_error: Exception | None = None
        try:
            for candidate in candidates:
                try:
                    data_source = self._request("GET", f"/data_sources/{candidate}")
                    configured["data_source_id"] = candidate
                    details = self._setup_details(data_source)
                    self.logger.info(str(details.get("summary") or "Notion data source 已读取"))
                    return SetupResult(
                        success=True,
                        message="Notion Store 配置已保存",
                        setup_payload=configured,
                        details=details,
                    )
                except Exception as exc:
                    data_source_error = exc
            for candidate in candidates:
                try:
                    database = self._request("GET", f"/databases/{candidate}")
                except Exception:
                    continue
                return SetupResult(
                    success=False,
                    message=self._database_id_message(database),
                )
            return SetupResult(
                success=False,
                message=str(
                    data_source_error
                    or "无法读取 Notion data source。请确认填写的是 data source ID。"
                ),
            )
        finally:
            self.setup_payload = original_payload

    def _setup_details(self, data_source: Mapping[str, Any]) -> dict[str, Any]:
        name = self._data_source_name(data_source)
        properties = data_source.get("properties")
        property_count = len(properties) if isinstance(properties, Mapping) else 0
        row_count, row_count_limited = self._count_data_source_rows(limit=100)
        summary = [
            f"名称：{name}" if name else "名称：未命名",
            f"字段数量：{property_count}",
        ]
        if row_count is not None:
            summary.append(f"数据数量：{row_count}{'+' if row_count_limited else ''}")
        return {
            "name": name,
            "property_count": property_count,
            "row_count": row_count,
            "row_count_limited": row_count_limited,
            "summary": "；".join(summary),
        }

    @staticmethod
    def _data_source_name(data_source: Mapping[str, Any]) -> str | None:
        title = data_source.get("title")
        if isinstance(title, list):
            parts = [
                str(item.get("plain_text") or item.get("text", {}).get("content") or "")
                for item in title
                if isinstance(item, Mapping)
            ]
            value = "".join(parts).strip()
            if value:
                return value
        for key in ("name", "display_name"):
            value = data_source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _count_data_source_rows(self, *, limit: int) -> tuple[int | None, bool]:
        response = self._request(
            "POST",
            f"/data_sources/{self._data_source_id()}/query",
            json={"page_size": min(limit, 100)},
        )
        results = response.get("results")
        count = len(results) if isinstance(results, list) else 0
        return min(count, limit), bool(response.get("has_more"))

    def status(self) -> StoreStatus:
        try:
            data_source_id = self._data_source_id()
            self._request("GET", f"/data_sources/{data_source_id}")
        except Exception as exc:
            return StoreStatus(
                status="unavailable",
                ready=False,
                message=str(exc),
            )
        return StoreStatus(status="ready", ready=True)

    def _token(self) -> str:
        token = self.setup_payload.get("token")
        if not token:
            raise CJDBError(
                "token is required",
                code="invalid_store_configuration",
            )
        return str(token)

    def _data_source_id(self) -> str:
        data_source_id = self.setup_payload.get("data_source_id")
        if not data_source_id:
            container = self.setup_payload.get("container")
            data_source_id = (
                container.get("data_source_id")
                if isinstance(container, Mapping)
                else None
            )
        candidates = self._extract_notion_ids(data_source_id)
        if not candidates:
            raise CJDBError(
                "data_source_id is required",
                code="invalid_store_configuration",
            )
        return candidates[0]

    @classmethod
    def _extract_notion_ids(cls, value: Any) -> list[str]:
        text = str(value or "").strip()
        if not text:
            return []
        candidates: list[str] = []

        parsed_text = text
        if "notion.so/" in parsed_text and not parsed_text.startswith(("http://", "https://")):
            parsed_text = f"https://{parsed_text}"
        parsed = urlparse(parsed_text)
        if parsed.scheme and parsed.netloc:
            query = parse_qs(parsed.query)
            for key in (
                "data_source_id",
                "dataSourceId",
                "datasource_id",
                "source_id",
                "source",
                "data_source",
            ):
                for item in query.get(key, []):
                    candidates.extend(cls._extract_ids_from_text(item))
            for segment in reversed([item for item in parsed.path.split("/") if item]):
                candidates.extend(cls._extract_ids_from_text(segment))
        else:
            candidates.extend(cls._extract_ids_from_text(text))

        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                unique.append(candidate)
        return unique

    @classmethod
    def _extract_ids_from_text(cls, text: str) -> list[str]:
        return [
            cls._normalize_notion_id(match.group(1))
            for match in cls.notion_id_pattern.finditer(text)
        ]

    @staticmethod
    def _normalize_notion_id(value: str) -> str:
        compact = value.replace("-", "").lower()
        return (
            f"{compact[0:8]}-{compact[8:12]}-{compact[12:16]}-"
            f"{compact[16:20]}-{compact[20:32]}"
        )

    @staticmethod
    def _database_id_message(database: Mapping[str, Any]) -> str:
        data_sources = database.get("data_sources")
        if not isinstance(data_sources, list) or not data_sources:
            return (
                "你填入的是 database ID，但没有读取到可用的 data source。"
                "请在 Notion database 设置中复制 data source ID 后重新填写。"
            )
        lines = [
            "你填入的是 database ID。Notion 现在需要 data source ID，请复制下面其中一个 ID 重新填写："
        ]
        for item in data_sources:
            if not isinstance(item, Mapping):
                continue
            data_source_id = item.get("id")
            if not data_source_id:
                continue
            name = item.get("name") or "未命名数据源"
            lines.append(f"- {name}: {data_source_id}")
        if len(lines) == 1:
            lines.append("未读取到 data source ID。")
        return "\n".join(lines)

    def _headers(
        self,
        *,
        content_type: str | None = "application/json",
        notion_version: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Notion-Version": notion_version or self.notion_version,
            "Content-Type": "application/json",
        }
        if content_type is None:
            headers.pop("Content-Type", None)
        else:
            headers["Content-Type"] = content_type
        return headers

    def _request(
        self,
        method: str,
        path: str,
        notion_version: str | None = None,
        content_type: str | None = "application/json",
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=30, trust_env=False) as client:
                response = client.request(
                    method,
                    f"{self.api_base_url}{path}",
                    headers=self._headers(
                        content_type=content_type,
                        notion_version=notion_version,
                    ),
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise CJDBError(
                str(exc),
                code="store_unavailable",
            ) from exc
        if response.status_code in {401, 403}:
            raise CJDBError(
                "Notion authentication failed",
                code="store_authentication_failed",
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise CJDBError(
                f"Notion returned HTTP {response.status_code}",
                code="store_unavailable",
            )
        if response.status_code >= 400:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            notion_code = (
                error_payload.get("code")
                if isinstance(error_payload, Mapping)
                else None
            )
            notion_message = (
                error_payload.get("message")
                if isinstance(error_payload, Mapping)
                else None
            )
            message = str(notion_message or response.text[:500])
            raise CJDBError(
                f"Notion returned HTTP {response.status_code}: {message}",
                code=(
                    "store_missing_field"
                    if self._is_missing_field_error(notion_code, message)
                    else "store_request_failed"
                ),
                data={
                    "http_status": response.status_code,
                    "notion_code": notion_code,
                },
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise CJDBError(
                "Notion returned invalid JSON",
                code="invalid_store_response",
            ) from exc
        if not isinstance(value, dict):
            raise CJDBError(
                "Notion returned an invalid response",
                code="invalid_store_response",
            )
        return value

    @staticmethod
    def _is_missing_field_error(
        notion_code: Any,
        message: str,
    ) -> bool:
        normalized = message.lower()
        return notion_code == "validation_error" and "property" in normalized and any(
            marker in normalized
            for marker in (
                "does not exist",
                "not found",
                "could not find",
                "unknown property",
                "not a property",
                "property that exists",
            )
        )

    @staticmethod
    def _rich_text_item(value: str) -> dict[str, Any]:
        return {"text": {"content": value}}

    @staticmethod
    def _notion_text_units(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    @classmethod
    def _split_notion_text(cls, value: str, limit: int) -> list[str]:
        chunks: list[str] = []
        current: list[str] = []
        current_units = 0
        for character in value:
            units = cls._notion_text_units(character)
            if current and current_units + units > limit:
                chunks.append("".join(current))
                current = []
                current_units = 0
            current.append(character)
            current_units += units
        if current:
            chunks.append("".join(current))
        return chunks

    @classmethod
    def _rich_text(cls, value: str | None) -> dict[str, Any]:
        text = value or ""
        if not text:
            return {"rich_text": [cls._rich_text_item("")]}
        return {
            "rich_text": [
                cls._rich_text_item(chunk)
                for chunk in cls._split_notion_text(text, cls.rich_text_content_limit)
            ]
        }

    @staticmethod
    def _title(value: str | None) -> dict[str, Any]:
        return {"title": [{"text": {"content": value or "Untitled"}}]}

    @staticmethod
    def _files(value: Any) -> dict[str, Any]:
        files = value if isinstance(value, list) else []
        return {"files": [item for item in files if isinstance(item, Mapping)]}

    def _properties(
        self,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for local_name, value in self._property_values(values).items():
            remote_name = self.property_mapping.get(local_name)
            if not remote_name:
                continue
            if local_name in {"title", "display_name"}:
                properties[str(remote_name)] = self._title(
                    None if value is None else str(value)
                )
            elif local_name in {"image_attachments", "video_attachments"}:
                properties[str(remote_name)] = self._files(value)
            elif local_name.endswith("_url") and value:
                properties[str(remote_name)] = {"url": str(value)}
            elif local_name == "published_at":
                properties[str(remote_name)] = {
                    "date": {"start": value.isoformat()} if value is not None else None
                }
            elif local_name == "duration_seconds":
                properties[str(remote_name)] = {
                    "number": float(value) if value is not None else None
                }
            elif local_name.endswith("_count"):
                properties[str(remote_name)] = {
                    "number": int(value) if value is not None else None
                }
            else:
                properties[str(remote_name)] = self._rich_text(
                    None if value is None else str(value)
                )
        return properties

    @classmethod
    def _transcription_property_text(cls, value: str) -> str:
        if cls._notion_text_units(value) <= cls.transcription_property_limit:
            return value
        suffix = cls.transcription_truncation_suffix
        prefix_limit = max(
            0,
            cls.transcription_property_limit - cls._notion_text_units(suffix),
        )
        prefix = cls._split_notion_text(value, prefix_limit)[0] if prefix_limit else ""
        return f"{prefix.rstrip()}{suffix}"

    @classmethod
    def _property_values(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        prepared = dict(values)
        for key in ("transcription_text", "text"):
            value = prepared.get(key)
            if value is not None:
                prepared[key] = cls._transcription_property_text(str(value))
        return prepared

    @classmethod
    def _body_transcription_text(cls, values: Mapping[str, Any]) -> str | None:
        for key in ("transcription_text", "text"):
            value = values.get(key)
            if value is None:
                continue
            text = str(value)
            if text:
                return text
        return None

    @staticmethod
    def _has_transcription_text_field(values: Mapping[str, Any]) -> bool:
        return "transcription_text" in values or "text" in values

    def _schema_for(
        self,
        values: Mapping[str, Any],
    ) -> dict[str, str]:
        schema: dict[str, str] = {}
        for local_name in values:
            remote_name = self.property_mapping.get(local_name)
            if remote_name:
                schema[remote_name] = self.required_properties[remote_name]
        return schema

    @staticmethod
    def _has_file_uploads(values: Mapping[str, Any]) -> bool:
        for local_name in ("image_attachments", "video_attachments"):
            attachments = values.get(local_name)
            if isinstance(attachments, list) and attachments:
                return True
        return False

    def _validate_data_source_schema(
        self,
        data_source: Mapping[str, Any],
        required_properties: Mapping[str, str] | None = None,
    ) -> None:
        required = required_properties or self.required_properties
        properties = data_source.get("properties")
        if not isinstance(properties, Mapping):
            raise CJDBError(
                "Notion data source properties are unavailable",
                code="store_schema_mismatch",
            )
        missing = [
            name
            for name in required
            if name not in properties
        ]
        if missing:
            raise CJDBError(
                "Notion data source is missing required properties: "
                + ", ".join(missing),
                code="store_schema_mismatch",
            )
        mismatched = []
        for name, expected_type in required.items():
            property_schema = properties.get(name)
            actual_type = (
                property_schema.get("type")
                if isinstance(property_schema, Mapping)
                else None
            )
            if actual_type != expected_type:
                mismatched.append(f"{name} should be {expected_type}, got {actual_type}")
        if mismatched:
            raise CJDBError(
                "Notion data source property types are invalid: "
                + "; ".join(mismatched),
                code="store_schema_mismatch",
            )

    def ensure_schema(self, required_properties: Mapping[str, str]) -> None:
        data_source_id = self._data_source_id()
        data_source = self._request("GET", f"/data_sources/{data_source_id}")
        properties = data_source.get("properties")
        if not isinstance(properties, Mapping):
            raise CJDBError(
                "Notion data source properties are unavailable",
                code="store_schema_mismatch",
            )

        updates: dict[str, Any] = {}
        for name, expected_type in required_properties.items():
            current = properties.get(name)
            if isinstance(current, Mapping):
                actual_type = current.get("type")
                if actual_type != expected_type:
                    raise CJDBError(
                        f"Notion property {name} should be {expected_type}, "
                        f"got {actual_type}",
                        code="store_schema_mismatch",
                    )
                continue
            if expected_type == "title":
                existing_title = next(
                    (
                        property_name
                        for property_name, schema in properties.items()
                        if isinstance(schema, Mapping)
                        and schema.get("type") == "title"
                    ),
                    None,
                )
                if not existing_title:
                    raise CJDBError(
                        "Notion data source has no title property",
                        code="store_schema_mismatch",
                    )
                updates[str(existing_title)] = {"name": name}
            else:
                updates[name] = {expected_type: {}}

        if updates:
            self._request(
                "PATCH",
                f"/data_sources/{data_source_id}",
                json={"properties": updates},
            )
        refreshed = self._request("GET", f"/data_sources/{data_source_id}")
        self._validate_data_source_schema(refreshed, required_properties)

    def _upsert(
        self,
        values: Mapping[str, Any],
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        remote_record_id = None
        if last_store_result is not None:
            value = last_store_result.success_payload.get("remote_record_id")
            remote_record_id = str(value) if value else None
        body: dict[str, Any] = {"properties": self._properties(values)}
        notion_version = (
            self.file_upload_notion_version
            if self._has_file_uploads(values)
            else self.notion_version
        )
        if remote_record_id:
            result = self._request(
                "PATCH",
                f"/pages/{remote_record_id}",
                notion_version=notion_version,
                json=body,
            )
        else:
            body["parent"] = {
                "type": "data_source_id",
                "data_source_id": self._data_source_id(),
            }
            result = self._request(
                "POST",
                "/pages",
                notion_version=notion_version,
                json=body,
            )
        record_id = result.get("id")
        if not record_id:
            raise CJDBError(
                "Notion response has no page id",
                code="invalid_store_response",
            )
        if self._has_transcription_text_field(values):
            self._sync_transcription_body_section(
                str(record_id),
                self._body_transcription_text(values),
            )
        return StoreResult(
            success=True,
            success_payload={
                "remote_record_id": str(record_id),
                "visit_url": result.get("url"),
            },
        )

    def _store(
        self,
        values: Mapping[str, Any],
        last_store_result: StoreResult | None,
        *,
        schema_retries_remaining: int = 1,
    ) -> StoreResult:
        try:
            return self._upsert(values, last_store_result)
        except CJDBError as exc:
            if exc.code == "store_missing_field" and schema_retries_remaining > 0:
                try:
                    self.ensure_schema(self._schema_for(values))
                except Exception as schema_exc:
                    return StoreResult(success=False, message=str(schema_exc))
                return self._store(
                    values,
                    last_store_result,
                    schema_retries_remaining=schema_retries_remaining - 1,
            )
            return StoreResult(success=False, message=str(exc))
        except Exception as exc:
            return StoreResult(success=False, message=str(exc))

    @staticmethod
    def _rich_text_plain_text(value: Any) -> str:
        if not isinstance(value, list):
            return ""
        return "".join(
            str(
                item.get("plain_text")
                or item.get("text", {}).get("content")
                or ""
            )
            for item in value
            if isinstance(item, Mapping)
        )

    @classmethod
    def _is_transcription_heading(cls, block: Mapping[str, Any]) -> bool:
        if block.get("type") != "heading_2":
            return False
        heading = block.get("heading_2")
        if not isinstance(heading, Mapping):
            return False
        return (
            cls._rich_text_plain_text(heading.get("rich_text")).strip()
            == cls.transcription_body_heading
        )

    @staticmethod
    def _is_next_top_level_heading(block: Mapping[str, Any]) -> bool:
        return block.get("type") in {"heading_1", "heading_2"}

    def _page_children(self, page_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        start_cursor: str | None = None
        while True:
            params: dict[str, Any] = {"page_size": 100}
            if start_cursor:
                params["start_cursor"] = start_cursor
            response = self._request(
                "GET",
                f"/blocks/{page_id}/children",
                params=params,
            )
            results = response.get("results")
            if isinstance(results, list):
                children.extend(item for item in results if isinstance(item, dict))
            if not response.get("has_more"):
                return children
            cursor = response.get("next_cursor")
            if not cursor:
                return children
            start_cursor = str(cursor)

    def _archive_block(self, block_id: Any) -> None:
        if not block_id:
            return
        self._request(
            "PATCH",
            f"/blocks/{block_id}",
            json={"archived": True},
        )

    def _clear_transcription_body_section(self, page_id: str) -> None:
        children = self._page_children(page_id)
        deleting = False
        for block in children:
            if self._is_transcription_heading(block):
                deleting = True
            elif deleting and self._is_next_top_level_heading(block):
                return
            if deleting:
                self._archive_block(block.get("id"))

    @classmethod
    def _paragraph_block(cls, text: str) -> dict[str, Any]:
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [cls._rich_text_item(text)]},
        }

    @classmethod
    def _transcription_body_blocks(cls, text: str) -> list[dict[str, Any]]:
        blocks = [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [cls._rich_text_item(cls.transcription_body_heading)]
                },
            }
        ]
        blocks.extend(
            cls._paragraph_block(chunk)
            for chunk in cls._split_notion_text(text, cls.rich_text_content_limit)
        )
        return blocks

    def _append_page_children(
        self,
        page_id: str,
        children: list[dict[str, Any]],
    ) -> None:
        for index in range(0, len(children), 100):
            self._request(
                "PATCH",
                f"/blocks/{page_id}/children",
                json={"children": children[index : index + 100]},
            )

    def _sync_transcription_body_section(
        self,
        page_id: str,
        transcription_text: str | None,
    ) -> None:
        self._clear_transcription_body_section(page_id)
        if not transcription_text:
            return
        self._append_page_children(
            page_id,
            self._transcription_body_blocks(transcription_text),
        )

    @staticmethod
    def _local_path_from_photo_item(item: str | Mapping[str, Any]) -> str | None:
        if isinstance(item, str):
            return item
        value = item.get("local_path") if isinstance(item, Mapping) else None
        return str(value) if value else None

    @staticmethod
    def _file_upload_object(upload_id: Any, filename: str) -> dict[str, Any]:
        if not upload_id:
            raise CJDBError(
                "Notion file upload response has no id",
                code="invalid_store_response",
            )
        return {
            "name": filename,
            "type": "file_upload",
            "file_upload": {"id": str(upload_id)},
        }

    @staticmethod
    def _content_type(path: Path) -> str:
        return mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    @staticmethod
    def _ensure_file_upload_status(
        response: Mapping[str, Any],
        expected_statuses: set[str],
    ) -> None:
        status = response.get("status")
        if status and status not in expected_statuses:
            raise CJDBError(
                f"Notion file upload is not ready: {status}",
                code="store_request_failed",
            )

    def _upload_file_attachment(self, path_value: str) -> dict[str, Any]:
        path = Path(path_value).expanduser()
        if not path.is_file():
            raise CJDBError(
                f"local attachment file does not exist: {path}",
                code="store_attachment_missing",
            )
        if path.stat().st_size <= self.single_part_upload_limit_bytes:
            return self._upload_single_part_file(path)
        return self._upload_multi_part_file(path)

    def _upload_single_part_file(self, path: Path) -> dict[str, Any]:
        content_type = self._content_type(path)
        upload = self._request(
            "POST",
            "/file_uploads",
            notion_version=self.file_upload_notion_version,
            json={
                "mode": "single_part",
                "filename": path.name,
                "content_type": content_type,
            },
        )
        upload_id = upload.get("id")
        self._file_upload_object(upload_id, path.name)
        with path.open("rb") as file_obj:
            sent = self._request(
                "POST",
                f"/file_uploads/{upload_id}/send",
                notion_version=self.file_upload_notion_version,
                content_type=None,
                files={"file": (path.name, file_obj, content_type)},
            )
        self._ensure_file_upload_status(sent, {"uploaded"})
        return self._file_upload_object(upload_id, path.name)

    def _upload_multi_part_file(self, path: Path) -> dict[str, Any]:
        content_type = self._content_type(path)
        file_size = path.stat().st_size
        part_count = (
            file_size + self.multipart_chunk_size_bytes - 1
        ) // self.multipart_chunk_size_bytes
        if part_count > self.multipart_max_parts:
            raise CJDBError(
                "local attachment file is too large for Notion multipart upload",
                code="store_attachment_too_large",
            )
        upload = self._request(
            "POST",
            "/file_uploads",
            notion_version=self.file_upload_notion_version,
            json={
                "mode": "multi_part",
                "filename": path.name,
                "content_type": content_type,
                "number_of_parts": part_count,
            },
        )
        upload_id = upload.get("id")
        self._file_upload_object(upload_id, path.name)
        with path.open("rb") as file_obj:
            for part_number in range(1, part_count + 1):
                chunk = file_obj.read(self.multipart_chunk_size_bytes)
                sent = self._request(
                    "POST",
                    f"/file_uploads/{upload_id}/send",
                    notion_version=self.file_upload_notion_version,
                    content_type=None,
                    data={"part_number": str(part_number)},
                    files={
                        "file": (
                            path.name,
                            BytesIO(chunk),
                            content_type,
                        )
                    },
                )
                self._ensure_file_upload_status(sent, {"pending", "uploaded"})
        completed = self._request(
            "POST",
            f"/file_uploads/{upload_id}/complete",
            notion_version=self.file_upload_notion_version,
            content_type=None,
        )
        self._ensure_file_upload_status(completed, {"uploaded"})
        return self._file_upload_object(upload_id, path.name)

    def _upload_photo_attachments(
        self,
        photo_paths: list[str | Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for item in photo_paths:
            path = self._local_path_from_photo_item(item)
            if path:
                files.append(self._upload_file_attachment(path))
        return files

    def store_aweme(
        self,
        payload: AwemeStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        image_attachments: list[dict[str, Any]] = []
        video_attachments: list[dict[str, Any]] = []
        if self.setup_payload.get("upload_image_attachments"):
            image_attachments = self._upload_photo_attachments(payload.photo_paths)
        if self.setup_payload.get("upload_video_attachments") and payload.video_path:
            video_attachments = [self._upload_file_attachment(payload.video_path)]

        return self._store(
            {
                "local_id": payload.local_id,
                "platform": payload.platform,
                "platform_aweme_id": payload.platform_aweme_id,
                "aweme_url": payload.aweme_url,
                "source_url": payload.source_url,
                "title": payload.title,
                "description": payload.description,
                "published_at": payload.published_at,
                "play_count": payload.metrics.get("play_count"),
                "like_count": payload.metrics.get("like_count"),
                "collect_count": payload.metrics.get("collect_count"),
                "share_count": payload.metrics.get("share_count"),
                "comment_count": payload.metrics.get("comment_count"),
                "transcription_text": payload.transcription_text,
                "image_attachments": image_attachments,
                "video_attachments": video_attachments,
            },
            last_store_result,
        )

    def store_account(
        self,
        payload: AccountStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        return self._store(
            {
                "local_id": payload.local_id,
                "platform": payload.platform,
                "platform_account_id": payload.platform_account_id,
                "profile_url": payload.profile_url,
                "display_name": payload.display_name,
            },
            last_store_result,
        )

    def store_transcription(
        self,
        payload: TranscriptionStorePayload,
        last_store_result: StoreResult | None,
    ) -> StoreResult:
        return self._store(
            {
                "local_id": payload.local_id,
                "aweme_id": payload.aweme_id,
                "source_url": payload.source_url,
                "video_path": payload.video_path,
                "status": payload.status,
                "text": payload.normalized_text or payload.text,
                "text_summary": payload.text_summary,
                "duration_seconds": payload.duration_seconds,
            },
            last_store_result,
        )

    def get_visit_url(self, result: StoreResult) -> str | None:
        value = result.success_payload.get("visit_url")
        return str(value) if value else None
