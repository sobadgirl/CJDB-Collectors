from __future__ import annotations

from typing import Any, Mapping

import httpx

from ..base import (
    AccountStoreProviderMixin,
    AwemeStoreProviderMixin,
    BaseStoreProvider,
    StoreAuthenticationError,
    StoreConfigurationError,
    StoreSchemaError,
    StoreUnavailableError,
)
from ..types import (
    AccountStorePayload,
    AwemeStorePayload,
    StoreParameter,
    StoreParameterType,
    StoreResult,
    StoreStatus,
)


class NotionStoreProvider(
    BaseStoreProvider,
    AwemeStoreProviderMixin,
    AccountStoreProviderMixin,
):
    type = "notion"
    name = "Notion"
    api_base_url = "https://api.notion.com/v1"
    notion_version = "2022-06-28"
    capabilities = {"aweme": True, "account": True, "attachments": False}
    parameters = (
        StoreParameter(
            key="token",
            type=StoreParameterType.PASSWORD,
            label="集成 Token",
            required=True,
        ),
        StoreParameter(
            key="database_id",
            type=StoreParameterType.TEXT,
            label="数据库 ID",
            required=True,
        ),
        StoreParameter(
            key="field_mapping",
            type=StoreParameterType.TEXT,
            label="字段映射",
            default={},
        ),
    )

    def __init__(self) -> None:
        self._client = httpx.Client(timeout=30, trust_env=False)

    def status(self, config: Mapping[str, Any]) -> StoreStatus:
        try:
            database_id = self._database_id(config)
            self._request("GET", f"/databases/{database_id}", config)
        except Exception as exc:
            return StoreStatus(
                status="unavailable",
                ready=False,
                message=str(exc),
            )
        return StoreStatus(status="ready", ready=True)

    @staticmethod
    def _token(config: Mapping[str, Any]) -> str:
        token = config.get("token") or config.get("secret")
        if not token:
            raise StoreConfigurationError("token is required")
        return str(token)

    @staticmethod
    def _database_id(config: Mapping[str, Any]) -> str:
        database_id = config.get("database_id")
        if not database_id:
            container = config.get("container")
            database_id = (
                container.get("database_id")
                if isinstance(container, Mapping)
                else None
            )
        if not database_id:
            raise StoreConfigurationError("database_id is required")
        return str(database_id)

    def _headers(self, config: Mapping[str, Any]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token(config)}",
            "Notion-Version": self.notion_version,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        config: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(
                method,
                f"{self.api_base_url}{path}",
                headers=self._headers(config),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise StoreUnavailableError(str(exc)) from exc
        if response.status_code in {401, 403}:
            raise StoreAuthenticationError("Notion authentication failed")
        if response.status_code == 429 or response.status_code >= 500:
            raise StoreUnavailableError(
                f"Notion returned HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise StoreSchemaError(
                f"Notion returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise StoreUnavailableError("Notion returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise StoreUnavailableError("Notion returned an invalid response")
        return value

    @staticmethod
    def _rich_text(value: str | None) -> dict[str, Any]:
        return {"rich_text": [{"text": {"content": value or ""}}]}

    @staticmethod
    def _title(value: str | None) -> dict[str, Any]:
        return {"title": [{"text": {"content": value or "Untitled"}}]}

    def _properties(
        self,
        values: Mapping[str, Any],
        mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for local_name, remote_name in mapping.items():
            value = values.get(local_name)
            if not remote_name:
                continue
            if local_name in {"title", "display_name"}:
                properties[str(remote_name)] = self._title(
                    None if value is None else str(value)
                )
            elif local_name.endswith("_url") and value:
                properties[str(remote_name)] = {"url": str(value)}
            else:
                properties[str(remote_name)] = self._rich_text(
                    None if value is None else str(value)
                )
        return properties

    def _upsert(
        self,
        values: Mapping[str, Any],
        config: Mapping[str, Any],
        remote_record_id: str | None,
    ) -> StoreResult:
        mapping = config.get("field_mapping")
        if not isinstance(mapping, Mapping) or not mapping:
            raise StoreConfigurationError("field_mapping is required")
        body: dict[str, Any] = {"properties": self._properties(values, mapping)}
        if remote_record_id:
            result = self._request(
                "PATCH",
                f"/pages/{remote_record_id}",
                config,
                json=body,
            )
        else:
            body["parent"] = {
                "type": "database_id",
                "database_id": self._database_id(config),
            }
            result = self._request("POST", "/pages", config, json=body)
        record_id = result.get("id")
        if not record_id:
            raise StoreUnavailableError("Notion response has no page id")
        return StoreResult(str(record_id), result.get("url"))

    def store_aweme(
        self,
        payload: AwemeStorePayload,
        config: Mapping[str, Any],
        remote_record_id: str | None = None,
    ) -> StoreResult:
        return self._upsert(
            {
                "local_id": payload.local_id,
                "platform": payload.platform,
                "platform_aweme_id": payload.platform_aweme_id,
                "aweme_url": payload.aweme_url,
                "source_url": payload.source_url,
                "title": payload.title,
                "description": payload.description,
                "transcription_text": payload.transcription_text,
            },
            config,
            remote_record_id,
        )

    def store_account(
        self,
        payload: AccountStorePayload,
        config: Mapping[str, Any],
        remote_record_id: str | None = None,
    ) -> StoreResult:
        return self._upsert(
            {
                "local_id": payload.local_id,
                "platform": payload.platform,
                "platform_account_id": payload.platform_account_id,
                "profile_url": payload.profile_url,
                "display_name": payload.display_name,
            },
            config,
            remote_record_id,
        )

    def close(self) -> None:
        self._client.close()
