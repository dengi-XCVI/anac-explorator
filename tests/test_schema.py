"""@notice Tests for CSV schema inference."""

from __future__ import annotations

import tempfile
import unittest

from anac_explorator.schema import map_csv_schema


class SchemaMappingTests(unittest.TestCase):
    """@notice Verify CSV schema mapping behavior."""

    def test_map_csv_schema_preserves_column_order_and_infers_types(self) -> None:
        """@notice Infer basic scalar types while keeping raw headers intact."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;importo;flag;data\n")
            handle.write("A001;10.50;true;2026-04-30\n")
            handle.write("A002;20.10;false;2026-05-01\n")
            handle.flush()

            mapping = map_csv_schema(handle.name)

        self.assertEqual([column.name for column in mapping.columns], ["cig", "importo", "flag", "data"])
        self.assertEqual(mapping.columns[0].inferred_type, "text")
        self.assertEqual(mapping.columns[1].inferred_type, "decimal")
        self.assertEqual(mapping.columns[2].inferred_type, "boolean")
        self.assertEqual(mapping.columns[3].inferred_type, "date")

    def test_map_csv_schema_keeps_numeric_codes_as_integers(self) -> None:
        """@notice Avoid treating numeric code columns as booleans just because values are 0/1."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("mese_pubblicazione;cod_modalita_realizzazione;flag_pnrr_pnc\n")
            handle.write("1;1;1\n")
            handle.write("1;1;0\n")
            handle.flush()

            mapping = map_csv_schema(handle.name)

        self.assertEqual(mapping.columns[0].inferred_type, "integer")
        self.assertEqual(mapping.columns[1].inferred_type, "integer")
        self.assertEqual(mapping.columns[2].inferred_type, "boolean")

    def test_map_csv_schema_scans_full_file_when_sample_limit_is_zero(self) -> None:
        """@notice Treat non-positive sample limits as a request to scan the full file."""

        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", suffix=".csv") as handle:
            handle.write("cig;flag\n")
            handle.write("A001;1\n")
            handle.write("A002;0\n")
            handle.write("A003;1\n")
            handle.flush()

            mapping = map_csv_schema(handle.name, sample_limit=0)

        self.assertEqual(mapping.rows_sampled, 3)
