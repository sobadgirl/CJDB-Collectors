from __future__ import annotations

from typing import Any

import httpx

from cjdb_collectors.models import Platform
from cjdb_collectors.services.data_providers import register_data_provider

from ...base import AccountProviderMixin, BaseDataProvider
from ...types import (
    AccountData,
    DataProviderType,
    FetchAccountRequest,
    ProviderParameter,
    ProviderParameterType,
    ProviderStatus,
)


class CollectorError(RuntimeError):
    """Normalized HTTP collector failure."""


class HttpCollectorClient:
    """Generic JSON collector client.

    Endpoint paths are constructor arguments on purpose: the first source API has
    not been fixed yet, and callers can replace this adapter without changing a
    service or route.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 10,
        *,
        aweme_path: str = "/awemes/resolve",
        account_path: str = "/accounts/resolve",
        comments_path: str = "/awemes/{platform_aweme_id}/comments",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.aweme_path = aweme_path
        self.account_path = account_path
        self.comments_path = comments_path
        self._client = client or httpx.Client(timeout=timeout_seconds, trust_env=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(
                method, f"{self.base_url}{path}", headers=self.headers, **kwargs
            )
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise CollectorError(str(exc)) from exc
        if not isinstance(value, dict):
            raise CollectorError("collector response must be a JSON object")
        return value

    def collect_aweme(self, source_url: str) -> dict[str, Any]:
        return self._request("POST", self.aweme_path, json={"url": source_url})

    def collect_account(self, profile_url: str) -> dict[str, Any]:
        return self._request("POST", self.account_path, json={"url": profile_url})

    def collect_comments(
        self, platform_aweme_id: str, cursor: str | None = None
    ) -> dict[str, Any]:
        path = self.comments_path.format(platform_aweme_id=platform_aweme_id)
        params = {"cursor": cursor} if cursor else None
        return self._request("GET", path, params=params)

    def close(self) -> None:
        self._client.close()

@register_data_provider
class HttpCollectorProvider(BaseDataProvider, AccountProviderMixin):
    namespace = "http_collector"
    name = "通用账号采集接口"
    supported_types = (DataProviderType.ACCOUNT_COLLECT,)
    platforms_by_type = {DataProviderType.ACCOUNT_COLLECT: set(Platform)}
    parameters = (
        ProviderParameter(
            key="base_url",
            type=ProviderParameterType.TEXT,
            label="服务地址",
            required=True,
            default="http://localhost:8001",
        ),
        ProviderParameter(
            key="api_key",
            type=ProviderParameterType.PASSWORD,
            label="接口密钥",
        ),
        ProviderParameter(
            key="timeout_seconds",
            type=ProviderParameterType.NUMBER,
            label="超时时间",
            required=True,
            default=10,
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self._client_key: tuple[str, str, float] | None = None
        self._client: HttpCollectorClient | None = None

    def refresh_status(self) -> ProviderStatus:
        configured = dict(self.parameter_values)
        values = {
            parameter.key: configured.get(parameter.key, parameter.default)
            for parameter in self.parameters
        }
        missing = [
            parameter.key
            for parameter in self.parameters
            if parameter.required and not values.get(parameter.key)
        ]
        if missing:
            return ProviderStatus(
                status="unconfigured",
                message=f"缺少必填参数：{', '.join(missing)}",
            )

        base_url = str(values.get("base_url") or "http://localhost:8001").rstrip("/")
        api_key = str(values.get("api_key") or "")
        timeout_seconds = float(values.get("timeout_seconds") or 10)
        try:
            with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
                response = client.get(
                    f"{base_url}/health",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return ProviderStatus(
                status="unavailable",
                message=str(exc),
                details={"base_url": base_url},
            )
        if not isinstance(payload, dict):
            return ProviderStatus(
                status="unavailable",
                message="collector response must be a JSON object",
                details={"base_url": base_url},
            )

        return ProviderStatus(
            status="ready",
            details={"base_url": base_url},
        )

    def fetch_account(self, request: FetchAccountRequest) -> AccountData:
        payload = self._collector().collect_account(request.profile_url)
        data = payload.get("data")
        value = data if isinstance(data, dict) else payload
        return AccountData(
            platform_account_id=value.get("platform_account_id"),
            display_name=value.get("display_name"),
            avatar_url=value.get("avatar_url"),
            profile_data_json=value.get("profile_data_json") or {},
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._client_key = None

    def _collector(self) -> HttpCollectorClient:
        values = dict(self.parameter_values)
        base_url = str(values.get("base_url") or "http://localhost:8001")
        api_key = str(values.get("api_key") or "")
        timeout_seconds = float(values.get("timeout_seconds") or 10)
        client_key = (base_url, api_key, timeout_seconds)
        if self._client is None or self._client_key != client_key:
            self.close()
            self._client = HttpCollectorClient(
                base_url,
                api_key or None,
                timeout_seconds,
            )
            self._client_key = client_key
        return self._client
