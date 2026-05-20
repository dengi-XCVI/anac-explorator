"""@notice Central error catalog, SQL policy checks, and CLI error translation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import duckdb

from anac_explorator.models import CommandError


@dataclass(frozen=True, slots=True)
class ErrorCatalogEntry:
    """@notice Describe one stable error code and its assigned exit code."""

    code: str
    exit_code: int
    retryable: bool


ERROR_CATALOG: dict[str, ErrorCatalogEntry] = {
    "DATASET_NOT_FOUND": ErrorCatalogEntry(code="DATASET_NOT_FOUND", exit_code=10, retryable=False),
    "DATASET_NOT_SUPPORTED": ErrorCatalogEntry(code="DATASET_NOT_SUPPORTED", exit_code=11, retryable=False),
    "TEMPORAL_SLICE_NOT_FOUND": ErrorCatalogEntry(code="TEMPORAL_SLICE_NOT_FOUND", exit_code=12, retryable=False),
    "DATASET_UPDATE_NOT_SUPPORTED": ErrorCatalogEntry(code="DATASET_UPDATE_NOT_SUPPORTED", exit_code=13, retryable=False),
    "NETWORK_ERROR": ErrorCatalogEntry(code="NETWORK_ERROR", exit_code=20, retryable=True),
    "TRANSPORT_BLOCKED": ErrorCatalogEntry(code="TRANSPORT_BLOCKED", exit_code=21, retryable=True),
    "PLAYWRIGHT_UNAVAILABLE": ErrorCatalogEntry(code="PLAYWRIGHT_UNAVAILABLE", exit_code=22, retryable=False),
    "LOCAL_DATASET_NOT_AVAILABLE": ErrorCatalogEntry(code="LOCAL_DATASET_NOT_AVAILABLE", exit_code=30, retryable=True),
    "SCHEMA_NOT_AVAILABLE": ErrorCatalogEntry(code="SCHEMA_NOT_AVAILABLE", exit_code=40, retryable=True),
    "SCHEMA_MISMATCH": ErrorCatalogEntry(code="SCHEMA_MISMATCH", exit_code=41, retryable=False),
    "VALIDATION_FAILED": ErrorCatalogEntry(code="VALIDATION_FAILED", exit_code=42, retryable=False),
    "WRITE_QUERY_BLOCKED": ErrorCatalogEntry(code="WRITE_QUERY_BLOCKED", exit_code=50, retryable=False),
    "QUERY_ERROR": ErrorCatalogEntry(code="QUERY_ERROR", exit_code=51, retryable=False),
    "UNKNOWN_RELATION": ErrorCatalogEntry(code="UNKNOWN_RELATION", exit_code=52, retryable=True),
    "CONFIG_ERROR": ErrorCatalogEntry(code="CONFIG_ERROR", exit_code=60, retryable=False),
    "CONFIG_KEY_NOT_FOUND": ErrorCatalogEntry(code="CONFIG_KEY_NOT_FOUND", exit_code=61, retryable=False),
    "INTEGRITY_FAILED": ErrorCatalogEntry(code="INTEGRITY_FAILED", exit_code=70, retryable=False),
}

_NETWORK_COMMANDS = {
    "download",
    "package-show",
    "download-cig-sample",
    "download-dataset-csv",
    "download-dataset-resource",
    "download-dataset-to-parquet",
    "sync-cig-periods",
    "build-vocabulary-crosswalks",
}
_WRITE_KEYWORDS = {
    "ALTER",
    "ATTACH",
    "CALL",
    "COPY",
    "CREATE",
    "DELETE",
    "DETACH",
    "DROP",
    "INSERT",
    "MERGE",
    "REPLACE",
    "TRUNCATE",
    "UPDATE",
    "VACUUM",
}
_UNKNOWN_RELATION_PATTERNS = (
    re.compile(r'(?i)(?:table|view)\s+with\s+name\s+"?([a-z0-9_.$-]+)"?\s+does not exist'),
    re.compile(r'(?i)relation\s+"?([a-z0-9_.$-]+)"?\s+does not exist'),
)


class CliCommandError(RuntimeError):
    """@notice Represent one structured CLI error with a stable code and exit code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """@notice Initialize the structured command error from the central catalog."""

        super().__init__(message)
        definition = ERROR_CATALOG[code]
        self.code = definition.code
        self.exit_code = definition.exit_code
        self.retryable = definition.retryable
        self.details = {} if details is None else details
        self.cause = cause

    def to_command_error(self) -> CommandError:
        """@notice Convert the structured CLI error into the shared envelope model."""

        return CommandError(
            code=self.code,
            message=str(self),
            retryable=self.retryable,
            details=self.details,
        )


