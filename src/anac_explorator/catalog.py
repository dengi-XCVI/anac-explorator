"""@notice Explicit dataset-family registry, catalog discovery, and adapter dispatch helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Sequence

import duckdb

from anac_explorator.ckan import CkanClientError
from anac_explorator.drop import execute_drop_plan, plan_cig_drop
from anac_explorator.errors import CliCommandError
from anac_explorator.integrity import validate_local_data_integrity
from anac_explorator.loader import (
    download_dataset_to_parquet,
    load_downloaded_resource,
    plan_cig_period_updates,
    sync_cig_periods_to_parquet,
)
from anac_explorator.metadata_views import ensure_metadata_views
from anac_explorator.models import (
    CommandWarning,
    CkanResource,
    DatasetIncrementalUpdateResult,
    DatasetParquetDownloadResult,
    DropPlan,
    DropPlanTarget,
    DownloadManifest,
    DownloadPlan,
    DownloadPlanItem,
    DownloadedResourceArtifact,
    UpdateCommandResult,
    UpdatePlan,
    WarehouseLoadResult,
)
from anac_explorator.paths import DEFAULT_CIG_SCHEMA_PATH
from anac_explorator.sample import SampleDownloadError, download_dataset_resource, select_resource
from anac_explorator.selection import TemporalSelection, select_available_slices
from anac_explorator.vocabulary import VOCABULARY_DATASET_CONFIGS

if TYPE_CHECKING:
    from anac_explorator.ckan import CkanClient


@dataclass(frozen=True, slots=True)
class DatasetFamilyDefinition:
    """@notice Describe one logical Phase 3 dataset family."""

    dataset: str
    title: str
    category: str
    description: str
    coverage_kind: str
    available_source_formats: tuple[str, ...]
    query_view_name: str | None
    update_supported: bool
    dictionary_available: bool
    adapter: "DatasetFamilyAdapter" = field(repr=False)

    @property
    def remote_dataset_ids(self) -> list[str]:
        """@notice Expose the registry's known CKAN dataset ids for this family."""

        return self.adapter.resolve_remote_dataset_ids()

    @property
    def remote_first_year(self) -> int | None:
        """@notice Expose the earliest known remote year when applicable."""

        return self.adapter.remote_first_year

    @property
    def remote_last_year(self) -> int | None:
        """@notice Expose the latest known remote year when applicable."""

        return self.adapter.remote_last_year

    @property
    def adapter_name(self) -> str:
        """@notice Expose the adapter class name for introspection and tests."""

        return type(self.adapter).__name__

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the family definition into a metadata-view friendly mapping."""

        return {
            "dataset": self.dataset,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "coverage_kind": self.coverage_kind,
            "available_source_formats": list(self.available_source_formats),
            "remote_dataset_ids": self.remote_dataset_ids,
            "remote_first_year": self.remote_first_year,
            "remote_last_year": self.remote_last_year,
            "query_view_name": self.query_view_name,
            "update_supported": self.update_supported,
            "dictionary_available": self.dictionary_available,
            "adapter_name": self.adapter_name,
        }


@dataclass(frozen=True, slots=True)
class _ResolvedRemoteResource:
    """@notice Capture one remote resource selected by a family adapter."""

    slice: str | None
    dataset_id: str
    resource: CkanResource
    source_format: str


class DatasetFamilyAdapter:
    """@notice Base adapter that resolves CKAN ids and optional family operations."""

    def __init__(
        self,
        *,
        family: str,
        coverage_kind: str,
        remote_first_year: int | None = None,
        remote_last_year: int | None = None,
        default_source_format: str = "csv",
        warehouse_load_supported: bool = False,
        update_supported: bool = False,
    ) -> None:
        """@notice Initialize the shared adapter metadata."""

        self.family = family
        self.coverage_kind = coverage_kind
        self.remote_first_year = remote_first_year
        self.remote_last_year = remote_last_year
        self.default_source_format = default_source_format.casefold()
        self.warehouse_load_supported = warehouse_load_supported
        self.update_supported = update_supported

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the logical family to one or more CKAN dataset ids."""

        raise NotImplementedError

    def plan_download(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection | None = None,
        preferred_resource_name: str | None = None,
        source_format: str = "auto",
        output_format: str = "parquet",
    ) -> DownloadPlan:
        """@notice Build the reusable download plan for one logical family request."""

        normalized_selection = TemporalSelection(mode="all") if selection is None else selection
        normalized_output_format = output_format.casefold()
        if normalized_output_format not in {"raw", "parquet", "both"}:
            raise ValueError(f"Unsupported output format {output_format!r}.")
        if normalized_output_format in {"parquet", "both"} and not self.warehouse_load_supported:
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Dataset family {self.family!r} does not support warehouse loading for output format {normalized_output_format!r}.",
                details={
                    "dataset": self.family,
                    "output_format": normalized_output_format,
                },
            )

        resolved_source_format = self._resolve_requested_source_format(source_format)
        resources = self.resolve_remote_resources(
            client,
            selection=normalized_selection,
            preferred_resource_name=preferred_resource_name,
            source_format=resolved_source_format,
        )
        normalized_slices = sorted({resource.slice for resource in resources if resource.slice is not None})
        resolved_dataset_ids = list(dict.fromkeys(resource.dataset_id for resource in resources))
        resolved_resource_names = list(dict.fromkeys(resource.resource.name for resource in resources))
        action = self._planned_action_for_output_format(normalized_output_format)
        reason = f"output_format_{normalized_output_format}"
        return DownloadPlan(
            dataset=self.family,
            output_format=normalized_output_format,
            requested_scope={
                "selection_mode": normalized_selection.mode,
                "requested_slices": list(normalized_selection.slices),
                "requested_years": list(normalized_selection.years),
                "requested_months": list(normalized_selection.months),
                "start_slice": normalized_selection.start_slice,
                "end_slice": normalized_selection.end_slice,
                "resource_name": preferred_resource_name,
                "source_format": source_format.casefold(),
                "resolved_source_format": resolved_source_format,
            },
            normalized_slices=normalized_slices,
            resolved_dataset_ids=resolved_dataset_ids,
            resolved_resource_names=resolved_resource_names,
            plan=[
                DownloadPlanItem(
                    slice=resource.slice,
                    dataset_id=resource.dataset_id,
                    resource_name=resource.resource.name,
                    source_format=resource.source_format,
                    action=action,
                    reason=reason,
                )
                for resource in resources
            ],
        )

    def resolve_remote_resources(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection,
        preferred_resource_name: str | None,
        source_format: str,
    ) -> list[_ResolvedRemoteResource]:
        """@notice Resolve the exact remote resources that satisfy one download request."""

        raise NotImplementedError

    def matches_dataset_id(self, dataset_id: str) -> bool:
        """@notice Report whether one raw CKAN dataset id belongs to this family."""

        return dataset_id in set(self.resolve_remote_dataset_ids())

    def download_to_parquet(
        self,
        client: "CkanClient",
        *,
        dataset_id: str,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        preferred_resource_name: str | None = None,
        schema_path: "Path | None" = None,
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
    ) -> WarehouseLoadResult:
        """@notice Dispatch a family-aware direct-to-Parquet download when supported."""

        raise CliCommandError(
            "DATASET_NOT_SUPPORTED",
            f"Dataset family {self.family!r} does not support download-to-Parquet dispatch yet.",
            details={"dataset": self.family},
        )

    def update(
        self,
        client: "CkanClient",
        *,
        dataset_id: str,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        periods: Sequence[str] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
        refresh_changed: bool,
    ) -> DatasetIncrementalUpdateResult:
        """@notice Dispatch a family-aware incremental update when supported."""

        raise CliCommandError(
            "DATASET_UPDATE_NOT_SUPPORTED",
            f"Dataset family {self.family!r} does not support incremental updates yet.",
            details={"dataset": self.family},
        )

    def plan_update(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection | None = None,
        warehouse_dir: "Path" = Path("data/warehouse"),
        refresh_changed: bool = False,
        force_full: bool = False,
    ) -> UpdatePlan:
        """@notice Build the reusable incremental-update plan when the family supports it."""

        raise CliCommandError(
            "DATASET_UPDATE_NOT_SUPPORTED",
            f"Dataset family {self.family!r} does not support incremental updates yet.",
            details={"dataset": self.family},
        )

    def apply_update_plan(
        self,
        client: "CkanClient",
        *,
        plan: UpdatePlan,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
        refresh_changed: bool,
        force_full: bool,
    ) -> list[DatasetIncrementalUpdateResult]:
        """@notice Execute one reusable incremental-update plan when the family supports it."""

        raise CliCommandError(
        "DATASET_UPDATE_NOT_SUPPORTED",
        f"Dataset family {self.family!r} does not support incremental updates yet.",
        details={"dataset": self.family},
        )

    def build_drop_plan(
        self,
        scope: TemporalSelection | None = None,
        layers: str = "all",
        *,
        warehouse_dir: "Path" = Path("data/warehouse"),
        resource_ids: Sequence[str] | None = None,
    ) -> DropPlan:
        """@notice Build the reusable drop plan when the family supports local pruning."""

        raise CliCommandError(
            "DATASET_NOT_SUPPORTED",
            f"Dataset family {self.family!r} does not support local drop planning yet.",
            details={"dataset": self.family},
        )

    def apply_drop_plan(
        self,
        *,
        plan: DropPlan,
        warehouse_dir: "Path" = Path("data/warehouse"),
    ) -> list[DropPlanTarget]:
        """@notice Execute one reusable drop plan when the family supports local pruning."""

        raise CliCommandError(
            "DATASET_NOT_SUPPORTED",
            f"Dataset family {self.family!r} does not support local drop execution yet.",
            details={"dataset": self.family},
        )

    def _resolve_requested_source_format(self, source_format: str) -> str:
        """@notice Normalize `auto|csv|json` into one concrete remote source format."""

        normalized_source_format = source_format.casefold()
        if normalized_source_format not in {"auto", "csv", "json"}:
            raise ValueError(f"Unsupported source format {source_format!r}.")
        if normalized_source_format == "auto":
            return self.default_source_format
        return normalized_source_format

    @staticmethod
    def _planned_action_for_output_format(output_format: str) -> str:
        """@notice Derive the planned action token from the requested output format."""

        if output_format == "raw":
            return "download"
        if output_format == "parquet":
            return "download_and_load"
        return "download_and_load_keep_raw"


