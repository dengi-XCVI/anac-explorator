"""@notice Tests for the shared CLI result-envelope rendering helpers."""

from __future__ import annotations

import io
import json
import time
import unittest

from anac_explorator.errors import CliCommandError
from anac_explorator.models import CommandOutput, CommandWarning
from anac_explorator.output import emit_error_result, print_json_result, print_table_result, print_yaml_result


class OutputTests(unittest.TestCase):
    """@notice Verify centralized JSON and table rendering for command results."""

    def test_print_json_result_wraps_payload_and_warnings(self) -> None:
        """@notice Render a success envelope with structured warnings and meta fields."""

        output = io.StringIO()
        print_json_result(
            "schema",
            CommandOutput(
                data={"item_count": 2},
                warnings=[
                    CommandWarning(
                        code="PARTIAL_RESULTS",
                        message="Only a sample of the rows was retained.",
                        details={"retained": 2},
                    )
                ],
                paths={"raw_dir": "/tmp/raw"},
                truncated=True,
            ),
            started_at_ns=time.perf_counter_ns() - 5_000_000,
            stream=output,
        )

        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "schema")
        self.assertEqual(payload["data"]["item_count"], 2)
        self.assertEqual(payload["warnings"][0]["code"], "PARTIAL_RESULTS")
        self.assertEqual(payload["warnings"][0]["details"]["retained"], 2)
        self.assertTrue(payload["meta"]["truncated"])
        self.assertEqual(payload["meta"]["paths"]["raw_dir"], "/tmp/raw")
        self.assertGreaterEqual(payload["meta"]["elapsed_ms"], 0)

    def test_print_table_result_renders_shared_sections(self) -> None:
        """@notice Render the generic table output path from the same command result model."""

        output = io.StringIO()
        print_table_result(
            "query",
            CommandOutput(
                data={
                    "row_count": 2,
                    "rows": [
                        {"id": 1, "label": "one"},
                        {"id": 2, "label": "two"},
                    ],
                },
                warnings=[CommandWarning(code="ROW_LIMIT", message="Result set was capped.")],
            ),
            started_at_ns=time.perf_counter_ns() - 5_000_000,
            stream=output,
        )

        rendered = output.getvalue()
        self.assertIn("summary", rendered)
        self.assertIn("command | query", rendered)
        self.assertIn("data", rendered)
        self.assertIn("row_count | 2", rendered)
        self.assertIn("rows", rendered)
        self.assertIn("warnings", rendered)
        self.assertIn("ROW_LIMIT", rendered)

    def test_emit_error_result_prints_shared_error_envelope(self) -> None:
        """@notice Render handled runtime failures through the central JSON error path."""

        output = io.StringIO()
        emit_error_result(
            "query",
            CliCommandError(
                "QUERY_ERROR",
                "SQL query must not be empty.",
                details={"sql_query": ""},
            ),
            started_at_ns=time.perf_counter_ns() - 5_000_000,
            stream=output,
        )

        payload = json.loads(output.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "query")
        self.assertEqual(payload["error"]["code"], "QUERY_ERROR")
        self.assertEqual(payload["error"]["message"], "SQL query must not be empty.")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["sql_query"], "")
        self.assertEqual(payload["warnings"], [])

    def test_print_yaml_result_renders_nested_payload_without_yaml_dependency(self) -> None:
        """@notice Render config-style nested payloads as simple YAML text."""

        output = io.StringIO()
        print_yaml_result(
            {
                "transport": {
                    "default": "playwright",
                    "timeout": 30,
                },
                "download": {"keep_materialized": False},
            },
            stream=output,
        )

        rendered = output.getvalue()
        self.assertIn("transport:", rendered)
        self.assertIn("default: playwright", rendered)
        self.assertIn("timeout: 30", rendered)
        self.assertIn("keep_materialized: false", rendered)


if __name__ == "__main__":
    unittest.main()
