from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from importlib import invalidate_caches
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Iterator

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


_DEPENDENCIES = (
    "torch",
    "torchaudio",
    "funasr>=1.3.29,<2",
    "imageio-ffmpeg>=0.6,<1",
)
_DEPENDENCY_MODULES = {
    "torch": "PyTorch",
    "torchaudio": "TorchAudio",
    "funasr": "FunASR",
    "imageio_ffmpeg": "FFmpeg",
}
_VIDEO_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ts",
    ".webm",
}
_MODEL_PROFILES: dict[str, dict[str, str | None]] = {
    "sensevoice-small": {
        "label": "SenseVoice Small",
        "ms_model": "iic/SenseVoiceSmall",
        "hf_model": "FunAudioLLM/SenseVoiceSmall",
        "ms_vad": "fsmn-vad",
        "hf_vad": "funasr/fsmn-vad",
        "ms_punc": None,
        "hf_punc": None,
    },
    "paraformer-zh": {
        "label": "Paraformer 中文",
        "ms_model": "paraformer-zh",
        "hf_model": "funasr/paraformer-zh",
        "ms_vad": "fsmn-vad",
        "hf_vad": "funasr/fsmn-vad",
        "ms_punc": "ct-punc",
        "hf_punc": "funasr/ct-punc",
    },
}


