import json
from pathlib import Path

from typer.testing import CliRunner

from cjdb_collectors import cli
from cjdb_collectors.cli import awemes as aweme_cli
from cjdb_collectors.cli import providers as provider_cli
from cjdb_collectors.cli import transcriptions as transcription_cli


cli_runner = CliRunner()


class StubTranscriptions:
    def __init__(self) -> None:
        self.aweme_ids: list[str] = []

    def transcribe_aweme(self, aweme_id: str) -> dict[str, str]:
        self.aweme_ids.append(aweme_id)
        return {"aweme_id": aweme_id, "status": "pending"}


class StubProviders:
    def __init__(self) -> None:
        self.status_calls: list[tuple[str, bool]] = []
        self.list_calls: list[tuple[str | None, bool]] = []
        self.select_calls: list[tuple[str, str]] = []

    def status(self, provider_type: str, *, refresh: bool = False) -> dict[str, object]:
        self.status_calls.append((provider_type, refresh))
        return {
            "type": provider_type,
            "selected": "faster_whisper",
            "provider": {
                "namespace": "faster_whisper",
                "name": "Faster Whisper",
            },
            "status": "ready",
            "ready": True,
        }

    def catalog(
        self,
        provider_type: str | None = None,
        *,
        include_status: bool = True,
    ) -> dict[str, object]:
        self.list_calls.append((provider_type, include_status))
        return {
            "type": provider_type,
            "selected": (
                "tikhub"
                if provider_type
                else {
                    "douyin_aweme_collect": "tikhub",
                    "xiaohongshu_aweme_collect": "tikhub",
                    "video_transcription": "faster_whisper",
                }
            ),
            "providers": [
                {
                    "type": "douyin_aweme_collect",
                    "label": "抖音数据采集",
                    "id": "tikhub",
                    "name": "TikHub",
                    "namespace": "tikhub",
                    "selected": True,
                    "status": "unconfigured",
                    "ready": False,
                    "message": "缺少必填参数：api_key",
                    "parameters": [
                        {
                            "key": "api_key",
                            "type": "password",
                        }
                    ],
                },
                {
                    "type": "xiaohongshu_aweme_collect",
                    "label": "小红书数据采集",
                    "id": "tikhub",
                    "name": "TikHub",
                    "namespace": "tikhub",
                    "selected": True,
                    "status": "unconfigured",
                    "ready": False,
                    "message": "缺少必填参数：api_key",
                    "parameters": [],
                },
                {
                    "type": "video_transcription",
                    "label": "视频转写",
                    "name": "Faster Whisper",
                    "namespace": "faster_whisper",
                    "selected": True,
                    "status": "ready",
                    "ready": True,
                    "message": None,
                    "parameters": [],
                },
            ],
        }

    def select(
        self,
        provider_type: str,
        namespace: str,
    ) -> dict[str, object]:
        self.select_calls.append((provider_type, namespace))
        provider = {
            "type": provider_type,
            "name": "TikHub",
            "namespace": namespace,
        }
        return {
            "type": provider_type,
            "selected": namespace,
            "providers": [provider],
        }


class StubServices:
    def __init__(self) -> None:
        self.transcriptions = StubTranscriptions()
        self.providers = StubProviders()


def test_aweme_transcription_aliases_delegate_to_the_same_service_method(
    monkeypatch,
) -> None:
    services = StubServices()
    monkeypatch.setattr(aweme_cli, "get_services", lambda: services)
    monkeypatch.setattr(transcription_cli, "get_services", lambda: services)

    from_aweme = cli_runner.invoke(
        cli.app,
        ["aweme", "transcription", "aweme-1", "--format", "json"],
    )
    from_transcription = cli_runner.invoke(
        cli.app,
        ["transcription", "aweme", "aweme-2", "--format", "json"],
    )

    assert from_aweme.exit_code == 0
    assert from_transcription.exit_code == 0
    assert services.transcriptions.aweme_ids == ["aweme-1", "aweme-2"]


