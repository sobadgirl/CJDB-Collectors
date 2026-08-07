from __future__ import annotations

from contextlib import contextmanager
from importlib import invalidate_caches
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any, Iterator

from cjdb_collectors.services.data_providers import register_data_provider

from ...base import BaseDataProvider, VideoTranscriptionProviderMixin
from ...types import (
    DataProviderType,
    SetupResult,
    ProviderStatus,
    TranscriptionRequest,
    TranscriptionResult,
    local_path_param,
    single_select_param,
    text_param,
)


_DEPENDENCIES = ("faster-whisper>=1.1.0,<2", "socksio>=1.0,<2")
_DEPENDENCY_MODULES = {
    "faster_whisper": "Faster Whisper",
    "socksio": "SOCKS proxy support",
}


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
        hf_endpoint: str | None = None,
    ) -> None:
        self.model_name = model
        self.model_dir = str(model_dir) if model_dir else None
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.vad_filter = vad_filter
        self.word_timestamps = word_timestamps
        self.hf_endpoint = hf_endpoint
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
            with self._hf_environment():
                return str(download_model(self.model_name))
        target = Path(self.model_dir) / self.model_name
        if (target / "model.bin").is_file() and (target / "config.json").is_file():
            return target
        target.mkdir(parents=True, exist_ok=True)
        with self._hf_environment():
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
        text = "".join(segment.text for segment in segments).strip()
        return TranscriptionResult(text=text)

    @contextmanager
    def _hf_environment(self) -> Iterator[None]:
        if not self.hf_endpoint:
            yield
            return
        previous = os.environ.get("HF_ENDPOINT")
        os.environ["HF_ENDPOINT"] = self.hf_endpoint
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop("HF_ENDPOINT", None)
            else:
                os.environ["HF_ENDPOINT"] = previous