class PeriodizedDatasetFamilyAdapter(DatasetFamilyAdapter):
    """@notice Resolve one monthly family whose CKAN ids follow `<prefix>-<year>`."""

    def __init__(
        self,
        *,
        family: str,
        dataset_prefix: str,
        first_year: int,
        last_year: int,
        supported_source_formats: Sequence[str] = ("csv",),
        default_source_format: str = "csv",
        warehouse_load_supported: bool = False,
        update_supported: bool = False,
    ) -> None:
        """@notice Initialize the periodized dataset metadata."""

        super().__init__(
            family=family,
            coverage_kind="periodic_monthly",
            remote_first_year=first_year,
            remote_last_year=last_year,
            default_source_format=default_source_format,
            warehouse_load_supported=warehouse_load_supported,
            update_supported=update_supported,
        )
        self.dataset_prefix = dataset_prefix
        self.supported_source_formats = tuple(value.casefold() for value in supported_source_formats)

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the selected period span to yearly CKAN dataset ids."""

        years = self._resolve_years(selection)
        return [f"{self.dataset_prefix}-{year_value:04d}" for year_value in years]

    def resolve_remote_resources(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection,
        preferred_resource_name: str | None,
        source_format: str,
    ) -> list[_ResolvedRemoteResource]:
        """@notice Resolve one or more periodized remote resources for the selected slices."""

        if source_format not in set(self.supported_source_formats):
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Dataset family {self.family!r} does not expose {source_format!r} resources.",
                details={"dataset": self.family, "source_format": source_format},
            )

        dataset_ids = self.resolve_remote_dataset_ids(selection)
        resolved_resources: list[_ResolvedRemoteResource] = []
        for dataset_id in dataset_ids:
            package = client.package_show(dataset_id)
            resolved_resources.extend(self._extract_periodized_resources(dataset_id, package.resources, source_format))
        if not resolved_resources:
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Dataset family {self.family!r} does not expose downloadable {source_format!r} resources.",
                details={"dataset": self.family, "source_format": source_format},
            )

        if preferred_resource_name is not None:
            matching_resources = [resource for resource in resolved_resources if resource.resource.name == preferred_resource_name]
            if not matching_resources:
                raise CliCommandError(
                    "DATASET_NOT_SUPPORTED",
                    f"Could not find resource {preferred_resource_name!r}.",
                    details={"dataset": self.family, "resource_name": preferred_resource_name},
                )
            if selection.mode != "all":
                try:
                    selected_slices = set(select_available_slices(selection, [resource.slice for resource in matching_resources if resource.slice is not None]))
                except ValueError as exc:
                    raise CliCommandError(
                        "TEMPORAL_SLICE_NOT_FOUND",
                        str(exc),
                        details={"dataset": self.family, "requested_slices": list(selection.slices)},
                        cause=exc,
                    ) from exc
                matching_resources = [resource for resource in matching_resources if resource.slice in selected_slices]
                if not matching_resources:
                    raise CliCommandError(
                        "TEMPORAL_SLICE_NOT_FOUND",
                        f"Resource {preferred_resource_name!r} does not match the requested temporal selection.",
                        details={"dataset": self.family, "resource_name": preferred_resource_name},
                    )
            selected_resources = matching_resources
        else:
            available_slices = [resource.slice for resource in resolved_resources if resource.slice is not None]
            try:
                selected_slices = select_available_slices(selection, available_slices)
            except ValueError as exc:
                raise CliCommandError(
                    "TEMPORAL_SLICE_NOT_FOUND",
                    str(exc),
                    details={"dataset": self.family, "requested_slices": list(selection.slices)},
                    cause=exc,
                ) from exc
            selected_resources = [resource for resource in resolved_resources if resource.slice in set(selected_slices)]

        return sorted(selected_resources, key=lambda resource: ("" if resource.slice is None else resource.slice, resource.resource.name))

    def matches_dataset_id(self, dataset_id: str) -> bool:
        """@notice Match the dataset id prefix and validated year range."""

        prefix = f"{self.dataset_prefix}-"
        if not dataset_id.startswith(prefix):
            return False
        try:
            requested_year = int(dataset_id[len(prefix) :])
        except ValueError:
            return False
        return self.remote_first_year is not None and self.remote_last_year is not None and self.remote_first_year <= requested_year <= self.remote_last_year

    def _resolve_years(self, selection: TemporalSelection | None) -> list[int]:
        """@notice Derive the requested year span from the shared temporal selection."""

        if self.remote_first_year is None or self.remote_last_year is None:
            return []
        if selection is None or selection.mode == "all":
            return list(range(self.remote_first_year, self.remote_last_year + 1))
        if selection.mode == "latest":
            return [self.remote_last_year]

        explicit_years = selection.years or sorted({int(slice_value[:4]) for slice_value in selection.slices})
        if not explicit_years:
            return list(range(self.remote_first_year, self.remote_last_year + 1))

        unknown_years = [
            year_value
            for year_value in explicit_years
            if year_value < self.remote_first_year or year_value > self.remote_last_year
        ]
        if unknown_years:
            raise CliCommandError(
                "TEMPORAL_SLICE_NOT_FOUND",
                f"Dataset family {self.family!r} does not expose yearly datasets for: {', '.join(str(value) for value in unknown_years)}.",
                details={"dataset": self.family, "requested_years": unknown_years},
            )
        return sorted(set(explicit_years))

    def _extract_periodized_resources(
        self,
        dataset_id: str,
        resources: Sequence[CkanResource],
        source_format: str,
    ) -> list[_ResolvedRemoteResource]:
        """@notice Extract the family's periodized resources from one CKAN package payload."""

        pattern = re.compile(rf"{re.escape(self.dataset_prefix)}_{re.escape(source_format)}_(\d{{4}})_(\d{{2}})$", re.IGNORECASE)
        resolved_resources: list[_ResolvedRemoteResource] = []
        for resource in resources:
            if resource.format.casefold() != source_format:
                continue
            match = pattern.fullmatch(resource.name.casefold())
            if match is None:
                continue
            slice_value = f"{match.group(1)}-{match.group(2)}"
            resolved_resources.append(
                _ResolvedRemoteResource(
                    slice=slice_value,
                    dataset_id=dataset_id,
                    resource=resource,
                    source_format=source_format,
                )
            )
        return resolved_resources