class QueryPolicyError(RuntimeError):
    """@notice Report a SQL statement rejected by the CLI read-only policy."""

    def __init__(self, sql_query: str, keyword: str) -> None:
        """@notice Initialize the rejected query with its leading mutating keyword."""

        super().__init__(f"Write SQL is blocked unless the CLI explicitly opts into mutations: {keyword}.")
        self.sql_query = sql_query
        self.keyword = keyword


def detect_mutating_query(sql_query: str) -> str | None:
    """@notice Return the leading mutating SQL keyword after stripping whitespace and comments."""

    normalized = _strip_leading_sql_comments(sql_query)
    if not normalized:
        return None

    keyword_match = re.match(r"([A-Za-z]+)", normalized)
    if keyword_match is None:
        return None

    keyword = keyword_match.group(1).upper()
    if keyword not in _WRITE_KEYWORDS:
        return None
    if keyword == "COPY" and " TO " not in normalized.upper():
        return None
    return keyword


def enforce_read_only_query(sql_query: str, *, allow_write: bool = False) -> None:
    """@notice Reject obviously mutating SQL before it reaches DuckDB execution."""

    if allow_write:
        return
    keyword = detect_mutating_query(sql_query)
    if keyword is None:
        return
    raise QueryPolicyError(sql_query, keyword)


def resolve_query_execution_error(
    exc: Exception,
    *,
    db_path: str | Path | None = None,
    sql_query: str | None = None,
    timeout_seconds: int | None = None,
) -> CliCommandError:
    """@notice Translate backend query execution failures into stable Phase 3 query errors."""

    details = {
        "db_path": None if db_path is None else str(db_path),
        "sql_query": sql_query,
    }
    if timeout_seconds is not None:
        details["timeout_seconds"] = timeout_seconds
    if isinstance(exc, QueryPolicyError):
        return CliCommandError(
            "WRITE_QUERY_BLOCKED",
            f"Write SQL is blocked: {exc.keyword}.",
            details={
                **details,
                "blocked_keyword": exc.keyword,
            },
            cause=exc,
        )
    if isinstance(exc, FileNotFoundError):
        missing_path = str(db_path) if db_path is not None else _missing_path(exc)
        return CliCommandError(
            "LOCAL_DATASET_NOT_AVAILABLE",
            f"DuckDB database not found: {missing_path}",
            details={
                **details,
                "path": missing_path,
            },
            cause=exc,
        )
    if isinstance(exc, TimeoutError):
        return CliCommandError(
            "QUERY_ERROR",
            str(exc),
            details=details,
            cause=exc,
        )
    if isinstance(exc, duckdb.Error):
        missing_relation = _extract_missing_relation(str(exc))
        if missing_relation is not None:
            return CliCommandError(
                "UNKNOWN_RELATION",
                f"Relation '{missing_relation}' does not exist.",
                details={
                    **details,
                    "relation": missing_relation,
                    **_collect_available_relations(None if db_path is None else str(db_path)),
                },
                cause=exc,
            )
        return CliCommandError(
            "QUERY_ERROR",
            str(exc),
            details=details,
            cause=exc,
        )
    if isinstance(exc, ValueError):
        return CliCommandError(
            "QUERY_ERROR",
            str(exc),
            details=details,
            cause=exc,
        )

    return CliCommandError(
        "QUERY_ERROR",
        str(exc),
        details={
            **details,
            "exception_type": type(exc).__name__,
        },
        cause=exc,
    )


