from __future__ import annotations

from enum import StrEnum


class ProviderSelectionMode(StrEnum):
    SINGLE = "single"
    MULTIPLE = "multiple"


class ProviderType(StrEnum):
    DOUYIN_AWEME_COLLECT = "douyin_aweme_collect"
    XIAOHONGSHU_AWEME_COLLECT = "xiaohongshu_aweme_collect"
    WECHAT_CHANNELS_AWEME_COLLECT = "wechat_channels_aweme_collect"
    WECHAT_MP_AWEME_COLLECT = "wechat_mp_aweme_collect"
    # V1.0 发布隐藏：评论/账号采集类型保留用于历史数据和配置兼容，
    # 但 Provider 注册、页面入口和 Worker 调度都会跳过这些类型。
    DOUYIN_COMMENT_COLLECT = "douyin_comment_collect"
    XIAOHONGSHU_COMMENT_COLLECT = "xiaohongshu_comment_collect"
    WECHAT_CHANNELS_COMMENT_COLLECT = "wechat_channels_comment_collect"
    WECHAT_MP_COMMENT_COLLECT = "wechat_mp_comment_collect"
    DOUYIN_ACCOUNT_COLLECT = "douyin_account_collect"
    XIAOHONGSHU_ACCOUNT_COLLECT = "xiaohongshu_account_collect"
    WECHAT_CHANNELS_ACCOUNT_COLLECT = "wechat_channels_account_collect"
    WECHAT_MP_ACCOUNT_COLLECT = "wechat_mp_account_collect"
    VIDEO_TRANSCRIPTION = "video_transcription"
    STORE_AWEME = "store_aweme"
    STORE_ACCOUNT = "store_account"
    STORE_VIDEO_TRANSCRIPTION = "store_video_transcription"

    @property
    def selection_mode(self) -> ProviderSelectionMode:
        if self.value.startswith("store_"):
            return ProviderSelectionMode.MULTIPLE
        return ProviderSelectionMode.SINGLE


__all__ = ["ProviderSelectionMode", "ProviderType"]
