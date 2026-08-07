from cjdb_collectors.models import Platform
from cjdb_collectors.models.accounts import Account
from cjdb_collectors.models.display import (
    display_count,
    display_gender,
    display_location,
    display_registered_at,
)


def test_display_count_uses_wan_unit_for_large_counts() -> None:
    assert display_count(None) == "-"
    assert display_count(9999) == "9999"
    assert display_count(10000) == "1.00万"
    assert display_count(12345) == "1.23万"
    assert display_count(99999) == "10.0万"
    assert display_count(123456) == "12.3万"


def test_account_count_display_fields() -> None:
    account = Account(
        platform=Platform.DOUYIN,
        profile_url="https://www.douyin.com/user/example",
        follower_count=12345,
        following_count=9999,
    )

    assert account.follower_count_display == "1.23万"
    assert account.following_count_display == "9999"


def test_author_profile_display_fields() -> None:
    account = Account(
        platform=Platform.XIAOHONGSHU,
        profile_url="https://www.xiaohongshu.com/user/profile/example",
        location="上海",
        ip_location="北京",
        gender="female",
        extra_data_json={"create_time": 1_700_000_000},
    )

    assert display_location("上海", "北京") == "上海 · 北京"
    assert display_gender("female") == "女"
    assert display_registered_at({"create_time": 1_700_000_000}) == "2023-11-14"
    assert account.location_display == "上海 · 北京"
    assert account.gender_display == "女"
    assert account.registered_at_display == "2023-11-14"