def resolve_command_error(command: str, exc: Exception, *, args: object | None = None) -> CliCommandError:
    """@notice Translate raw exceptions into stable Phase 3 CLI errors."""

    if isinstance(exc, CliCommandError):
        return exc

    if command in {"query", "query-local-data"}:
        return _resolve_query_error(exc, args=args)
    if isinstance(exc, FileNotFoundError):
        return _resolve_file_not_found(command, exc, args=args)
    if isinstance(exc, UnicodeDecodeError):
        return CliCommandError(
            "VALIDATION_FAILED",
            f"Failed to decode the requested input: {exc}",
            details={"command": command},
            cause=exc,
        )
    if isinstance(exc, ValueError):
        return _resolve_value_error(command, exc, args=args)
    if isinstance(exc, duckdb.Error):
        return _resolve_duckdb_error(command, exc, args=args)
    if command in _NETWORK_COMMANDS:
        return _resolve_network_error(command, exc, args=args)

    return CliCommandError(
        "VALIDATION_FAILED",
        str(exc),
        details={"command": command, "exception_type": type(exc).__name__},
        cause=exc,
    )


def _resolve_query_error(exc: Exception, *, args: object | None) -> CliCommandError:
    """@notice Map query-command failures to the documented query error codes."""

    return resolve_query_execution_error(
        exc,
        db_path=_get_attr(args, "db_path"),
        sql_query=_get_attr(args, "sql_query"),
        timeout_seconds=_get_attr(args, "query_timeout"),
    )


def _resolve_network_error(command: str, exc: Exception, *, args: object | None) -> CliCommandError:
    """@notice Map remote-access failures to stable transport and network codes."""

    message = str(exc)
    lowered = message.casefold()
    details = {
        "command": command,
        "dataset_id": _get_attr(args, "dataset_id") or _get_attr(args, "dataset"),
        "transport": _get_attr(args, "transport"),
    }
    if "playwright is not installed" in lowered:
        return CliCommandError("PLAYWRIGHT_UNAVAILABLE", message, details=details, cause=exc)
    if "not found" in lowered and command == "package-show":
        return CliCommandError("DATASET_NOT_FOUND", message, details=details, cause=exc)
    if (
        "could not find a downloadable" in lowered
        or "could not find resource" in lowered
        or "does not match expected format" in lowered
    ):
        return CliCommandError("DATASET_NOT_SUPPORTED", message, details=details, cause=exc)
    if "blocked or filtered" in lowered or "html instead of json" in lowered or "waf" in lowered:
        return CliCommandError("TRANSPORT_BLOCKED", message, details=details, cause=exc)
    return CliCommandError("NETWORK_ERROR", message, details=details, cause=exc)


def _resolve_file_not_found(command: str, exc: FileNotFoundError, *, args: object | None) -> CliCommandError:
    """@notice Map missing local files to schema or local-artifact errors."""

    missing_path = _best_effort_missing_path(exc, args)
    if _is_schema_related_path(missing_path, args):
        return CliCommandError(
            "SCHEMA_NOT_AVAILABLE",
            f"Required schema artifact not found: {missing_path}",
            details={"command": command, "path": missing_path},
            cause=exc,
        )
    return CliCommandError(
        "LOCAL_DATASET_NOT_AVAILABLE",
        f"Required local artifact not found: {missing_path}",
        details={"command": command, "path": missing_path},
        cause=exc,
    )


def _resolve_value_error(command: str, exc: ValueError, *, args: object | None) -> CliCommandError:
    """@notice Map validation-style runtime errors to stable command codes."""

    message = str(exc)
    lowered = message.casefold()
    if command == "sync-cig-periods" and "requested cig periods were not found" in lowered:
        return CliCommandError(
            "TEMPORAL_SLICE_NOT_FOUND",
            message,
            details={
                "command": command,
                "dataset_id": _get_attr(args, "dataset_id"),
                "requested_periods": list(_get_attr(args, "period") or []),
            },
            cause=exc,
        )
    return CliCommandError(
        "VALIDATION_FAILED",
        message,
        details={"command": command},
        cause=exc,
    )


