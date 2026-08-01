from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from cjdb_collectors.models import ContentType, Platform
from cjdb_collectors.services.data_providers import register_data_provider

from ...base import AwemeProviderMixin, BaseDataProvider, CommentProviderMixin
from ...types import (
    AwemeData,
    CommentPage,
    DataProviderType,
    FetchAwemeRequest,
    FetchCommentsRequest,
    ProviderParameter,
    ProviderParameterType,
    ProviderSetupResult,
    ProviderStatus,
    ResolvedMedia,
    ResolveVideoRequest,
)


class TikHubError(RuntimeError):
    pass


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return _as_dict(value)


def _first_url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return value
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for key in (
            "full_url",
            "master_url",
            "backup_url",
            "url",
            "url_list",
            "urlList",
            "url_size_large",
            "original",
            "thumbnail",
            "first_frame",
        ):
            found = _first_url(value.get(key))
            if found:
                return found
        for item in value.values():
            found = _first_url(item)
            if found:
                return found
    return None


def _url_list(value: Any) -> list[str]:
    urls: list[str] = []

    def collect(candidate: Any) -> None:
        if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
            urls.append(candidate)
            return
        if isinstance(candidate, list):
            for item in candidate:
                collect(item)
            return
        if isinstance(candidate, dict):
            for key in (
                "full_url",
                "master_url",
                "backup_url",
                "url",
                "url_list",
                "urlList",
                "image_url",
                "url_size_large",
                "original",
                "thumbnail",
                "first_frame",
            ):
                collect(candidate.get(key))

    collect(value)
    return list(dict.fromkeys(urls))


def _video_url(value: Any) -> str | None:
    data = _as_dict(value)
    media = _as_dict(data.get("media"))
    stream = _as_dict(media.get("stream") or data.get("stream"))
    for key in ("h265", "h264", "av1", "h266"):
        found = _first_url(stream.get(key))
        if found:
            return found
    for key in ("master_url", "backup_url", "url", "url_list", "urlList"):
        found = _first_url(data.get(key))
        if found:
            return found
    return None


def _xiaohongshu_image_urls(value: Any) -> list[str]:
    if not isinstance(value, list):
        return _url_list(value)
    urls: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            found = _first_url(item)
        else:
            found = _first_url(
                item.get("original")
                or item.get("url_size_large")
                or item.get("url")
                or item
            )
        if found:
            urls.append(found)
    return list(dict.fromkeys(urls))


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value /= 1000
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.isdigit():
        return _timestamp(int(value))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _comments(value: Any) -> list[dict[str, Any]]:
    data = _as_dict(value)
    containers = [
        data,
        _as_dict(data.get("data")),
        _as_dict(data.get("comment_data")),
    ]
    raw: list[Any] = []
    for container in containers:
        candidates = (
            container.get("comments"),
            container.get("comment_list"),
            container.get("commentList"),
            container.get("commentInfo"),
            container.get("items"),
            container.get("list"),
        )
        raw = next((item for item in candidates if isinstance(item, list)), [])
        if raw:
            break
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        author = _as_dict(
            item.get("user") or item.get("author") or item.get("user_info")
        )
        cleaned.append(
            {
                "id": str(
                    item.get("cid")
                    or item.get("id")
                    or item.get("content_id")
                    or item.get("comment_id")
                    or item.get("commentId")
                    or ""
                ),
                "text": item.get("text")
                or item.get("content")
                or item.get("desc")
                or "",
                "created_at": item.get("create_time")
                or item.get("createTime")
                or item.get("time"),
                "like_count": item.get("digg_count")
                or item.get("like_count")
                or item.get("likeCount")
                or item.get("like_num")
                or 0,
                "reply_count": item.get("reply_comment_total")
                or item.get("sub_comment_count")
                or item.get("replyCount")
                or item.get("reply_total")
                or 0,
                "author": {
                    "id": str(
                        author.get("uid")
                        or author.get("user_id")
                        or author.get("id")
                        or author.get("username")
                        or item.get("username")
                        or item.get("openid")
                        or ""
                    ),
                    "name": author.get("nickname")
                    or author.get("name")
                    or author.get("nick_name")
                    or item.get("nickname")
                    or item.get("nick_name"),
                    "avatar_url": _first_url(
                        author.get("avatar")
                        or author.get("avatar_thumb")
                        or author.get("image")
                        or item.get("head_url")
                        or item.get("headUrl")
                        or item.get("logo_url")
                    ),
                },
            }
        )
    return cleaned


