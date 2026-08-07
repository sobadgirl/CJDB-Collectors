from __future__ import annotations

import json
from pathlib import Path

from cjdb_collectors.domains.data_provider.providers.tikhub import TikHubProvider
from cjdb_collectors.models import ContentType


FIXTURE_DIR = Path("tests/fixtures/tikhub/xiaohongshu")


def fixture_data(name: str) -> dict:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return payload["body"]["data"]


def test_xiaohongshu_image_note_detail_matches_real_response_shape() -> None:
    provider = TikHubProvider({"api_key": "x", "base_url": "https://api.test"})
    data = fixture_data(
        "xiaohongshu_note_640df902000000002700360b_get_image_note_detail.raw.json"
    )

    result = provider.xiaohongshu_aweme_from_note(data["data"][0]["note_list"][0])

    assert result.platform_aweme_id == "640df902000000002700360b"
    assert result.content_type == ContentType.IMAGE
    assert result.title == "编了第二条，给另一个朋友"
    assert result.like_count == 22629
    assert result.comment_count == 727
    assert result.share_count == 717
    assert result.collect_count == 9409
    assert len(result.photos) == 4
    assert result.cover_url
    assert result.video_url is None
    provider.close()


def test_xiaohongshu_video_detail_extracts_stream_url_and_metrics() -> None:
    provider = TikHubProvider({"api_key": "x", "base_url": "https://api.test"})
    data = fixture_data(
        "xiaohongshu_video_64b40886000000001002a0a8_get_video_note_detail.raw.json"
    )

    result = provider.xiaohongshu_aweme_from_video_detail(data)

    assert result.platform_aweme_id == "64b40886000000001002a0a8"
    assert result.content_type == ContentType.VIDEO
    assert result.title == "新疆⛰️旅行真的会让人变漂亮✨"
    assert result.video_url
    assert result.video_url.endswith(".mp4")
    assert result.like_count == 40757
    assert result.comment_count == 394
    assert result.share_count == 744
    assert result.collect_count == 6484
    assert result.cover_url
    provider.close()