class SnapshotDatasetFamilyAdapter(DatasetFamilyAdapter):
    """@notice Resolve one snapshot family that maps to exactly one CKAN dataset id."""

    def __init__(
        self,
        *,
        family: str,
        dataset_id: str,
        supported_source_formats: Sequence[str] = ("csv",),
        default_source_format: str = "csv",
        warehouse_load_supported: bool = False,
        update_supported: bool = False,
    ) -> None:
        """@notice Initialize the fixed snapshot mapping."""

        super().__init__(
            family=family,
            coverage_kind="snapshot",
            default_source_format=default_source_format,
            warehouse_load_supported=warehouse_load_supported,
            update_supported=update_supported,
        )
        self.dataset_id = dataset_id
        self.supported_source_formats = tuple(value.casefold() for value in supported_source_formats)

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the one snapshot dataset id and reject temporal selectors."""

        if selection is not None and selection.mode != "all":
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Temporal selection is not supported for snapshot dataset family {self.family!r}.",
                details={"dataset": self.family, "coverage_kind": self.coverage_kind},
            )
        return [self.dataset_id]

    def resolve_remote_resources(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection,
        preferred_resource_name: str | None,
        source_format: str,
    ) -> list[_ResolvedRemoteResource]:
        """@notice Resolve the one snapshot resource selected for download."""

        self.resolve_remote_dataset_ids(selection)
        if source_format not in set(self.supported_source_formats):
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Dataset family {self.family!r} does not expose {source_format!r} resources.",
                details={"dataset": self.family, "source_format": source_format},
            )

        package = client.package_show(self.dataset_id)
        try:
            resource = select_resource(
                package.resources,
                preferred_name=preferred_resource_name,
                preferred_format=source_format.upper(),
            )
        except SampleDownloadError as exc:
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                str(exc),
                details={"dataset": self.family, "source_format": source_format},
                cause=exc,
            ) from exc
        return [
            _ResolvedRemoteResource(
                slice=None,
                dataset_id=self.dataset_id,
                resource=resource,
                source_format=source_format,
            )
        ]

    def matches_dataset_id(self, dataset_id: str) -> bool:
        """@notice Match one exact snapshot dataset id."""

        return dataset_id == self.dataset_id


class CigDatasetFamilyAdapter(PeriodizedDatasetFamilyAdapter):
    """@notice Provide the currently implemented Phase 3 family operations for CIG."""

    def build_drop_plan(
        self,
        scope: TemporalSelection | None = None,
        layers: str = "all",
        *,
        warehouse_dir: "Path" = Path("data/warehouse"),
        resource_ids: Sequence[str] | None = None,
    ) -> DropPlan:
        """@notice Build the family-level dry-run plan for local CIG pruning."""

        return plan_cig_drop(
            scope=scope,
            layers=layers,
            warehouse_dir=warehouse_dir,
            resource_ids=resource_ids,
        )

    def apply_drop_plan(
        self,
        *,
        plan: DropPlan,
        warehouse_dir: "Path" = Path("data/warehouse"),
    ) -> list[DropPlanTarget]:
        """@notice Execute the family-level CIG drop plan and reconcile local state."""

        return execute_drop_plan(
            plan,
            warehouse_dir=warehouse_dir,
        )

    def download_to_parquet(
        self,
        client: "CkanClient",
        *,
        dataset_id: str,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        preferred_resource_name: str | None = None,
        schema_path: "Path | None" = None,
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
    ) -> WarehouseLoadResult:
        """@notice Reuse the current direct-to-Parquet loader through the family adapter."""

        return download_dataset_to_parquet(
            client,
            dataset_id=dataset_id,
            output_dir=output_dir,
            schemas_dir=schemas_dir,
            warehouse_dir=warehouse_dir,
            preferred_resource_name=preferred_resource_name,
            schema_path=schema_path,
            vocabulary_index_path=vocabulary_index_path,
            delimiter=delimiter,
            encoding=encoding,
            schema_sample_limit=schema_sample_limit,
            keep_materialized=keep_materialized,
            register_crosswalks=register_crosswalks,
        )

    def update(
        self,
        client: "CkanClient",
        *,
        dataset_id: str,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        periods: Sequence[str] | None = None,
        period_start: str | None = None,
        period_end: str | None = None,
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
        refresh_changed: bool,
    ) -> DatasetIncrementalUpdateResult:
        """@notice Reuse the current incremental CIG sync through the family adapter."""

        return sync_cig_periods_to_parquet(
            client,
            dataset_id=dataset_id,
            output_dir=output_dir,
            schemas_dir=schemas_dir,
            warehouse_dir=warehouse_dir,
            periods=None if periods is None else list(periods),
            period_start=period_start,
            period_end=period_end,
            vocabulary_index_path=vocabulary_index_path,
            delimiter=delimiter,
            encoding=encoding,
            schema_sample_limit=schema_sample_limit,
            keep_materialized=keep_materialized,
            register_crosswalks=register_crosswalks,
            refresh_changed=refresh_changed,
        )

    def plan_update(
        self,
        client: "CkanClient",
        *,
        selection: TemporalSelection | None = None,
        warehouse_dir: "Path" = Path("data/warehouse"),
        refresh_changed: bool = False,
        force_full: bool = False,
    ) -> UpdatePlan:
        """@notice Build the family-level dry-run plan for incremental CIG updates."""

        normalized_selection = TemporalSelection(mode="all") if selection is None else selection
        selected_dataset_ids = (
            None
            if normalized_selection.mode == "all"
            else self.resolve_remote_dataset_ids(normalized_selection)
        )
        return plan_cig_period_updates(
            client,
            selection=normalized_selection,
            warehouse_dir=warehouse_dir,
            refresh_changed=refresh_changed,
            dataset_prefix=self.dataset_prefix,
            remote_first_year=self.remote_first_year if self.remote_first_year is not None else 2007,
            remote_last_year=self.remote_last_year if self.remote_last_year is not None else 2025,
            force_full=force_full,
            selected_dataset_ids=selected_dataset_ids,
        )

    def apply_update_plan(
        self,
        client: "CkanClient",
        *,
        plan: UpdatePlan,
        output_dir: "Path",
        schemas_dir: "Path",
        warehouse_dir: "Path",
        vocabulary_index_path: "Path",
        delimiter: str,
        encoding: str,
        schema_sample_limit: int,
        keep_materialized: bool,
        register_crosswalks: bool,
        refresh_changed: bool,
        force_full: bool,
    ) -> list[DatasetIncrementalUpdateResult]:
        """@notice Execute the normalized plan by delegating each targeted year to the existing sync helper."""

        applicable_items = [item for item in plan.plan if item.action != "skip"]
        if not applicable_items:
            return []

        periods_by_dataset: dict[str, list[str]] = {}
        for item in applicable_items:
            periods_by_dataset.setdefault(item.dataset_id, []).append(item.slice.replace("-", "_"))

        results: list[DatasetIncrementalUpdateResult] = []
        for dataset_id, periods in periods_by_dataset.items():
            results.append(
                sync_cig_periods_to_parquet(
                    client,
                    dataset_id=dataset_id,
                    output_dir=output_dir,
                    schemas_dir=schemas_dir,
                    warehouse_dir=warehouse_dir,
                    periods=periods,
                    vocabulary_index_path=vocabulary_index_path,
                    delimiter=delimiter,
                    encoding=encoding,
                    schema_sample_limit=schema_sample_limit,
                    keep_materialized=keep_materialized,
                    register_crosswalks=register_crosswalks,
                    refresh_changed=refresh_changed,
                    force_full=force_full,
                )
            )
        return results


FamilyAdapter = DatasetFamilyAdapter
PeriodizedAdapter = PeriodizedDatasetFamilyAdapter
SnapshotAdapter = SnapshotDatasetFamilyAdapter


DownloadExecutionArtifact = DownloadedResourceArtifact | DatasetParquetDownloadResult


class DatasetFamilyRegistry:
    """@notice Hold the explicit logical-family registry used by the Phase 3 CLI."""

    def __init__(self, families: Iterable[DatasetFamilyDefinition]) -> None:
        """@notice Build the registry from explicit family definitions."""

        self._families = {family.dataset: family for family in families}

    def list_families(self) -> list[DatasetFamilyDefinition]:
        """@notice Return the registered families in insertion order."""

        return list(self._families.values())

    def get_family(self, dataset: str) -> DatasetFamilyDefinition:
        """@notice Resolve one logical family id or raise a structured not-found error."""

        family = self._families.get(dataset)
        if family is None:
            raise CliCommandError(
                "DATASET_NOT_FOUND",
                f"Unknown dataset family {dataset!r}.",
                details={"dataset": dataset},
            )
        return family

    def resolve_family_for_dataset_id(self, dataset_id: str) -> DatasetFamilyDefinition | None:
        """@notice Resolve a raw CKAN dataset id back to its logical family when known."""

        for family in self._families.values():
            if family.adapter.matches_dataset_id(dataset_id):
                return family
        return None

    def resolve_remote_dataset_ids(
        self,
        dataset: str,
        *,
        selection: TemporalSelection | None = None,
    ) -> list[str]:
        """@notice Resolve a logical family to the known CKAN package ids for one selection."""

        return self.get_family(dataset).adapter.resolve_remote_dataset_ids(selection=selection)

    def plan_download(
        self,
        dataset: str,
        client: "CkanClient",
        *,
        selection: TemporalSelection | None = None,
        preferred_resource_name: str | None = None,
        source_format: str = "auto",
        output_format: str = "parquet",
    ) -> DownloadPlan:
        """@notice Build a reusable download plan through the family's adapter."""

        return self.get_family(dataset).adapter.plan_download(
            client,
            selection=selection,
            preferred_resource_name=preferred_resource_name,
            source_format=source_format,
            output_format=output_format,
        )

    def download_to_parquet(self, dataset: str, client: "CkanClient", **kwargs: object) -> WarehouseLoadResult:
        """@notice Dispatch a direct-to-Parquet download through the family's adapter."""

        return self.get_family(dataset).adapter.download_to_parquet(client, **kwargs)

    def update(self, dataset: str, client: "CkanClient", **kwargs: object) -> DatasetIncrementalUpdateResult:
        """@notice Dispatch an incremental update through the family's adapter."""

        return self.get_family(dataset).adapter.update(client, **kwargs)

    def plan_update(self, dataset: str, client: "CkanClient", **kwargs: object) -> UpdatePlan:
        """@notice Build the reusable incremental-update plan through the family's adapter."""

        return self.get_family(dataset).adapter.plan_update(client, **kwargs)

    def apply_update_plan(
        self,
        dataset: str,
        client: "CkanClient",
        **kwargs: object,
    ) -> list[DatasetIncrementalUpdateResult]:
        """@notice Execute the reusable incremental-update plan through the family's adapter."""

        return self.get_family(dataset).adapter.apply_update_plan(client, **kwargs)

    def build_drop_plan(self, dataset: str, **kwargs: object) -> DropPlan:
        """@notice Build the reusable local drop plan through the family's adapter."""

        return self.get_family(dataset).adapter.build_drop_plan(**kwargs)

    def apply_drop_plan(self, dataset: str, **kwargs: object) -> list[DropPlanTarget]:
        """@notice Execute the reusable local drop plan through the family's adapter."""

        return self.get_family(dataset).adapter.apply_drop_plan(**kwargs)