def _cursor(value: Any) -> str | None:
    data = _as_dict(value)
    nested = _as_dict(data.get("data"))
    cursor = next(
        (
            candidate
            for candidate in (
                data.get("next_cursor"),
                data.get("cursor"),
                data.get("max_cursor"),
                data.get("last_buffer"),
                data.get("lastBuffer"),
                data.get("buffer"),
                nested.get("next_cursor"),
                nested.get("cursor"),
                nested.get("last_buffer"),
                nested.get("buffer"),
            )
            if candidate not in (None, "")
        ),
        None,
    )
    if isinstance(cursor, dict):
        cursor = cursor.get("cursor")
    return str(cursor) if cursor not in (None, "") else None


class _Transport:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client or self._new_client(timeout_seconds)

    @staticmethod
    def _new_client(timeout_seconds: float) -> httpx.Client:
        return httpx.Client(timeout=timeout_seconds, trust_env=False)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.request_envelope(method, path, **kwargs).get("data")

    def request_envelope(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                **kwargs,
            )
            response.raise_for_status()
            envelope = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TikHubError(str(exc)) from exc
        if not isinstance(envelope, dict):
            raise TikHubError("TikHub response must be a JSON object")
        code = envelope.get("code", 200)
        if isinstance(code, int) and code >= 400:
            raise TikHubError(
                str(envelope.get("message_zh") or envelope.get("message") or code)
            )
        return envelope

    def close(self) -> None:
        self.client.close()

class DouyinAppV3API:
    prefix = "/api/v1/douyin/app/v3"

    def __init__(self, transport: _Transport) -> None:
        self._transport = transport

    def fetch_one_video(self, aweme_id: str) -> AwemeData:
        data = self._transport.request(
            "GET",
            f"{self.prefix}/fetch_one_video",
            params={"aweme_id": aweme_id},
        )
        root = _as_dict(data)
        root = _as_dict(root.get("data")) or root
        detail = (
            _as_dict(root.get("aweme_detail"))
            or _as_dict(root.get("aweme"))
            or _first_dict(root.get("aweme_list"))
            or _first_dict(root.get("item_list"))
            or root
        )
        stats = _as_dict(detail.get("statistics") or detail.get("stats"))
        video = _as_dict(detail.get("video"))
        author = _as_dict(detail.get("author"))
        images = detail.get("images") or _as_dict(detail.get("image_post_info")).get(
            "images"
        )
        cover_url = _first_url(
            video.get("cover") or video.get("origin_cover") or detail.get("cover")
        )
        photos = _xiaohongshu_image_urls(images)
        return AwemeData(
            platform_aweme_id=str(
                detail.get("aweme_id") or detail.get("item_id") or aweme_id
            ),
            content_type=ContentType.IMAGE if images else ContentType.VIDEO,
            title=detail.get("desc") or detail.get("title"),
            description=detail.get("desc"),
            published_at=_timestamp(detail.get("create_time")),
            video_url=_first_url(
                video.get("play_addr")
                or video.get("download_addr")
                or detail.get("video_url")
            ),
            cover_url=cover_url,
            photos=photos,
            play_count=stats.get("play_count"),
            like_count=stats.get("digg_count"),
            comment_count=stats.get("comment_count"),
            share_count=stats.get("share_count"),
            collect_count=stats.get("collect_count"),
            extra_data_json={
                "author": {
                    "id": str(author.get("uid") or ""),
                    "sec_uid": author.get("sec_uid"),
                    "name": author.get("nickname"),
                },
                "music": _as_dict(detail.get("music")),
            },
        )

    def fetch_video_high_quality_play_url(
        self,
        *,
        aweme_id: str = "",
        share_url: str = "",
        region: str = "",
    ) -> ResolvedMedia:
        data = self._transport.request(
            "GET",
            f"{self.prefix}/fetch_video_high_quality_play_url",
            params={
                key: value
                for key, value in {
                    "aweme_id": aweme_id,
                    "share_url": share_url,
                    "region": region,
                }.items()
                if value
            },
        )
        url = _first_url(data)
        if not url:
            raise TikHubError("TikHub did not return a video URL")
        return ResolvedMedia(url=url)

    def fetch_video_comments(
        self,
        aweme_id: str,
        *,
        cursor: str | None = None,
        count: int = 20,
    ) -> CommentPage:
        data = self._transport.request(
            "GET",
            f"{self.prefix}/fetch_video_comments",
            params={
                "aweme_id": aweme_id,
                "cursor": cursor or "0",
                "count": count,
            },
        )
        return CommentPage(comments=_comments(data), next_cursor=_cursor(data))