def _resolve_duckdb_error(command: str, exc: duckdb.Error, *, args: object | None) -> CliCommandError:
    """@notice Map non-query DuckDB failures to stable validation or schema errors."""

    message = str(exc)
    lowered = message.casefold()
    if "schema" in lowered or "column" in lowered:
        return CliCommandError(
            "SCHEMA_MISMATCH",
            message,
            details={"command": command},
            cause=exc,
        )
    return CliCommandError(
        "VALIDATION_FAILED",
        message,
        details={"command": command},
        cause=exc,
    )


def _strip_leading_sql_comments(sql_query: str) -> str:
    """@notice Remove leading whitespace and SQL comments before verb inspection."""

    remaining = sql_query
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            newline_index = remaining.find("\n")
            remaining = "" if newline_index < 0 else remaining[newline_index + 1 :]
            continue
        if remaining.startswith("/*"):
            comment_end = remaining.find("*/")
            remaining = "" if comment_end < 0 else remaining[comment_end + 2 :]
            continue
        return remaining


def _extract_missing_relation(message: str) -> str | None:
    """@notice Pull the missing relation name out of common DuckDB error strings."""

    for pattern in _UNKNOWN_RELATION_PATTERNS:
        match = pattern.search(message)
        if match is not None:
            return match.group(1)
    return None


def _collect_available_relations(db_path: object) -> dict[str, list[str]]:
    """@notice Enumerate current logical relations for unknown-relation recovery hints."""

    if not isinstance(db_path, str) or not db_path:
        return {
            "available_dataset_views": [],
            "available_metadata_views": [],
        }

    path = Path(db_path)
    if not path.exists():
        return {
            "available_dataset_views": [],
            "available_metadata_views": [],
        }

    connection = duckdb.connect(str(path), read_only=True)
    try:
        from anac_explorator.metadata_views import ensure_metadata_views

        ensure_metadata_views(connection, db_path=path)
        rows = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
            ORDER BY table_name
            """
        ).fetchall()
    except duckdb.Error:
        return {
            "available_dataset_views": [],
            "available_metadata_views": [],
        }
    finally:
        connection.close()

    names = [str(row[0]) for row in rows]
    return {
        "available_dataset_views": [
            name for name in names if not name.startswith("anac_") and not name.startswith("__tmp_")
        ],
        "available_metadata_views": [name for name in names if name.startswith("anac_")],
    }


def _missing_path(exc: FileNotFoundError) -> str:
    """@notice Return the missing path from a FileNotFoundError when available."""

    if exc.filename:
        return str(exc.filename)
    message = str(exc)
    quote_match = re.search(r"[\"']([^\"']+)[\"']", message)
    if quote_match is not None:
        return quote_match.group(1)
    return message


def _best_effort_missing_path(exc: FileNotFoundError, args: object | None) -> str:
    """@notice Prefer an explicit CLI path argument when FileNotFoundError omitted it."""

    if exc.filename:
        return str(exc.filename)
    for attribute_name in (
        "db_path",
        "manifest_path",
        "resource_path",
        "csv_path",
        "schema_path",
        "left_schema_path",
        "right_schema_path",
        "comparison_path",
        "vocabulary_index_path",
    ):
        attribute_value = _get_attr(args, attribute_name)
        if isinstance(attribute_value, str) and attribute_value:
            return attribute_value
    return _missing_path(exc)


def _is_schema_related_path(path: str, args: object | None) -> bool:
    """@notice Determine whether a missing file belongs to the schema artifact family."""

    if path.endswith(".schema.json"):
        return True
    if args is None:
        return False

    for attribute_name in ("schema_path", "left_schema_path", "right_schema_path"):
        attribute_value = _get_attr(args, attribute_name)
        if isinstance(attribute_value, str) and attribute_value == path:
            return True
    return False


def _get_attr(target: object | None, name: str) -> object | None:
    """@notice Return one optional attribute from a loose args-like object."""

    if target is None:
        return None
    return getattr(target, name, None)
