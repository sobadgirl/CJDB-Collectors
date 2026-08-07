from __future__ import annotations

import sys
from types import ModuleType

import pytest

from cjdb_collectors.domains.data_provider.providers.faster_whisper import (
    faster_whisper,
)
from cjdb_collectors.domains.data_provider.providers.faster_whisper import (
    FasterWhisperEngine,
    FasterWhisperProvider,
)


def test_faster_whisper_setup_installs_dependencies_before_preparing_model(
    monkeypatch,
) -> None:
    provider = FasterWhisperProvider()
    calls: list[str] = []

    def install_dependencies(self: FasterWhisperProvider) -> None:
        calls.append("install")

    def prepare(self: FasterWhisperEngine) -> str:
        calls.append("prepare")
        return "/models/turbo"

    monkeypatch.setattr(
        FasterWhisperProvider,
        "_install_dependencies",
        install_dependencies,
    )
    monkeypatch.setattr(FasterWhisperEngine, "prepare", prepare)
    result = provider.setup({})

    assert calls == ["install", "prepare"]
    assert result.success is True
    assert result.setup_payload["model_path"] == "/models/turbo"
    assert result.message == "Faster Whisper setup 完成"


def test_faster_whisper_dependency_install_uses_uv_when_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(faster_whisper.shutil, "which", lambda name: "/bin/uv")

    command = FasterWhisperProvider._dependency_install_command()

    assert command == [
        "/bin/uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "faster-whisper>=1.1.0,<2",
        "socksio>=1.0,<2",
    ]


def test_faster_whisper_dependency_install_falls_back_to_python_pip(
    monkeypatch,
) -> None:
    monkeypatch.setattr(faster_whisper.shutil, "which", lambda name: None)

    command = FasterWhisperProvider._dependency_install_command()

    assert command == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "faster-whisper>=1.1.0,<2",
        "socksio>=1.0,<2",
    ]


def test_faster_whisper_setup_clears_cached_download_modules() -> None:
    kept = ModuleType("unrelated")
    sys.modules["unrelated"] = kept
    sys.modules["httpcore"] = ModuleType("httpcore")
    sys.modules["httpcore._sync"] = ModuleType("httpcore._sync")
    sys.modules["httpx"] = ModuleType("httpx")
    sys.modules["huggingface_hub"] = ModuleType("huggingface_hub")
    sys.modules["faster_whisper"] = ModuleType("faster_whisper")

    try:
        FasterWhisperProvider._clear_transcription_imports()

        assert sys.modules["unrelated"] is kept
        assert "httpcore" not in sys.modules
        assert "httpcore._sync" not in sys.modules
        assert "httpx" not in sys.modules
        assert "huggingface_hub" not in sys.modules
        assert "faster_whisper" not in sys.modules
    finally:
        sys.modules.pop("unrelated", None)


def test_faster_whisper_engine_applies_hf_endpoint_temporarily(monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://original.example")
    engine = FasterWhisperEngine(hf_endpoint="https://mirror.example")

    with engine._hf_environment():
        assert faster_whisper.os.environ["HF_ENDPOINT"] == "https://mirror.example"

    assert faster_whisper.os.environ["HF_ENDPOINT"] == "https://original.example"


def test_faster_whisper_engine_restores_missing_hf_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    engine = FasterWhisperEngine(hf_endpoint="https://mirror.example")

    with engine._hf_environment():
        assert faster_whisper.os.environ["HF_ENDPOINT"] == "https://mirror.example"

    assert "HF_ENDPOINT" not in faster_whisper.os.environ


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred",
            "Hugging Face 连接被中断",
        ),
        ("name resolution failed", "模型下载地址无法解析"),
        ("disk quota exceeded", "磁盘空间不足"),
    ],
)
def test_faster_whisper_setup_reports_friendly_download_errors(
    message: str,
    expected: str,
) -> None:
    assert expected in FasterWhisperProvider._friendly_error(RuntimeError(message))
