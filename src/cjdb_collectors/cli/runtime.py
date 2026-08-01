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
    from cjdb_collectors.config import load_settings

    settings = load_settings()
    base = settings.app.logs_dir if suffix == "log" else settings.app.data_dir
    path = Path(base) / f"{name}.{suffix}"
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


def _start_daemon(name: str, command: list[str]) -> dict[str, Any]:
    if pid := _read_pid(name):
        return {"status": "running", "pid": pid, "log": str(_log_path(name))}
    log_path = _log_path(name)
    log = log_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log.close()
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
    from cjdb_collectors.config import PROJECT_ROOT

    command = [str(PROJECT_ROOT / "cjdb"), "webui"]
    if host:
        command.extend(["--host", host])
    if port is not None:
        command.extend(["--port", str(port)])
    if reload:
        command.append("--reload")
    return command


def _run_webui(
    host: str | None = None,
    port: int | None = None,
    reload: bool = False,
) -> None:
    import uvicorn

    from cjdb_collectors.config import load_settings

    current_pid = os.getpid()
    if (pid := _read_pid("webui")) and pid != current_pid:
        raise typer.BadParameter(f"webui is already running (pid {pid})")
    settings = load_settings()
    _pid_path("webui").write_text(str(current_pid), encoding="utf-8")
    try:
        uvicorn.run(
            "cjdb_collectors.main:app",
            host=host or settings.web.host,
            port=port or settings.web.port,
            reload=reload,
        )
    finally:
        _pid_path("webui").unlink(missing_ok=True)


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
            _start_daemon(
                "webui",
                _webui_command(host, port, reload),
            ),
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
            _start_daemon(
                "webui",
                _webui_command(host, port, reload),
            ),
            view="runtime",
        )
    _run_webui(host, port, reload)


@webui_app.command("stop")
@output_command
def webui_stop(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        _stop_daemon("webui"),
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
    command = _webui_command(host, port, reload)
    if detach:
        return cli_result(
            _restart_daemon("webui", command),
            view="runtime",
        )
    _stop_daemon("webui")
    _run_webui(host, port, reload)


@webui_app.command("status")
@output_command
def webui_status(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    pid = _read_pid("webui")
    return cli_result(
        {
            "status": "running" if pid else "stopped",
            "pid": pid,
            "log": str(_log_path("webui")),
        },
        view="runtime",
    )


@webui_app.command("logs")
@output_command
def webui_logs(
    follow: bool = typer.Option(False, "-f", "--follow"),
    lines: int = typer.Option(100, "-n", "--lines", min=0),
    timestamps: bool = typer.Option(False, "-t", "--timestamps"),
    output_format: OutputFormat = format_option(),
) -> CLIResult | None:
    return show_log_file(
        _log_path("webui"),
        follow=follow,
        lines=lines,
        timestamps=timestamps,
        output_format=output_format,
    )


def _worker_command() -> list[str]:
    from cjdb_collectors.config import PROJECT_ROOT

    return [str(PROJECT_ROOT / "cjdb"), "worker"]


def _run_worker() -> None:
    from cjdb_collectors.config import load_settings
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
    config: str | None = typer.Option(None, "--config"),
) -> None:
    from cjdb_collectors.config import DEFAULT_CONFIG_PATH
    from cjdb_collectors.worker.worker import run_worker_task

    run_worker_task(worker_task_id, config or str(DEFAULT_CONFIG_PATH))