class XiaohongshuAppV2API:
    prefix = "/api/v1/xiaohongshu/app_v2"

    def __init__(self, transport: _Transport) -> None:
        self._transport = transport

    def _note(self, method: str, note_id: str, share_text: str = "") -> AwemeData:
        data = self._transport.request(
            "GET",
            f"{self.prefix}/{method}",
            params={"note_id": note_id, "share_text": share_text},
        )
        detail = self._extract_note_detail(data, note_id)
        return self._clean_note(detail, note_id)

    def _extract_note_detail(self, data: Any, note_id: str) -> dict[str, Any]:
        candidates: list[dict[str, Any]] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                value_id = value.get("note_id") or value.get("noteId") or value.get("id")
                if value_id == note_id:
                    candidates.append(value)
                for key in (
                    "data",
                    "note",
                    "note_card",
                    "item",
                    "items",
                    "note_list",
                    "list",
                ):
                    collect(value.get(key))
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(data)
        for item in candidates:
            if item.get("model_type") == "note" or item.get("type") in {
                "normal",
                "video",
            }:
                return item
        if candidates:
            return candidates[0]
        if note_id:
            raise TikHubError(f"TikHub did not return Xiaohongshu note {note_id}")

        root = _as_dict(data)
        nested = root.get("data")
        for value in nested if isinstance(nested, list) else [nested, root]:
            detail = _as_dict(value)
            if detail.get("model_type") == "note" or detail.get("type") in {
                "normal",
                "video",
            }:
                return detail
            note = _first_dict(detail.get("note_list"))
            if note:
                return note
        raise TikHubError(f"TikHub did not return Xiaohongshu note {note_id}")

    def _clean_note(self, detail: dict[str, Any], note_id: str) -> AwemeData:
        user = _as_dict(detail.get("user") or detail.get("author") or detail.get("user_info"))
        stats = _as_dict(detail.get("interact_info") or detail.get("statistics"))
        video_info = (
            detail.get("video")
            or detail.get("video_info_v2")
            or detail.get("video_info")
            or detail.get("videoInfo")
        )
        images = (
            detail.get("image_list")
            or detail.get("images")
            or detail.get("images_list")
            or detail.get("imageList")
        )
        photos = _xiaohongshu_image_urls(images)
        note_type = str(
            detail.get("type")
            or detail.get("note_type")
            or detail.get("noteType")
            or ""
        ).lower()
        content_type = (
            ContentType.VIDEO
            if note_type == "video" or bool(video_info)
            else ContentType.IMAGE
        )
        cover_url = _first_url(
            detail.get("cover")
            or _as_dict(video_info).get("image")
            or detail.get("share_info")
            or images
        )
        return AwemeData(
            platform_aweme_id=str(detail.get("note_id") or detail.get("id") or note_id),
            content_type=content_type,
            title=detail.get("title") or detail.get("display_title"),
            description=detail.get("desc") or detail.get("description"),
            published_at=_timestamp(
                detail.get("time")
                or detail.get("create_time")
                or detail.get("last_update_time")
            ),
            video_url=_video_url(video_info),
            cover_url=cover_url,
            photos=photos,
            play_count=detail.get("view_count") or stats.get("view_count"),
            like_count=detail.get("liked_count")
            or stats.get("liked_count")
            or stats.get("like_count"),
            comment_count=detail.get("comments_count")
            or stats.get("comment_count")
            or stats.get("comments_count"),
            share_count=detail.get("shared_count")
            or stats.get("share_count")
            or stats.get("shared_count"),
            collect_count=detail.get("collected_count")
            or stats.get("collected_count")
            or stats.get("collect_count"),
            extra_data_json={
                "author": {
                    "id": str(user.get("user_id") or user.get("userid") or user.get("id") or ""),
                    "name": user.get("nickname") or user.get("name"),
                },
                "xsec_token": detail.get("xsec_token"),
                "raw_type": note_type or None,
            },
        )

    def get_image_note_detail(self, note_id: str, *, share_text: str = "") -> AwemeData:
        return self._note("get_image_note_detail", note_id, share_text)

    def get_video_note_detail(self, note_id: str, *, share_text: str = "") -> AwemeData:
        return self._note("get_video_note_detail", note_id, share_text)

    def get_note_detail(self, note_id: str, *, share_text: str = "") -> AwemeData:
        data = self.get_image_note_detail(note_id, share_text=share_text)
        if data.content_type != ContentType.VIDEO or data.video_url:
            return data
        try:
            return self.get_video_note_detail(note_id, share_text=share_text)
        except TikHubError:
            return data

    def get_note_comments(
        self,
        *,
        note_id: str = "",
        share_text: str = "",
        cursor: str | None = None,
    ) -> CommentPage:
        data = self._transport.request(
            "GET",
            f"{self.prefix}/get_note_comments",
            params={
                "note_id": note_id,
                "share_text": share_text,
                "cursor": cursor or "",
            },
        )
        return CommentPage(comments=_comments(data), next_cursor=_cursor(data))


