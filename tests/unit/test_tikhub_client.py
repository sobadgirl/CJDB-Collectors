from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from cjdb_collectors.domains.data_provider.providers.tikhub import TikHubProvider
from cjdb_collectors.domains.data_provider.types import (
    FetchAccountRequest,
    FetchAwemeRequest,
    FetchCommentsRequest,
)
from cjdb_collectors.models import ContentType, Platform


def provider(
    handler: Callable[[httpx.Request], httpx.Response],
) -> TikHubProvider:
    return TikHubProvider(
        {
            "api_key": "secret",
            "base_url": "https://api.tikhub.test",
            "timeout_seconds": 30,
        },
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def response(data: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 200, "data": data})


def test_get_user_info_uses_account_endpoint_and_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/tikhub/user/get_user_info"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "code": 200,
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

    item = provider(handler)

    result = item.get_user_info()

    assert result["api_key_data"]["api_key_name"] == "cjdb web"
    assert result["user_data"]["is_active"] is True
    item.close()


def test_douyin_aweme_fetches_detail_and_statistics() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer secret"
        if request.url.path.endswith("/fetch_video_statistics"):
            assert request.url.params["aweme_ids"] == "731234"
            return response(
                {
                    "statistics_list": [
                        {
                            "aweme_id": "731234",
                            "play_count": "4567",
                            "digg_count": "45",
                            "share_count": 9,
                        }
                    ]
                }
            )
        if request.url.path.endswith("/fetch_video_high_quality_play_url"):
            assert request.url.params["aweme_id"] == "731234"
            return response(
                {
                    "video_url": "https://cdn.test/video-hq.mp4",
                    "quality": "original",
                    "size": 1024,
                }
            )
        assert request.url.path == "/api/v1/douyin/app/v3/fetch_one_video"
        assert request.url.params["aweme_id"] == "731234"
        return response(
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

    item = provider(handler)
    result = item.fetch_aweme(
        FetchAwemeRequest(
            platform=Platform.DOUYIN,
            platform_aweme_id="731234",
        )
    )

    assert result.platform_aweme_id == "731234"
    assert result.content_type == ContentType.VIDEO
    assert result.title == "抖音作品"
    assert result.platform_account_id == "sec-100"
    assert result.video_url == "https://cdn.test/video-hq.mp4"
    assert result.play_count == 4567
    assert result.like_count == 45
    assert result.share_count == 9
    assert result.extra_data_json["statistics_enrichment"]["status"] == "succeeded"
    assert result.extra_data_json["video_enrichment"]["status"] == "succeeded"
    assert paths == [
        "/api/v1/douyin/app/v3/fetch_one_video",
        "/api/v1/douyin/app/v3/fetch_video_statistics",
        "/api/v1/douyin/app/v3/fetch_video_high_quality_play_url",
    ]
    item.close()


def test_xiaohongshu_video_note_is_cleaned_from_fixed_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/get_video_note_detail")
        assert request.url.params["note_id"] == "note-1"
        return response(
            {
                "data": [
                    {
                        "id": "note-1",
                        "title": "小红书标题",
                        "desc": "正文",
                        "type": "video",
                        "liked_count": "20",
                        "collected_count": "5",
                        "comments_count": "3",
                        "shared_count": "2",
                        "images_list": [
                            {"original": "https://cdn.test/one.jpg"},
                            {"url": "https://cdn.test/two.jpg"},
                        ],
                        "video_info_v2": {
                            "media": {
                                "stream": {
                                    "h264": [
                                        {"master_url": "https://cdn.test/xhs.mp4"}
                                    ]
                                }
                            }
                        },
                        "user": {"userid": "xhs-user", "nickname": "博主"},
                        "share_info": {
                            "image": "https://cdn.test/cover.jpg",
                            "link": "https://xhs.test/note-1",
                        },
                    }
                ]
            }
        )

    item = provider(handler)
    result = item.fetch_aweme(
        FetchAwemeRequest(
            platform=Platform.XIAOHONGSHU,
            platform_aweme_id="note-1",
            content_type=ContentType.VIDEO,
        )
    )

    assert result.content_type == ContentType.VIDEO
    assert result.platform_account_id == "xhs-user"
    assert result.video_url == "https://cdn.test/xhs.mp4"
    assert result.cover_url == "https://cdn.test/cover.jpg"
    assert result.photos == [
        "https://cdn.test/one.jpg",
        "https://cdn.test/two.jpg",
    ]
    assert result.collect_count == 5
    item.close()


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
        return response(
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

    item = provider(handler)
    result = item.fetch_aweme(
        FetchAwemeRequest(
            platform=Platform.WECHAT_CHANNELS,
            platform_aweme_id="14941130915890399732",
        )
    )

    assert result.platform_aweme_id == "14941130915890399732"
    assert result.video_url == "https://cdn.test/channels.mp4?token=x"
    assert result.play_count == 100
    item.close()


def test_wechat_mp_article_projects_content_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/fetch_article_detail")
        return response(
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

    item = provider(handler)
    result = item.fetch_aweme(
        FetchAwemeRequest(
            platform=Platform.WECHAT_MP,
            source_url="https://mp.weixin.qq.com/s/test",
        )
    )

    assert result.content_type == ContentType.ARTICLE
    assert result.platform_aweme_id == "999999999999999999"
    assert result.extra_data_json["content"] == "<p>正文</p>"
    assert result.extra_data_json["comment_id"] == "888888888888888888"
    item.close()


def test_comment_cleaning_supports_wechat_mp_fields() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return response(
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

    item = provider(handler)
    page = item.fetch_comments(
        FetchCommentsRequest(
            platform=Platform.WECHAT_MP,
            platform_aweme_id="999",
            source_url="https://mp.weixin.qq.com/s/test",
        )
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
    item.close()


def test_douyin_account_profile_is_cleaned() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/handler_user_profile")
        assert request.url.params["sec_user_id"] == "sec-100"
        return response(
            {
                "user": {
                    "sec_uid": "sec-100",
                    "nickname": "抖音作者",
                    "avatar_thumb": {"url_list": ["https://cdn.test/avatar.jpg"]},
                    "follower_count": 1000,
                    "total_favorited": 2000,
                    "aweme_count": 12,
                }
            }
        )

    item = provider(handler)
    result = item.fetch_douyin_account(
        FetchAccountRequest(
            platform=Platform.DOUYIN,
            profile_url="",
            platform_account_id="sec-100",
        )
    )

    assert result.platform_account_id == "sec-100"
    assert result.display_name == "抖音作者"
    assert result.avatar_url == "https://cdn.test/avatar.jpg"
    assert result.follower_count == 1000
    assert result.like_count == 2000
    assert result.total_favorited == 2000
    assert result.work_count == 12
    item.close()


def test_xiaohongshu_account_profile_handles_nested_app_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/get_user_info")
        assert request.url.params["user_id"] == "62459118000000001000e289"
        return response(
            {
                "data": {
                    "userid": "62459118000000001000e289",
                    "nickname": "Hubland",
                    "images": "https://cdn.test/avatar.webp",
                    "fans": 2488,
                    "follows": 4,
                    "liked": 224931,
                    "collected": 32495,
                    "ndiscovery": 121,
                    "note_num_stat": {
                        "liked": 224931,
                        "collected": 32495,
                        "posted": 121,
                    },
                    "red_id": "2175738003",
                    "ip_location": "Sichuan",
                    "location": "意大利  米兰",
                    "desc": "Welcome!",
                    "red_official_verified": False,
                },
            }
        )

    item = provider(handler)
    result = item.fetch_xiaohongshu_account(
        FetchAccountRequest(
            platform=Platform.XIAOHONGSHU,
            profile_url="",
            platform_account_id="62459118000000001000e289",
        )
    )

    assert result.platform_account_id == "62459118000000001000e289"
    assert result.display_name == "Hubland"
    assert result.avatar_url == "https://cdn.test/avatar.webp"
    assert result.follower_count == 2488
    assert result.following_count == 4
    assert result.like_count == 224931
    assert result.collect_count == 32495
    assert result.total_favorited == 257426
    assert result.work_count == 121
    assert result.extra_data_json["red_id"] == "2175738003"
    item.close()
