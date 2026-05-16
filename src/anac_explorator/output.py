"""@notice Shared result-envelope rendering helpers for CLI commands."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import TextIO

from anac_explorator.errors import CliCommandError
from anac_explorator.models import (
    CommandError,
    CommandErrorEnvelope,
    CommandMeta,
    CommandOutput,
    CommandSuccessEnvelope,
)


def print_json_result(
    command: str,
    result: object,
    *,
    started_at_ns: int,
    stream: TextIO | None = None,
) -> None:
    """@notice Render one successful command result as a shared JSON envelope."""

    envelope = build_success_envelope(command, result, started_at_ns=started_at_ns)
    active_stream = sys.stdout if stream is None else stream
    active_stream.write(json.dumps(envelope.to_dict(), indent=2, ensure_ascii=False) + "\n")


def print_table_result(
    command: str,
    result: object,
    *,
    started_at_ns: int,
    stream: TextIO | None = None,
) -> None:
    """@notice Render one successful command result using a shared table path."""

    envelope = build_success_envelope(command, result, started_at_ns=started_at_ns)
    active_stream = sys.stdout if stream is None else stream
    active_stream.write(_render_success_table(envelope) + "\n")


def print_yaml_result(payload: object, *, stream: TextIO | None = None) -> None:
    """@notice Render one payload as simple dependency-free YAML."""

    active_stream = sys.stdout if stream is None else stream
    serialized = _serialize_yaml_payload(payload)
    active_stream.write(_render_yaml(serialized) + "\n")


def emit_error_result(
    command: str,
    error: Exception,
    *,
    started_at_ns: int,
    paths: dict[str, object] | None = None,
    stream: TextIO | None = None,
) -> None:
    """@notice Render one failed command result as a shared JSON error envelope."""

    envelope = build_error_envelope(command, error, started_at_ns=started_at_ns, paths=paths)
    active_stream = sys.stdout if stream is None else stream
    active_stream.write(json.dumps(envelope.to_dict(), indent=2, ensure_ascii=False) + "\n")


def build_success_envelope(
    command: str,
    result: object,
    *,
    started_at_ns: int,
) -> CommandSuccessEnvelope:
    """@notice Normalize a handler result into the shared success envelope."""

    normalized_result = _normalize_command_output(result)
    return CommandSuccessEnvelope(
        command=command,
        data=_serialize_payload(normalized_result.data),
        warnings=normalized_result.warnings,
        meta=_build_meta(
            started_at_ns,
            paths=normalized_result.paths,
            truncated=normalized_result.truncated,
        ),
    )


def build_error_envelope(
    command: str,
    error: Exception,
    *,
    started_at_ns: int,
    paths: dict[str, object] | None = None,
) -> CommandErrorEnvelope:
    """@notice Convert one exception into the shared error envelope."""

    if isinstance(error, CliCommandError):
        command_error = error.to_command_error()
    else:
        command_error = CommandError(
            code="VALIDATION_FAILED",
            message=str(error),
            retryable=False,
            details={"exception_type": type(error).__name__},
        )
    return CommandErrorEnvelope(
        command=command,
        error=command_error,
        meta=_build_meta(started_at_ns, paths=paths),
    )


def _build_meta(
    started_at_ns: int,
    *,
    paths: dict[str, object] | None = None,
    truncated: bool | None = None,
) -> CommandMeta:
    """@notice Build the shared meta block for one rendered command result."""

    elapsed_ms = max(0, (time.perf_counter_ns() - started_at_ns) // 1_000_000)
    return CommandMeta(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        elapsed_ms=elapsed_ms,
        paths={} if paths is None else paths,
        truncated=truncated,
    )


def _normalize_command_output(result: object) -> CommandOutput:
    """@notice Accept raw payloads or explicit command outputs from handlers."""

    if isinstance(result, CommandOutput):
        return result
    return CommandOutput(data=result)


def _serialize_payload(payload: object) -> dict[str, object]:
    """@notice Convert an arbitrary handler payload into a serializable mapping."""

    if isinstance(payload, dict):
        return payload

    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        serialized = to_dict()
        if not isinstance(serialized, dict):
            raise TypeError("Command payload to_dict() must return a dictionary.")
        return serialized

    if is_dataclass(payload):
        serialized = asdict(payload)
        if not isinstance(serialized, dict):
            raise TypeError("Command payload dataclass must serialize to a dictionary.")
        return serialized

    raise TypeError(f"Unsupported command payload type: {type(payload).__name__}")


def _serialize_yaml_payload(payload: object) -> object:
    """@notice Convert a payload into simple Python data suitable for YAML rendering."""

    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return payload
    if isinstance(payload, list):
        return [_serialize_yaml_payload(item) for item in payload]
    if isinstance(payload, dict):
        return {
            str(key): _serialize_yaml_payload(value)
            for key, value in payload.items()
        }
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        return _serialize_yaml_payload(to_dict())
    if is_dataclass(payload):
        return _serialize_yaml_payload(asdict(payload))
    raise TypeError(f"Unsupported YAML payload type: {type(payload).__name__}")


def _render_success_table(envelope: CommandSuccessEnvelope) -> str:
    """@notice Convert a success envelope into a simple human-readable table layout."""

    sections = [
        _render_key_value_table(
            "summary",
            {
                "status": "ok",
                "command": envelope.command,
                "contract_version": envelope.contract_version,
                "generated_at": envelope.meta.generated_at,
                "elapsed_ms": envelope.meta.elapsed_ms,
                "truncated": envelope.meta.truncated,
            },
        ),
        _render_payload_sections(envelope.data),
    ]
    if envelope.warnings:
        sections.append(
            _render_dict_rows_table(
                "warnings",
                [warning.to_dict() for warning in envelope.warnings],
            )
        )
    return "\n\n".join(section for section in sections if section)


def _render_payload_sections(payload: dict[str, object]) -> str:
    """@notice Render a nested payload into one or more simple table sections."""

    if not payload:
        return _render_key_value_table("data", {})

    sections: list[str] = []
    summary_rows = {
        key: value
        for key, value in payload.items()
        if not isinstance(value, (dict, list))
    }
    if summary_rows:
        sections.append(_render_key_value_table("data", summary_rows))

    for key, value in payload.items():
        if key in summary_rows:
            continue
        if isinstance(value, dict):
            sections.append(_render_key_value_table(key, value))
            continue
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            sections.append(_render_dict_rows_table(key, value))
            continue
        sections.append(_render_key_value_table(key, {key: value}))

    return "\n\n".join(sections)


def _render_key_value_table(title: str, rows: dict[str, object]) -> str:
    """@notice Render one mapping as a two-column markdown-like table."""

    lines = [title, "key | value", "--- | ---"]
    if not rows:
        lines.append("(empty) |")
        return "\n".join(lines)

    for key, value in rows.items():
        if value is None:
            continue
        lines.append(f"{key} | {_stringify_table_value(value)}")
    return "\n".join(lines)


def _render_dict_rows_table(title: str, rows: list[dict[str, object]]) -> str:
    """@notice Render a list of dictionaries as a markdown-like table."""

    if not rows:
        return _render_key_value_table(title, {})

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)

    header = " | ".join(columns)
    divider = " | ".join("---" for _ in columns)
    lines = [title, header, divider]
    for row in rows:
        lines.append(" | ".join(_stringify_table_value(row.get(column)) for column in columns))
    return "\n".join(lines)


def _stringify_table_value(value: object) -> str:
    """@notice Convert one value into a stable scalar used by the table renderer."""

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _render_yaml(payload: object, *, indent: int = 0) -> str:
    """@notice Render a simple JSON-compatible payload as YAML text."""

    prefix = "  " * indent
    if isinstance(payload, dict):
        if not payload:
            return "{}"
        lines: list[str] = []
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_render_yaml(value, indent=indent + 1))
            else:
                lines.append(f"{prefix}{key}: {_render_yaml_scalar(value)}")
        return "\n".join(lines)
    if isinstance(payload, list):
        if not payload:
            return "[]"
        lines = []
        for item in payload:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.append(_render_yaml(item, indent=indent + 1))
            else:
                lines.append(f"{prefix}- {_render_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_render_yaml_scalar(payload)}"


def _render_yaml_scalar(value: object) -> str:
    """@notice Render one scalar value as a YAML-compatible token."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(character in text for character in ":#\n") or text.strip() != text:
        return json.dumps(text, ensure_ascii=False)
    return text