class WeChatChannelsV2API:
    prefix = "/api/v1/wechat_channels/v2"

    def __init__(self, transport: _Transport) -> None:
        self._transport = transport

    def fetch_video_detail(
        self,
        *,
        object_id: str,
        export_id: str = "",
        object_nonce_id: str = "",
        share_url: str = "",
        raw: bool = False,
    ) -> AwemeData:
        data = self._transport.request(
            "POST",
            f"{self.prefix}/fetch_video_detail",
            json={
                "object_id": object_id,
                "export_id": export_id,
                "object_nonce_id": object_nonce_id,
                "share_url": share_url,
                "raw": raw,
            },
        )
        detail = _as_dict(data)
        media = _as_dict(detail.get("media"))
        cover_url = _first_url(
            detail.get("cover_url") or detail.get("cover") or media.get("cover_url")
        )
        return AwemeData(
            platform_aweme_id=str(detail.get("id") or object_id),
            content_type=ContentType.VIDEO,
            title=detail.get("title"),
            description=detail.get("description") or detail.get("desc"),
            published_at=_timestamp(
                detail.get("create_time") or detail.get("createTime")
            ),
            video_url=_first_url(media),
            cover_url=cover_url,
            play_count=detail.get("read_count"),
            like_count=detail.get("like_count"),
            comment_count=detail.get("comment_count"),
            share_count=detail.get("forward_count"),
            collect_count=detail.get("fav_count"),
            extra_data_json={
                "username": detail.get("username"),
                "nickname": detail.get("nickname"),
                "object_type": detail.get("object_type"),
                "location": detail.get("location"),
                "export_id": detail.get("export_id"),
                "object_nonce_id": detail.get("object_nonce_id"),
            },
        )

    def fetch_video_comments(
        self,
        *,
        object_id: str,
        last_buffer: str = "",
        comment_id: str = "",
        raw: bool = False,
    ) -> CommentPage:
        data = self._transport.request(
            "POST",
            f"{self.prefix}/fetch_video_comments",
            json={
                "object_id": object_id,
                "last_buffer": last_buffer,
                "comment_id": comment_id,
                "raw": raw,
            },
        )
        return CommentPage(comments=_comments(data), next_cursor=_cursor(data))


