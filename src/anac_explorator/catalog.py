"""@notice Explicit dataset-family registry and adapter dispatch helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

from anac_explorator.errors import CliCommandError
from anac_explorator.loader import download_dataset_to_parquet, sync_cig_periods_to_parquet
from anac_explorator.models import DatasetIncrementalUpdateResult, WarehouseLoadResult
from anac_explorator.selection import TemporalSelection
from anac_explorator.vocabulary import VOCABULARY_DATASET_CONFIGS

if TYPE_CHECKING:
    from pathlib import Path

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


class DatasetFamilyAdapter:
    """@notice Base adapter that resolves CKAN ids and optional family operations."""

    def __init__(
        self,
        *,
        family: str,
        coverage_kind: str,
        remote_first_year: int | None = None,
        remote_last_year: int | None = None,
    ) -> None:
        """@notice Initialize the shared adapter metadata."""

        self.family = family
        self.coverage_kind = coverage_kind
        self.remote_first_year = remote_first_year
        self.remote_last_year = remote_last_year

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the logical family to one or more CKAN dataset ids."""

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


class PeriodizedDatasetFamilyAdapter(DatasetFamilyAdapter):
    """@notice Resolve one monthly family whose CKAN ids follow `<prefix>-<year>`."""

    def __init__(self, *, family: str, dataset_prefix: str, first_year: int, last_year: int) -> None:
        """@notice Initialize the periodized dataset metadata."""

        super().__init__(
            family=family,
            coverage_kind="periodic_monthly",
            remote_first_year=first_year,
            remote_last_year=last_year,
        )
        self.dataset_prefix = dataset_prefix

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the selected period span to yearly CKAN dataset ids."""

        years = self._resolve_years(selection)
        return [f"{self.dataset_prefix}-{year_value:04d}" for year_value in years]

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


class SnapshotDatasetFamilyAdapter(DatasetFamilyAdapter):
    """@notice Resolve one snapshot family that maps to exactly one CKAN dataset id."""

    def __init__(self, *, family: str, dataset_id: str) -> None:
        """@notice Initialize the fixed snapshot mapping."""

        super().__init__(family=family, coverage_kind="snapshot")
        self.dataset_id = dataset_id

    def resolve_remote_dataset_ids(self, selection: TemporalSelection | None = None) -> list[str]:
        """@notice Resolve the one snapshot dataset id and reject temporal selectors."""

        if selection is not None and selection.mode != "all":
            raise CliCommandError(
                "DATASET_NOT_SUPPORTED",
                f"Temporal selection is not supported for snapshot dataset family {self.family!r}.",
                details={"dataset": self.family, "coverage_kind": self.coverage_kind},
            )
        return [self.dataset_id]

    def matches_dataset_id(self, dataset_id: str) -> bool:
        """@notice Match one exact snapshot dataset id."""

        return dataset_id == self.dataset_id


class CigDatasetFamilyAdapter(PeriodizedDatasetFamilyAdapter):
    """@notice Provide the currently implemented Phase 3 family operations for CIG."""

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

    def download_to_parquet(self, dataset: str, client: "CkanClient", **kwargs: object) -> WarehouseLoadResult:
        """@notice Dispatch a direct-to-Parquet download through the family's adapter."""

        return self.get_family(dataset).adapter.download_to_parquet(client, **kwargs)

    def update(self, dataset: str, client: "CkanClient", **kwargs: object) -> DatasetIncrementalUpdateResult:
        """@notice Dispatch an incremental update through the family's adapter."""

        return self.get_family(dataset).adapter.update(client, **kwargs)


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
            adapter=CigDatasetFamilyAdapter(family="cig", dataset_prefix="cig", first_year=2007, last_year=2025),
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
            adapter=PeriodizedDatasetFamilyAdapter(family="smartcig", dataset_prefix="smartcig", first_year=2011, last_year=2025),
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
            adapter=SnapshotDatasetFamilyAdapter(family="stazioni-appaltanti", dataset_id="stazioni-appaltanti"),
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
            adapter=SnapshotDatasetFamilyAdapter(family="aggiudicatari", dataset_id="aggiudicatari"),
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
                adapter=SnapshotDatasetFamilyAdapter(family=dataset_id, dataset_id=dataset_id),
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

