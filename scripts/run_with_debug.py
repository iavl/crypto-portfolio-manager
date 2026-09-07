#!/usr/bin/env python3
"""Run one command and emit a safe record for any execution failure."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_portfolio.providers.http import redact_log, redact_secrets  # noqa: E402


def _environment_secrets() -> tuple[str, ...]:
    markers = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "COOKIE")
    return tuple(dict.fromkeys(
        value for name, value in os.environ.items()
        if value and any(marker in name.upper() for marker in markers)
    ))


def _output_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _safe_command(command: Sequence[str], secrets: tuple[str, ...]) -> list[str]:
    return [str(redact_secrets(item, secrets)) for item in command]


def _failure_record(
    script: str,
    command: Sequence[str],
    *,
    status: str,
    exit_code: int | None,
    log_source: str,
    log: str,
    secrets: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "script": script,
        "command": _safe_command(command, secrets),
        "status": status,
        "exit_code": exit_code,
        "log_source": log_source,
        "log": redact_log(log, secrets),
    }


def run_script(script: str, command: Sequence[str], *, timeout_seconds: float = 120.0) -> dict[str, Any]:
    """Run argv without a shell and return a report-ready failure record."""
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be a non-empty string")
    if isinstance(command, (str, bytes)) or not isinstance(command, Sequence) or not command:
        raise ValueError("command must be a non-empty sequence")
    if any(not isinstance(item, str) or not item for item in command):
        raise ValueError("command must contain non-empty strings")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    secrets = _environment_secrets()
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=float(timeout_seconds),
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _output_text(exc.stderr)
        stdout = _output_text(exc.stdout)
        return _failure_record(
            script,
            command,
            status="TIMEOUT",
            exit_code=None,
            log_source="STDERR" if stderr.strip() else "STDOUT" if stdout.strip() else "LAUNCHER",
            log=stderr if stderr.strip() else stdout if stdout.strip() else f"process timed out after {timeout_seconds:g}s",
            secrets=secrets,
        )
    except OSError as exc:
        return _failure_record(
            script,
            command,
            status="LAUNCH_FAILED",
            exit_code=None,
            log_source="LAUNCHER",
            log="".join(traceback.format_exception_only(type(exc), exc)),
            secrets=secrets,
        )

    if completed.returncode == 0:
        return {
            "script": script,
            "command": _safe_command(command, secrets),
            "status": "SUCCESS",
            "exit_code": 0,
        }
    stderr = completed.stderr or ""
    stdout = completed.stdout or ""
    return _failure_record(
        script,
        command,
        status="FAILED",
        exit_code=completed.returncode,
        log_source="STDERR" if stderr.strip() else "STDOUT" if stdout.strip() else "LAUNCHER",
        log=stderr if stderr.strip() else stdout if stdout.strip() else f"process exited with code {completed.returncode}",
        secrets=secrets,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, help="report label for the command")
    parser.add_argument("--timeout", type=float, default=120.0, help="timeout in seconds")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="command after --")
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        parser.error("a command is required after --")
    try:
        record = run_script(args.script, command, timeout_seconds=args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