def _reuse_cached_parquet_result(
    *,
    manifest_path: Path,
    warehouse_root: Path,
    delimiter: str,
    encoding: str,
) -> DatasetParquetDownloadResult | None:
    """@notice Reuse an existing Parquet load when the raw working file was intentionally pruned."""

    if not manifest_path.exists():
        return None

    manifest = DownloadManifest.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
    materialized_path = Path(manifest.materialized_path)
    if materialized_path.exists() or manifest.archive_path is not None:
        return None

    try:
        load_result = load_downloaded_resource(
            manifest_path,
            warehouse_dir=warehouse_root,
            delimiter=delimiter,
            encoding=encoding,
            force_load=False,
        )
    except FileNotFoundError:
        return None

    if load_result.load_status != "cache_hit":
        return None

    return DatasetParquetDownloadResult(
        dataset_id=manifest.dataset_id,
        resource_name=manifest.resource_name,
        manifest_path=str(manifest_path),
        download_cache_status=manifest.cache_status,
        schema_path=load_result.schema_path,
        schema_generated=False,
        removed_materialized_path=False,
        load_result=load_result,
    )


def execute_download_plan(
    plan: DownloadPlan,
    client: "CkanClient",
    *,
    output_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    warehouse_dir: str | Path = "data/warehouse",
    preferred_schema_path: str | Path | None = None,
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    register_crosswalks: bool = True,
    force_download: bool = False,
    force_load: bool = False,
) -> list[DownloadExecutionArtifact]:
    """@notice Execute a reusable download plan through the existing low-level helpers."""

    output_root = Path(output_dir)
    schemas_root = Path(schemas_dir)
    warehouse_root = Path(warehouse_dir)
    schema_path = None if preferred_schema_path is None else Path(preferred_schema_path)
    vocabulary_index = Path(vocabulary_index_path)

    applied: list[DownloadExecutionArtifact] = []
    for plan_item in plan.plan:
        if plan.output_format == "raw":
            applied.append(
                download_dataset_resource(
                    client,
                    dataset_id=plan_item.dataset_id,
                    output_dir=output_root,
                    preferred_resource_name=plan_item.resource_name,
                    preferred_format=plan_item.source_format.upper(),
                    force_download=force_download,
                )
            )
            continue

        if plan_item.source_format != "csv":
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Dataset family {plan.dataset!r} does not support Parquet loading from {plan_item.source_format!r} resources.",
                details={
                    "dataset": plan.dataset,
                    "output_format": plan.output_format,
                    "resource_name": plan_item.resource_name,
                    "source_format": plan_item.source_format,
                },
            )

        if plan.output_format == "parquet" and not force_download and not force_load:
            cached_result = _reuse_cached_parquet_result(
                manifest_path=output_root / plan_item.dataset_id / plan_item.resource_name / "manifest.json",
                warehouse_root=warehouse_root,
                delimiter=delimiter,
                encoding=encoding,
            )
            if cached_result is not None:
                applied.append(cached_result)
                continue

        applied.append(
            download_dataset_to_parquet(
                client,
                dataset_id=plan_item.dataset_id,
                output_dir=output_root,
                schemas_dir=schemas_root,
                warehouse_dir=warehouse_root,
                preferred_resource_name=plan_item.resource_name,
                schema_path=schema_path,
                vocabulary_index_path=vocabulary_index,
                delimiter=delimiter,
                encoding=encoding,
                schema_sample_limit=schema_sample_limit,
                keep_materialized=plan.output_format == "both",
                register_crosswalks=register_crosswalks,
                force_download=force_download,
                force_load=force_load,
            )
        )
    return applied


