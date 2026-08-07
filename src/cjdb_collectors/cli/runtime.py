from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import psutil
import typer

from .common import get_services
from .logs import show_log_file
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)

webui_app = typer.Typer(no_args_is_help=False, help="运行 WebUI 和 API。")
worker_app = typer.Typer(no_args_is_help=False, help="运行后台调度 Worker。")


def _runtime_path(name: str, suffix: str) -> Path:
    from cjdb_collectors.settings import load_settings
    from cjdb_collectors.services.logger import LoggerService, LogType

    settings = load_settings()
    path = (
        LoggerService.get_log_path(LogType.RUNTIME, name, settings=settings)
        if suffix == "log"
        else Path(settings.app.data_dir) / f"{name}.{suffix}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _pid_path(name: str) -> Path:
    return _runtime_path(name, "pid")


def _log_path(name: str) -> Path:
    return _runtime_path(name, "log")


def _read_pid(name: str) -> int | None:
    path = _pid_path(name)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not psutil.pid_exists(pid):
        path.unlink(missing_ok=True)
        return None
    return pid


def _webui_endpoint(
    host: str | None = None,
    port: int | None = None,
) -> tuple[str, int]:
    from cjdb_collectors.settings import load_settings

    settings = load_settings()
    return host or settings.web.host, port or settings.web.port


def _webui_name(
    host: str | None = None,
    port: int | None = None,
) -> str:
    _selected_host, selected_port = _webui_endpoint(host, port)
    return f"webui-{selected_port}"


def _webui_instance_status(
    name: str,
    *,
    host: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    pid = _read_pid(name)
    legacy = False
    if pid is None and name == _webui_name() and (legacy_pid := _read_pid("webui")):
        name = "webui"
        pid = legacy_pid
        legacy = True
    return {
        "name": name,
        "status": "running" if pid else "stopped",
        "pid": pid,
        "host": host,
        "port": port,
        "pid_path": str(_pid_path(name)),
        "log": str(_log_path(name)),
        **({"legacy": True} if legacy else {}),
    }


def _webui_statuses() -> list[dict[str, Any]]:
    from cjdb_collectors.settings import load_settings

    settings = load_settings()
    names = {
        path.stem
        for path in settings.app.data_dir.glob("webui*.pid")
        if path.is_file()
    }
    default_host, default_port = _webui_endpoint()
    names.add(_webui_name(default_host, default_port))

    values: list[dict[str, Any]] = []
    default_name = _webui_name(default_host, default_port)
    legacy_pid = _read_pid("webui")
    for name in sorted(names):
        if name == "webui":
            item = _webui_instance_status(
                name,
                host=default_host,
                port=default_port,
            )
            item["legacy"] = True
            values.append(item)
            continue
        if name == default_name and legacy_pid and not _read_pid(default_name):
            continue
        host = None
        port = None
        if name == default_name:
            host = default_host
            port = default_port
        elif name.startswith("webui-"):
            port_text = name.removeprefix("webui-")
            if port_text.isdigit():
                port = int(port_text)
        values.append(_webui_instance_status(name, host=host, port=port))
    return [item for item in values if item["pid"] is not None or item["name"] != "webui"]


def _start_webui_daemon(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
) -> dict[str, Any]:
    selected_host, selected_port = _webui_endpoint(host, port)
    name = _webui_name(selected_host, selected_port)
    if name == _webui_name() and (pid := _read_pid("webui")):
        return {
            "status": "running",
            "pid": pid,
            "name": "webui",
            "host": selected_host,
            "port": selected_port,
            "log": str(_log_path("webui")),
            "legacy": True,
        }
    return {
        **_start_daemon(
            name,
            _webui_command(selected_host, selected_port, reload),
        ),
        "name": name,
        "host": selected_host,
        "port": selected_port,
    }


def _stop_webui_daemon(
    host: str | None = None,
    port: int | None = None,
) -> dict[str, Any]:
    selected_host, selected_port = _webui_endpoint(host, port)
    name = _webui_name(selected_host, selected_port)
    result = _stop_daemon(name)
    stopped_name = name
    if result["pid"] is None and name == _webui_name():
        legacy_result = _stop_daemon("webui")
        if legacy_result["pid"] is not None:
            result = legacy_result
            stopped_name = "webui"
    return {
        **result,
        "name": stopped_name,
        "host": selected_host,
        "port": selected_port,
        "log": str(_log_path(stopped_name)),
    }


def _start_daemon(name: str, command: list[str]) -> dict[str, Any]:
    if pid := _read_pid(name):
        return {"status": "running", "pid": pid, "log": str(_log_path(name))}
    log_path = _log_path(name)
    from cjdb_collectors.services.logger import LoggerService

    with LoggerService.open_binary_append(log_path) as log:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    for _ in range(50):
        if process.poll() is not None:
            raise typer.BadParameter(
                f"{name} exited during startup; see {log_path}"
            )
        if _read_pid(name) == process.pid:
            break
        time.sleep(0.1)
    return {"status": "running", "pid": process.pid, "log": str(log_path)}


def _stop_daemon(name: str, timeout_seconds: float = 10) -> dict[str, Any]:
    pid = _read_pid(name)
    if not pid:
        return {"status": "stopped", "pid": None}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _pid_path(name).unlink(missing_ok=True)
        return {"status": "stopped", "pid": pid}
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline and psutil.pid_exists(pid):
        time.sleep(0.1)
    if psutil.pid_exists(pid):
        os.kill(pid, signal.SIGKILL)
    _pid_path(name).unlink(missing_ok=True)
    return {"status": "stopped", "pid": pid}


def _restart_daemon(name: str, command: list[str]) -> dict[str, Any]:
    _stop_daemon(name)
    return _start_daemon(name, command)


def _webui_command(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
) -> list[str]:
    from cjdb_collectors.settings import PROJECT_ROOT

    selected_host, selected_port = _webui_endpoint(host, port)
    command = [str(PROJECT_ROOT / "cjdb"), "webui"]
    command.extend(["--host", selected_host, "--port", str(selected_port)])
    if reload:
        command.append("--reload")
    return command


def _run_webui(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
) -> None:
    import uvicorn

    current_pid = os.getpid()
    selected_host, selected_port = _webui_endpoint(host, port)
    name = _webui_name(selected_host, selected_port)
    if name == _webui_name() and (pid := _read_pid("webui")) and pid != current_pid:
        raise typer.BadParameter(
            f"webui {selected_host}:{selected_port} is already running (pid {pid})"
        )
    if (pid := _read_pid(name)) and pid != current_pid:
        raise typer.BadParameter(
            f"webui {selected_host}:{selected_port} is already running (pid {pid})"
        )
    _pid_path(name).write_text(str(current_pid), encoding="utf-8")
    try:
        uvicorn.run(
            "cjdb_collectors.main:app",
            host=selected_host,
            port=selected_port,
            reload=reload,
        )
    finally:
        _pid_path(name).unlink(missing_ok=True)


@webui_app.callback(invoke_without_command=True)
@output_command
def webui(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    if ctx.invoked_subcommand is not None:
        return
    if detach:
        return cli_result(
            _start_webui_daemon(host, port, reload),
            view="runtime",
        )
    _run_webui(host, port, reload)


@webui_app.command("start")
@output_command
def webui_start(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    if detach:
        return cli_result(
            _start_webui_daemon(host, port, reload),
            view="runtime",
        )
    _run_webui(host, port, reload)


@webui_app.command("stop")
@output_command
def webui_stop(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        _stop_webui_daemon(host, port),
        view="runtime",
    )


@webui_app.command("restart")
@output_command
def webui_restart(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    selected_host, selected_port = _webui_endpoint(host, port)
    name = _webui_name(selected_host, selected_port)
    command = _webui_command(selected_host, selected_port, reload)
    if detach:
        _stop_webui_daemon(selected_host, selected_port)
        return cli_result(
            _start_daemon(name, command),
            view="runtime",
        )
    _stop_webui_daemon(selected_host, selected_port)
    _run_webui(selected_host, selected_port, reload)


@webui_app.command("status")
@output_command
def webui_status(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    if host is None and port is None:
        instances = _webui_statuses()
        running = [item for item in instances if item["pid"] is not None]
        return cli_result(
            {
                "status": "running" if running else "stopped",
                "running": len(running),
                "instances": instances,
            },
            view="runtime",
        )
    selected_host, selected_port = _webui_endpoint(host, port)
    name = _webui_name(selected_host, selected_port)
    return cli_result(
        _webui_instance_status(
            name,
            host=selected_host,
            port=selected_port,
        ),
        view="runtime",
    )


@webui_app.command("logs")
@output_command
def webui_logs(
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(100, "-n", "--lines", min=0),
    timestamps: bool = typer.Option(False, "-t", "--timestamps"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    return show_log_file(
        _log_path(_webui_name(host, port)),
        follow=follow,
        lines=lines,
        timestamps=timestamps,
        output_format=output_format,
    )


def _worker_command() -> list[str]:
    from cjdb_collectors.settings import PROJECT_ROOT

    return [str(PROJECT_ROOT / "cjdb"), "worker"]


def _run_worker() -> None:
    from cjdb_collectors.settings import load_settings
    from cjdb_collectors.db import migrate_database
    from cjdb_collectors.worker import Worker

    current_pid = os.getpid()
    if (pid := _read_pid("worker")) and pid != current_pid:
        raise typer.BadParameter(f"worker is already running (pid {pid})")
    settings = load_settings()
    migrate_database(settings)
    worker = Worker(settings)
    _pid_path("worker").write_text(str(current_pid), encoding="utf-8")

    def request_stop(_signum: int, _frame: Any) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        worker.run_forever()
    finally:
        _pid_path("worker").unlink(missing_ok=True)


@worker_app.callback(invoke_without_command=True)
@output_command
def worker(
    ctx: typer.Context,
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    if ctx.invoked_subcommand is not None:
        return
    if detach:
        return cli_result(
            _start_daemon("worker", _worker_command()),
            view="runtime",
        )
    _run_worker()


@worker_app.command("start")
@output_command
def worker_start(
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    if detach:
        return cli_result(
            _start_daemon("worker", _worker_command()),
            view="runtime",
        )
    _run_worker()


@worker_app.command("stop")
@output_command
def worker_stop(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        _stop_daemon("worker"),
        view="runtime",
    )


@worker_app.command("restart")
@output_command
def worker_restart(
    detach: bool = typer.Option(False, "-d", "--detach"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    if detach:
        return cli_result(
            _restart_daemon("worker", _worker_command()),
            view="runtime",
        )
    _stop_daemon("worker")
    _run_worker()


@worker_app.command("status")
@output_command
def worker_status(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    data = get_services().worker_tasks.health()
    pid = _read_pid("worker")
    data["status"] = "running" if pid else "stopped"
    data["pid"] = pid
    return cli_result(
        data,
        view="worker_status",
    )


@worker_app.command("logs")
@output_command
def worker_logs(
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(100, "-n", "--lines", min=0),
    timestamps: bool = typer.Option(False, "-t", "--timestamps"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    return show_log_file(
        _log_path("worker"),
        follow=follow,
        lines=lines,
        timestamps=timestamps,
        output_format=output_format,
    )


@worker_app.command("run-task", hidden=True)
def worker_run_task(
    worker_task_id: str = typer.Option(..., "--worker-task-id"),
    settings_path: str | None = typer.Option(None, "--config"),
) -> None:
    from cjdb_collectors.settings import DEFAULT_SETTINGS_PATH
    from cjdb_collectors.worker.worker import run_worker_task

    run_worker_task(worker_task_id, settings_path or str(DEFAULT_SETTINGS_PATH))
