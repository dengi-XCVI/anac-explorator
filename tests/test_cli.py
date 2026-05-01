"""@notice Tests for the command-line facade."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout

from anac_explorator.cli import main


class CliTests(unittest.TestCase):
    """@notice Verify that the CLI emits machine-readable payloads."""

    def test_inspect_csv_schema_prints_json(self) -> None:
        """@notice Emit a schema mapping for a semicolon-delimited CSV file."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo;data_pubblicazione\n")
            handle.write("0001;100.50;2026-01-31\n")
            handle.write("0002;;2026-02-28\n")
            handle.flush()

            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["inspect-csv-schema", handle.name])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["rows_sampled"], 2)
        self.assertEqual(payload["columns"][1]["inferred_type"], "decimal")
        self.assertTrue(payload["columns"][1]["nullable"])

    def test_download_dataset_csv_parser_accepts_dataset_slug(self) -> None:
        """@notice Parse the generic dataset-download subcommand without execution."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["download-dataset-csv", "bandi-cig-tipo-scelta-contraente"])

        self.assertEqual(args.dataset_id, "bandi-cig-tipo-scelta-contraente")

    def test_build_data_dictionary_parser_uses_default_artifacts(self) -> None:
        """@notice Parse the data-dictionary subcommand with its default artifact paths."""

        parser = main.__globals__["build_parser"]()
        args = parser.parse_args(["build-data-dictionary"])

        self.assertEqual(args.schema_path, "schemas/cig_2025_01.schema.json")
        self.assertEqual(args.vocabulary_index_path, "vocabularies/index.json")