def test_provider_status_uses_provider_type_and_displays_namespace(
    monkeypatch,
) -> None:
    services = StubServices()
    monkeypatch.setattr(provider_cli, "get_services", lambda: services)

    result = cli_runner.invoke(
        cli.app,
        [
            "provider",
            "status",
            "video_transcription",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert services.providers.status_calls == [("video_transcription", False)]
    assert '"namespace": "faster_whisper"' in result.stdout


def test_provider_status_refresh_passes_refresh_flag(monkeypatch) -> None:
    services = StubServices()
    monkeypatch.setattr(provider_cli, "get_services", lambda: services)

    result = cli_runner.invoke(
        cli.app,
        [
            "provider",
            "status",
            "video_transcription",
            "--refresh",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert services.providers.status_calls == [("video_transcription", True)]


def test_provider_list_displays_each_implementation_once(monkeypatch) -> None:
    services = StubServices()
    monkeypatch.setattr(provider_cli, "get_services", lambda: services)

    result = cli_runner.invoke(cli.app, ["provider", "list"])

    assert result.exit_code == 0
    assert services.providers.list_calls == [(None, False)]
    assert "可用 Provider（2）" in result.stdout
    assert result.stdout.count("TikHub") == 1
    assert "抖音数据采集、小红书数据采集" in result.stdout
    assert "Faster Whisper [faster_whisper]" in result.stdout
    assert "当前使用" not in result.stdout
    assert "未配置" not in result.stdout
    assert "缺少必填参数：api_key" not in result.stdout
    assert "parameters" not in result.stdout


def test_provider_list_json_is_structured_but_still_concise(monkeypatch) -> None:
    services = StubServices()
    monkeypatch.setattr(provider_cli, "get_services", lambda: services)

    result = cli_runner.invoke(
        cli.app,
        ["provider", "list", "--format=json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert payload["providers"][0]["name"] == "TikHub"
    assert payload["providers"][0]["supported_types"] == [
        {
            "type": "douyin_aweme_collect",
            "label": "抖音数据采集",
        },
        {
            "type": "xiaohongshu_aweme_collect",
            "label": "小红书数据采集",
        },
    ]
    assert "status" not in payload["providers"][0]
    assert "selected" not in payload["providers"][0]
    assert "parameters" not in payload["providers"][0]


def test_provider_select_binds_an_implementation_to_a_service_type(
    monkeypatch,
) -> None:
    services = StubServices()
    monkeypatch.setattr(provider_cli, "get_services", lambda: services)

    result = cli_runner.invoke(
        cli.app,
        [
            "provider",
            "select",
            "douyin_aweme_collect",
            "tikhub",
            "--format=json",
        ],
    )

    assert result.exit_code == 0
    assert services.providers.select_calls == [
        ("douyin_aweme_collect", "tikhub")
    ]
    payload = json.loads(result.stdout)
    assert payload["type"] == "douyin_aweme_collect"
    assert payload["selected"] == "tikhub"
    assert payload["provider"]["namespace"] == "tikhub"


def test_settings_validate_prompts_init_when_config_is_missing(
    tmp_path: Path,
) -> None:
    missing_config = tmp_path / "missing.yaml"

    result = cli_runner.invoke(
        cli.app,
        ["settings", "validate"],
        env={"CJDB_CONFIG": str(missing_config)},
    )

    assert result.exit_code == 1
    assert "Settings file not found" in result.output
    assert "./cjdb settings init" in result.output


def test_settings_init_creates_default_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    result = cli_runner.invoke(
        cli.app,
        ["settings", "init", "--path", str(config_path), "--format", "json"],
    )

    assert result.exit_code == 0
    assert config_path.is_file()
    payload = json.loads(result.output)
    assert payload["created"] is True
    assert payload["config_path"] == str(config_path)