def _resolve_update_validation_schema_path(
    *,
    dataset: str,
    schemas_dir: Path,
    validation_schema_path: str | Path | None,
) -> Path:
    """@notice Resolve the default validation schema path for one update-capable family."""

    if validation_schema_path is not None:
        return Path(validation_schema_path)
    if dataset == "cig":
        return schemas_dir / DEFAULT_CIG_SCHEMA_PATH.name
    raise CliCommandError(
        "DATASET_NOT_SUPPORTED",
        f"Dataset family {dataset!r} does not support post-update validation yet.",
        details={"dataset": dataset},
    )


def execute_update_plan(
    plan: UpdatePlan,
    client: "CkanClient",
    *,
    output_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    warehouse_dir: str | Path = "data/warehouse",
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    keep_materialized: bool = False,
    register_crosswalks: bool = True,
    refresh_changed: bool = False,
    force_full: bool = False,
    validate: bool = False,
    validation_schema_path: str | Path | None = None,
    registry: DatasetFamilyRegistry | None = None,
) -> UpdateCommandResult:
    """@notice Execute one reusable update plan through the family adapter contract."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    output_root = Path(output_dir)
    schemas_root = Path(schemas_dir)
    warehouse_root = Path(warehouse_dir)
    vocabulary_index = Path(vocabulary_index_path)

    execution_results = registry.apply_update_plan(
        plan.dataset,
        client,
        plan=plan,
        output_dir=output_root,
        schemas_dir=schemas_root,
        warehouse_dir=warehouse_root,
        vocabulary_index_path=vocabulary_index,
        delimiter=delimiter,
        encoding=encoding,
        schema_sample_limit=schema_sample_limit,
        keep_materialized=keep_materialized,
        register_crosswalks=register_crosswalks,
        refresh_changed=refresh_changed,
        force_full=force_full,
    )

    applied: list[DatasetParquetDownloadResult] = []
    for result in execution_results:
        applied.extend(result.applied_loads)

    validation = None
    if validate:
        validation = validate_local_data_integrity(
            warehouse_root / "anac.duckdb",
            dataset_type=plan.dataset,
            schema_path=_resolve_update_validation_schema_path(
                dataset=plan.dataset,
                schemas_dir=schemas_root,
                validation_schema_path=validation_schema_path,
            ),
            vocabulary_index_path=vocabulary_index,
        )

    return UpdateCommandResult(
        scope={
            **plan.scope,
            "dataset": plan.dataset,
            "refresh_changed": refresh_changed,
            "force_full": force_full,
            "validate": validate,
        },
        latest_local_state=plan.latest_local_state,
        plan=plan.plan,
        applied=applied,
        validation=validation,
    )


def run_dataset_update(
    dataset: str,
    client: "CkanClient",
    *,
    selection: TemporalSelection | None = None,
    output_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    warehouse_dir: str | Path = "data/warehouse",
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    keep_materialized: bool = False,
    register_crosswalks: bool = True,
    refresh_changed: bool = False,
    force_full: bool = False,
    validate: bool = False,
    validation_schema_path: str | Path | None = None,
    dry_run: bool = False,
    registry: DatasetFamilyRegistry | None = None,
) -> UpdateCommandResult:
    """@notice Plan one family update and optionally execute it through the shared contract."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    warehouse_root = Path(warehouse_dir)
    plan = registry.plan_update(
        dataset,
        client,
        selection=selection,
        warehouse_dir=warehouse_root,
        refresh_changed=refresh_changed,
        force_full=force_full,
    )
    if dry_run:
        return UpdateCommandResult(
            scope={
                **plan.scope,
                "dataset": plan.dataset,
                "refresh_changed": refresh_changed,
                "force_full": force_full,
                "validate": validate,
            },
            latest_local_state=plan.latest_local_state,
            plan=plan.plan,
        )
    return execute_update_plan(
        plan,
        client,
        output_dir=output_dir,
        schemas_dir=schemas_dir,
        warehouse_dir=warehouse_root,
        vocabulary_index_path=vocabulary_index_path,
        delimiter=delimiter,
        encoding=encoding,
        schema_sample_limit=schema_sample_limit,
        keep_materialized=keep_materialized,
        register_crosswalks=register_crosswalks,
        refresh_changed=refresh_changed,
        force_full=force_full,
        validate=validate,
        validation_schema_path=validation_schema_path,
        registry=registry,
    )


