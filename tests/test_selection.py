"""@notice Tests for shared temporal selection parsing helpers."""

from __future__ import annotations

import unittest

from anac_explorator.selection import (
    enumerate_slice_range,
    parse_legacy_period_selection,
    parse_temporal_selection,
    period_to_slice_identifier,
    select_available_slices,
    slice_to_period_identifier,
)


class TemporalSelectionTests(unittest.TestCase):
    """@notice Verify canonical temporal-selection parsing and compatibility helpers."""

    def test_parse_year_selection_expands_to_full_year(self) -> None:
        """@notice Expand a bare year selection into all canonical monthly slices."""

        selection = parse_temporal_selection(year="2025")

        self.assertEqual(selection.mode, "range")
        self.assertEqual(selection.years, [2025])
        self.assertEqual(selection.months, list(range(1, 13)))
        self.assertEqual(selection.slices[0], "2025-01")
        self.assertEqual(selection.slices[-1], "2025-12")
        self.assertEqual(len(selection.slices), 12)

    def test_parse_year_and_month_ranges_expands_cross_product(self) -> None:
        """@notice Expand inclusive year and month ranges into canonical slices."""

        selection = parse_temporal_selection(year="2024-2025", month="2-3")

        self.assertEqual(selection.years, [2024, 2025])
        self.assertEqual(selection.months, [2, 3])
        self.assertEqual(selection.slices, ["2024-02", "2024-03", "2025-02", "2025-03"])

    def test_parse_slice_list_normalizes_and_deduplicates(self) -> None:
        """@notice Normalize explicit slice lists into sorted canonical values."""

        selection = parse_temporal_selection(slice_value="2025-2, 2025-02,2024-12")

        self.assertEqual(selection.mode, "slice")
        self.assertEqual(selection.slices, ["2024-12", "2025-02"])

    def test_parse_latest_selection_is_exclusive(self) -> None:
        """@notice Accept `--latest` alone and reject mixed temporal modes."""

        self.assertEqual(parse_temporal_selection(latest=True).mode, "latest")
        with self.assertRaisesRegex(ValueError, "Use --latest by itself"):
            parse_temporal_selection(year="2025", latest=True)

    def test_parse_temporal_selection_rejects_invalid_flag_combinations(self) -> None:
        """@notice Reject unsupported shared temporal flag combinations."""

        with self.assertRaisesRegex(ValueError, "Use --month only together with --year"):
            parse_temporal_selection(month="1")
        with self.assertRaisesRegex(ValueError, "Use either --slice or --year/--month"):
            parse_temporal_selection(year="2025", slice_value="2025-01")

    def test_parse_temporal_selection_rejects_invalid_ranges(self) -> None:
        """@notice Reject malformed or descending year and month ranges."""

        with self.assertRaisesRegex(ValueError, "Invalid year range"):
            parse_temporal_selection(year="2025-2024")
        with self.assertRaisesRegex(ValueError, "Invalid month range"):
            parse_temporal_selection(year="2025", month="4-2")

    def test_legacy_period_selection_normalizes_underscores_and_ranges(self) -> None:
        """@notice Parse current sync-period inputs through the shared canonical layer."""

        explicit = parse_legacy_period_selection(periods=["2025_1", "2025-02"])
        ranged = parse_legacy_period_selection(period_start="2025-02", period_end="2025_04")
        lower_bounded = parse_legacy_period_selection(period_start="2025-02")

        self.assertEqual(explicit.mode, "slice")
        self.assertEqual(explicit.slices, ["2025-01", "2025-02"])
        self.assertEqual(ranged.mode, "range")
        self.assertEqual(ranged.slices, ["2025-02", "2025-03", "2025-04"])
        self.assertEqual(lower_bounded.start_slice, "2025-02")
        self.assertIsNone(lower_bounded.end_slice)

    def test_slice_and_period_conversion_helpers_round_trip(self) -> None:
        """@notice Convert between canonical CLI slices and warehouse period identifiers."""

        self.assertEqual(period_to_slice_identifier("2025_3"), "2025-03")
        self.assertEqual(slice_to_period_identifier("2025-03"), "2025_03")
        self.assertEqual(enumerate_slice_range("2024-12", "2025-02"), ["2024-12", "2025-01", "2025-02"])

    def test_select_available_slices_validates_explicit_and_range_modes(self) -> None:
        """@notice Match selections against available inventory with mode-specific validation."""

        self.assertEqual(
            select_available_slices(
                parse_temporal_selection(year="2025", month="2-4"),
                ["2025-01", "2025-03", "2025-04", "2025-05"],
            ),
            ["2025-03", "2025-04"],
        )
        self.assertEqual(
            select_available_slices(parse_temporal_selection(latest=True), ["2025-01", "2025-03"]),
            ["2025-03"],
        )
        with self.assertRaisesRegex(ValueError, "requested slices were not found"):
            select_available_slices(
                parse_temporal_selection(slice_value="2025-01,2025-02"),
                ["2025-01"],
            )
        self.assertEqual(
            select_available_slices(
                parse_legacy_period_selection(period_end="2025-02"),
                ["2025-01", "2025-02", "2025-03"],
            ),
            ["2025-01", "2025-02"],
        )
