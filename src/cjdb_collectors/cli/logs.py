from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import typer

from .output import CLIResult, OutputFormat


def provider_log_path(namespace: str) -> Path:
    from cjdb_collectors.config import load_settings

    path = Path(load_settings().app.logs_dir) / f"provider-{namespace}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def show_log_file(
    path: Path,
    *,
    follow: bool,
    lines: int,
    timestamps: bool,
    output_format: OutputFormat = OutputFormat.TEXT,
) -> CLIResult | None:
    if not path.exists():
        raise typer.BadParameter(f"log file does not exist: {path}")
    if follow and output_format == OutputFormat.JSON:
        raise typer.BadParameter(
            "--format=json 不能与 --follow/-f 同时使用"
        )

    def format_line(line: str) -> str:
        value = line.rstrip("\n")
        if timestamps:
            value = f"{datetime.now().astimezone().isoformat()} {value}"
        return value

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
        selected = [
            format_line(line)
            for line in (content[-lines:] if lines else [])
        ]
        if not follow:
            return CLIResult(
                text="\n".join(selected),
                json={
                    "path": str(path),
                    "lines": selected,
                },
            )
        for line in selected:
            typer.echo(line)
        while True:
            line = handle.readline()
            if line:
                typer.echo(format_line(line))
            else:
                time.sleep(0.2)
