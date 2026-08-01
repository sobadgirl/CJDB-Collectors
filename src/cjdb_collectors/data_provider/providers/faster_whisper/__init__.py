from __future__ import annotations

from pathlib import Path
from typing import Any

from cjdb_collectors.services.data_providers import register_data_provider

from ...base import BaseDataProvider, VideoTranscriptionProviderMixin
from ...types import (
    DataProviderType,
    ProviderParameter,
    ProviderParameterType,
    ProviderSetupResult,
    ProviderStatus,
    TranscriptionRequest,
    TranscriptionResult,
)


class FasterWhisperEngine:
    """Lazy faster-whisper adapter; importing the web app never loads a model."""

    def __init__(
        self,
        model: str = "turbo",
        *,
        model_dir: str | Path | None = None,
        device: str = "auto",
        compute_type: str = "auto",
        language: str | None = "zh",
        vad_filter: bool = True,
        word_timestamps: bool = False,
    ) -> None:
        self.model_name = model
        self.model_dir = str(model_dir) if model_dir else None
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.vad_filter = vad_filter
        self.word_timestamps = word_timestamps
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise RuntimeError("faster-whisper is not installed") from exc
            model_source = (
                str(Path(self.model_dir) / self.model_name)
                if self.model_dir
                else self.model_name
            )
            self._model = WhisperModel(
                model_source,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def prepare(self) -> Path | str:
        try:
            from faster_whisper.utils import download_model
        except ImportError as exc:
            raise RuntimeError(
                "faster-whisper is not installed; run `uv sync --extra transcription`"
            ) from exc
        if not self.model_dir:
            return str(download_model(self.model_name))
        target = Path(self.model_dir) / self.model_name
        if (target / "model.bin").is_file() and (target / "config.json").is_file():
            return target
        target.mkdir(parents=True, exist_ok=True)
        download_model(self.model_name, output_dir=str(target))
        return target

    def transcribe(self, video_path: str | Path) -> TranscriptionResult:
        path = Path(video_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        segments, _ = self._get_model().transcribe(
            str(path),
            language=self.language,
            vad_filter=self.vad_filter,
            word_timestamps=self.word_timestamps,
        )
        values = [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "words": [
                    {
                        "start": word.start,
                        "end": word.end,
                        "word": word.word,
                        "probability": word.probability,
                    }
                    for word in (segment.words or [])
                ],
            }
            for segment in segments
        ]
        text = "".join(item["text"] for item in values).strip()
        return TranscriptionResult(text=text, normalized_text=text, segments=values)

@register_data_provider
class FasterWhisperProvider(BaseDataProvider, VideoTranscriptionProviderMixin):
    namespace = "faster_whisper"
    name = "Faster Whisper"
    supported_types = (DataProviderType.VIDEO_TRANSCRIPTION,)
    parameters = (
        ProviderParameter(
            key="model",
            type=ProviderParameterType.SINGLE_SELECT,
            label="模型",
            required=True,
            default="turbo",
            options=[
                {"value": value, "label": value}
                for value in ("tiny", "base", "small", "medium", "large-v3", "turbo")
            ],
        ),
        ProviderParameter(
            key="model_dir",
            type=ProviderParameterType.LOCAL_PATH,
            label="模型目录",
            required=False,
            default="",
            help="可留空；留空时使用 faster-whisper/Hugging Face 官方默认缓存，已有缓存会被优先复用。",
        ),
        ProviderParameter(
            key="device",
            type=ProviderParameterType.TEXT,
            label="运行设备",
            default="auto",
        ),
        ProviderParameter(
            key="language",
            type=ProviderParameterType.TEXT,
            label="语言",
            default="zh",
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self._engine_key: tuple[str, str, str, str | None] | None = None
        self._engine: FasterWhisperEngine | None = None

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

        model = str(values.get("model") or "turbo")
        model_dir_value = values.get("model_dir")
        model_dir: Path | None = None
        if model_dir_value not in (None, ""):
            model_dir = Path(str(model_dir_value)).expanduser()
            if not model_dir.is_absolute():
                model_dir = Path.cwd() / model_dir
            model_dir = model_dir.resolve()

        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return ProviderStatus(
                status="unavailable",
                message="faster-whisper is not installed",
            )

        if model_dir is not None:
            model_path = model_dir / model
            if not (
                (model_path / "model.bin").is_file()
                and (model_path / "config.json").is_file()
            ):
                return ProviderStatus(
                    status="unavailable",
                    message=f"transcription model is not prepared: {model_path}",
                    details={"model": model, "model_dir": str(model_dir)},
                )

        return ProviderStatus(
            status="ready",
            details={
                "model": model,
                "model_dir": str(model_dir) if model_dir is not None else None,
                "device": str(values.get("device") or "auto"),
            },
        )

    def setup(self) -> ProviderSetupResult:
        self._engine = None
        self._engine_key = None
        path = self._transcription_engine().prepare()
        status = self.refresh_status()
        return ProviderSetupResult(
            status=status,
            logs=[
                "已保存 Faster Whisper Provider 配置",
                f"已准备转写模型：{path}",
            ],
        )

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        result = self._transcription_engine().transcribe(request.video_path)
        return TranscriptionResult(
            text=result.text,
            normalized_text=result.normalized_text,
            segments=result.segments,
        )

    def _transcription_engine(self) -> FasterWhisperEngine:
        values = dict(self.parameter_values)
        model = str(values.get("model") or "turbo")
        model_dir = self._resolve_model_dir(values.get("model_dir"))
        device = str(values.get("device") or "auto")
        language_value = values.get("language", "zh")
        language = str(language_value) if language_value else None
        engine_key = (model, str(model_dir), device, language)
        if self._engine is None or self._engine_key != engine_key:
            self._engine = FasterWhisperEngine(
                model,
                model_dir=model_dir,
                device=device,
                compute_type=str(values.get("compute_type") or "auto"),
                language=language,
                vad_filter=bool(values.get("vad_filter", True)),
                word_timestamps=bool(values.get("word_timestamps", False)),
            )
            self._engine_key = engine_key
        return self._engine

    def _resolve_model_dir(self, value: Any) -> Path | None:
        if value in (None, ""):
            return None
        model_dir = Path(str(value)).expanduser()
        if not model_dir.is_absolute():
            model_dir = Path.cwd() / model_dir
        return model_dir.resolve()
