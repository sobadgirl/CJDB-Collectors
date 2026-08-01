from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cjdb_collectors.data_provider.providers.tikhub import TikHubClient
from cjdb_collectors.models import ContentType


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TikHubClient:
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return TikHubClient(
        "https://api.tikhub.test",
        "secret",
        client=http_client,
    )


def _response(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": data})


def test_get_user_info_uses_the_account_endpoint_and_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tikhub/user/get_user_info"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "code": 200,
                "router": "/api/v1/tikhub/user/get_user_info",
                "api_key_data": {
                    "api_key_name": "cjdb web",
                    "api_key_status": 1,
                },
                "user_data": {
                    "email": "user@example.com",
                    "is_active": True,
                },
            },
        )

    client = _client(handler)

    result = client.get_user_info()

    assert result["api_key_data"]["api_key_name"] == "cjdb web"
    assert result["user_data"]["is_active"] is True
    assert client.is_ready() == (True, None)
    client.close()


def test_douyin_method_keeps_openapi_name_and_cleans_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/douyin/app/v3/fetch_one_video"
        assert request.url.params["aweme_id"] == "731234"
        assert request.headers["authorization"] == "Bearer secret"
        return _response(
            {
                "aweme_detail": {
                    "aweme_id": "731234",
                    "desc": "抖音作品",
                    "create_time": 1_700_000_000,
                    "statistics": {
                        "digg_count": 12,
                        "comment_count": 3,
                    },
                    "video": {
                        "play_addr": {"url_list": ["https://cdn.test/video.mp4"]},
                        "cover": {"url_list": ["https://cdn.test/cover.jpg"]},
                    },
                    "author": {
                        "uid": "100",
                        "sec_uid": "sec-100",
                        "nickname": "作者",
                    },
                }
            }
        )

    client = _client(handler)
    result = client.douyin.fetch_one_video("731234")

    assert result.platform_aweme_id == "731234"
    assert result.content_type == ContentType.VIDEO
    assert result.title == "抖音作品"
    assert result.video_url == "https://cdn.test/video.mp4"
    assert result.like_count == 12
    assert result.extra_data_json["author"]["sec_uid"] == "sec-100"
    client.close()


@pytest.mark.parametrize(
    ("platform_name", "method_name"),
    [
        ("douyin", "fetch_one_video"),
        ("douyin", "fetch_video_high_quality_play_url"),
        ("douyin", "fetch_video_comments"),
        ("xiaohongshu", "get_image_note_detail"),
        ("xiaohongshu", "get_video_note_detail"),
        ("xiaohongshu", "get_note_comments"),
        ("wechat_channels", "fetch_video_detail"),
        ("wechat_channels", "fetch_video_comments"),
        ("wechat_mp", "fetch_article_detail"),
        ("wechat_mp", "fetch_article_comments"),
    ],
)
def test_wrapper_method_names_match_openapi(
    platform_name: str,
    method_name: str,
) -> None:
    client = _client(lambda _request: _response({}))
    assert callable(getattr(getattr(client, platform_name), method_name))
    client.close()


def test_xiaohongshu_video_note_is_cleaned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/get_video_note_detail")
        assert request.url.params["note_id"] == "note-1"
        return _response(
            {
                "note": {
                    "note_id": "note-1",
                    "title": "小红书标题",
                    "desc": "正文",
                    "interact_info": {
                        "liked_count": "20",
                        "collected_count": "5",
                    },
                    "video": {"master_url": "https://cdn.test/xhs.mp4"},
                    "image_list": [
                        {"url": "https://cdn.test/one.jpg"},
                        {"url_list": ["https://cdn.test/two.jpg"]},
                    ],
                    "user": {"user_id": "xhs-user", "nickname": "博主"},
                    "xsec_token": "token",
                }
            }
        )

    client = _client(handler)
    result = client.xiaohongshu.get_video_note_detail("note-1")

    assert result.content_type == ContentType.VIDEO
    assert result.video_url == "https://cdn.test/xhs.mp4"
    assert result.cover_url == "https://cdn.test/one.jpg"
    assert result.photos == [
        "https://cdn.test/one.jpg",
        "https://cdn.test/two.jpg",
    ]
    assert result.collect_count == 5
    assert result.extra_data_json["xsec_token"] == "token"
    client.close()


def test_wechat_channels_uses_simplified_response_and_projects_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/fetch_video_detail")
        assert body == {
            "object_id": "14941130915890399732",
            "export_id": "",
            "object_nonce_id": "",
            "share_url": "",
            "raw": False,
        }
        return _response(
            {
                "id": "14941130915890399732",
                "title": "视频号作品",
                "read_count": 100,
                "media": {
                    "full_url": "https://cdn.test/channels.mp4?token=x",
                    "decode_key": "decode-this-response",
                },
            }
        )

    client = _client(handler)
    result = client.wechat_channels.fetch_video_detail(object_id="14941130915890399732")

    assert result.platform_aweme_id == "14941130915890399732"
    assert result.video_url == "https://cdn.test/channels.mp4?token=x"
    assert result.play_count == 100
    client.close()


def test_wechat_mp_article_projects_content_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/fetch_article_detail")
        return _response(
            {
                "bizUin": 123,
                "itemIdx": 1,
                "content": {
                    "msgId": "999999999999999999",
                    "title": "公众号文章",
                    "desc": "摘要",
                    "nick_name": "公众号",
                    "content": "<p>正文</p>",
                    "comment_id": "888888888888888888",
                },
            }
        )

    client = _client(handler)
    result = client.wechat_mp.fetch_article_detail(
        url="https://mp.weixin.qq.com/s/test"
    )

    assert result.content_type == ContentType.ARTICLE
    assert result.platform_aweme_id == "999999999999999999"
    assert result.extra_data_json["content"] == "<p>正文</p>"
    assert result.extra_data_json["comment_id"] == "888888888888888888"
    client.close()


def test_comment_cleaning_supports_wechat_mp_fields() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _response(
            {
                "buffer": "next-page",
                "comments": [
                    {
                        "content_id": "comment-1",
                        "content": "评论正文",
                        "nick_name": "读者",
                        "logo_url": "https://cdn.test/avatar.jpg",
                        "like_num": 7,
                        "reply_total": 2,
                    }
                ],
            }
        )

    client = _client(handler)
    page = client.wechat_mp.fetch_article_comments(
        url="https://mp.weixin.qq.com/s/test"
    )

    assert page.next_cursor == "next-page"
    assert page.comments == [
        {
            "id": "comment-1",
            "text": "评论正文",
            "created_at": None,
            "like_count": 7,
            "reply_count": 2,
            "author": {
                "id": "",
                "name": "读者",
                "avatar_url": "https://cdn.test/avatar.jpg",
            },
        }
    ]
    client.close()
