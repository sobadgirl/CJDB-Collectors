from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import logging
from typing import Any

import httpx

from cjdb_collectors.models import ContentType
from cjdb_collectors.services.data_providers import register_data_provider

from ...base import (
    BaseDataProvider,
    DouyinAccountProviderMixin,
    DouyinAwemeProviderMixin,
    DouyinCommentProviderMixin,
    WeChatChannelsAccountProviderMixin,
    WeChatChannelsAwemeProviderMixin,
    WeChatChannelsCommentProviderMixin,
    WeChatMpAccountProviderMixin,
    WeChatMpAwemeProviderMixin,
    WeChatMpCommentProviderMixin,
    XiaohongshuAccountProviderMixin,
    XiaohongshuAwemeProviderMixin,
    XiaohongshuCommentProviderMixin,
)
from ...types import (
    AccountData,
    AccountAwemePage,
    AwemeData,
    CommentPage,
    DataProviderType,
    FetchAccountAwemesRequest,
    FetchAccountRequest,
    FetchAwemeRequest,
    FetchCommentsRequest,
    PageStopPolicy,
    SetupResult,
    ProviderStatus,
    number_param,
    password_param,
    single_select_param,
)


class TikHubError(RuntimeError):
    pass


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value /= 1000
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def page_policy(stop_policy: PageStopPolicy | None) -> PageStopPolicy:
    return stop_policy or PageStopPolicy(max_pages=1)


def request_cursor(
    cursor: str | None,
    progress_payload: dict[str, Any],
    default: str = "",
) -> str:
    return str(progress_payload.get("cursor") or cursor or default)


def reaches_earliest(
    published_at: datetime | None,
    earliest_date: datetime | None,
) -> bool:
    if published_at is None or earliest_date is None:
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if earliest_date.tzinfo is None:
        earliest_date = earliest_date.replace(tzinfo=timezone.utc)
    return published_at < earliest_date


def comment_author(
    *,
    user: dict[str, Any],
    fallback_name: str | None = None,
    fallback_avatar_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(
            user.get("uid")
            or user.get("userid")
            or user.get("user_id")
            or user.get("id")
            or user.get("username")
            or ""
        ),
        "name": user.get("nickname")
        or user.get("nick_name")
        or user.get("name")
        or fallback_name,
        "avatar_url": user.get("avatar_url")
        or user.get("avatar")
        or user.get("image")
        or fallback_avatar_url,
    }


def finish_aweme_page(
    *,
    awemes: list[AwemeData],
    cursor: str | None,
    has_more: bool,
    page_count: int,
    stopped_by_date: bool,
    policy: PageStopPolicy,
    request: dict[str, Any],
) -> AccountAwemePage:
    if policy.max_count is not None:
        awemes = awemes[: policy.max_count]
    done = (
        stopped_by_date
        or not has_more
        or not cursor
        or (policy.max_pages is not None and page_count >= policy.max_pages)
        or (policy.max_count is not None and len(awemes) >= policy.max_count)
    )
    return AccountAwemePage(
        awemes=awemes,
        next_cursor=cursor,
        has_more=has_more,
        done=done,
        request=request,
        progress_payload={
            "cursor": cursor,
            "page_count": page_count,
            "fetched_count": len(awemes),
            "last_item_time": (
                awemes[-1].published_at.isoformat()
                if awemes and awemes[-1].published_at
                else None
            ),
        },
    )


def finish_comment_page(
    *,
    comments: list[dict[str, Any]],
    cursor: str | None,
    has_more: bool,
    page_count: int,
    stopped_by_date: bool,
    policy: PageStopPolicy,
    request: dict[str, Any],
) -> CommentPage:
    if policy.max_count is not None:
        comments = comments[: policy.max_count]
    done = (
        stopped_by_date
        or not has_more
        or not cursor
        or (policy.max_pages is not None and page_count >= policy.max_pages)
        or (policy.max_count is not None and len(comments) >= policy.max_count)
    )
    return CommentPage(
        comments=comments,
        next_cursor=cursor,
        has_more=has_more,
        done=done,
        request=request,
        progress_payload={
            "cursor": cursor,
            "page_count": page_count,
            "fetched_count": len(comments),
            "last_item_time": comments[-1].get("created_at") if comments else None,
        },
    )


