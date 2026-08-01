from __future__ import annotations

from typing import Any

from .output import CLIResult, cli_result, serializable


AWEME_LIST_FIELDS = (
    "id",
    "account_id",
    "platform",
    "content_type",
    "platform_aweme_id",
    "source_url",
    "title",
    "published_at",
    "play_count",
    "like_count",
    "collect_count",
    "share_count",
    "comment_count",
    "collection_status",
    "media_download_status",
    "comment_collection_status",
    "video_transcription_status",
    "video_path",
    "photo_paths",
    "updated_at",
)
ACCOUNT_LIST_FIELDS = (
    "id",
    "platform",
    "platform_account_id",
    "profile_url",
    "display_name",
    "collection_status",
    "last_collected_at",
    "updated_at",
)
TRANSCRIPTION_LIST_FIELDS = (
    "id",
    "aweme_id",
    "source_url",
    "video_path",
    "status",
    "progress",
    "attempt_count",
    "error_message",
    "updated_at",
)
GROUP_LIST_FIELDS = (
    "id",
    "name",
    "description",
    "color",
    "sort_order",
    "status",
    "updated_at",
)
STORE_LIST_FIELDS = (
    "id",
    "name",
    "type",
    "status",
    "default",
    "conflict_policy",
    "last_validated_at",
    "validation_error",
    "updated_at",
)
SYNC_LIST_FIELDS = (
    "id",
    "aweme_id",
    "account_id",
    "data_storer_id",
    "status",
    "enabled",
    "remote_record_id",
    "remote_url",
    "last_synced_at",
    "attempt_count",
    "error_message",
    "updated_at",
)


def _summaries(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    items = serializable(value)
    return [
        {
            field: item.get(field)
            for field in fields
            if field in item
        }
        for item in items
    ]


def _paged_result(
    value: Any,
    *,
    view: str,
    fields: tuple[str, ...],
    page: int,
    size: int,
) -> CLIResult:
    items = _summaries(value, fields)
    return cli_result(
        value,
        view=view,
        json_value={
            "items": items,
            "pagination": {
                "page": page,
                "size": size,
                "returned": len(items),
            },
        },
    )


def aweme_list_result(value: Any, *, page: int, size: int) -> CLIResult:
    return _paged_result(
        value,
        view="aweme_list",
        fields=AWEME_LIST_FIELDS,
        page=page,
        size=size,
    )


def account_list_result(value: Any, *, page: int, size: int) -> CLIResult:
    return _paged_result(
        value,
        view="account_list",
        fields=ACCOUNT_LIST_FIELDS,
        page=page,
        size=size,
    )


def transcription_list_result(
    value: Any,
    *,
    page: int,
    size: int,
) -> CLIResult:
    return _paged_result(
        value,
        view="transcription_list",
        fields=TRANSCRIPTION_LIST_FIELDS,
        page=page,
        size=size,
    )


def group_list_result(value: Any) -> CLIResult:
    items = _summaries(value, GROUP_LIST_FIELDS)
    return cli_result(
        value,
        view="group_list",
        json_value={"items": items, "count": len(items)},
    )


def store_list_result(value: Any) -> CLIResult:
    items = _summaries(value, STORE_LIST_FIELDS)
    return cli_result(
        value,
        view="store_list",
        json_value={"items": items, "count": len(items)},
    )


def store_type_list_result(value: Any) -> CLIResult:
    items = serializable(value)
    compact = [
        {
            "type": item.get("type"),
            "name": item.get("name"),
            "capabilities": item.get("capabilities", {}),
            "parameters": [
                {
                    "key": parameter.get("key"),
                    "type": parameter.get("type"),
                    "required": parameter.get("required", False),
                }
                for parameter in item.get("parameters", [])
            ],
        }
        for item in items
    ]
    return cli_result(
        value,
        view="store_type_list",
        json_value={"items": compact, "count": len(compact)},
    )


def sync_list_result(value: Any) -> CLIResult:
    items = _summaries(value, SYNC_LIST_FIELDS)
    return cli_result(
        value,
        view="sync_list",
        json_value={"items": items, "count": len(items)},
    )


__all__ = [
    "account_list_result",
    "aweme_list_result",
    "group_list_result",
    "store_list_result",
    "store_type_list_result",
    "sync_list_result",
    "transcription_list_result",
]