@register_data_provider
class FasterWhisperProvider(BaseDataProvider, VideoTranscriptionProviderMixin):
    namespace = "faster_whisper"
    name = "Faster Whisper"
    supported_types = (DataProviderType.VIDEO_TRANSCRIPTION,)
    parameters = (
        single_select_param(
            "model",
            "模型",
            required=True,
            default="turbo",
            options=[
                {"value": value, "label": value}
                for value in ("tiny", "base", "small", "medium", "large-v3", "turbo")
            ],
        ),
        local_path_param(
            "model_dir",
            "模型目录",
            required=False,
            default="",
            help="可留空；留空时使用 faster-whisper/Hugging Face 官方默认缓存，已有缓存会被优先复用。",
        ),
        text_param(
            "device",
            "运行设备",
            default="auto",
        ),
        text_param(
            "language",
            "语言",
            default="zh",
        ),
        text_param(
            "hf_endpoint",
            "Hugging Face Endpoint",
            required=False,
            default="",
            help="可留空；网络受限时可填写 Hugging Face 兼容镜像地址。",
        ),
    )

    def __init__(
        self,
        setup_payload: dict[str, Any] | None = None,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(setup_payload, logger=logger)
        self._engine_key: tuple[str, str, str, str | None, str | None] | None = None
        self._engine: FasterWhisperEngine | None = None

    def refresh_status(self) -> ProviderStatus:
        configured = dict(self.setup_payload)
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

        missing_dependencies = [
            label
            for module, label in _DEPENDENCY_MODULES.items()
            if not self._module_available(module)
        ]
        if missing_dependencies:
            return ProviderStatus(
                status="unavailable",
                message=(
                    f"缺少依赖：{', '.join(missing_dependencies)}，"
                    "请运行 setup"
                ),
                details={"versions": self._installed_versions()},
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
                "versions": self._installed_versions(),
                "model": model,
                "model_dir": str(model_dir) if model_dir is not None else None,
                "device": str(values.get("device") or "auto"),
                "hf_endpoint": str(values.get("hf_endpoint") or "") or None,
            },
        )

    def setup(self, params: dict[str, Any]) -> SetupResult:
        self.setup_payload = dict(params)
        try:
            return self._setup()
        except Exception as exc:
            return SetupResult(success=False, message=str(exc))

    def _setup(self) -> SetupResult:
        self._engine = None
        self._engine_key = None
        self._install_dependencies()
        try:
            path = self._transcription_engine().prepare()
        except Exception as exc:
            raise RuntimeError(
                f"Faster Whisper 模型准备失败：{self._friendly_error(exc)}"
            ) from exc
        self.logger.info("已保存 Faster Whisper Provider 配置")
        self.logger.info("Faster Whisper 运行依赖已准备")
        self.logger.info("已准备转写模型：%s", path)
        return SetupResult(
            success=True,
            message="Faster Whisper setup 完成",
            setup_payload={**self.setup_payload, "model_path": str(path)},
        )

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        return self._transcription_engine().transcribe(request.video_path)

    def _transcription_engine(self) -> FasterWhisperEngine:
        values = dict(self.setup_payload)
        model = str(values.get("model") or "turbo")
        model_dir = self._resolve_model_dir(values.get("model_dir"))
        device = str(values.get("device") or "auto")
        language_value = values.get("language", "zh")
        language = str(language_value) if language_value else None
        hf_endpoint = str(values.get("hf_endpoint") or "") or None
        engine_key = (model, str(model_dir), device, language, hf_endpoint)
        if self._engine is None or self._engine_key != engine_key:
            self._engine = FasterWhisperEngine(
                model,
                model_dir=model_dir,
                device=device,
                compute_type=str(values.get("compute_type") or "auto"),
                language=language,
                vad_filter=bool(values.get("vad_filter", True)),
                word_timestamps=bool(values.get("word_timestamps", False)),
                hf_endpoint=hf_endpoint,
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

    def _install_dependencies(self) -> None:
        if all(self._module_available(module) for module in _DEPENDENCY_MODULES):
            return
        command = self._dependency_install_command()
        self._log("正在检查并安装 Faster Whisper 运行依赖")
        self._log(f"执行命令：{self._format_command(command)}")
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._log(line.rstrip())
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, command)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "Faster Whisper 依赖安装失败，请检查网络、磁盘空间和 Python 环境"
            ) from exc
        invalidate_caches()
        self._clear_transcription_imports()
        missing = [
            label
            for module, label in _DEPENDENCY_MODULES.items()
            if not self._module_available(module)
        ]
        if missing:
            raise RuntimeError(f"Faster Whisper 依赖安装后仍不可用：{', '.join(missing)}")

    @staticmethod
    def _dependency_install_command() -> list[str]:
        uv = shutil.which("uv")
        if uv:
            return [uv, "pip", "install", "--python", sys.executable, *_DEPENDENCIES]
        return [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *_DEPENDENCIES,
        ]

    @staticmethod
    def _module_available(module: str) -> bool:
        return find_spec(module) is not None

    @staticmethod
    def _installed_versions() -> dict[str, str | None]:
        try:
            faster_whisper_version = version("faster-whisper")
        except PackageNotFoundError:
            faster_whisper_version = None
        return {"faster-whisper": faster_whisper_version}

    @staticmethod
    def _format_command(command: list[str]) -> str:
        if os.name == "nt":
            return subprocess.list2cmdline(command)
        return " ".join(shlex.quote(part) for part in command)

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        message = str(error).strip()
        lowered = message.lower()
        if "unexpected_eof_while_reading" in lowered or "ssl" in lowered:
            return (
                "Hugging Face 连接被中断，请检查代理/证书，"
                "或配置 hf_endpoint 使用可访问的镜像"
            )
        if "socks" in lowered and "socksio" in lowered:
            return "SOCKS 代理依赖不可用，请重新运行 setup"
        if any(value in lowered for value in ("name resolution", "dns", "nodename")):
            return "模型下载地址无法解析，请检查网络或配置 hf_endpoint"
        if any(value in lowered for value in ("timed out", "connecttimeout")):
            return "模型下载连接超时，请检查网络或配置 hf_endpoint"
        if any(value in lowered for value in ("no space", "disk quota")):
            return "磁盘空间不足"
        return message or error.__class__.__name__

    @staticmethod
    def _clear_transcription_imports() -> None:
        prefixes = ("faster_whisper", "huggingface_hub", "httpcore", "httpx")
        for name in tuple(sys.modules):
            if name in prefixes or name.startswith(tuple(f"{prefix}." for prefix in prefixes)):
                sys.modules.pop(name, None)

    @staticmethod
    def _log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)