@dataclass(slots=True)
class DatasetLocalCoverage:
    """@notice Summarize the local materialization state for one logical dataset family."""

    slice_count: int
    first_slice: str | None
    last_slice: str | None
    resource_count: int
    raw_resource_count: int
    loaded_resource_count: int

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the local-coverage summary into a serializable mapping."""

        return {
            "slice_count": self.slice_count,
            "first_slice": self.first_slice,
            "last_slice": self.last_slice,
            "resource_count": self.resource_count,
            "raw_resource_count": self.raw_resource_count,
            "loaded_resource_count": self.loaded_resource_count,
        }


@dataclass(slots=True)
class DatasetCatalogEntry:
    """@notice Capture the merged local and remote discoverability state for one family."""

    dataset: str
    title: str
    category: str
    description: str
    coverage_kind: str
    available_source_formats: list[str]
    remote_dataset_ids: list[str]
    remote_coverage: dict[str, object]
    local_status: str
    local_coverage: DatasetLocalCoverage
    query_view_name: str | None
    update_supported: bool
    dictionary_available: bool
    vocabulary_views: list[str]
    aliases: list[str] = field(default_factory=list, repr=False)

    def to_dict(self, *, include_extended: bool) -> dict[str, object]:
        """@notice Convert the merged entry into a JSON-friendly payload."""

        payload: dict[str, object] = {
            "dataset": self.dataset,
            "title": self.title,
            "coverage_kind": self.coverage_kind,
            "available_source_formats": self.available_source_formats,
            "local_status": self.local_status,
            "update_supported": self.update_supported,
        }
        if include_extended:
            payload.update(
                {
                    "category": self.category,
                    "description": self.description,
                    "remote_dataset_ids": self.remote_dataset_ids,
                    "remote_coverage": self.remote_coverage,
                    "local_coverage": self.local_coverage.to_dict(),
                    "query_view_name": self.query_view_name,
                    "dictionary_available": self.dictionary_available,
                    "vocabulary_views": self.vocabulary_views,
                }
            )
        return payload


@dataclass(slots=True)
class DatasetCatalogListResult:
    """@notice Hold the filtered list-mode result for `datasets`."""

    items: list[DatasetCatalogEntry]
    filters: dict[str, object]
    warnings: list[CommandWarning] = field(default_factory=list)

    def to_dict(self, *, include_extended: bool) -> dict[str, object]:
        """@notice Convert the list result into the documented datasets payload."""

        return {
            "items": [item.to_dict(include_extended=include_extended) for item in self.items],
            "item_count": len(self.items),
            "filters": self.filters,
        }


@dataclass(slots=True)
class DatasetCatalogDetailResult:
    """@notice Hold the single-family detail result for `datasets`."""

    item: DatasetCatalogEntry
    warnings: list[CommandWarning] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """@notice Convert the detail result into the documented detail payload."""

        return self.item.to_dict(include_extended=True)


def list_dataset_families(
    *,
    db_path: str | Path = "data/warehouse/anac.duckdb",
    raw_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    registry: DatasetFamilyRegistry | None = None,
    search: str | None = None,
    year: int | None = None,
    downloaded: bool = False,
    missing: bool = False,
    source_format: str | None = None,
) -> DatasetCatalogListResult:
    """@notice List logical dataset families with normalized search and filter support."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_filters = _normalize_dataset_filters(
        search=search,
        year=year,
        downloaded=downloaded,
        missing=missing,
        source_format=source_format,
    )
    items = [
        entry
        for entry in _load_dataset_catalog_entries(
            db_path=Path(db_path),
            raw_dir=Path(raw_dir),
            schemas_dir=Path(schemas_dir),
            dictionaries_dir=Path(dictionaries_dir),
            vocabulary_index_path=Path(vocabulary_index_path),
            registry=registry,
        )
        if _matches_dataset_filters(entry, normalized_filters)
    ]
    return DatasetCatalogListResult(items=items, filters=normalized_filters)


