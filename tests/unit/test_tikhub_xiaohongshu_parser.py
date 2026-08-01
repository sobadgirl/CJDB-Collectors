from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cjdb_collectors.data_provider.providers.tikhub import (
    XiaohongshuAppV2API,
)
from cjdb_collectors.models import ContentType


FIXTURE_DIR = Path("tests/fixtures/tikhub/xiaohongshu")


class FixtureTransport:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def request(self, _method: str, path: str, **_kwargs: Any) -> Any:
        fixture = self.mapping[path.rsplit("/", 1)[-1]]
        payload = json.loads((FIXTURE_DIR / fixture).read_text(encoding="utf-8"))
        return payload["body"]["data"]


def test_xiaohongshu_image_note_detail_matches_real_response_shape() -> None:
    api = XiaohongshuAppV2API(
        FixtureTransport(
            {
                "get_image_note_detail": (
                    "xiaohongshu_note_640df902000000002700360b_"
                    "get_image_note_detail.raw.json"
                )
            }
        )
    )

    data = api.get_image_note_detail("640df902000000002700360b")

    assert data.platform_aweme_id == "640df902000000002700360b"
    assert data.content_type == ContentType.IMAGE
    assert data.title == "编了第二条，给另一个朋友"
    assert data.like_count == 22629
    assert data.comment_count == 727
    assert data.share_count == 717
    assert data.collect_count == 9409
    assert len(data.photos) == 4
    assert data.cover_url
    assert data.video_url is None


def test_xiaohongshu_video_detail_extracts_stream_url_and_metrics() -> None:
    api = XiaohongshuAppV2API(
        FixtureTransport(
            {
                "get_video_note_detail": (
                    "xiaohongshu_video_64b40886000000001002a0a8_"
                    "get_video_note_detail.raw.json"
                )
            }
        )
    )

    data = api.get_video_note_detail("64b40886000000001002a0a8")

    assert data.platform_aweme_id == "64b40886000000001002a0a8"
    assert data.content_type == ContentType.VIDEO
    assert data.title == "新疆⛰️旅行真的会让人变漂亮✨"
    assert data.video_url
    assert data.video_url.endswith(".mp4")
    assert data.like_count == 40757
    assert data.comment_count == 394
    assert data.share_count == 744
    assert data.collect_count == 6484
    assert data.cover_url


def test_xiaohongshu_unknown_detail_uses_real_type_from_response() -> None:
    api = XiaohongshuAppV2API(
        FixtureTransport(
            {
                "get_image_note_detail": (
                    "xiaohongshu_note_640df902000000002700360b_"
                    "get_image_note_detail.raw.json"
                )
            }
        )
    )

    data = api.get_note_detail("640df902000000002700360b")

    assert data.content_type == ContentType.IMAGE


def test_xiaohongshu_unknown_video_detail_fetches_video_stream() -> None:
    api = XiaohongshuAppV2API(
        FixtureTransport(
            {
                "get_image_note_detail": (
                    "xiaohongshu_video_64b40886000000001002a0a8_"
                    "get_image_note_detail.raw.json"
                ),
                "get_video_note_detail": (
                    "xiaohongshu_video_64b40886000000001002a0a8_"
                    "get_video_note_detail.raw.json"
                ),
            }
        )
    )

    data = api.get_note_detail("64b40886000000001002a0a8")

    assert data.content_type == ContentType.VIDEO
    assert data.video_url
    assert data.video_url.endswith(".mp4")