class WeChatMediaPlatformV2API:
    prefix = "/api/v1/wechat_mp/v2"

    def __init__(self, transport: _Transport) -> None:
        self._transport = transport

    def fetch_article_detail(self, *, url: str, raw: bool = False) -> AwemeData:
        data = self._transport.request(
            "POST",
            f"{self.prefix}/fetch_article_detail",
            json={"url": url, "raw": raw},
        )
        root = _as_dict(data)
        content = _as_dict(root.get("content")) or root
        cover_url = _first_url(content.get("cdn_url") or content.get("cover"))
        return AwemeData(
            platform_aweme_id=str(
                content.get("msgid")
                or content.get("msgId")
                or content.get("comment_id")
                or url
            ),
            content_type=ContentType.ARTICLE,
            title=content.get("title"),
            description=content.get("desc") or content.get("description"),
            published_at=_timestamp(
                content.get("ori_create_time") or content.get("create_time")
            ),
            cover_url=cover_url,
            extra_data_json={
                "author": content.get("author"),
                "account_name": content.get("nick_name"),
                "username": content.get("user_name"),
                "biz_uin": root.get("bizUin"),
                "item_index": root.get("itemIdx"),
                "comment_id": (
                    str(content["comment_id"])
                    if content.get("comment_id") is not None
                    else None
                ),
                "content": content.get("content"),
                "album": content.get("appmsgalbuminfo"),
            },
        )

    def fetch_article_comments(
        self,
        *,
        url: str,
        buffer: str = "",
        raw: bool = False,
    ) -> CommentPage:
        data = self._transport.request(
            "POST",
            f"{self.prefix}/fetch_article_comments",
            json={"url": url, "buffer": buffer, "raw": raw},
        )
        return CommentPage(comments=_comments(data), next_cursor=_cursor(data))


class TikHubClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float = 30,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._transport = _Transport(base_url, api_key, timeout_seconds, client)
        self.douyin = DouyinAppV3API(self._transport)
        self.xiaohongshu = XiaohongshuAppV2API(self._transport)
        self.wechat_channels = WeChatChannelsV2API(self._transport)
        self.wechat_mp = WeChatMediaPlatformV2API(self._transport)

    def close(self) -> None:
        self._transport.close()