def run_global_update(
    client: "CkanClient",
    *,
    selection: TemporalSelection | None = None,
    db_path: str | Path = "data/warehouse/anac.duckdb",
    raw_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    output_dir: str | Path = "data/raw",
    warehouse_dir: str | Path = "data/warehouse",
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
    schema_sample_limit: int = 2_000,
    keep_materialized: bool = False,
    register_crosswalks: bool = True,
    refresh_changed: bool = False,
    force_full: bool = False,
    validate: bool = False,
    validation_schema_path: str | Path | None = None,
    dry_run: bool = False,
    registry: DatasetFamilyRegistry | None = None,
) -> UpdateCommandResult:
    """@notice Update only locally present, update-capable families without scanning the full remote catalog."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    catalog_result = list_dataset_families(
        db_path=db_path,
        raw_dir=raw_dir,
        schemas_dir=schemas_dir,
        dictionaries_dir=dictionaries_dir,
        vocabulary_index_path=vocabulary_index_path,
        registry=registry,
        downloaded=True,
    )
    targeted_datasets = [item.dataset for item in catalog_result.items if item.update_supported]

    latest_local_state: dict[str, object] = {}
    aggregated_plan = []
    aggregated_applied: list[DatasetParquetDownloadResult] = []
    validation_reports: dict[str, object] = {}
    for dataset in targeted_datasets:
        result = run_dataset_update(
            dataset,
            client,
            selection=selection,
            output_dir=output_dir,
            schemas_dir=schemas_dir,
            warehouse_dir=warehouse_dir,
            vocabulary_index_path=vocabulary_index_path,
            delimiter=delimiter,
            encoding=encoding,
            schema_sample_limit=schema_sample_limit,
            keep_materialized=keep_materialized,
            register_crosswalks=register_crosswalks,
            refresh_changed=refresh_changed,
            force_full=force_full,
            validate=validate,
            validation_schema_path=validation_schema_path,
            dry_run=dry_run,
            registry=registry,
        )
        latest_local_state[dataset] = result.latest_local_state
        aggregated_plan.extend(result.plan)
        aggregated_applied.extend(result.applied)
        if result.validation is not None:
            validation_reports[dataset] = result.validation

    return UpdateCommandResult(
        scope={
            "mode": "global",
            "datasets": targeted_datasets,
            "refresh_changed": refresh_changed,
            "force_full": force_full,
            "validate": validate,
            "dry_run": dry_run,
        },
        latest_local_state=latest_local_state,
        plan=aggregated_plan,
        applied=aggregated_applied,
        validation=None if not validation_reports else validation_reports,
    )


def get_dataset_family(
    dataset: str,
    *,
    db_path: str | Path = "data/warehouse/anac.duckdb",
    raw_dir: str | Path = "data/raw",
    schemas_dir: str | Path = "schemas",
    dictionaries_dir: str | Path = "dictionaries",
    vocabulary_index_path: str | Path = "vocabularies/index.json",
    registry: DatasetFamilyRegistry | None = None,
    client: "CkanClient | None" = None,
    search: str | None = None,
    year: int | None = None,
    downloaded: bool = False,
    missing: bool = False,
    source_format: str | None = None,
) -> DatasetCatalogDetailResult:
    """@notice Return one logical dataset family with the required detail-mode fields."""

    registry = DATASET_FAMILY_REGISTRY if registry is None else registry
    normalized_filters = _normalize_dataset_filters(
        search=search,
        year=year,
        downloaded=downloaded,
        missing=missing,
        source_format=source_format,
    )
    family = registry.get_family(dataset)
    entries = {
        entry.dataset: entry
        for entry in _load_dataset_catalog_entries(
            db_path=Path(db_path),
            raw_dir=Path(raw_dir),
            schemas_dir=Path(schemas_dir),
            dictionaries_dir=Path(dictionaries_dir),
            vocabulary_index_path=Path(vocabulary_index_path),
            registry=registry,
        )
    }
    entry = entries.get(family.dataset)
    if entry is None:
        raise CliCommandError(
            "DATASET_NOT_FOUND",
            f"Unknown dataset family {dataset!r}.",
            details={"dataset": dataset},
        )
    if not _matches_dataset_filters(entry, normalized_filters):
        raise CliCommandError(
            "DATASET_NOT_FOUND",
            f"Dataset family {dataset!r} did not match the requested filters.",
            details={"dataset": dataset, "filters": normalized_filters},
        )
    warnings: list[CommandWarning] = []
    if client is not None:
        entry, warnings = _enrich_entry_with_live_remote_metadata(entry, client)
    return DatasetCatalogDetailResult(item=entry, warnings=warnings)


def _normalize_dataset_filters(
    *,
    search: str | None,
    year: int | None,
    downloaded: bool,
    missing: bool,
    source_format: str | None,
) -> dict[str, object]:
    """@notice Normalize list/detail filters into one stable internal mapping."""

    if downloaded and missing:
        raise ValueError("Cannot combine --downloaded and --missing.")
    filters: dict[str, object] = {}
    normalized_search = None if search is None else search.strip()
    if normalized_search:
        filters["search"] = normalized_search.casefold()
    if year is not None:
        filters["year"] = int(year)
    if downloaded:
        filters["downloaded"] = True
    if missing:
        filters["missing"] = True
    if source_format is not None:
        filters["source_format"] = source_format.strip().casefold()
    return filters


def _load_dataset_catalog_entries(
    *,
    db_path: Path,
    raw_dir: Path,
    schemas_dir: Path,
    dictionaries_dir: Path,
    vocabulary_index_path: Path,
    registry: DatasetFamilyRegistry,
) -> list[DatasetCatalogEntry]:
    """@notice Load merged catalog entries by querying the on-demand metadata views."""

    connection = _open_catalog_connection(db_path)
    try:
        ensure_metadata_views(
            connection,
            db_path=db_path,
            raw_dir=raw_dir,
            schemas_dir=schemas_dir,
            dictionaries_dir=dictionaries_dir,
            vocabulary_index_path=vocabulary_index_path,
            registry=registry,
        )
        dataset_rows = _fetch_relation_rows(connection, "SELECT * FROM anac_datasets ORDER BY dataset")
        resource_rows = _fetch_relation_rows(
            connection,
            "SELECT dataset, local_status FROM anac_dataset_resources ORDER BY dataset, dataset_id, resource_name",
        )
        dictionary_rows = _fetch_relation_rows(
            connection,
            """
            SELECT dataset, vocabulary_table
            FROM anac_dictionary_fields
            WHERE vocabulary_table IS NOT NULL AND vocabulary_table <> ''
            ORDER BY dataset, vocabulary_table
            """,
        )
    finally:
        connection.close()

    resource_status_counts: dict[str, dict[str, int]] = {}
    for row in resource_rows:
        dataset = str(row["dataset"])
        status = str(row["local_status"]).casefold()
        resource_status_counts.setdefault(dataset, {})
        resource_status_counts[dataset][status] = resource_status_counts[dataset].get(status, 0) + 1

    vocabulary_views_by_dataset: dict[str, list[str]] = {}
    for row in dictionary_rows:
        dataset = str(row["dataset"])
        vocabulary_table = str(row["vocabulary_table"])
        if vocabulary_table not in vocabulary_views_by_dataset.setdefault(dataset, []):
            vocabulary_views_by_dataset[dataset].append(vocabulary_table)

    entries: list[DatasetCatalogEntry] = []
    for row in dataset_rows:
        dataset = str(row["dataset"])
        available_source_formats = _decode_json_text_list(row["available_source_formats"])
        remote_dataset_ids = _decode_json_text_list(row["remote_dataset_ids"])
        local_coverage = DatasetLocalCoverage(
            slice_count=int(row["local_slice_count"]),
            first_slice=_nullable_text(row["local_first_slice"]),
            last_slice=_nullable_text(row["local_last_slice"]),
            resource_count=sum(resource_status_counts.get(dataset, {}).values()),
            raw_resource_count=resource_status_counts.get(dataset, {}).get("raw", 0),
            loaded_resource_count=resource_status_counts.get(dataset, {}).get("loaded", 0),
        )
        query_view_name = _nullable_text(row["query_view_name"])
        aliases = [dataset, *remote_dataset_ids]
        if query_view_name is not None:
            aliases.append(query_view_name)
        entries.append(
            DatasetCatalogEntry(
                dataset=dataset,
                title=str(row["title"]),
                category=str(row["category"]),
                description=str(row["description"]),
                coverage_kind=str(row["coverage_kind"]),
                available_source_formats=sorted({value.casefold() for value in available_source_formats}),
                remote_dataset_ids=remote_dataset_ids,
                remote_coverage={
                    "status": "registry",
                    "dataset_ids": remote_dataset_ids,
                    "dataset_count": len(remote_dataset_ids),
                    "first_year": row["remote_first_year"],
                    "last_year": row["remote_last_year"],
                    "source_formats": sorted({value.casefold() for value in available_source_formats}),
                },
                local_status=_derive_family_local_status(local_coverage),
                local_coverage=local_coverage,
                query_view_name=query_view_name,
                update_supported=bool(row["update_supported"]),
                dictionary_available=bool(row["dictionary_available"]),
                vocabulary_views=sorted(vocabulary_views_by_dataset.get(dataset, [])),
                aliases=sorted({alias.casefold() for alias in aliases if alias}),
            )
        )
    return entries


def _enrich_entry_with_live_remote_metadata(
    entry: DatasetCatalogEntry,
    client: "CkanClient",
) -> tuple[DatasetCatalogEntry, list[CommandWarning]]:
    """@notice Attempt a live CKAN refresh for detail mode without losing local fallback state."""

    live_formats = set(entry.available_source_formats)
    packages: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for dataset_id in entry.remote_dataset_ids:
        try:
            package = client.package_show(dataset_id)
        except CkanClientError as exc:
            failures.append({"dataset_id": dataset_id, "message": str(exc)})
            continue
        package_formats = sorted(
            {
                resource.format.strip().casefold()
                for resource in package.resources
                if resource.format.strip()
            }
        )
        live_formats.update(package_formats)
        packages.append(
            {
                "dataset_id": dataset_id,
                "name": package.name,
                "title": package.title,
                "resource_count": len(package.resources),
                "source_formats": package_formats,
                "latest_resource_modified": max(
                    (resource.last_modified for resource in package.resources if resource.last_modified),
                    default=None,
                ),
            }
        )

    if not failures:
        return (
            replace(
                entry,
                available_source_formats=sorted(live_formats),
                remote_coverage={
                    **entry.remote_coverage,
                    "status": "live",
                    "source_formats": sorted(live_formats),
                    "packages": packages,
                },
            ),
            [],
        )

    warning = CommandWarning(
        code="REMOTE_METADATA_UNAVAILABLE",
        message=f"Remote metadata refresh failed for dataset family {entry.dataset!r}; returning registry and local metadata.",
        details={
            "dataset": entry.dataset,
            "failed_dataset_ids": [failure["dataset_id"] for failure in failures],
            "failure_count": len(failures),
            "reason": failures[0]["message"],
        },
    )
    remote_status = "partial" if packages else "registry"
    enriched_packages: list[dict[str, object]] = packages if packages else []
    return (
        replace(
            entry,
            available_source_formats=sorted(live_formats),
            remote_coverage={
                **entry.remote_coverage,
                "status": remote_status,
                "source_formats": sorted(live_formats),
                "packages": enriched_packages,
            },
        ),
        [warning],
    )


def _matches_dataset_filters(entry: DatasetCatalogEntry, filters: dict[str, object]) -> bool:
    """@notice Apply the normalized list/detail filters to one merged catalog entry."""

    search = filters.get("search")
    if isinstance(search, str) and search not in _catalog_search_haystack(entry):
        return False
    if filters.get("downloaded") is True and entry.local_status == "missing":
        return False
    if filters.get("missing") is True and entry.local_status != "missing":
        return False
    year = filters.get("year")
    if isinstance(year, int) and not _entry_has_remote_year(entry, year):
        return False
    source_format = filters.get("source_format")
    if isinstance(source_format, str) and source_format not in set(entry.available_source_formats):
        return False
    return True


def _catalog_search_haystack(entry: DatasetCatalogEntry) -> str:
    """@notice Build the normalized free-text haystack used by `--search`."""

    return " ".join(
        [
            entry.dataset.casefold(),
            entry.title.casefold(),
            entry.description.casefold(),
            *entry.aliases,
        ]
    )


def _entry_has_remote_year(entry: DatasetCatalogEntry, year: int) -> bool:
    """@notice Report whether one dataset family advertises remote coverage for a year."""

    first_year = entry.remote_coverage.get("first_year")
    last_year = entry.remote_coverage.get("last_year")
    if isinstance(first_year, int) and isinstance(last_year, int):
        return first_year <= year <= last_year
    return False


def _derive_family_local_status(local_coverage: DatasetLocalCoverage) -> str:
    """@notice Collapse detailed local counts into the documented family-local status."""

    if local_coverage.loaded_resource_count > 0 or local_coverage.slice_count > 0:
        return "loaded"
    if local_coverage.raw_resource_count > 0:
        return "raw"
    return "missing"


def _open_catalog_connection(db_path: Path) -> duckdb.DuckDBPyConnection:
    """@notice Open a read-only warehouse connection or an in-memory fallback when absent."""

    if db_path.exists():
        return duckdb.connect(str(db_path), read_only=True)
    return duckdb.connect(":memory:")


def _fetch_relation_rows(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, object]]:
    """@notice Execute one metadata query and return dictionary-shaped rows."""

    cursor = connection.execute(sql)
    column_names = [column[0] for column in cursor.description]
    return [
        {column_name: row[index] for index, column_name in enumerate(column_names)}
        for row in cursor.fetchall()
    ]


def _decode_json_text_list(value: object) -> list[str]:
    """@notice Decode the JSON-text array representation used by metadata temp views."""

    if value in (None, ""):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    decoded = json.loads(str(value))
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded]


def _nullable_text(value: object) -> str | None:
    """@notice Normalize nullable text columns from metadata-view rows."""

    if value in (None, ""):
        return None
    return str(value)


def _build_dataset_family_registry() -> DatasetFamilyRegistry:
    """@notice Construct the explicit family registry, including wired vocabulary datasets."""

    families: list[DatasetFamilyDefinition] = [
        DatasetFamilyDefinition(
            dataset="cig",
            title="CIG",
            category="procurement",
            description="Monthly ANAC CIG procurement notices exposed as yearly CKAN datasets.",
            coverage_kind="periodic_monthly",
            available_source_formats=("csv", "json"),
            query_view_name="cig",
            update_supported=True,
            dictionary_available=True,
            adapter=CigDatasetFamilyAdapter(
                family="cig",
                dataset_prefix="cig",
                first_year=2007,
                last_year=2025,
                supported_source_formats=("csv", "json"),
                default_source_format="csv",
                warehouse_load_supported=True,
                update_supported=True,
            ),
        ),
        DatasetFamilyDefinition(
            dataset="smartcig",
            title="SMARTCIG",
            category="procurement",
            description="Monthly SMARTCIG notices exposed as yearly CKAN datasets.",
            coverage_kind="periodic_monthly",
            available_source_formats=("csv", "json"),
            query_view_name="smartcig",
            update_supported=False,
            dictionary_available=False,
            adapter=PeriodizedDatasetFamilyAdapter(
                family="smartcig",
                dataset_prefix="smartcig",
                first_year=2011,
                last_year=2025,
                supported_source_formats=("csv", "json"),
                default_source_format="csv",
            ),
        ),
        DatasetFamilyDefinition(
            dataset="stazioni-appaltanti",
            title="Stazioni appaltanti",
            category="registry",
            description="Snapshot registry of contracting authorities published by ANAC.",
            coverage_kind="snapshot",
            available_source_formats=("csv",),
            query_view_name="stazioni_appaltanti",
            update_supported=False,
            dictionary_available=False,
            adapter=SnapshotDatasetFamilyAdapter(
                family="stazioni-appaltanti",
                dataset_id="stazioni-appaltanti",
                supported_source_formats=("csv",),
                default_source_format="csv",
            ),
        ),
        DatasetFamilyDefinition(
            dataset="aggiudicatari",
            title="Aggiudicatari",
            category="registry",
            description="Snapshot registry of winning economic operators published by ANAC.",
            coverage_kind="snapshot",
            available_source_formats=("csv",),
            query_view_name="aggiudicatari",
            update_supported=False,
            dictionary_available=False,
            adapter=SnapshotDatasetFamilyAdapter(
                family="aggiudicatari",
                dataset_id="aggiudicatari",
                supported_source_formats=("csv",),
                default_source_format="csv",
            ),
        ),
    ]

    for dataset_id, config in VOCABULARY_DATASET_CONFIGS.items():
        families.append(
            DatasetFamilyDefinition(
                dataset=dataset_id,
                title=_slug_to_title(dataset_id),
                category="vocabulary",
                description=config.description,
                coverage_kind="snapshot",
                available_source_formats=("csv",),
                query_view_name=config.tables[0].name if config.tables else None,
                update_supported=False,
                dictionary_available=False,
                adapter=SnapshotDatasetFamilyAdapter(
                    family=dataset_id,
                    dataset_id=dataset_id,
                    supported_source_formats=("csv",),
                    default_source_format="csv",
                ),
            )
        )

    return DatasetFamilyRegistry(families)


def _slug_to_title(value: str) -> str:
    """@notice Convert a dataset-family slug into a readable title."""

    title_words: list[str] = []
    for word in value.split("-"):
        if word.casefold() == "cig":
            title_words.append("CIG")
        elif word.casefold() == "smartcig":
            title_words.append("SMARTCIG")
        else:
            title_words.append(word.capitalize())
    return " ".join(title_words)


DATASET_FAMILY_REGISTRY = _build_dataset_family_registry()
