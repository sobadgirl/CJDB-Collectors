from __future__ import annotations

from enum import Enum, StrEnum


class TaskStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class SyncObjectType(str, Enum):
    AWEME = "aweme"
    ACCOUNT = "account"
    VIDEO_TRANSCRIPTION = "video_transcription"


class ContentType(str, Enum):
    UNKNOWN = "unknown"
    VIDEO = "video"
    IMAGE = "image"
    ARTICLE = "article"
    LIVE = "live"


class AwemeDataSource(str, Enum):
    DIRECT_PROVIDER = "direct_provider"
    ACCOUNT_HISTORY = "account_history"


class Platform(StrEnum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_MP = "wechat_mp"
    WECHAT_CHANNELS = "wechat_channels"


class ProjectStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class CommentKind(str, Enum):
    COMMENT = "comment"
    REPLY = "reply"


class WorkerTaskType(str, Enum):
    DATA_COLLECT = "data_collect"
    # V1.0 发布隐藏：账号/作者历史采集任务类型保留用于历史数据兼容，不参与调度。
    ACCOUNT_HISTORY_COLLECT = "account_history_collect"
    MEDIA_DOWNLOAD = "media_download"
    VIDEO_TRANSCRIPTION = "video_transcription"
    # V1.0 发布隐藏：评论采集任务类型保留用于历史数据兼容，不参与调度。
    COMMENT_COLLECT = "comment_collect"
    DATA_SYNC = "data_sync"


class WorkerSubject(str, Enum):
    ACCOUNT = "account"
    AWEME = "aweme"
    VIDEO_TRANSCRIPTION = "video_transcription"
    AWEME_SYNC = "aweme_sync"
    ACCOUNT_SYNC = "account_sync"
    VIDEO_TRANSCRIPTION_SYNC = "video_transcription_sync"


class WorkerTaskStatus(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    TIMEOUT = "timeout"


TASK_STATUS_DISPLAY = {
    TaskStatus.NOT_REQUESTED: "未开始",
    TaskStatus.PENDING: "等待中",
    TaskStatus.RUNNING: "进行中",
    TaskStatus.SUCCEEDED: "已完成",
    TaskStatus.FAILED: "失败",
    TaskStatus.RETRY_WAIT: "等待重试",
    TaskStatus.TIMEOUT: "已超时",
    TaskStatus.CANCELLED: "已取消",
}

CONTENT_TYPE_DISPLAY = {
    ContentType.UNKNOWN: "待识别",
    ContentType.VIDEO: "视频",
    ContentType.IMAGE: "图文",
    ContentType.ARTICLE: "文章",
    ContentType.LIVE: "直播",
}

PLATFORM_DISPLAY = {
    Platform.DOUYIN: "抖音",
    Platform.XIAOHONGSHU: "小红书",
    Platform.WECHAT_MP: "公众号",
    Platform.WECHAT_CHANNELS: "视频号",
}


def display_task_status(value: TaskStatus | str | None) -> str:
    if value is None:
        return "-"
    try:
        status = value if isinstance(value, TaskStatus) else TaskStatus(str(value))
    except ValueError:
        return str(value)
    return TASK_STATUS_DISPLAY[status]


def display_content_type(value: ContentType | str | None) -> str:
    if value is None:
        return "-"
    try:
        content_type = (
            value if isinstance(value, ContentType) else ContentType(str(value))
        )
    except ValueError:
        return str(value)
    return CONTENT_TYPE_DISPLAY[content_type]


def display_platform(value: Platform | str | None) -> str:
    if value is None:
        return "-"
    try:
        platform = value if isinstance(value, Platform) else Platform(str(value))
    except ValueError:
        return str(value)
    return PLATFORM_DISPLAY[platform]