@register_data_provider
class TikHubProvider(BaseDataProvider, AwemeProviderMixin, CommentProviderMixin):
    namespace = "tikhub"
    name = "TikHub"
    supported_types = (
        DataProviderType.DOUYIN_AWEME_COLLECT,
        DataProviderType.XIAOHONGSHU_AWEME_COLLECT,
        DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT,
        DataProviderType.WECHAT_MP_AWEME_COLLECT,
        DataProviderType.XIAOHONGSHU_COMMENT_COLLECT,
    )
    platforms_by_type = {
        DataProviderType.DOUYIN_AWEME_COLLECT: {Platform.DOUYIN},
        DataProviderType.XIAOHONGSHU_AWEME_COLLECT: {Platform.XIAOHONGSHU},
        DataProviderType.WECHAT_CHANNELS_AWEME_COLLECT: {Platform.WECHAT_CHANNELS},
        DataProviderType.WECHAT_MP_AWEME_COLLECT: {Platform.WECHAT_MP},
        DataProviderType.XIAOHONGSHU_COMMENT_COLLECT: {Platform.XIAOHONGSHU},
    }
    parameters = (
        ProviderParameter(
            key="api_key",
            type=ProviderParameterType.PASSWORD,
            label="接口密钥",
            required=True,
            help="TikHub 接口密钥。",
        ),
        ProviderParameter(
            key="base_url",
            type=ProviderParameterType.SINGLE_SELECT,
            label="服务地址",
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
        ProviderParameter(
            key="timeout_seconds",
            type=ProviderParameterType.NUMBER,
            label="超时时间",
            required=True,
            default=30,
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self._client_key: tuple[str, str, float] | None = None
        self._client: TikHubClient | None = None

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

        base_url = str(values.get("base_url") or "https://api.tikhub.dev").rstrip("/")
        api_key = str(values.get("api_key") or "")
        timeout_seconds = float(values.get("timeout_seconds") or 30)
        try:
            with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
                response = client.get(
                    f"{base_url}/api/v1/tikhub/user/get_user_info",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                user_info = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            message = str(exc)
            lowered = message.lower()
            if (
                "nodename nor servname" in lowered
                or "name or service not known" in lowered
            ):
                message = (
                    f"无法解析 TikHub 服务地址 {base_url}，"
                    "请检查网络或切换服务地址"
                )
            return ProviderStatus(
                status="unavailable",
                message=message,
                details={"base_url": base_url},
            )
        if not isinstance(user_info, dict):
            return ProviderStatus(
                status="unavailable",
                message="TikHub response must be a JSON object",
                details={"base_url": base_url},
            )
        code = user_info.get("code", 200)
        if isinstance(code, int) and code >= 400:
            message = str(
                user_info.get("message_zh")
                or user_info.get("message")
                or code
            )
            return ProviderStatus(
                status="unavailable",
                message=message,
                details={"base_url": base_url},
            )

        api_key_data = user_info.get("api_key_data")
        if not isinstance(api_key_data, dict):
            api_key_data = {}
        user_data = user_info.get("user_data")
        if not isinstance(user_data, dict):
            user_data = {}
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
            details={"base_url": base_url, "account": account},
        )

    def setup(self) -> ProviderSetupResult:
        self.close()
        status = self.refresh_status()
        account = (status.details or {}).get("account") or {}
        base_url = self.parameter_values.get("base_url")
        logs = [
            "已保存 TikHub Provider 配置",
            (
                "已调用用户信息接口："
                f"{base_url}/api/v1/tikhub/user/get_user_info"
            ),
        ]
        if status.status == "ready":
            logs.append("TikHub 连接验证成功")
            if account.get("email"):
                logs.append(f"用户：{account['email']}")
            if account.get("api_key_name"):
                logs.append(
                    f"API Key：{account['api_key_name']}"
                    f"（状态：{account.get('api_key_status', '未知')}）"
                )
            if account.get("balance") is not None:
                logs.append(
                    f"余额：{account['balance']}；"
                    f"赠送额度：{account.get('free_credit', 0)}"
                )
        else:
            logs.append(f"TikHub 连接验证失败：{status.message}")
        return ProviderSetupResult(
            status=status,
            logs=logs,
        )

    def fetch_aweme(self, request: FetchAwemeRequest) -> AwemeData:
        client = self._tikhub()
        if request.platform == Platform.DOUYIN:
            return client.douyin.fetch_one_video(request.platform_aweme_id)
        if request.platform == Platform.XIAOHONGSHU:
            if request.content_type == ContentType.IMAGE:
                return client.xiaohongshu.get_image_note_detail(
                    request.platform_aweme_id,
                    share_text=request.source_url,
                )
            if request.content_type == ContentType.VIDEO:
                return client.xiaohongshu.get_video_note_detail(
                    request.platform_aweme_id,
                    share_text=request.source_url,
                )
            return client.xiaohongshu.get_note_detail(
                request.platform_aweme_id,
                share_text=request.source_url,
            )
        if request.platform == Platform.WECHAT_CHANNELS:
            return client.wechat_channels.fetch_video_detail(
                object_id=request.platform_aweme_id
            )
        if request.platform == Platform.WECHAT_MP:
            return client.wechat_mp.fetch_article_detail(
                url=request.source_url or request.platform_aweme_id
            )
        raise ValueError(f"unsupported TikHub aweme platform: {request.platform}")

    def resolve_video(self, request: ResolveVideoRequest) -> ResolvedMedia | None:
        if request.platform == Platform.DOUYIN:
            resolved = self._tikhub().douyin.fetch_video_high_quality_play_url(
                aweme_id=request.platform_aweme_id
            )
            return ResolvedMedia(url=resolved.url, metadata=resolved.metadata)
        if request.media_url:
            return ResolvedMedia(url=request.media_url)
        return None

    def fetch_comments(self, request: FetchCommentsRequest) -> CommentPage:
        if request.platform != Platform.XIAOHONGSHU:
            raise ValueError(
                f"unsupported TikHub comment platform: {request.platform}"
            )
        return self._tikhub().xiaohongshu.get_note_comments(
            note_id=request.platform_aweme_id,
            cursor=request.cursor,
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._client_key = None

    def _tikhub(self) -> TikHubClient:
        values = dict(self.parameter_values)
        base_url = str(values.get("base_url") or "https://api.tikhub.dev")
        api_key = str(values.get("api_key") or "")
        timeout_seconds = float(values.get("timeout_seconds") or 30)
        client_key = (base_url, api_key, timeout_seconds)
        if self._client is None or self._client_key != client_key:
            self.close()
            self._client = TikHubClient(base_url, api_key or None, timeout_seconds)
            self._client_key = client_key
        return self._client
