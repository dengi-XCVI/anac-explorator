"""@notice Shared temporal-selection parsing and normalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Sequence


@dataclass(frozen=True, slots=True)
class TemporalSelection:
    """@notice Capture a normalized temporal selection in canonical CLI slice form.

    @param mode Selection mode such as `all`, `range`, `slice`, or `latest`.
    @param slices Canonical `YYYY-MM` slices covered by the selection when explicit.
    @param years Inclusive normalized year values when the selection came from `--year`.
    @param months Inclusive normalized month values when the selection came from `--month`.
    """

    mode: str
    slices: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    months: list[int] = field(default_factory=list)
    start_slice: str | None = None
    end_slice: str | None = None

    def to_period_identifiers(self) -> list[str]:
        """@notice Convert canonical slices into the warehouse's `YYYY_MM` period form."""

        return [slice_to_period_identifier(slice_value) for slice_value in self.slices]


def parse_temporal_selection(
    *,
    year: str | None = None,
    month: str | None = None,
    slice_value: str | None = None,
    latest: bool = False,
) -> TemporalSelection:
    """@notice Parse shared Phase 3 temporal flags into one normalized selection object."""

    if latest and any(value not in (None, "") for value in (year, month, slice_value)):
        raise ValueError("Use --latest by itself.")
    if slice_value not in (None, "") and any(value not in (None, "") for value in (year, month)):
        raise ValueError("Use either --slice or --year/--month, not both.")
    if month not in (None, "") and year in (None, ""):
        raise ValueError("Use --month only together with --year.")

    if latest:
        return TemporalSelection(mode="latest")
    if slice_value not in (None, ""):
        slice_parts = [part.strip() for part in str(slice_value).split(",") if part.strip()]
        if not slice_parts:
            raise ValueError("Use --slice with at least one YYYY-MM value.")
        return TemporalSelection(
            mode="slice",
            slices=sorted({normalize_slice_identifier(part) for part in slice_parts}),
        )
    if year not in (None, ""):
        years = parse_year_selection(str(year))
        months = parse_month_selection(str(month)) if month not in (None, "") else list(range(1, 13))
        slices = [
            f"{year_value:04d}-{month_value:02d}"
            for year_value in years
            for month_value in months
        ]
        return TemporalSelection(
            mode="range",
            slices=slices,
            years=years,
            months=months,
            start_slice=slices[0],
            end_slice=slices[-1],
        )
    return TemporalSelection(mode="all")


def parse_legacy_period_selection(
    *,
    periods: Sequence[str] | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
) -> TemporalSelection:
    """@notice Parse the current legacy CIG period arguments via the shared normalizers."""

    requested_periods = list(periods or [])
    normalized_periods = [period_to_slice_identifier(period) for period in requested_periods]
    normalized_start = None if period_start is None else period_to_slice_identifier(period_start)
    normalized_end = None if period_end is None else period_to_slice_identifier(period_end)

    if normalized_periods and (normalized_start is not None or normalized_end is not None):
        raise ValueError("Use either explicit --period values or a --from-period/--to-period range, not both.")
    if normalized_start is not None and normalized_end is not None and normalized_start > normalized_end:
        raise ValueError("The starting period must not be greater than the ending period.")

    if normalized_periods:
        return TemporalSelection(mode="slice", slices=sorted(set(normalized_periods)))
    if normalized_start is not None or normalized_end is not None:
        range_slices = (
            enumerate_slice_range(normalized_start, normalized_end)
            if normalized_start is not None and normalized_end is not None
            else []
        )
        return TemporalSelection(
            mode="range",
            slices=range_slices,
            start_slice=normalized_start,
            end_slice=normalized_end,
        )
    return TemporalSelection(mode="all")


def parse_year_selection(value: str) -> list[int]:
    """@notice Normalize one year or inclusive year range into ordered year values."""

    text = value.strip()
    range_match = re.fullmatch(r"(\d{4})-(\d{4})", text)
    if range_match is not None:
        start_year = int(range_match.group(1))
        end_year = int(range_match.group(2))
        if start_year > end_year:
            raise ValueError(f"Invalid year range {value!r}; start year must not be greater than end year.")
        return list(range(start_year, end_year + 1))

    single_match = re.fullmatch(r"\d{4}", text)
    if single_match is None:
        raise ValueError(f"Invalid year {value!r}; expected YYYY or YYYY-YYYY.")
    return [int(text)]


