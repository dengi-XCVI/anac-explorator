"""@notice Tests for data-dictionary generation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anac_explorator.dictionary import build_cig_data_dictionary


class DataDictionaryTests(unittest.TestCase):
    """@notice Verify data-dictionary generation from existing artifacts."""

    def test_build_cig_data_dictionary_links_vocab_and_cross_year_notes(self) -> None:
        """@notice Merge schema, comparison, and vocabulary artifacts into one dictionary."""

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            comparison_path = temp_path / "comparison.json"
            vocabulary_dir = temp_path / "vocabularies"
            vocabulary_dir.mkdir()
            vocabulary_index_path = vocabulary_dir / "index.json"
            output_dir = temp_path / "dictionaries"

            schema_path.write_text(
                json.dumps(
                    {
                        "source_path": "demo.csv",
                        "delimiter": ";",
                        "encoding": "utf-8-sig",
                        "rows_sampled": 2,
                        "row_length_mismatches": 0,
                        "columns": [
                            {
                                "name": "numero_gara",
                                "inferred_type": "text",
                                "nullable": False,
                                "non_empty_samples": ["A-001"],
                            },
                            {
                                "name": "cod_tipo_scelta_contraente",
                                "inferred_type": "integer",
                                "nullable": False,
                                "non_empty_samples": ["24"],
                            },
                            {
                                "name": "tipo_scelta_contraente",
                                "inferred_type": "text",
                                "nullable": False,
                                "non_empty_samples": ["AFFIDAMENTO DIRETTO"],
                            },
                            {
                                "name": "cod_esito",
                                "inferred_type": "decimal",
                                "nullable": True,
                                "non_empty_samples": ["1.0"],
                            },
                            {
                                "name": "ESITO",
                                "inferred_type": "text",
                                "nullable": True,
                                "non_empty_samples": ["AGGIUDICATA"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            comparison_path.write_text(
                json.dumps(
                    {
                        "type_changes": [
                            {
                                "name": "numero_gara",
                                "left_type": "integer",
                                "right_type": "text",
                            }
                        ],
                        "nullable_changes": [],
                    }
                ),
                encoding="utf-8",
            )
            (vocabulary_dir / "bandi-cig-tipo-scelta-contraente.json").write_text(
                json.dumps(
                    {
                        "tables": [
                            {
                                "name": "tipo_scelta_contraente",
                                "entry_count": 2,
                                "entries": [
                                    {"code": "1", "label": "PROCEDURA APERTA"},
                                    {"code": "24", "label": "AFFIDAMENTO DIRETTO"},
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            vocabulary_index_path.write_text(
                json.dumps(
                    {
                        "field_links": [
                            {
                                "scope": "current_cig_schema",
                                "dataset_id": "bandi-cig-tipo-scelta-contraente",
                                "table_name": "tipo_scelta_contraente",
                                "source_code_field": "cod_tipo_scelta_contraente",
                                "source_label_field": "tipo_scelta_contraente",
                                "target_code_field": "code",
                                "target_label_field": "label",
                                "code_meaning_status": "resolved_external",
                                "external_vocabulary_status": "resolved",
                                "resolved_fields": [
                                    "cod_tipo_scelta_contraente",
                                    "tipo_scelta_contraente",
                                ],
                                "join_contract": {
                                    "target_dataset": "bandi-cig-tipo-scelta-contraente",
                                    "target_table": "tipo_scelta_contraente",
                                    "source_field": "cod_tipo_scelta_contraente",
                                    "target_field": "code",
                                    "target_label_field": "label",
                                    "join_type": "left",
                                },
                                "notes": "Demo code mapping.",
                            }
                        ],
                        "current_cig_schema_gaps": [
                            {
                                "field": "cod_esito",
                                "label_field": "ESITO",
                                "semantic_type": "controlled_vocabulary_code",
                                "code_meaning_status": "resolved_inline",
                                "external_vocabulary_status": "missing_dataset",
                                "observed_pattern": "code_like",
                                "non_empty_row_count": 1,
                                "unique_value_count": 1,
                                "unique_values_sample": ["1.0"],
                                "unique_label_count": 1,
                                "unique_labels_sample": ["AGGIUDICATA"],
                                "paired_values_sample": [{"code": "1.0", "label": "AGGIUDICATA"}],
                                "normalization": {
                                    "canonicalization_strategy": "preserve_raw",
                                    "canonicalization_safe": False,
                                    "unsafe_rules": [
                                        {
                                            "rule": "heuristic_code_merge",
                                            "reason": "Demo inline-only mapping.",
                                        }
                                    ],
                                    "label_alias_groups": [],
                                },
                                "hypothesis": "Likely controlled vocabulary already embedded in the CIG extract; no dedicated external dataset is wired yet.",
                                "notes": "No dedicated controlled vocabulary dataset has been wired yet.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = build_cig_data_dictionary(
                schema_path=schema_path,
                comparison_path=comparison_path,
                vocabulary_index_path=vocabulary_index_path,
                vocabulary_dir=vocabulary_dir,
                output_dir=output_dir,
                dictionary_name="demo",
                dataset_id="demo-dataset",
            )

            self.assertEqual(result["entry_count"], 5)
            self.assertEqual(
                result["resolved_code_fields"],
                ["ESITO", "cod_esito", "cod_tipo_scelta_contraente", "tipo_scelta_contraente"],
            )
            self.assertEqual(result["unresolved_code_fields"], [])
            self.assertEqual(result["missing_external_vocabulary_fields"], ["cod_esito"])

            dictionary_payload = json.loads((output_dir / "demo.dictionary.json").read_text(encoding="utf-8"))
            by_name = {entry["name"]: entry for entry in dictionary_payload["entries"]}
            self.assertEqual(by_name["cod_tipo_scelta_contraente"]["semantic_type"], "controlled_vocabulary_code")
            self.assertEqual(by_name["cod_tipo_scelta_contraente"]["code_meaning_status"], "resolved_external")
            self.assertEqual(by_name["cod_tipo_scelta_contraente"]["code_reference"]["join_contract"]["target_table"], "tipo_scelta_contraente")
            self.assertEqual(by_name["cod_tipo_scelta_contraente"]["code_reference"]["entry_count"], 2)
            self.assertIn("Compared with January 2007", by_name["numero_gara"]["cross_year_notes"][0])
            self.assertEqual(by_name["cod_esito"]["code_meaning_status"], "resolved_inline")
            self.assertEqual(by_name["cod_esito"]["external_vocabulary_status"], "missing_dataset")
            self.assertEqual(by_name["cod_esito"]["paired_field"], "ESITO")
            self.assertEqual(by_name["ESITO"]["semantic_type"], "controlled_vocabulary_label")

            markdown = (output_dir / "demo.dictionary.md").read_text(encoding="utf-8")
            self.assertIn("## Publication and procedure", markdown)
            self.assertIn("### `cod_tipo_scelta_contraente`", markdown)
