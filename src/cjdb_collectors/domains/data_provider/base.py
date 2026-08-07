from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
import logging
from threading import RLock
from typing import Any

import psutil

from cjdb_collectors.exceptions import InvalidOperationError
from cjdb_collectors.config_fields import clean_config_values
from cjdb_collectors.models import Platform
from cjdb_collectors.domains.provider import BaseProvider

from .types import (
    AccountData,
    AccountAwemePage,
    AwemeData,
    CommentPage,
    DataProviderType,
    FetchAccountAwemesRequest,
    FetchAccountRequest,
    FetchAwemeRequest,
    FetchCommentsRequest,
    ProviderMetadata,
    ProviderParameter,
    SetupResult,
    ProviderStatus,
    TranscriptionRequest,
    TranscriptionResult,
)


class BaseDataProvider(BaseProvider, ABC):
    namespace: str
    name: str
    supported_types: tuple[DataProviderType, ...]
    parameters: tuple[ProviderParameter, ...] = ()
    status_refresh_seconds = 30

    def __init__(
        self,
        setup_payload: dict[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(setup_payload, logger=logger)
        self._current_status: ProviderStatus | None = None
        self._status_lock = RLock()

    def metadata(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> ProviderMetadata:
        selected_type = self._metadata_type(provider_type)
        return ProviderMetadata(
            namespace=self.namespace,
            name=self.name,
            type=selected_type,
            platforms=[selected_type.value.split("_", 1)[0]],
            parameters=[parameter.model_dump() for parameter in self.parameters],
        )

    def set_status(self, status: ProviderStatus) -> ProviderStatus:
        with self._status_lock:
            if status.checked_at is None:
                status = replace(status, checked_at=datetime.now(timezone.utc))
            self._current_status = status
            return status

    def get_status(self) -> ProviderStatus | None:
        with self._status_lock:
            return self._current_status

    def status(self, current: ProviderStatus | None = None) -> ProviderStatus:
        with self._status_lock:
            if current is not None and (
                self._current_status is None
                or self._status_is_newer(current, self._current_status)
            ):
                self._current_status = current
            selected = self._current_status
            if selected is not None and not self._status_needs_refresh(selected):
                return selected
            return self.set_status(self.refresh_status())

    @abstractmethod
    def refresh_status(self) -> ProviderStatus:
        values = dict(self.setup_payload)
        missing = [
            parameter.key
            for parameter in self.parameters
            if parameter.required
            and not values.get(parameter.key, parameter.default)
        ]
        if missing:
            return ProviderStatus(
                status="unconfigured",
                message=f"缺少必填参数：{', '.join(missing)}",
            )
        return ProviderStatus(status="ready")

    @abstractmethod
    def setup(self, params: dict[str, Any]) -> SetupResult:
        """根据本次临时参数执行初始化，并返回需要持久化的 payload。"""

    def fetch_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        if request.platform == Platform.DOUYIN and isinstance(
            self, DouyinAwemeProviderMixin
        ):
            return self.fetch_douyin_aweme(request)
        if request.platform == Platform.XIAOHONGSHU and isinstance(
            self, XiaohongshuAwemeProviderMixin
        ):
            return self.fetch_xiaohongshu_aweme(request)
        if request.platform == Platform.WECHAT_CHANNELS and isinstance(
            self, WeChatChannelsAwemeProviderMixin
        ):
            return self.fetch_wechat_channels_aweme(request)
        if request.platform == Platform.WECHAT_MP and isinstance(
            self, WeChatMpAwemeProviderMixin
        ):
            return self.fetch_wechat_mp_aweme(request)
        raise InvalidOperationError(
            f"provider {self.namespace} does not support {request.platform.value} awemes"
        )

    def fetch_comments(self, request: FetchCommentsRequest) -> CommentPage:
        if request.platform == Platform.DOUYIN and isinstance(
            self, DouyinCommentProviderMixin
        ):
            return self.fetch_douyin_comments(request)
        if request.platform == Platform.XIAOHONGSHU and isinstance(
            self, XiaohongshuCommentProviderMixin
        ):
            return self.fetch_xiaohongshu_comments(request)
        if request.platform == Platform.WECHAT_CHANNELS and isinstance(
            self, WeChatChannelsCommentProviderMixin
        ):
            return self.fetch_wechat_channels_comments(request)
        if request.platform == Platform.WECHAT_MP and isinstance(
            self, WeChatMpCommentProviderMixin
        ):
            return self.fetch_wechat_mp_comments(request)
        raise InvalidOperationError(
            f"provider {self.namespace} does not support {request.platform.value} comments"
        )

    def fetch_history_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """按历史补全语义分页采集评论。

        Provider 平台方法应从 request.progress_payload 或 request.cursor 中恢复
        上次游标，持续翻页直到满足任一停止条件：max_count、max_pages、
        earliest_date，或平台侧没有更多数据。
        """
        return self.fetch_comments(request)

    def fetch_latest_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """按最新同步语义采集评论。

        默认从第一页开始，不读取长期历史游标；停止条件由 request.stop_policy
        控制。平台需要特殊逻辑时可覆盖该方法。
        """
        return self.fetch_comments(replace(request, cursor=None, progress_payload={}))

    def close(self) -> None:
        pass

    def parse_setup_params(
        self,
        values: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._parse_setup_params(values, current=current)

    def _metadata_type(
        self,
        provider_type: DataProviderType | str | None = None,
    ) -> DataProviderType:
        if provider_type is not None:
            selected_type = DataProviderType(provider_type)
            if selected_type not in self.supported_types:
                raise InvalidOperationError(
                    f"provider {self.namespace} does not support {selected_type.value}"
                )
            return selected_type
        return self.supported_types[0]

    def _parse_setup_params(
        self,
        values: dict[str, Any],
        *,
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return clean_config_values(
            self.parameters,
            values,
            current=current if current is not None else self.setup_payload,
            unknown_message="unknown provider parameters: {keys}",
            required_message="provider parameter is required: {key}",
            error_type=InvalidOperationError,
        )

    def _status_needs_refresh(self, status: ProviderStatus) -> bool:
        if status.status == "setting_up":
            return not self._status_process_alive(status)
        checked_at = status.checked_at
        if checked_at is None:
            return True
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - checked_at).total_seconds()
        return age > self.status_refresh_seconds

    @staticmethod
    def _status_is_newer(candidate: ProviderStatus, current: ProviderStatus) -> bool:
        if candidate.checked_at is None:
            return False
        if current.checked_at is None:
            return True
        candidate_checked_at = candidate.checked_at
        current_checked_at = current.checked_at
        if candidate_checked_at.tzinfo is None:
            candidate_checked_at = candidate_checked_at.replace(tzinfo=timezone.utc)
        if current_checked_at.tzinfo is None:
            current_checked_at = current_checked_at.replace(tzinfo=timezone.utc)
        return candidate_checked_at > current_checked_at

    @staticmethod
    def _status_process_alive(status: ProviderStatus) -> bool:
        if status.setup_pid is None:
            return False
        try:
            process = psutil.Process(status.setup_pid)
            checked_at = status.checked_at
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


class DouyinAwemeProviderMixin(ABC):
    @abstractmethod
    def fetch_douyin_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        """采集并清洗抖音作品数据。"""


class XiaohongshuAwemeProviderMixin(ABC):
    @abstractmethod
    def fetch_xiaohongshu_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        """采集并清洗小红书作品数据。"""


class WeChatChannelsAwemeProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_channels_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        """采集并清洗视频号作品数据。"""


class WeChatMpAwemeProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_mp_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        """采集并清洗公众号文章数据。"""


class DouyinCommentProviderMixin(ABC):
    @abstractmethod
    def fetch_douyin_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """采集并清洗抖音评论数据。"""


class XiaohongshuCommentProviderMixin(ABC):
    @abstractmethod
    def fetch_xiaohongshu_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """采集并清洗小红书评论数据。"""


class WeChatChannelsCommentProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_channels_comments(
        self, request: FetchCommentsRequest
    ) -> CommentPage:
        """采集并清洗视频号评论数据。"""


class WeChatMpCommentProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_mp_comments(self, request: FetchCommentsRequest) -> CommentPage:
        """采集并清洗公众号评论数据。"""


class DouyinAccountProviderMixin(ABC):
    @abstractmethod
    def fetch_douyin_account(self, request: FetchAccountRequest) -> AccountData:
        """采集并清洗抖音账号数据。"""

    @abstractmethod
    def fetch_douyin_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """分页补全抖音账号发布历史。

        Provider 应从 request.progress_payload 或 request.cursor 中恢复游标，
        持续翻页直到满足 max_count、max_pages、earliest_date 任一停止条件，
        或平台侧没有更多数据。返回 AccountAwemePage.done 表示本次 request
        是否结束；has_more 表示平台侧是否仍有更早作品；progress_payload
        是下次继续补历史时要持久化并传回的进度。
        """

    def fetch_latest_douyin_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """拉取抖音账号最新发布作品。

        Provider 应从账号首页或平台最新列表第一页开始，不读取长期历史游标。
        停止条件仍由 request.stop_policy 控制。返回的 progress_payload 只用于
        本次请求结果记录，不应作为下一次最新同步的强依赖。
        """
        return self.fetch_douyin_account_awemes(
            replace(request, cursor=None, progress_payload={})
        )


class XiaohongshuAccountProviderMixin(ABC):
    @abstractmethod
    def fetch_xiaohongshu_account(self, request: FetchAccountRequest) -> AccountData:
        """采集并清洗小红书账号数据。"""

    @abstractmethod
    def fetch_xiaohongshu_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """分页补全小红书账号发布历史。

        语义同 fetch_douyin_account_awemes：历史补全读取并返回 progress_payload，
        停止条件由 request.stop_policy 控制，has_more 仅表达平台侧是否还有更早作品。
        """

    def fetch_latest_xiaohongshu_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """拉取小红书账号最新发布笔记。

        从最新列表第一页开始，不读取长期历史游标；停止条件由 request.stop_policy
        控制，progress_payload 只作为本次同步摘要。
        """
        return self.fetch_xiaohongshu_account_awemes(
            replace(request, cursor=None, progress_payload={})
        )


class WeChatChannelsAccountProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_channels_account(
        self,
        request: FetchAccountRequest,
    ) -> AccountData:
        """采集并清洗视频号账号数据。"""

    @abstractmethod
    def fetch_wechat_channels_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """分页补全视频号账号发布历史。

        语义同 fetch_douyin_account_awemes：历史补全读取并返回 progress_payload，
        停止条件由 request.stop_policy 控制，has_more 仅表达平台侧是否还有更早作品。
        """

    def fetch_latest_wechat_channels_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """拉取视频号账号最新发布视频。

        从最新列表第一页开始，不读取长期历史游标；停止条件由 request.stop_policy
        控制，progress_payload 只作为本次同步摘要。
        """
        return self.fetch_wechat_channels_account_awemes(
            replace(request, cursor=None, progress_payload={})
        )


class WeChatMpAccountProviderMixin(ABC):
    @abstractmethod
    def fetch_wechat_mp_account(self, request: FetchAccountRequest) -> AccountData:
        """采集并清洗公众号账号数据。"""

    @abstractmethod
    def fetch_wechat_mp_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """分页补全公众号发布历史。

        语义同 fetch_douyin_account_awemes：历史补全读取并返回 progress_payload，
        停止条件由 request.stop_policy 控制，has_more 仅表达平台侧是否还有更早文章。
        """

    def fetch_latest_wechat_mp_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        """拉取公众号最新发布文章。

        从最新列表第一页开始，不读取长期历史游标；停止条件由 request.stop_policy
        控制，progress_payload 只作为本次同步摘要。
        """
        return self.fetch_wechat_mp_account_awemes(
            replace(request, cursor=None, progress_payload={})
        )


class VideoTranscriptionProviderMixin(ABC):
    @abstractmethod
    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        """转写视频。"""
