from __future__ import annotations

import json
from pathlib import Path

from cjdb_collectors.domains.data_provider import TranscriptionRequest
from cjdb_collectors.domains.data_provider.providers.funasr import FunASRProvider


def test_funasr_provider_exposes_video_transcription_parameters() -> None:
    provider = FunASRProvider()

    metadata = provider.metadata("video_transcription").model_dump()

    assert metadata["namespace"] == "funasr"
    assert metadata["name"] == "FunASR"
    assert metadata["type"] == "video_transcription"
    assert [parameter["key"] for parameter in metadata["parameters"]] == [
        "model",
        "hub",
        "model_dir",
        "device",
        "language",
        "batch_size_s",
        "max_single_segment_time",
    ]


def test_funasr_ready_marker_uses_model_and_hub(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    vad_path = tmp_path / "vad"
    model_path.mkdir()
    vad_path.mkdir()
    provider = FunASRProvider(
        {
            "model": "sensevoice-small",
            "hub": "ms",
            "model_dir": str(tmp_path),
        }
    )
    values = provider._effective_values()

    provider._write_marker(
        values,
        {"model": str(model_path), "vad": str(vad_path)},
        "cpu",
    )

    marker = provider._marker_path(values)
    assert marker.is_file()
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["model"] == "sensevoice-small"
    assert payload["hub"] == "ms"
    assert payload["paths"] == {
        "model": str(model_path),
        "vad": str(vad_path),
    }
    assert provider._ready_marker_paths(values) == payload["paths"]


def test_funasr_transcribe_normalizes_generate_result(
    monkeypatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "audio.wav"
    video.write_bytes(b"placeholder")
    provider = FunASRProvider({"model_dir": str(tmp_path)})

    class FakeModel:
        def generate(self, **kwargs: object) -> list[dict[str, object]]:
            assert kwargs["input"] == str(video)
            assert kwargs["batch_size_s"] == 60
            return [
                {
                    "text": "  你好世界  ",
                    "sentence_info": [
                        {"start": 0, "end": 1200, "text": "你好", "spk": 1},
                        {"start": 1200, "end": 2400, "sentence": "世界"},
                    ],
                }
            ]

    monkeypatch.setattr(
        provider,
        "status",
        lambda: type("Status", (), {"status": "ready", "message": None})(),
    )
    monkeypatch.setattr(provider, "_get_model", lambda values: FakeModel())

    result = provider.transcribe(TranscriptionRequest(video_path=video))

    assert result.text == "你好世界"
    assert not hasattr(result, "normalized_text")
    assert not hasattr(result, "segments")