@register_data_provider
class TikHubProvider(
    BaseDataProvider,
    DouyinAwemeProviderMixin,
    XiaohongshuAwemeProviderMixin,
    WeChatChannelsAwemeProviderMixin,
    WeChatMpAwemeProviderMixin,
    DouyinCommentProviderMixin,
    XiaohongshuCommentProviderMixin,
    WeChatChannelsCommentProviderMixin,
    WeChatMpCommentProviderMixin,
    DouyinAccountProviderMixin,
    XiaohongshuAccountProviderMixin,
    WeChatChannelsAccountProviderMixin,
    WeChatMpAccountProviderMixin,
):
    namespace = "tikhub"
    name = "TikHub"
    supported_types = (
        DataProviderType.DOUYIN_AWEME_COLLECT,
        DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
        DataProviderType.WECHAT_MP_AWEME_COLLECT,
        # V1.0 发布隐藏：评论采集和账号/作者采集能力暂不注册，避免被误启动。
        # DataProviderType.DOUYIN_COMMENT_COLLECT,
        # DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
        # DataProviderType.WECHAT_CHANNELS_COMMENT_COLLECT,
        # DataProviderType.WECHAT_MP_COMMENT_COLLECT,
        # DataProviderType.DOUYIN_ACCOUNT_COLLECT,
        # DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
        # DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
        # DataProviderType.WECHAT_MP_ACCOUNT_COLLECT,
    )
    parameters = (
        password_param(
            "api_key",
            "接口密钥",
            required=True,
            help="TikHub 接口密钥。",
        ),
        single_select_param(
            "base_url",
            "服务地址",
            required=True,
            default="https://api.tikhub.dev",
            options=[
                {
                    "value": "https://api.tikhub.dev",
                    "label": "中国大陆：https://api.tikhub.dev",
                },
                {
                    "value": "https://api.tikhub.io",
                    "label": "中国大陆以外：https://api.tikhub.io",
                },
            ],
        ),
        number_param(
            "timeout_seconds",
            "超时时间",
            required=True,
            default=30,
        ),
    )

    def __init__(
        self,
        setup_payload: dict[str, Any] | None = None,
        *,
        client: httpx.Client | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(setup_payload, logger=logger)
        self.client = client or httpx.Client(
            timeout=float(self.setup_payload.get("timeout_seconds") or 30),
            trust_env=False,
        )

    @property
    def base_url(self) -> str:
        return str(self.setup_payload.get("base_url") or "https://api.tikhub.dev").rstrip(
            "/"
        )

    @property
    def headers(self) -> dict[str, str]:
        api_key = self.setup_payload.get("api_key")
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def request_envelope(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                **kwargs,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TikHubError(str(exc)) from exc
        if not isinstance(payload, dict):
            raise TikHubError("TikHub response must be a JSON object")
        code = payload.get("code", 200)
        if isinstance(code, int) and code >= 400:
            raise TikHubError(str(payload.get("message_zh") or payload.get("message") or code))
        return payload

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.request_envelope(method, path, **kwargs).get("data")

    def get_user_info(self) -> dict[str, Any]:
        return self.request_envelope("GET", "/api/v1/tikhub/user/get_user_info")

    def refresh_status(self) -> ProviderStatus:
        configured = dict(self.setup_payload)
        values = {
            parameter.key: configured.get(parameter.key, parameter.default)
            for parameter in self.parameters
        }
        details = {
            "base_url": str(values.get("base_url") or "https://api.tikhub.dev").rstrip("/"),
            "configured_parameters": {
                parameter.key: bool(values.get(parameter.key))
                for parameter in self.parameters
            },
            "values": {
                parameter.key: (
                    "***configured***"
                    if parameter.type.value == "password"
                    and values.get(parameter.key)
                    else values.get(parameter.key) or ""
                )
                for parameter in self.parameters
            },
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
                details=details,
            )

        try:
            user_info = self.get_user_info()
        except (TikHubError, httpx.HTTPError, ValueError) as exc:
            return ProviderStatus(
                status="unavailable",
                message=str(exc),
                details=details,
            )

        api_key_data = user_info.get("api_key_data") or {}
        user_data = user_info.get("user_data") or {}
        account = {
            "api_key_name": api_key_data.get("api_key_name"),
            "api_key_status": api_key_data.get("api_key_status"),
            "email": user_data.get("email"),
            "balance": user_data.get("balance"),
            "free_credit": user_data.get("free_credit"),
            "email_verified": user_data.get("email_verified"),
            "account_disabled": user_data.get("account_disabled"),
            "is_active": user_data.get("is_active"),
        }
        return ProviderStatus(
            status="ready",
            message="TikHub 连接可用",
            details={**details, "account": account},
        )

    def setup(self, params: dict[str, Any]) -> SetupResult:
        self.logger.info("已保存 TikHub Provider 配置")
        return SetupResult(
            success=True,
            message="TikHub Provider 配置已保存",
            setup_payload=dict(params),
        )

    def fetch_douyin_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        aweme_id = request.platform_aweme_id or ""
        if not aweme_id:
            raise ValueError("Douyin aweme collection requires aweme_id")
        data = self.request(
            "GET",
            "/api/v1/douyin/app/v3/fetch_one_video",
            params={"aweme_id": aweme_id},
        )
        detail = data["aweme_detail"]
        statistics = detail.get("statistics") or {}
        statistics_enrichment: dict[str, Any] = {"status": "not_requested"}
        try:
            stats_data = self.request(
                "GET",
                "/api/v1/douyin/app/v3/fetch_video_statistics",
                params={"aweme_ids": str(detail["aweme_id"])},
            )
            statistics = {**statistics, **stats_data["statistics_list"][0]}
            statistics_enrichment = {
                "status": "succeeded",
                "source": "fetch_video_statistics",
            }
        except (KeyError, IndexError, TikHubError) as exc:
            statistics_enrichment = {"status": "failed", "error": str(exc)}

        video = detail.get("video") or {}
        video_url = (video.get("play_addr") or {}).get("url_list", [None])[0]
        video_enrichment: dict[str, Any] = {"status": "not_requested"}
        if video_url:
            try:
                play_url_data = self.request(
                    "GET",
                    "/api/v1/douyin/app/v3/fetch_video_high_quality_play_url",
                    params={
                        "aweme_id": str(detail["aweme_id"]),
                        "share_url": request.source_url,
                        "region": "CN",
                    },
                )
                video_url = play_url_data["video_url"]
                video_enrichment = {
                    "status": "succeeded",
                    "source": "fetch_video_high_quality_play_url",
                    "quality": play_url_data.get("quality"),
                    "size": play_url_data.get("size"),
                }
            except (KeyError, TikHubError) as exc:
                video_enrichment = {"status": "failed", "error": str(exc)}
        author = detail.get("author") or {}
        images = (detail.get("image_post_info") or {}).get("images") or []
        photos = [
            image["url_list"][0]
            for image in images
            if image.get("url_list")
        ]
        return AwemeData(
            platform_aweme_id=str(detail["aweme_id"]),
            platform_account_id=author.get("sec_uid"),
            content_type=ContentType.IMAGE if images else ContentType.VIDEO,
            title=detail.get("desc") or detail.get("title"),
            description=detail.get("desc"),
            published_at=parse_timestamp(detail.get("create_time")),
            video_url=video_url,
            cover_url=(video.get("cover") or {}).get("url_list", [None])[0],
            photos=photos,
            play_count=to_int(statistics.get("play_count")),
            like_count=to_int(statistics.get("digg_count")),
            comment_count=to_int(statistics.get("comment_count")),
            share_count=to_int(statistics.get("share_count")),
            collect_count=to_int(statistics.get("collect_count")),
            extra_data_json={
                "author": {
                    "id": str(author.get("uid") or ""),
                    "sec_uid": author.get("sec_uid"),
                    "name": author.get("nickname"),
                },
                "music": detail.get("music") or {},
                "source_url": (detail.get("share_info") or {}).get("share_url"),
                "statistics_enrichment": statistics_enrichment,
                "video_enrichment": video_enrichment,
            },
        )

    def fetch_xiaohongshu_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        note_id = request.platform_aweme_id or ""
        if request.content_type == ContentType.VIDEO:
            data = self.request(
                "GET",
                "/api/v1/xiaohongshu/app/v2/get_video_note_detail",
                params={"note_id": note_id, "share_text": request.source_url or ""},
            )
            return self.xiaohongshu_aweme_from_video_detail(data)

        data = self.request(
            "GET",
            "/api/v1/xiaohongshu/app/v2/get_image_note_detail",
            params={"note_id": note_id, "share_text": request.source_url or ""},
        )
        note = data["data"][0]["note_list"][0]
        if request.content_type == ContentType.UNKNOWN and note.get("type") == "video":
            return self.fetch_xiaohongshu_aweme(
                replace(request, content_type=ContentType.VIDEO)
            )
        return self.xiaohongshu_aweme_from_note(note)

    def xiaohongshu_aweme_from_video_detail(self, data: dict[str, Any]) -> AwemeData:
        note = data["data"][0]
        aweme = self.xiaohongshu_aweme_from_note(note)
        stream = note["video_info_v2"]["media"]["stream"]
        h264 = stream["h264"][0]
        return aweme.model_copy(
            update={
                "content_type": ContentType.VIDEO,
                "video_url": h264["master_url"],
            }
        )

    def xiaohongshu_aweme_from_note(self, note: dict[str, Any]) -> AwemeData:
        user = note.get("user") or {}
        images = note.get("images_list") or []
        photos = [
            image.get("original") or image.get("url_size_large") or image.get("url")
            for image in images
            if image.get("original") or image.get("url_size_large") or image.get("url")
        ]
        share_info = note.get("share_info") or {}
        return AwemeData(
            platform_aweme_id=str(note["id"]),
            platform_account_id=user.get("userid") or user.get("id"),
            content_type=ContentType.VIDEO
            if note.get("type") == "video"
            else ContentType.IMAGE,
            title=note.get("title"),
            description=note.get("desc"),
            published_at=parse_timestamp(note.get("time") or note.get("last_update_time")),
            video_url=None,
            cover_url=share_info.get("image") or (photos[0] if photos else None),
            photos=photos,
            play_count=to_int(note.get("view_count")),
            like_count=to_int(note.get("liked_count")),
            comment_count=to_int(note.get("comments_count")),
            share_count=to_int(note.get("shared_count")),
            collect_count=to_int(note.get("collected_count")),
            extra_data_json={
                "xsec_token": note.get("xsec_token"),
                "share_url": share_info.get("link"),
                "author": {
                    "id": user.get("userid") or user.get("id"),
                    "name": user.get("nickname") or user.get("name"),
                },
            },
        )

    def fetch_wechat_channels_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        data = self.request(
            "POST",
            "/api/v1/wechat/channels/v2/fetch_video_detail",
            json={
                "object_id": request.platform_aweme_id or "",
                "export_id": "",
                "object_nonce_id": "",
                "share_url": request.source_url,
                "raw": False,
            },
        )
        media = data.get("media") or {}
        return AwemeData(
            platform_aweme_id=str(data["id"]),
            platform_account_id=data.get("finder_username") or data.get("username"),
            content_type=ContentType.VIDEO,
            title=data.get("title") or data.get("description"),
            description=data.get("description"),
            published_at=parse_timestamp(data.get("create_time")),
            video_url=media.get("full_url"),
            cover_url=data.get("cover_url"),
            play_count=to_int(data.get("read_count")),
            like_count=to_int(data.get("like_count")),
            comment_count=to_int(data.get("comment_count")),
            share_count=to_int(data.get("share_count")),
            collect_count=to_int(data.get("fav_count")),
            extra_data_json={"decode_key": media.get("decode_key")},
        )

    def fetch_wechat_mp_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        data = self.request(
            "POST",
            "/api/v1/wechat/media_platform/v2/fetch_article_detail",
            json={"url": request.source_url or request.platform_aweme_id or "", "raw": False},
        )
        content = data["content"]
        return AwemeData(
            platform_aweme_id=str(content["msgId"]),
            platform_account_id=str(data.get("bizUin") or content.get("biz")),
            content_type=ContentType.ARTICLE,
            title=content.get("title"),
            description=content.get("desc"),
            published_at=parse_timestamp(content.get("publish_time")),
            cover_url=content.get("cover"),
            play_count=to_int(content.get("read_count")),
            like_count=to_int(content.get("like_count")),
            comment_count=to_int(content.get("comment_count")),
            extra_data_json={
                "content": content.get("content"),
                "comment_id": content.get("comment_id"),
                "item_index": data.get("itemIdx") or content.get("itemidx"),
                "author_name": content.get("nick_name"),
            },
        )

    def fetch_douyin_comments(self, request: FetchCommentsRequest) -> CommentPage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload, "0")
        snapshot = {
            "cursor": cursor,
            "page_size": min(request.max_comments or 20, 50),
            "stop_policy": policy.model_dump(mode="json"),
        }
        comments: list[dict[str, Any]] = []
        page_count = 0
        has_more = False
        stopped_by_date = False
        while True:
            data = self.request(
                "GET",
                "/api/v1/douyin/app/v3/fetch_video_comments",
                params={
                    "aweme_id": request.platform_aweme_id,
                    "cursor": cursor,
                    "count": snapshot["page_size"],
                },
            )
            page_count += 1
            for item in data.get("comments") or []:
                created_at = parse_timestamp(item.get("create_time"))
                if reaches_earliest(created_at, policy.earliest_date):
                    stopped_by_date = True
                    continue
                user = item.get("user") or {}
                comments.append(
                    {
                        "id": str(item["cid"]),
                        "text": item.get("text") or "",
                        "created_at": item.get("create_time"),
                        "like_count": item.get("digg_count") or 0,
                        "reply_count": item.get("reply_comment_total") or 0,
                        "author": comment_author(user=user),
                    }
                )
            cursor = str(data.get("cursor") or "")
            has_more = bool(data.get("has_more"))
            if (
                stopped_by_date
                or not has_more
                or not cursor
                or (policy.max_pages is not None and page_count >= policy.max_pages)
                or (policy.max_count is not None and len(comments) >= policy.max_count)
            ):
                break
        return finish_comment_page(
            comments=comments,
            cursor=cursor,
            has_more=has_more,
            page_count=page_count,
            stopped_by_date=stopped_by_date,
            policy=policy,
            request=snapshot,
        )

    def fetch_xiaohongshu_comments(self, request: FetchCommentsRequest) -> CommentPage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "GET",
            "/api/v1/xiaohongshu/app/v2/get_note_comments",
            params={"note_id": request.platform_aweme_id, "cursor": cursor},
        )
        comments = [
            {
                "id": str(item["id"]),
                "text": item.get("content") or "",
                "created_at": item.get("create_time"),
                "like_count": item.get("like_count") or 0,
                "reply_count": item.get("sub_comment_count") or 0,
                "author": comment_author(user=item.get("user_info") or {}),
            }
            for item in data.get("comments") or []
        ]
        return finish_comment_page(
            comments=comments,
            cursor=data.get("cursor"),
            has_more=bool(data.get("has_more")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.max_comments or 20,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def fetch_wechat_channels_comments(
        self,
        request: FetchCommentsRequest,
    ) -> CommentPage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "POST",
            "/api/v1/wechat/channels/v2/fetch_video_comments",
            json={
                "object_id": request.platform_aweme_id,
                "last_buffer": cursor,
                "raw": False,
            },
        )
        comments = [
            {
                "id": str(item["id"]),
                "text": item.get("content") or "",
                "created_at": item.get("create_time"),
                "like_count": item.get("like_count") or 0,
                "reply_count": item.get("reply_count") or 0,
                "author": comment_author(user=item.get("user") or {}),
            }
            for item in data.get("comments") or []
        ]
        return finish_comment_page(
            comments=comments,
            cursor=data.get("last_buffer") or data.get("buffer"),
            has_more=bool(data.get("continue_flag") or data.get("has_more")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.max_comments or 20,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def fetch_wechat_mp_comments(self, request: FetchCommentsRequest) -> CommentPage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "POST",
            "/api/v1/wechat/media_platform/v2/fetch_article_comments",
            json={
                "url": request.source_url or request.platform_aweme_id,
                "buffer": cursor,
                "raw": False,
            },
        )
        comments = [
            {
                "id": str(item["content_id"]),
                "text": item.get("content") or "",
                "created_at": item.get("create_time"),
                "like_count": item.get("like_num") or 0,
                "reply_count": item.get("reply_total") or 0,
                "author": comment_author(
                    user={},
                    fallback_name=item.get("nick_name"),
                    fallback_avatar_url=item.get("logo_url"),
                ),
            }
            for item in data.get("comments") or []
        ]
        return finish_comment_page(
            comments=comments,
            cursor=data.get("buffer"),
            has_more=bool(data.get("has_more") or data.get("buffer")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.max_comments or 20,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def fetch_douyin_account(self, request: FetchAccountRequest) -> AccountData:
        sec_uid = request.platform_account_id or ""
        if not sec_uid:
            raise ValueError("Douyin account collection requires sec_uid")
        data = self.request(
            "GET",
            "/api/v1/douyin/app/v3/handler_user_profile",
            params={"sec_user_id": sec_uid},
        )
        user = data["user"]
        avatar = (user.get("avatar_thumb") or {}).get("url_list", [None])[0]
        return AccountData(
            platform_account_id=user.get("sec_uid"),
            display_name=user.get("nickname"),
            avatar_url=avatar,
            signature=user.get("signature"),
            ip_location=user.get("ip_location"),
            gender=user.get("gender"),
            verified=bool(user.get("enterprise_verify_reason")),
            follower_count=to_int(user.get("follower_count")),
            following_count=to_int(user.get("following_count")),
            work_count=to_int(user.get("aweme_count")),
            like_count=to_int(user.get("total_favorited")),
            total_favorited=to_int(user.get("total_favorited")),
            extra_data_json={
                "uid": user.get("uid"),
                "create_time": user.get("create_time"),
            },
        )

    def fetch_douyin_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload, "0")
        snapshot = {
            "cursor": cursor,
            "page_size": request.page_size,
            "stop_policy": policy.model_dump(mode="json"),
        }
        awemes: list[AwemeData] = []
        page_count = 0
        has_more = False
        stopped_by_date = False
        while True:
            data = self.request(
                "GET",
                "/api/v1/douyin/app/v3/fetch_user_post_videos",
                params={
                    "sec_user_id": request.platform_account_id or "",
                    "max_cursor": cursor,
                    "count": request.page_size,
                    "sort_type": 0,
                },
            )
            page_count += 1
            for item in data.get("aweme_list") or []:
                aweme = self.douyin_aweme_from_history_item(item)
                if reaches_earliest(aweme.published_at, policy.earliest_date):
                    stopped_by_date = True
                    continue
                awemes.append(aweme)
            cursor = str(data.get("cursor") or data.get("max_cursor") or "")
            has_more = bool(data.get("has_more"))
            if (
                stopped_by_date
                or not has_more
                or not cursor
                or (policy.max_pages is not None and page_count >= policy.max_pages)
                or (policy.max_count is not None and len(awemes) >= policy.max_count)
            ):
                break
        return finish_aweme_page(
            awemes=awemes,
            cursor=cursor,
            has_more=has_more,
            page_count=page_count,
            stopped_by_date=stopped_by_date,
            policy=policy,
            request=snapshot,
        )

    def douyin_aweme_from_history_item(self, item: dict[str, Any]) -> AwemeData:
        video = item.get("video") or {}
        statistics = item.get("statistics") or {}
        author = item.get("author") or {}
        return AwemeData(
            platform_aweme_id=str(item["aweme_id"]),
            platform_account_id=author.get("sec_uid"),
            content_type=ContentType.VIDEO,
            title=item.get("desc") or item.get("title"),
            description=item.get("desc"),
            published_at=parse_timestamp(item.get("create_time")),
            video_url=(video.get("play_addr") or {}).get("url_list", [None])[0],
            cover_url=(video.get("cover") or {}).get("url_list", [None])[0],
            play_count=to_int(statistics.get("play_count")),
            like_count=to_int(statistics.get("digg_count")),
            comment_count=to_int(statistics.get("comment_count")),
            share_count=to_int(statistics.get("share_count")),
            collect_count=to_int(statistics.get("collect_count")),
            extra_data_json={"source": "account_history"},
        )

    def fetch_xiaohongshu_account(
        self,
        request: FetchAccountRequest,
    ) -> AccountData:
        data = self.request(
            "GET",
            "/api/v1/xiaohongshu/app/v2/get_user_info",
            params={
                "user_id": request.platform_account_id or "",
                "share_text": request.profile_url,
            },
        )
        profile = data["data"]
        stats = profile.get("note_num_stat") or {}
        like_count = to_int(stats.get("liked") or profile.get("liked"))
        collect_count = to_int(stats.get("collected") or profile.get("collected"))
        return AccountData(
            platform_account_id=profile.get("userid"),
            display_name=profile.get("nickname"),
            avatar_url=profile.get("images"),
            signature=profile.get("desc"),
            location=profile.get("location"),
            ip_location=profile.get("ip_location"),
            gender=profile.get("gender") or profile.get("sex"),
            verified=profile.get("red_official_verified"),
            follower_count=to_int(profile.get("fans")),
            following_count=to_int(profile.get("follows")),
            work_count=to_int(stats.get("posted") or profile.get("ndiscovery")),
            like_count=like_count,
            collect_count=collect_count,
            total_favorited=(like_count or 0) + (collect_count or 0),
            extra_data_json={
                "red_id": profile.get("red_id"),
                "create_time": profile.get("create_time") or profile.get("created_at"),
            },
        )

    def fetch_xiaohongshu_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "GET",
            "/api/v1/xiaohongshu/app/v2/get_user_posted_notes",
            params={
                "user_id": request.platform_account_id or "",
                "share_text": request.profile_url,
                "cursor": cursor,
                "num": request.page_size,
            },
        )
        notes = [self.xiaohongshu_aweme_from_note(item) for item in data.get("notes") or []]
        return finish_aweme_page(
            awemes=notes,
            cursor=data.get("cursor"),
            has_more=bool(data.get("has_more")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.page_size,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def fetch_wechat_channels_account(
        self,
        request: FetchAccountRequest,
    ) -> AccountData:
        data = self.request(
            "POST",
            "/api/v1/wechat/channels/v2/fetch_user_profile",
            json={"username": request.platform_account_id or "", "raw": False},
        )
        return AccountData(
            platform_account_id=data.get("username"),
            display_name=data.get("nickname"),
            avatar_url=data.get("avatar_url"),
            signature=data.get("signature"),
            follower_count=to_int(data.get("follower_count")),
            work_count=to_int(data.get("feeds_count")),
            like_count=to_int(data.get("like_count")),
            extra_data_json={"finder_username": data.get("finder_username")},
        )

    def fetch_wechat_channels_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "POST",
            "/api/v1/wechat/channels/v2/fetch_user_videos",
            json={
                "username": request.platform_account_id or "",
                "last_buffer": cursor,
                "raw": False,
            },
        )
        awemes = [
            AwemeData(
                platform_aweme_id=str(item["id"]),
                platform_account_id=request.platform_account_id,
                content_type=ContentType.VIDEO,
                title=item.get("title") or item.get("description"),
                description=item.get("description"),
                published_at=parse_timestamp(item.get("create_time")),
                video_url=(item.get("media") or {}).get("full_url"),
                cover_url=item.get("cover_url"),
                play_count=to_int(item.get("read_count")),
                like_count=to_int(item.get("like_count")),
                comment_count=to_int(item.get("comment_count")),
                share_count=to_int(item.get("share_count")),
                collect_count=to_int(item.get("fav_count")),
                extra_data_json={"source": "account_history"},
            )
            for item in data.get("items") or []
        ]
        return finish_aweme_page(
            awemes=awemes,
            cursor=data.get("last_buffer") or data.get("buffer"),
            has_more=bool(data.get("continue_flag") or data.get("has_more")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.page_size,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def fetch_wechat_mp_account(self, request: FetchAccountRequest) -> AccountData:
        data = self.request(
            "POST",
            "/api/v1/wechat/media_platform/v2/fetch_account_profile",
            json={"username": request.platform_account_id or "", "raw": False},
        )
        return AccountData(
            platform_account_id=data.get("user_name") or data.get("username"),
            display_name=data.get("nick_name") or data.get("nickname"),
            avatar_url=data.get("head_url"),
            signature=data.get("signature"),
            verified=data.get("verify_type") is not None,
            extra_data_json={
                "ban_type": data.get("ban_type"),
                "service_type": data.get("service_type"),
                "verify_type": data.get("verify_type"),
            },
        )

    def fetch_wechat_mp_account_awemes(
        self,
        request: FetchAccountAwemesRequest,
    ) -> AccountAwemePage:
        policy = page_policy(request.stop_policy)
        cursor = request_cursor(request.cursor, request.progress_payload)
        data = self.request(
            "POST",
            "/api/v1/wechat/media_platform/v2/fetch_account_articles",
            json={
                "username": request.platform_account_id or "",
                "offset": int(cursor or 0),
                "count": request.page_size,
                "raw": False,
            },
        )
        awemes = [
            AwemeData(
                platform_aweme_id=str(item["msgid"]),
                platform_account_id=request.platform_account_id,
                content_type=ContentType.ARTICLE,
                title=item.get("title"),
                description=item.get("digest"),
                published_at=parse_timestamp(item.get("publish_time")),
                cover_url=item.get("cover"),
                extra_data_json={
                    "source": "account_history",
                    "url": item.get("link"),
                    "item_index": item.get("itemidx") or item.get("item_index"),
                },
            )
            for item in data.get("articles") or []
        ]
        next_cursor = str(data.get("next_offset") or "")
        return finish_aweme_page(
            awemes=awemes,
            cursor=next_cursor,
            has_more=bool(data.get("has_more")),
            page_count=1,
            stopped_by_date=False,
            policy=policy,
            request={
                "cursor": cursor,
                "page_size": request.page_size,
                "stop_policy": policy.model_dump(mode="json"),
            },
        )

    def close(self) -> None:
        self.client.close()
        super().close()


__all__ = ["TikHubError", "TikHubProvider"]