def parse_month_selection(value: str) -> list[int]:
    """@notice Normalize one month or inclusive month range into ordered month values."""

    text = value.strip()
    range_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)
    if range_match is not None:
        start_month = int(range_match.group(1))
        end_month = int(range_match.group(2))
        _validate_month(start_month, raw_value=value)
        _validate_month(end_month, raw_value=value)
        if start_month > end_month:
            raise ValueError(f"Invalid month range {value!r}; start month must not be greater than end month.")
        return list(range(start_month, end_month + 1))

    single_match = re.fullmatch(r"\d{1,2}", text)
    if single_match is None:
        raise ValueError(f"Invalid month {value!r}; expected M or M-M.")
    month = int(text)
    _validate_month(month, raw_value=value)
    return [month]


def normalize_slice_identifier(value: str) -> str:
    """@notice Normalize a canonical CLI slice into `YYYY-MM` form."""

    match = re.fullmatch(r"(\d{4})-(\d{1,2})", value.strip())
    if match is None:
        raise ValueError(f"Invalid slice {value!r}; expected YYYY-MM.")
    month = int(match.group(2))
    _validate_month(month, raw_value=value)
    return f"{match.group(1)}-{month:02d}"


def period_to_slice_identifier(value: str) -> str:
    """@notice Normalize a legacy `YYYY_MM` or `YYYY-MM` period into canonical slice form."""

    match = re.fullmatch(r"(\d{4})[-_](\d{1,2})", value.strip())
    if match is None:
        raise ValueError(f"Invalid period {value!r}; expected YYYY_MM.")
    month = int(match.group(2))
    _validate_month(month, raw_value=value)
    return f"{match.group(1)}-{month:02d}"


def slice_to_period_identifier(value: str) -> str:
    """@notice Convert a canonical CLI slice into the warehouse's `YYYY_MM` identifier."""

    normalized = normalize_slice_identifier(value)
    year, month = normalized.split("-", 1)
    return f"{year}_{month}"


def enumerate_slice_range(start_slice: str, end_slice: str) -> list[str]:
    """@notice Enumerate all inclusive monthly slices between two canonical endpoints."""

    normalized_start = normalize_slice_identifier(start_slice)
    normalized_end = normalize_slice_identifier(end_slice)
    if normalized_start > normalized_end:
        raise ValueError("The starting slice must not be greater than the ending slice.")

    start_year, start_month = (int(part) for part in normalized_start.split("-", 1))
    end_year, end_month = (int(part) for part in normalized_end.split("-", 1))
    year = start_year
    month = start_month
    slices: list[str] = []
    while (year, month) <= (end_year, end_month):
        slices.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return slices


def select_available_slices(selection: TemporalSelection, available_slices: Sequence[str]) -> list[str]:
    """@notice Apply one normalized selection to the available remote slice inventory."""

    normalized_available = sorted({normalize_slice_identifier(value) for value in available_slices})
    if selection.mode == "latest":
        if not normalized_available:
            raise ValueError("No remote slices are available for latest selection.")
        return [normalized_available[-1]]
    if selection.mode == "all":
        return normalized_available

    requested_slices = set(selection.slices)
    if selection.mode == "slice":
        missing_slices = sorted(requested_slices.difference(normalized_available))
        if missing_slices:
            raise ValueError(
                "The requested slices were not found in the available slice inventory: "
                + ", ".join(missing_slices)
            )

    if selection.mode == "range" and not selection.slices:
        selected_slices = [
            slice_value
            for slice_value in normalized_available
            if (selection.start_slice is None or slice_value >= selection.start_slice)
            and (selection.end_slice is None or slice_value <= selection.end_slice)
        ]
    else:
        selected_slices = [slice_value for slice_value in normalized_available if slice_value in requested_slices]
    if not selected_slices and selection.mode == "range":
        raise ValueError("No available slices matched the requested temporal range.")
    return selected_slices


def _validate_month(month: int, *, raw_value: str) -> None:
    """@notice Validate one normalized month integer."""

    if not 1 <= month <= 12:
        raise ValueError(f"Invalid month {raw_value!r}; month must be between 1 and 12.")