@register_data_provider
class FunASRProvider(BaseDataProvider, VideoTranscriptionProviderMixin):
    namespace = "funasr"
    name = "FunASR"
    supported_types = (DataProviderType.VIDEO_TRANSCRIPTION,)
    parameters = (
        ProviderParameter(
            key="model",
            type=ProviderParameterType.SINGLE_SELECT,
            label="转写模型",
            required=True,
            default="sensevoice-small",
            options=[
                {
                    "value": "sensevoice-small",
                    "label": "SenseVoice Small（推荐）",
                },
                {
                    "value": "paraformer-zh",
                    "label": "Paraformer 中文",
                },
            ],
            help="SenseVoice 适合 CPU 和多语言；Paraformer 适合中文长音视频。",
        ),
        ProviderParameter(
            key="hub",
            type=ProviderParameterType.SINGLE_SELECT,
            label="模型下载源",
            required=True,
            default="ms",
            options=[
                {"value": "ms", "label": "ModelScope（中国大陆）"},
                {"value": "hf", "label": "Hugging Face（海外）"},
            ],
        ),
        ProviderParameter(
            key="model_dir",
            type=ProviderParameterType.LOCAL_PATH,
            label="模型目录",
            required=False,
            default="",
            help="可留空；留空时使用 ModelScope/Hugging Face 官方默认缓存，已有缓存会被优先复用。",
        ),
        ProviderParameter(
            key="device",
            type=ProviderParameterType.SINGLE_SELECT,
            label="运行设备",
            required=True,
            default="auto",
            options=[
                {"value": "auto", "label": "自动选择"},
                {"value": "cpu", "label": "CPU"},
                {"value": "cuda:0", "label": "NVIDIA GPU"},
                {"value": "mps", "label": "Apple GPU"},
            ],
        ),
        ProviderParameter(
            key="language",
            type=ProviderParameterType.SINGLE_SELECT,
            label="识别语言",
            required=True,
            default="auto",
            options=[
                {"value": "auto", "label": "自动识别"},
                {"value": "zh", "label": "中文"},
                {"value": "en", "label": "英文"},
                {"value": "yue", "label": "粤语"},
                {"value": "ja", "label": "日语"},
                {"value": "ko", "label": "韩语"},
            ],
        ),
        ProviderParameter(
            key="batch_size_s",
            type=ProviderParameterType.NUMBER,
            label="批处理时长（秒）",
            required=True,
            default=60,
        ),
        ProviderParameter(
            key="max_single_segment_time",
            type=ProviderParameterType.NUMBER,
            label="最大分段时长（毫秒）",
            required=True,
            default=30000,
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        self._model_key: str | None = None
        self._model: Any = None

    def refresh_status(self) -> ProviderStatus:
        configured = dict(self.parameter_values)
        values = {
            parameter.key: configured.get(parameter.key, parameter.default)
            for parameter in self.parameters
        }
        missing_parameters = [
            parameter.key
            for parameter in self.parameters
            if parameter.required and not values.get(parameter.key)
        ]
        if missing_parameters:
            return ProviderStatus(
                status="unconfigured",
                message=f"缺少必填参数：{', '.join(missing_parameters)}",
            )

        versions: dict[str, str | None] = {}
        for package in ("funasr", "torch", "torchaudio", "imageio-ffmpeg"):
            try:
                versions[package] = version(package)
            except PackageNotFoundError:
                versions[package] = None
        details: dict[str, Any] = {"versions": versions}

        missing_dependencies = [
            label
            for module, label in _DEPENDENCY_MODULES.items()
            if find_spec(module) is None
        ]
        if missing_dependencies:
            return ProviderStatus(
                status="unavailable",
                message=(
                    f"缺少依赖：{', '.join(missing_dependencies)}，"
                    "请运行 setup"
                ),
                details=details,
            )

        try:
            import torch
        except ImportError:
            return ProviderStatus(
                status="unavailable",
                message="PyTorch 未安装，请运行 setup",
                details=details,
            )

        requested_device = str(values.get("device") or "auto")
        if requested_device == "auto":
            if torch.cuda.is_available():
                device = "cuda:0"
            elif (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            ):
                device = "mps"
            else:
                device = "cpu"
        elif requested_device.startswith("cuda") and not torch.cuda.is_available():
            return ProviderStatus(
                status="unavailable",
                message="未检测到可用的 NVIDIA GPU",
                details=details,
            )
        elif requested_device == "mps" and not (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            return ProviderStatus(
                status="unavailable",
                message="未检测到可用的 Apple GPU",
                details=details,
            )
        elif requested_device not in {"cpu", "mps", "cuda:0"}:
            return ProviderStatus(
                status="unavailable",
                message=f"不支持的运行设备：{requested_device}",
                details=details,
            )
        else:
            device = requested_device

        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            ffmpeg_path = Path(get_ffmpeg_exe())
        except Exception:
            return ProviderStatus(
                status="unavailable",
                message="FFmpeg 不可用，请重新运行 setup",
                details=details,
            )
        if not ffmpeg_path.is_file():
            return ProviderStatus(
                status="unavailable",
                message="FFmpeg 文件不存在，请重新运行 setup",
                details=details,
            )

        model = str(values.get("model") or "")
        profile = _MODEL_PROFILES.get(model)
        if profile is None:
            return ProviderStatus(
                status="unavailable",
                message=f"不支持的 FunASR 模型：{model}",
                details=details,
            )
        hub = str(values.get("hub") or "")
        if hub not in {"ms", "hf"}:
            return ProviderStatus(
                status="unavailable",
                message=f"不支持的模型下载源：{hub}",
                details=details,
            )

        model_dir_value = values.get("model_dir")
        model_dir: Path | None = None
        if model_dir_value not in (None, ""):
            model_dir = Path(str(model_dir_value)).expanduser()
            if not model_dir.is_absolute():
                model_dir = Path.cwd() / model_dir
            model_dir = model_dir.resolve()
        if model_dir is not None:
            state_dir = model_dir
        else:
            state_dir = Path.cwd() / ".data" / "models" / "funasr"

        signature = json.dumps(
            {"model": model, "hub": hub},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(signature.encode()).hexdigest()[:16]
        marker_path = state_dir / f".cjdb-funasr-{digest}.json"
        if not marker_path.is_file():
            return ProviderStatus(
                status="unavailable",
                message="模型尚未准备，请运行 setup",
                details=details,
            )

        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            marker = None
        paths = marker.get("paths") if isinstance(marker, dict) else None
        paths_ready = (
            isinstance(paths, dict)
            and bool(paths)
            and all(
                isinstance(path, str) and Path(path).is_dir()
                for path in paths.values()
            )
        )
        if not paths_ready:
            return ProviderStatus(
                status="unavailable",
                message="模型文件不完整，请重新运行 setup",
                details=details,
            )

        details.update(
            {
                "device": device,
                "ffmpeg": str(ffmpeg_path),
                "marker": str(marker_path),
                "model_paths": paths,
            }
        )
        return ProviderStatus(
            status="ready",
            message=f"FunASR 可用：{profile['label']} / {device}",
            details=details,
        )

    def setup(self) -> ProviderSetupResult:
        cleaned = self._effective_values()
        logs = ["已保存 FunASR Provider 配置"]

        self._log("正在检查并安装 FunASR 运行依赖")
        uv = shutil.which("uv")
        command = (
            [uv, "pip", "install", "--python", sys.executable, *_DEPENDENCIES]
            if uv
            else [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *_DEPENDENCIES]
        )
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
                "FunASR 依赖安装失败，请检查网络、磁盘空间和 Python 环境"
            ) from exc
        invalidate_caches()
        logs.append("FunASR、PyTorch、TorchAudio 与 FFmpeg 依赖已准备")

        ffmpeg_path = self._ffmpeg_path()
        self._log(f"FFmpeg 已准备：{ffmpeg_path}")
        logs.append(f"FFmpeg 已准备：{ffmpeg_path}")

        profile = self._profile(cleaned)
        hub = str(cleaned["hub"])
        if hub not in {"ms", "hf"}:
            raise RuntimeError(f"不支持的模型下载源：{hub}")
        device = self._resolve_device(str(cleaned["device"]))
        local_paths = self._ready_marker_paths(cleaned)
        if local_paths:
            model_source = local_paths["model"]
            vad_source = local_paths["vad"]
            punc_source = local_paths.get("punc")
        else:
            model_source = str(profile[f"{hub}_model"])
            vad_source = str(profile[f"{hub}_vad"])
            punc_value = profile[f"{hub}_punc"]
            punc_source = str(punc_value) if punc_value else None
        model_values: dict[str, Any] = {
            "model": model_source,
            "vad_model": vad_source,
            "vad_kwargs": {
                "max_single_segment_time": int(cleaned["max_single_segment_time"])
            },
            "device": device,
            "hub": hub,
            "disable_update": bool(local_paths),
            "log_level": "INFO",
        }
        if punc_source:
            model_values["punc_model"] = punc_source

        self._log(f"正在下载并加载模型：{profile['label']}")
        with self._model_cache_environment(self._model_dir(cleaned)):
            try:
                from funasr import AutoModel

                model = AutoModel(**model_values)
            except Exception as exc:
                raise RuntimeError(
                    f"FunASR 模型加载失败：{self._friendly_error(exc)}"
                ) from exc

        paths: dict[str, str | None] = {
            "model": getattr(model, "model_path", None),
            "vad": getattr(model, "vad_kwargs", {}).get("model_path"),
        }
        if profile[f"{hub}_punc"]:
            paths["punc"] = getattr(model, "punc_kwargs", {}).get("model_path")
        missing = [name for name, path in paths.items() if not path]
        if missing:
            raise RuntimeError(f"FunASR 未返回模型路径：{', '.join(missing)}")
        normalized_paths = {
            name: str(Path(str(path)).expanduser().resolve())
            for name, path in paths.items()
        }
        if not self._paths_exist(normalized_paths):
            raise RuntimeError("FunASR 模型下载完成后未找到模型文件")

        self._write_marker(cleaned, normalized_paths, device)
        self._model = model
        self._model_key = self._pipeline_key(cleaned)
        logs.extend(
            [
                f"模型已准备：{profile['label']}",
                f"运行设备：{device}",
                f"模型目录：{self._model_dir_label(cleaned)}",
            ]
        )

        status = self.refresh_status()
        if status.status != "ready":
            raise RuntimeError(status.message or "FunASR setup 未能完成")
        self._log(status.message or "FunASR setup 完成")
        return ProviderSetupResult(status=status, logs=logs)

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        source = Path(request.video_path).expanduser()
        if not source.is_file():
            raise FileNotFoundError(source)

        values = self._effective_values()
        status = self.status()
        if status.status != "ready":
            raise RuntimeError(status.message or "FunASR Provider 不可用")

        model = self._get_model(values)
        with self._prepared_audio(source) as audio_path:
            generate_values: dict[str, Any] = {
                "input": str(audio_path),
                "cache": {},
                "batch_size_s": int(values["batch_size_s"]),
                "merge_vad": True,
                "merge_length_s": 15,
                "use_itn": True,
            }
            if str(values["model"]) == "sensevoice-small":
                generate_values["language"] = str(values["language"])
            result = model.generate(**generate_values)
        return self._normalize_result(result)

    def _effective_values(self) -> dict[str, Any]:
        configured = dict(self.parameter_values)
        return {
            parameter.key: configured.get(parameter.key, parameter.default)
            for parameter in self.parameters
        }

    def _profile(self, values: dict[str, Any]) -> dict[str, str | None]:
        model = str(values.get("model") or "")
        try:
            return _MODEL_PROFILES[model]
        except KeyError as exc:
            raise RuntimeError(f"不支持的 FunASR 模型：{model}") from exc

    def _installed_versions(self) -> dict[str, str | None]:
        return {
            package: self._package_version(package)
            for package in ("funasr", "torch", "torchaudio", "imageio-ffmpeg")
        }

    @staticmethod
    def _package_version(package: str) -> str | None:
        try:
            return version(package)
        except PackageNotFoundError:
            return None

    def _get_model(self, values: dict[str, Any]) -> Any:
        key = self._pipeline_key(values)
        if self._model is not None and self._model_key == key:
            return self._model

        local_paths = self._ready_marker_paths(values)
        if local_paths is None:
            raise RuntimeError("FunASR 模型文件不完整，请重新运行 setup")
        self._model, _ = self._build_model(
            values,
            local_paths=local_paths,
            log_level="ERROR",
        )
        self._model_key = key
        return self._model

    def _build_model(
        self,
        values: dict[str, Any],
        *,
        local_paths: dict[str, str] | None,
        log_level: str,
    ) -> tuple[Any, str]:
        profile = self._profile(values)
        hub = str(values["hub"])
        if hub not in {"ms", "hf"}:
            raise RuntimeError(f"不支持的模型下载源：{hub}")
        device = self._resolve_device(str(values["device"]))

        if local_paths:
            model_source = local_paths["model"]
            vad_source = local_paths["vad"]
            punc_source = local_paths.get("punc")
        else:
            model_source = str(profile[f"{hub}_model"])
            vad_source = str(profile[f"{hub}_vad"])
            punc_value = profile[f"{hub}_punc"]
            punc_source = str(punc_value) if punc_value else None

        model_values: dict[str, Any] = {
            "model": model_source,
            "vad_model": vad_source,
            "vad_kwargs": {
                "max_single_segment_time": int(
                    values["max_single_segment_time"]
                )
            },
            "device": device,
            "hub": hub,
            "disable_update": bool(local_paths),
            "log_level": log_level,
        }
        if punc_source:
            model_values["punc_model"] = punc_source

        with self._model_cache_environment(self._model_dir(values)):
            try:
                from funasr import AutoModel

                return AutoModel(**model_values), device
            except Exception as exc:
                raise RuntimeError(
                    f"FunASR 模型加载失败：{self._friendly_error(exc)}"
                ) from exc

    def _model_dir(self, values: dict[str, Any]) -> Path | None:
        value = values.get("model_dir")
        if value in (None, ""):
            return None
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.resolve()

    def _state_dir(self, values: dict[str, Any]) -> Path:
        model_dir = self._model_dir(values)
        if model_dir is not None:
            return model_dir
        return Path.cwd() / ".data" / "models" / "funasr"

    def _model_dir_label(self, values: dict[str, Any]) -> str:
        model_dir = self._model_dir(values)
        return str(model_dir) if model_dir is not None else "官方默认缓存"

    @contextmanager
    def _model_cache_environment(self, model_dir: Path | None) -> Iterator[None]:
        if model_dir is None:
            yield
            return
        model_dir.mkdir(parents=True, exist_ok=True)
        updates = {
            "MODELSCOPE_CACHE": str(model_dir / "modelscope"),
            "HF_HOME": str(model_dir / "huggingface"),
        }
        previous = {key: os.environ.get(key) for key in updates}
        os.environ.update(updates)
        try:
            yield
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def _marker_path(self, values: dict[str, Any]) -> Path:
        signature = json.dumps(
            {
                "model": str(values["model"]),
                "hub": str(values["hub"]),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(signature.encode()).hexdigest()[:16]
        return self._state_dir(values) / f".cjdb-funasr-{digest}.json"

    def _write_marker(
        self,
        values: dict[str, Any],
        paths: dict[str, str],
        device: str,
    ) -> None:
        marker = self._marker_path(values)
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": str(values["model"]),
            "hub": str(values["hub"]),
            "device": device,
            "paths": paths,
            "versions": self._installed_versions(),
        }
        temporary = marker.with_suffix(f"{marker.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(marker)

    def _read_marker(self, values: dict[str, Any]) -> dict[str, Any] | None:
        marker = self._marker_path(values)
        if not marker.is_file():
            return None
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _ready_marker_paths(
        self,
        values: dict[str, Any],
    ) -> dict[str, str] | None:
        marker = self._read_marker(values)
        paths = marker.get("paths") if marker else None
        if not isinstance(paths, dict) or not self._paths_exist(paths):
            return None
        return {str(key): str(value) for key, value in paths.items()}

    @staticmethod
    def _paths_exist(paths: dict[str, Any]) -> bool:
        return bool(paths) and all(
            isinstance(path, str) and Path(path).is_dir()
            for path in paths.values()
        )

    def _pipeline_key(self, values: dict[str, Any]) -> str:
        marker = self._marker_path(values)
        marker_time = marker.stat().st_mtime_ns if marker.is_file() else 0
        return json.dumps(
            {
                key: values.get(key)
                for key in (
                    "model",
                    "hub",
                    "model_dir",
                    "device",
                    "language",
                    "batch_size_s",
                    "max_single_segment_time",
                )
            },
            sort_keys=True,
            default=str,
        ) + f":{marker_time}"

    @staticmethod
    def _resolve_device(requested: str) -> str:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("PyTorch 未安装，请运行 setup") from exc

        if requested == "auto":
            if torch.cuda.is_available():
                return "cuda:0"
            if (
                hasattr(torch.backends, "mps")
                and torch.backends.mps.is_available()
            ):
                return "mps"
            return "cpu"
        if requested.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError("未检测到可用的 NVIDIA GPU")
        if requested == "mps" and not (
            hasattr(torch.backends, "mps")
            and torch.backends.mps.is_available()
        ):
            raise RuntimeError("未检测到可用的 Apple GPU")
        if requested not in {"cpu", "mps", "cuda:0"}:
            raise RuntimeError(f"不支持的运行设备：{requested}")
        return requested

    @staticmethod
    def _ffmpeg_path() -> Path:
        try:
            from imageio_ffmpeg import get_ffmpeg_exe

            path = Path(get_ffmpeg_exe())
        except Exception as exc:
            raise RuntimeError("FFmpeg 不可用，请重新运行 setup") from exc
        if not path.is_file():
            raise RuntimeError("FFmpeg 文件不存在，请重新运行 setup")
        return path

    @contextmanager
    def _prepared_audio(self, source: Path) -> Iterator[Path]:
        if source.suffix.lower() not in _VIDEO_EXTENSIONS:
            yield source
            return

        with TemporaryDirectory(prefix="cjdb-funasr-") as directory:
            target = Path(directory) / "audio.wav"
            command = [
                str(self._ffmpeg_path()),
                "-nostdin",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(target),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            if completed.returncode != 0 or not target.is_file():
                reason = completed.stderr.strip().splitlines()
                message = reason[-1] if reason else "未知错误"
                raise RuntimeError(f"提取音轨失败：{message}")
            yield target

    def _normalize_result(self, result: Any) -> TranscriptionResult:
        entries = result if isinstance(result, list) else [result]
        texts: list[str] = []
        segments: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            text = self._clean_text(entry.get("text", ""))
            if text:
                texts.append(text)
            sentence_info = entry.get("sentence_info")
            if isinstance(sentence_info, list):
                segments.extend(self._normalize_segments(sentence_info))

        normalized_text = "".join(texts).strip()
        if not normalized_text and segments:
            normalized_text = "".join(
                str(segment.get("text", "")) for segment in segments
            ).strip()
        return TranscriptionResult(
            text=normalized_text,
            normalized_text=normalized_text,
            segments=segments,
        )

    def _normalize_segments(
        self,
        sentence_info: list[Any],
    ) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for sentence in sentence_info:
            if not isinstance(sentence, dict):
                continue
            text = self._clean_text(
                sentence.get("text") or sentence.get("sentence") or ""
            )
            value: dict[str, Any] = {
                "start": self._milliseconds_to_seconds(sentence.get("start")),
                "end": self._milliseconds_to_seconds(sentence.get("end")),
                "text": text,
            }
            if sentence.get("spk") is not None:
                value["speaker"] = sentence["spk"]
            values.append(value)
        return values

    @staticmethod
    def _milliseconds_to_seconds(value: Any) -> float | None:
        try:
            return float(value) / 1000
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = str(value or "")
        try:
            from funasr.utils.postprocess_utils import (
                rich_transcription_postprocess,
            )

            return str(rich_transcription_postprocess(text)).strip()
        except (ImportError, TypeError, ValueError):
            return text.strip()

    @staticmethod
    def _friendly_error(error: Exception) -> str:
        message = str(error).strip()
        lowered = message.lower()
        if any(value in lowered for value in ("name resolution", "dns", "nodename")):
            return "模型下载地址无法解析，请检查网络或切换下载源"
        if any(value in lowered for value in ("no space", "disk quota")):
            return "磁盘空间不足"
        return message or error.__class__.__name__

    @staticmethod
    def _format_command(command: list[str]) -> str:
        return " ".join(shlex.quote(part) for part in command)

    @staticmethod
    def _log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)
