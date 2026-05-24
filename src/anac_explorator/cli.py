"""@notice Command-line entry points for the ANAC explorator project.

@dev The canonical CLI surface is now the completed Phase 3 contract:
1. datasets
2. download
3. schema
4. query
5. stats
6. update
7. config
8. drop

Legacy low-level commands are still exposed for backwards compatibility through
the same parser and executable.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Sequence

import duckdb

from anac_explorator.ckan import (
    CkanClient,
    CkanClientError,
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_CKAN_BASE_URL,
    DEFAULT_REFERER,
    DEFAULT_TRANSPORT,
    DEFAULT_USER_AGENT,
)
from anac_explorator.catalog import DATASET_FAMILY_REGISTRY, execute_download_plan
from anac_explorator.catalog import get_dataset_family, list_dataset_families, run_dataset_update, run_global_update
from anac_explorator.cleaner import clean_csv_resource, clean_json_resource
from anac_explorator.comparison import compare_schema_mappings, load_schema_mapping
from anac_explorator.config import (
    apply_effective_config,
    get_config,
    reset_config,
    set_config,
    show_config,
    unset_config,
    validate_config,
)
from anac_explorator.dictionary import build_cig_data_dictionary
from anac_explorator.errors import CliCommandError, detect_mutating_query, resolve_command_error
from anac_explorator.integrity import validate_local_data_integrity
from anac_explorator.loader import (
    download_dataset_to_parquet,
    load_downloaded_resource,
    run_local_query,
)
from anac_explorator.models import CommandOutput, DownloadCommandResult
from anac_explorator.output import emit_error_result, print_json_result, print_table_result, print_yaml_result
from anac_explorator.parsing import parse_csv_resource, parse_json_resource
from anac_explorator.paths import DEFAULT_CIG_SCHEMA_PATH, apply_effective_paths
from anac_explorator.sample import (
    SampleDownloadError,
    download_cig_monthly_sample,
    download_dataset_csv_resource,
    download_dataset_resource,
)
from anac_explorator.selection import parse_temporal_selection
from anac_explorator.schema import map_csv_schema
from anac_explorator.schema_service import diff_schema_targets, inspect_schema, inspect_schema_ddl
from anac_explorator.stats import compute_dataset_stats, compute_global_stats, list_dataset_partitions, profile_dataset
from anac_explorator.vocabulary import VOCABULARY_DATASET_CONFIGS, build_vocabulary_crosswalks


def build_parser() -> argparse.ArgumentParser:
    """@notice Construct the top-level CLI parser."""

    parser = argparse.ArgumentParser(
        prog="anacx",
        description=(
            "Stable Phase 3 ANAC CLI. Prefer datasets, download, schema, query, "
            "stats, update, config, and drop. Legacy low-level commands remain "
            "available for compatibility."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Emit traceback detail on stderr when a handled command fails.",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        help="Explicit config file path.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore config files and use defaults plus CLI flags.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Suppress confirmations for destructive or write-enabled actions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    datasets_parser = subparsers.add_parser(
        "datasets",
        help="Discover logical dataset families with local materialization and remote coverage metadata.",
    )
    datasets_parser.add_argument(
        "dataset",
        nargs="?",
        help="Optional logical dataset family identifier for single-dataset detail mode.",
    )
    datasets_parser.add_argument(
        "--search",
        help="Search dataset id, title, description, and aliases.",
    )
    datasets_parser.add_argument(
        "--year",
        type=int,
        help="Filter to dataset families that advertise remote coverage for one year.",
    )
    datasets_parser.add_argument(
        "--downloaded",
        action="store_true",
        help="Show only families with local raw or loaded materialization.",
    )
    datasets_parser.add_argument(
        "--missing",
        action="store_true",
        help="Show only families that are not yet materialized locally.",
    )
    datasets_parser.add_argument(
        "--long",
        action="store_true",
        help="Include the extended dataset fields in list mode.",
    )
    datasets_parser.add_argument(
        "--source-format",
        choices=["csv", "json"],
        help="Filter by available remote source format.",
    )
    datasets_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for dataset catalog discovery.",
    )
    datasets_parser.add_argument(
        "--db-path",
        help="Path to the local DuckDB database used for local metadata discovery.",
    )
    datasets_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    datasets_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    datasets_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN metadata requests.",
    )
    datasets_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN metadata requests.",
    )
    datasets_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN metadata requests.",
    )
    datasets_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN metadata requests.",
    )
    datasets_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", DEFAULT_TRANSPORT),
        help="Transport used for CKAN metadata requests.",
    )
    datasets_parser.set_defaults(handler=_handle_datasets)

    download_parser = subparsers.add_parser(
        "download",
        help="Resolve one logical dataset family into raw artifacts and optional warehouse loads.",
    )
    download_parser.add_argument("dataset", help="Logical dataset family identifier, such as cig.")
    download_parser.add_argument(
        "--year",
        help="One year or inclusive year range in YYYY or YYYY-YYYY form.",
    )
    download_parser.add_argument(
        "--month",
        help="One month or inclusive month range in M or M-M form, used together with --year.",
    )
    download_parser.add_argument(
        "--slice",
        dest="slice_value",
        help="Explicit slice list in canonical YYYY-MM[,YYYY-MM,...] form.",
    )
    download_parser.add_argument(
        "--latest",
        action="store_true",
        help="Resolve the newest remote slice for periodized families.",
    )
    download_parser.add_argument(
        "--resource-name",
        help="Exact CKAN resource name override when a family exposes multiple resources.",
    )
    download_parser.add_argument(
        "--source-format",
        choices=["auto", "csv", "json"],
        default="auto",
        help="Required remote source format.",
    )
    download_parser.add_argument(
        "--output-format",
        dest="download_output_format",
        choices=["parquet", "raw", "both"],
        default="parquet",
        help="Local persistence mode for the selected resources.",
    )
    download_parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-fetch the raw source even when a manifest-backed cache already exists.",
    )
    download_parser.add_argument(
        "--force-load",
        action="store_true",
        help="Re-run warehouse loading even when the Parquet slice is already registered.",
    )
    download_parser.add_argument(
        "--validate",
        action="store_true",
        help="Run local integrity validation after successful warehouse loads.",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return the resolved plan without downloading or loading any resources.",
    )
    download_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the normalized download result.",
    )
    download_parser.add_argument(
        "--output-dir",
        help="Base directory used for raw manifests, archives, and materialized files.",
    )
    download_parser.add_argument(
        "--schemas-dir",
        help="Directory used for generated or reused schema artifacts during warehouse loading.",
    )
    download_parser.add_argument(
        "--schema-path",
        help="Optional schema artifact override for warehouse loading or post-load validation.",
    )
    download_parser.add_argument(
        "--warehouse-dir",
        help="Base directory for the local DuckDB database and Parquet files.",
    )
    download_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    download_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    download_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN metadata and download requests.",
    )
    download_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN metadata and download requests.",
    )
    download_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN metadata and download requests.",
    )
    download_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN metadata and download requests.",
    )
    download_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", DEFAULT_TRANSPORT),
        help="Transport used for CKAN metadata and download requests.",
    )
    download_parser.set_defaults(handler=_handle_download)

    schema_parser = subparsers.add_parser(
        "schema",
        help="Inspect artifact-driven dataset schemas, semantic overlays, historical diffs, or warehouse DDL.",
    )
    schema_parser.add_argument("dataset", help="Logical dataset family identifier, such as cig.")
    schema_parser.add_argument(
        "--year",
        help="One target year in YYYY form. Combine with --month for one monthly slice.",
    )
    schema_parser.add_argument(
        "--month",
        help="One target month in M form, used together with --year.",
    )
    schema_parser.add_argument(
        "--slice",
        dest="slice_value",
        help="One explicit target slice in canonical YYYY-MM form.",
    )
    schema_modes = schema_parser.add_mutually_exclusive_group()
    schema_modes.add_argument(
        "--describe",
        action="store_true",
        help="Enrich schema columns with local dictionary and vocabulary metadata when available.",
    )
    schema_modes.add_argument(
        "--ddl",
        action="store_true",
        help="Return the registered DuckDB view definition instead of artifact-driven column metadata.",
    )
    schema_modes.add_argument(
        "--diff",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help="Compare two schema targets, each in canonical, YYYY, or YYYY-MM form.",
    )
    schema_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the normalized schema result.",
    )
    schema_parser.add_argument(
        "--db-path",
        help="Path to the local DuckDB database used for warehouse DDL inspection.",
    )
    schema_parser.add_argument(
        "--schemas-dir",
        help="Directory containing local schema artifacts.",
    )
    schema_parser.add_argument(
        "--dictionaries-dir",
        help="Directory containing local data-dictionary artifacts.",
    )
    schema_parser.set_defaults(handler=_handle_schema)

    query_parser = subparsers.add_parser(
        "query",
        help="Execute safe SQL against the local DuckDB warehouse and metadata discoverability views.",
    )
    query_parser.add_argument("sql_query", help="SQL query executed against the local DuckDB warehouse.")
    query_parser.add_argument(
        "--db-path",
        help="Path to the local DuckDB database.",
    )
    query_parser.add_argument(
        "--row-limit",
        type=int,
        default=1_000,
        help="Maximum number of rows returned to stdout or exported output. Use 0 to retain all.",
    )
    query_parser.add_argument(
        "--timeout",
        dest="query_timeout",
        type=int,
        default=None,
        help="Maximum execution time in seconds for one query operation.",
    )
    query_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table", "csv", "parquet"],
        default=None,
        help="Output format for stdout rendering or direct export.",
    )
    query_parser.add_argument(
        "--output",
        help="Required output path for csv or parquet exports.",
    )
    query_parser.add_argument(
        "--explain",
        action="store_true",
        help="Return the structured DuckDB execution plan instead of query result rows.",
    )
    query_parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Opt into write SQL when combined with --yes.",
    )
    query_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm write SQL when combined with --allow-write.",
    )
    query_parser.set_defaults(handler=_handle_query)

    stats_parser = subparsers.add_parser(
        "stats",
        help="Summarize local storage coverage and optionally inspect live dataset values.",
    )
    stats_parser.add_argument(
        "dataset",
        nargs="?",
        help="Optional logical dataset family identifier for dataset-level stats.",
    )
    stats_parser.add_argument(
        "--year",
        help="One year or inclusive year range in YYYY or YYYY-YYYY form.",
    )
    stats_parser.add_argument(
        "--month",
        help="One month or inclusive month range in M or M-M form, used together with --year.",
    )
    stats_parser.add_argument(
        "--slice",
        dest="slice_value",
        help="Explicit slice list in canonical YYYY-MM[,YYYY-MM,...] form.",
    )
    stats_parser.add_argument(
        "--latest",
        action="store_true",
        help="Restrict stats to the latest locally loaded temporal slice when available.",
    )
    stats_parser.add_argument(
        "--profile",
        action="store_true",
        help="Run a live DuckDB column-profile pass for the selected dataset family.",
    )
    stats_parser.add_argument(
        "--partitions",
        action="store_true",
        help="Include ordered local partition coverage for temporal dataset families.",
    )
    stats_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the stats result.",
    )
    stats_parser.add_argument(
        "--db-path",
        help="Path to the local DuckDB database used for stats and profiling.",
    )
    stats_parser.set_defaults(handler=_handle_stats)

    update_parser = subparsers.add_parser(
        "update",
        help="Plan or apply incremental updates for one dataset family or all locally present update-capable families.",
    )
    update_parser.add_argument(
        "dataset",
        nargs="?",
        help="Optional logical dataset family identifier. Omit to target all locally present update-capable families.",
    )
    update_parser.add_argument(
        "--year",
        help="One year or inclusive year range in YYYY or YYYY-YYYY form.",
    )
    update_parser.add_argument(
        "--month",
        help="One month or inclusive month range in M or M-M form, used together with --year.",
    )
    update_parser.add_argument(
        "--slice",
        dest="slice_value",
        help="Explicit slice list in canonical YYYY-MM[,YYYY-MM,...] form.",
    )
    update_parser.add_argument(
        "--latest",
        action="store_true",
        help="Restrict the update to the latest available remote slice within the selected scope.",
    )
    update_parser.add_argument(
        "--refresh-changed",
        action="store_true",
        help="Also refresh already-loaded slices when remote metadata changed upstream.",
    )
    update_parser.add_argument(
        "--force-full",
        action="store_true",
        help="Rebuild every slice in the selected update scope regardless of current local state.",
    )
    update_parser.add_argument(
        "--validate",
        action="store_true",
        help="Run local integrity validation after successful updates.",
    )
    update_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return the resolved update plan without applying downloads or loads.",
    )
    update_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the normalized update result.",
    )
    update_parser.add_argument(
        "--output-dir",
        help="Base directory used for raw manifests, archives, and materialized files.",
    )
    update_parser.add_argument(
        "--schemas-dir",
        help="Directory used for generated or reused schema artifacts during updates.",
    )
    update_parser.add_argument(
        "--schema-path",
        help="Optional schema artifact override for post-update validation.",
    )
    update_parser.add_argument(
        "--warehouse-dir",
        help="Base directory for the local DuckDB database and Parquet files.",
    )
    update_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    update_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    update_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN metadata and download requests.",
    )
    update_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN metadata and download requests.",
    )
    update_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN metadata and download requests.",
    )
    update_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN metadata and download requests.",
    )
    update_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", DEFAULT_TRANSPORT),
        help="Transport used for CKAN metadata and download requests.",
    )
    update_parser.set_defaults(handler=_handle_update)

    drop_parser = subparsers.add_parser(
        "drop",
        help="Plan or apply safe local pruning for one dataset family's raw files, Parquet slices, or both.",
    )
    drop_parser.add_argument("dataset", help="Logical dataset family identifier, such as cig.")
    drop_parser.add_argument(
        "--year",
        help="One year or inclusive year range in YYYY or YYYY-YYYY form.",
    )
    drop_parser.add_argument(
        "--month",
        help="One month or inclusive month range in M or M-M form, used together with --year.",
    )
    drop_parser.add_argument(
        "--slice",
        dest="slice_value",
        help="Explicit slice list in canonical YYYY-MM[,YYYY-MM,...] form.",
    )
    drop_parser.add_argument(
        "--latest",
        action="store_true",
        help="Restrict the drop scope to the latest locally available slice when applicable.",
    )
    drop_parser.add_argument(
        "--resource-id",
        dest="resource_ids",
        action="append",
        help="Optional local resource id or name filter. May be repeated or given as comma-separated values.",
    )
    drop_parser.add_argument(
        "--layer",
        choices=["raw", "parquet", "all"],
        default="all",
        help="Local storage layer to prune.",
    )
    drop_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return the resolved drop plan without deleting any files.",
    )
    drop_parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive local file deletion when not using --dry-run.",
    )
    drop_parser.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the normalized drop result.",
    )
    drop_parser.set_defaults(handler=_handle_drop)

    package_show = subparsers.add_parser(
        "package-show",
        help="Legacy: fetch CKAN metadata for one dataset identifier.",
    )
    package_show.add_argument("dataset_id", help="CKAN dataset slug or identifier.")
    package_show.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    package_show.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    package_show.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for the CKAN request.",
    )
    package_show.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for the CKAN request.",
    )
    package_show.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for the CKAN request.",
    )
    package_show.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for the CKAN request.",
    )
    package_show.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", DEFAULT_TRANSPORT),
        help="Transport used for CKAN access.",
    )
    package_show.set_defaults(handler=_handle_package_show)

    download_dataset_csv = subparsers.add_parser(
        "download-dataset-csv",
        help="Legacy: resolve one dataset CSV resource, download it, and materialize the CSV locally.",
    )
    download_dataset_csv.add_argument("dataset_id", help="CKAN dataset slug to resolve.")
    download_dataset_csv.add_argument(
        "--resource-name",
        help="Exact CKAN resource name to choose when a dataset exposes multiple CSV resources.",
    )
    download_dataset_csv.add_argument(
        "--output-dir",
        help="Base directory used for the downloaded archive and extracted CSV.",
    )
    download_dataset_csv.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    download_dataset_csv.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    download_dataset_csv.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    download_dataset_csv.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    download_dataset_csv.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    download_dataset_csv.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    download_dataset_csv.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    download_dataset_csv.set_defaults(handler=_handle_download_dataset_csv)

    download_dataset_resource_parser = subparsers.add_parser(
        "download-dataset-resource",
        help="Legacy: resolve one CKAN CSV or JSON resource, download it, and persist a download manifest.",
    )
    download_dataset_resource_parser.add_argument("dataset_id", help="CKAN dataset slug to resolve.")
    download_dataset_resource_parser.add_argument(
        "--resource-name",
        help="Exact CKAN resource name to choose when a dataset exposes multiple matching resources.",
    )
    download_dataset_resource_parser.add_argument(
        "--resource-format",
        choices=["csv", "json"],
        default="csv",
        help="High-level resource format to download.",
    )
    download_dataset_resource_parser.add_argument(
        "--output-dir",
        help="Base directory used for downloaded archives and materialized files.",
    )
    download_dataset_resource_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    download_dataset_resource_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    download_dataset_resource_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    download_dataset_resource_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    download_dataset_resource_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    download_dataset_resource_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    download_dataset_resource_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    download_dataset_resource_parser.set_defaults(handler=_handle_download_dataset_resource)

    download_dataset_to_parquet_parser = subparsers.add_parser(
        "download-dataset-to-parquet",
        help="Legacy: download one CSV dataset resource, load it into Parquet, and register DuckDB views.",
    )
    download_dataset_to_parquet_parser.add_argument("dataset_id", help="CKAN dataset slug to resolve.")
    download_dataset_to_parquet_parser.add_argument(
        "--resource-name",
        help="Exact CKAN resource name to choose when a dataset exposes multiple CSV resources.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--output-dir",
        help="Base directory used for downloaded raw archives and manifests.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--schemas-dir",
        help="Directory used for generated or reused schema artifacts.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--schema-path",
        help="Optional schema artifact used or generated for typed warehouse loading.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--schema-sample-limit",
        type=int,
        default=2_000,
        help="Maximum number of rows inspected when generating a missing schema artifact. Use 0 to scan the full file.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--warehouse-dir",
        help="Base directory for the local DuckDB database and Parquet files.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--keep-materialized",
        action="store_true",
        help="Keep the extracted uncompressed CSV after loading instead of pruning it when an archive is available.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--skip-crosswalks",
        action="store_true",
        help="Skip automatic registration of local vocabulary crosswalk artifacts in DuckDB.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--vocabulary-index-path",
        help="Vocabulary index used when registering crosswalk views for DuckDB querying.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--delimiter",
        default=";",
        help="CSV delimiter used for schema generation and loading.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Encoding used for schema generation and loading.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    download_dataset_to_parquet_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    download_dataset_to_parquet_parser.set_defaults(handler=_handle_download_dataset_to_parquet)

    sync_cig_periods_parser = subparsers.add_parser(
        "sync-cig-periods",
        help="Legacy: incrementally load selected or newer monthly CIG periods into Parquet-backed DuckDB views.",
    )
    sync_cig_periods_parser.add_argument("dataset_id", help="CKAN yearly CIG dataset slug to inspect, such as cig-2025.")
    sync_cig_periods_parser.add_argument(
        "--period",
        action="append",
        default=[],
        help="Explicit period to sync in YYYY_MM form. Repeat to request multiple periods.",
    )
    sync_cig_periods_parser.add_argument(
        "--from-period",
        help="Inclusive start of a period range in YYYY_MM form.",
    )
    sync_cig_periods_parser.add_argument(
        "--to-period",
        help="Inclusive end of a period range in YYYY_MM form.",
    )
    sync_cig_periods_parser.add_argument(
        "--refresh-changed",
        action="store_true",
        help="Also refresh already-loaded periods when CKAN metadata shows they changed upstream.",
    )
    sync_cig_periods_parser.add_argument(
        "--output-dir",
        help="Base directory used for downloaded raw archives and manifests.",
    )
    sync_cig_periods_parser.add_argument(
        "--schemas-dir",
        help="Directory used for generated or reused schema artifacts.",
    )
    sync_cig_periods_parser.add_argument(
        "--warehouse-dir",
        help="Base directory for the local DuckDB database and Parquet files.",
    )
    sync_cig_periods_parser.add_argument(
        "--schema-sample-limit",
        type=int,
        default=2_000,
        help="Maximum number of rows inspected when generating a missing schema artifact. Use 0 to scan the full file.",
    )
    sync_cig_periods_parser.add_argument(
        "--keep-materialized",
        action="store_true",
        help="Keep extracted uncompressed CSV files after loading instead of pruning them when an archive is available.",
    )
    sync_cig_periods_parser.add_argument(
        "--skip-crosswalks",
        action="store_true",
        help="Skip automatic registration of local vocabulary crosswalk artifacts in DuckDB.",
    )
    sync_cig_periods_parser.add_argument(
        "--vocabulary-index-path",
        help="Vocabulary index used when registering crosswalk views for DuckDB querying.",
    )
    sync_cig_periods_parser.add_argument(
        "--delimiter",
        default=";",
        help="CSV delimiter used for schema generation and loading.",
    )
    sync_cig_periods_parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Encoding used for schema generation and loading.",
    )
    sync_cig_periods_parser.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    sync_cig_periods_parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    sync_cig_periods_parser.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    sync_cig_periods_parser.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    sync_cig_periods_parser.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    sync_cig_periods_parser.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    sync_cig_periods_parser.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    sync_cig_periods_parser.set_defaults(handler=_handle_sync_cig_periods)

    download_cig_sample = subparsers.add_parser(
        "download-cig-sample",
        help="Legacy: resolve one monthly CIG CSV resource, download it, and extract the CSV.",
    )
    download_cig_sample.add_argument(
        "--year",
        type=int,
        required=True,
        help="CIG dataset year to resolve.",
    )
    download_cig_sample.add_argument(
        "--month",
        type=int,
        required=True,
        help="CIG dataset month to resolve.",
    )
    download_cig_sample.add_argument(
        "--output-dir",
        help="Base directory used for the downloaded archive and extracted CSV.",
    )
    download_cig_sample.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    download_cig_sample.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    download_cig_sample.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    download_cig_sample.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    download_cig_sample.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    download_cig_sample.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    download_cig_sample.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    download_cig_sample.set_defaults(handler=_handle_download_cig_sample)

    compare_schema_files = subparsers.add_parser(
        "compare-schema-files",
        help="Legacy: compare two schema JSON artifacts and report differences.",
    )
    compare_schema_files.add_argument("left_schema_path", help="Path to the left schema JSON file.")
    compare_schema_files.add_argument("right_schema_path", help="Path to the right schema JSON file.")
    compare_schema_files.set_defaults(handler=_handle_compare_schema_files)

    build_vocabularies = subparsers.add_parser(
        "build-vocabulary-crosswalks",
        help="Legacy: download configured vocabulary datasets and emit normalized cross-reference tables.",
    )
    build_vocabularies.add_argument(
        "dataset_ids",
        nargs="*",
        help="Optional subset of configured vocabulary dataset slugs to build.",
    )
    build_vocabularies.add_argument(
        "--data-dir",
        help="Base directory used for downloaded raw dataset files.",
    )
    build_vocabularies.add_argument(
        "--schemas-dir",
        help="Directory used for raw schema artifacts.",
    )
    build_vocabularies.add_argument(
        "--output-dir",
        help="Directory used for normalized vocabulary artifacts.",
    )
    build_vocabularies.add_argument(
        "--base-url",
        default=DEFAULT_CKAN_BASE_URL,
        help="CKAN action API base URL.",
    )
    build_vocabularies.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds.",
    )
    build_vocabularies.add_argument(
        "--user-agent",
        default=os.getenv("ANAC_EXPLORATOR_USER_AGENT", DEFAULT_USER_AGENT),
        help="User-Agent header for CKAN and download requests.",
    )
    build_vocabularies.add_argument(
        "--accept-language",
        default=os.getenv("ANAC_EXPLORATOR_ACCEPT_LANGUAGE", DEFAULT_ACCEPT_LANGUAGE),
        help="Accept-Language header for CKAN and download requests.",
    )
    build_vocabularies.add_argument(
        "--referer",
        default=os.getenv("ANAC_EXPLORATOR_REFERER", DEFAULT_REFERER),
        help="Referer header for CKAN and download requests.",
    )
    build_vocabularies.add_argument(
        "--proxy-url",
        default=os.getenv("ANAC_EXPLORATOR_PROXY_URL"),
        help="Optional HTTP(S) proxy URL used for CKAN and download requests.",
    )
    build_vocabularies.add_argument(
        "--transport",
        choices=["auto", "http", "playwright"],
        default=os.getenv("ANAC_EXPLORATOR_TRANSPORT", "playwright"),
        help="Transport used for CKAN and download requests.",
    )
    build_vocabularies.set_defaults(handler=_handle_build_vocabulary_crosswalks)

    build_data_dictionary = subparsers.add_parser(
        "build-data-dictionary",
        help="Legacy: build the January 2025 CIG data dictionary from schema and vocabulary artifacts.",
    )
    build_data_dictionary.add_argument(
        "--schema-path",
        help="Source schema artifact for the current CIG surface.",
    )
    build_data_dictionary.add_argument(
        "--comparison-path",
        help="Cross-year comparison artifact used for field notes.",
    )
    build_data_dictionary.add_argument(
        "--vocabulary-index-path",
        help="Vocabulary index artifact used for code-meaning links and gaps.",
    )
    build_data_dictionary.add_argument(
        "--vocabulary-dir",
        help="Directory containing generated vocabulary artifacts.",
    )
    build_data_dictionary.add_argument(
        "--output-dir",
        help="Directory used for generated data-dictionary artifacts.",
    )
    build_data_dictionary.set_defaults(handler=_handle_build_data_dictionary)

    inspect_csv_schema = subparsers.add_parser(
        "inspect-csv-schema",
        help="Legacy: inspect a local CSV file and emit a schema mapping as JSON.",
    )
    inspect_csv_schema.add_argument("csv_path", help="Path to the CSV file to inspect.")
    inspect_csv_schema.add_argument(
        "--delimiter",
        default=";",
        help="Delimiter used by the CSV file.",
    )
    inspect_csv_schema.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Text encoding used by the CSV file.",
    )
    inspect_csv_schema.add_argument(
        "--sample-limit",
        type=int,
        default=2_000,
        help="Maximum number of data rows to inspect. Use 0 to scan the full file.",
    )
    inspect_csv_schema.set_defaults(handler=_handle_inspect_csv_schema)

    parse_resource = subparsers.add_parser(
        "parse-resource",
        help="Legacy: parse a local CSV or JSON resource into a structured machine-readable payload.",
    )
    parse_resource.add_argument("resource_path", help="Path to the local CSV or JSON resource.")
    parse_resource.add_argument(
        "--format",
        choices=["csv", "json"],
        required=True,
        help="Resource format to parse.",
    )
    parse_resource.add_argument(
        "--delimiter",
        default=";",
        help="CSV delimiter used when parsing CSV resources.",
    )
    parse_resource.add_argument(
        "--encoding",
        help="Optional text encoding override. Defaults to utf-8-sig for CSV and utf-8 for JSON.",
    )
    parse_resource.add_argument(
        "--record-limit",
        type=int,
        default=100,
        help="Maximum number of records or items retained in memory. Use 0 to retain all.",
    )
    parse_resource.set_defaults(handler=_handle_parse_resource)

    clean_resource = subparsers.add_parser(
        "clean-resource",
        help="Legacy: parse and clean a local CSV or JSON resource for later database loading.",
    )
    clean_resource.add_argument("resource_path", help="Path to the local CSV or JSON resource.")
    clean_resource.add_argument(
        "--format",
        choices=["csv", "json"],
        required=True,
        help="Resource format to clean.",
    )
    clean_resource.add_argument(
        "--schema-path",
        help="Optional schema artifact used to drive CSV type coercion.",
    )
    clean_resource.add_argument(
        "--delimiter",
        default=";",
        help="CSV delimiter used when parsing CSV resources.",
    )
    clean_resource.add_argument(
        "--encoding",
        help="Optional text encoding override. Defaults to utf-8-sig for CSV and utf-8 for JSON.",
    )
    clean_resource.add_argument(
        "--record-limit",
        type=int,
        default=100,
        help="Maximum number of records or items retained in memory. Use 0 to retain all.",
    )
    clean_resource.set_defaults(handler=_handle_clean_resource)

    load_downloaded_resource_parser = subparsers.add_parser(
        "load-downloaded-resource",
        help="Legacy: load one manifest-backed CSV resource into partitioned Parquet and register a DuckDB view.",
    )
    load_downloaded_resource_parser.add_argument(
        "manifest_path",
        help="Path to the manifest.json file produced by download-dataset-resource.",
    )
    load_downloaded_resource_parser.add_argument(
        "--schema-path",
        help="Optional schema artifact used for typed warehouse projection.",
    )
    load_downloaded_resource_parser.add_argument(
        "--warehouse-dir",
        help="Base directory for the local DuckDB database and Parquet files.",
    )
    load_downloaded_resource_parser.add_argument(
        "--delimiter",
        default=";",
        help="CSV delimiter used by the downloaded resource.",
    )
    load_downloaded_resource_parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="Encoding used when reading the source header if no schema artifact is supplied.",
    )
    load_downloaded_resource_parser.set_defaults(handler=_handle_load_downloaded_resource)

    query_local_data = subparsers.add_parser(
        "query-local-data",
        help="Legacy: execute SQL against the local DuckDB warehouse and emit JSON rows.",
    )
    query_local_data.add_argument("sql_query", help="SQL query executed against the local DuckDB warehouse.")
    query_local_data.add_argument(
        "--db-path",
        help="Path to the local DuckDB database.",
    )
    query_local_data.add_argument(
        "--row-limit",
        type=int,
        default=1_000,
        help="Maximum number of rows returned. Use 0 to retain all.",
    )
    query_local_data.set_defaults(handler=_handle_query_local_data)

    validate_local_data = subparsers.add_parser(
        "validate-local-data-integrity",
        help="Legacy: run read-only integrity validation against the local DuckDB/Parquet warehouse.",
    )
    validate_local_data.add_argument(
        "--db-path",
        help="Path to the local DuckDB database.",
    )
    validate_local_data.add_argument(
        "--dataset-type",
        default="cig",
        help="Logical dataset family to validate. The current implementation focuses on monthly CIG.",
    )
    validate_local_data.add_argument(
        "--schema-path",
        help="Schema artifact used for loaded CIG schema validation.",
    )
    validate_local_data.add_argument(
        "--vocabulary-index-path",
        help="Vocabulary index used for external referential-integrity checks.",
    )
    validate_local_data.set_defaults(handler=_handle_validate_local_data_integrity)

    config_parser = subparsers.add_parser(
        "config",
        help="Show, read, persist, reset, or validate CLI configuration.",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_subcommand", required=True)

    config_show = config_subparsers.add_parser(
        "show",
        help="Show effective configuration after file, env, and defaults are merged.",
    )
    config_show.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table", "yaml"],
        default=None,
        help="Output format for the effective configuration view.",
    )
    config_show.set_defaults(handler=_handle_config_show)

    config_get = config_subparsers.add_parser(
        "get",
        help="Read one resolved configuration key and show its source.",
    )
    config_get.add_argument("key", help="Configuration key in domain.field form.")
    config_get.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the resolved key lookup.",
    )
    config_get.set_defaults(handler=_handle_config_get)

    config_set = config_subparsers.add_parser(
        "set",
        help="Persist one configuration value into the config file.",
    )
    config_set.add_argument("key", help="Configuration key in domain.field form.")
    config_set.add_argument("value", help="Configuration value to persist.")
    config_set.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the persistence result.",
    )
    config_set.set_defaults(handler=_handle_config_set)

    config_unset = config_subparsers.add_parser(
        "unset",
        help="Remove one explicitly persisted configuration value.",
    )
    config_unset.add_argument("key", help="Configuration key in domain.field form.")
    config_unset.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the unset result.",
    )
    config_unset.set_defaults(handler=_handle_config_unset)

    config_reset = config_subparsers.add_parser(
        "reset",
        help="Reset the persisted configuration file.",
    )
    config_reset.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the reset result.",
    )
    config_reset.set_defaults(handler=_handle_config_reset)

    config_validate = config_subparsers.add_parser(
        "validate",
        help="Validate the current effective configuration and return all detected issues.",
    )
    config_validate.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "table"],
        default=None,
        help="Output format for the validation result.",
    )
    config_validate.set_defaults(handler=_handle_config_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """@notice Execute the CLI with the provided argument vector.

    @param argv Optional explicit argument vector.
    @return Process exit code.
    """

    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(raw_argv)
    started_at_ns = time.perf_counter_ns()
    command = str(args.command)
    output_format = getattr(args, "output_format", None)

    try:
        args = apply_effective_config(args, argv=raw_argv)
        args = apply_effective_paths(args, config_paths={"paths": args.resolved_config.config.paths.to_dict()}, env={})
        result = _attach_meta_paths(args.handler(args), args)
    except (CkanClientError, SampleDownloadError, FileNotFoundError, UnicodeDecodeError, ValueError, duckdb.Error) as exc:
        cli_error = resolve_command_error(command, exc, args=args)
        if args.debug:
            traceback.print_exception(cli_error.cause or exc, file=sys.stderr)
        emit_error_result(
            command,
            cli_error,
            started_at_ns=started_at_ns,
            paths=_current_meta_paths(args),
        )
        return cli_error.exit_code
    except Exception as exc:
        cli_error = resolve_command_error(command, exc, args=args)
        if args.debug:
            traceback.print_exception(cli_error.cause or exc, file=sys.stderr)
        emit_error_result(
            command,
            cli_error,
            started_at_ns=started_at_ns,
            paths=_current_meta_paths(args),
        )
        return cli_error.exit_code

    if output_format == "table":
        print_table_result(command, result, started_at_ns=started_at_ns)
        return 0
    if command == "config" and output_format == "yaml":
        print_yaml_result(result.data if isinstance(result, CommandOutput) else result)
        return 0
    print_json_result(command, result, started_at_ns=started_at_ns)
    return 0


def _attach_meta_paths(result: object, args: argparse.Namespace) -> CommandOutput:
    """@notice Attach resolved shared paths to the command result envelope metadata."""

    meta_paths = args.effective_paths.to_meta_paths()
    if isinstance(result, CommandOutput):
        return CommandOutput(
            data=result.data,
            warnings=result.warnings,
            paths={**meta_paths, **result.paths},
            truncated=result.truncated,
        )
    return CommandOutput(data=result, paths=meta_paths)


def _current_meta_paths(args: argparse.Namespace) -> dict[str, object]:
    """@notice Return the best available path metadata during success or failure handling."""

    if hasattr(args, "effective_paths"):
        return args.effective_paths.to_meta_paths()
    if hasattr(args, "resolved_config"):
        return args.resolved_config.config.paths.to_dict()
    return {}


def _handle_config_show(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config show` CLI subcommand."""

    return show_config(config_path=args.config_path, no_config=args.no_config)


def _handle_config_get(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config get` CLI subcommand."""

    return get_config(args.key, config_path=args.config_path, no_config=args.no_config)


def _handle_config_set(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config set` CLI subcommand."""

    return set_config(args.config_path, args.key, args.value)


def _handle_config_unset(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config unset` CLI subcommand."""

    return unset_config(args.config_path, args.key)


def _handle_config_reset(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config reset` CLI subcommand."""

    if not args.yes:
        raise CliCommandError(
            "CONFIG_ERROR",
            "Config reset requires --yes.",
            details={"subcommand": "reset"},
        )
    return reset_config(args.config_path)


def _handle_config_validate(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `config validate` CLI subcommand."""

    return validate_config(config_path=args.config_path, no_config=args.no_config)


def _handle_package_show(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `package-show` CLI subcommand."""

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    package = client.package_show(args.dataset_id)
    return package.to_dict()


def _handle_datasets(args: argparse.Namespace) -> CommandOutput:
    """@notice Execute the `datasets` CLI subcommand."""

    client = None
    if args.dataset is not None:
        client = CkanClient(
            base_url=args.base_url,
            timeout=args.timeout,
            user_agent=args.user_agent,
            accept_language=args.accept_language,
            referer=args.referer,
            proxy_url=args.proxy_url,
            transport=args.transport,
        )

    common_kwargs = {
        "db_path": Path(args.db_path),
        "raw_dir": args.effective_paths.raw_dir,
        "schemas_dir": args.effective_paths.schemas_dir,
        "dictionaries_dir": args.effective_paths.dictionaries_dir,
        "vocabulary_index_path": args.effective_paths.vocabulary_index_path,
        "registry": DATASET_FAMILY_REGISTRY,
        "search": args.search,
        "year": args.year,
        "downloaded": args.downloaded,
        "missing": args.missing,
        "source_format": args.source_format,
    }
    if args.dataset is None:
        result = list_dataset_families(**common_kwargs)
        return CommandOutput(
            data=result.to_dict(include_extended=bool(args.long)),
            warnings=result.warnings,
        )

    result = get_dataset_family(
        args.dataset,
        client=client,
        **common_kwargs,
    )
    return CommandOutput(
        data=result.to_dict(),
        warnings=result.warnings,
    )


def _handle_download(args: argparse.Namespace) -> DownloadCommandResult:
    """@notice Execute the Phase 3 `download` CLI subcommand."""

    temporal_selection = parse_temporal_selection(
        year=args.year,
        month=args.month,
        slice_value=args.slice_value,
        latest=args.latest,
    )
    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    plan = DATASET_FAMILY_REGISTRY.plan_download(
        args.dataset,
        client,
        selection=temporal_selection,
        preferred_resource_name=args.resource_name,
        source_format=args.source_format,
        output_format=args.download_output_format,
    )
    if plan.output_format == "raw" and args.force_load:
        raise CliCommandError(
            "VALIDATION_FAILED",
            "--force-load requires --output-format parquet or both.",
            details={
                "dataset": plan.dataset,
                "output_format": plan.output_format,
            },
        )
    if plan.output_format == "raw" and args.validate:
        raise CliCommandError(
            "VALIDATION_FAILED",
            "--validate requires --output-format parquet or both.",
            details={
                "dataset": plan.dataset,
                "output_format": plan.output_format,
            },
        )

    requested_selection = {
        "dataset": plan.dataset,
        "output_format": plan.output_format,
        "dry_run": bool(args.dry_run),
        "validate": bool(args.validate),
        "force_download": bool(args.force_download),
        "force_load": bool(args.force_load),
        **plan.requested_scope,
    }
    applied_actions = []
    validation_result = None
    if not args.dry_run:
        applied_actions = execute_download_plan(
            plan,
            client,
            output_dir=Path(args.output_dir),
            schemas_dir=Path(args.schemas_dir),
            warehouse_dir=Path(args.warehouse_dir),
            preferred_schema_path=None if args.schema_path is None else Path(args.schema_path),
            vocabulary_index_path=args.effective_paths.vocabulary_index_path,
            force_download=args.force_download,
            force_load=args.force_load,
        )
        if args.validate:
            if plan.dataset != "cig":
                raise CliCommandError(
                    "DATASET_NOT_SUPPORTED",
                    f"Integrity validation is not available for dataset family {plan.dataset!r} yet.",
                    details={"dataset": plan.dataset},
                )
            validation_schema_path = (
                Path(args.schema_path)
                if args.schema_path is not None
                else args.effective_paths.schemas_dir / DEFAULT_CIG_SCHEMA_PATH.name
            )
            validation_result = validate_local_data_integrity(
                args.effective_paths.warehouse_db_path,
                dataset_type=plan.dataset,
                schema_path=validation_schema_path,
                vocabulary_index_path=args.effective_paths.vocabulary_index_path,
            )

    return DownloadCommandResult(
        requested_selection=requested_selection,
        resolved_plan=plan,
        applied_actions=applied_actions,
        validation_result=validation_result,
        dry_run=bool(args.dry_run),
    )


def _handle_schema(args: argparse.Namespace) -> object:
    """@notice Execute the Phase 3 `schema` CLI subcommand."""

    if args.diff is not None:
        if _schema_has_temporal_flags(args):
            raise CliCommandError(
                "VALIDATION_FAILED",
                "--diff cannot be combined with --year, --month, or --slice.",
                details={"dataset": args.dataset, "diff": list(args.diff)},
            )
        return diff_schema_targets(
            args.dataset,
            left_target=args.diff[0],
            right_target=args.diff[1],
            schemas_dir=args.effective_paths.schemas_dir,
            dictionaries_dir=args.effective_paths.dictionaries_dir,
            registry=DATASET_FAMILY_REGISTRY,
        )

    if args.ddl:
        if _schema_has_temporal_flags(args):
            raise CliCommandError(
                "VALIDATION_FAILED",
                "--ddl cannot be combined with --year, --month, or --slice.",
                details={"dataset": args.dataset},
            )
        return inspect_schema_ddl(
            args.dataset,
            db_path=args.effective_paths.warehouse_db_path,
            registry=DATASET_FAMILY_REGISTRY,
        )

    return inspect_schema(
        args.dataset,
        target=_resolve_schema_cli_target(args),
        describe=bool(args.describe),
        schemas_dir=args.effective_paths.schemas_dir,
        dictionaries_dir=args.effective_paths.dictionaries_dir,
        registry=DATASET_FAMILY_REGISTRY,
    )


def _handle_query(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the Phase 3 `query` CLI subcommand."""

    mutating_keyword = detect_mutating_query(args.sql_query)
    if mutating_keyword is not None and not args.allow_write:
        raise CliCommandError(
            "WRITE_QUERY_BLOCKED",
            "Write SQL requires both --allow-write and --yes.",
            details={
                "db_path": args.db_path,
                "sql_query": args.sql_query,
                "blocked_keyword": mutating_keyword,
                "allow_write": False,
                "confirmation_required": True,
            },
        )
    if mutating_keyword is not None and not args.yes:
        raise CliCommandError(
            "WRITE_QUERY_BLOCKED",
            "Write SQL requires --yes together with --allow-write.",
            details={
                "db_path": args.db_path,
                "sql_query": args.sql_query,
                "blocked_keyword": mutating_keyword,
                "allow_write": True,
                "confirmation_required": True,
            },
        )

    return run_local_query(
        Path(args.db_path),
        args.sql_query,
        row_limit=args.row_limit,
        allow_write=bool(args.allow_write),
        explain=bool(args.explain),
        output_format="json" if args.output_format is None else str(args.output_format),
        output_path=None if args.output is None else Path(args.output),
        timeout_seconds=args.query_timeout,
    ).to_dict()


def _handle_stats(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the Phase 3 `stats` CLI subcommand."""

    common_kwargs = _stats_common_kwargs(args)
    temporal_selection = _resolve_stats_selection(args)
    if args.dataset is None:
        if args.profile:
            raise CliCommandError(
                "VALIDATION_FAILED",
                "--profile requires a dataset family argument.",
                details={"scope": "global"},
            )
        if args.partitions:
            raise CliCommandError(
                "VALIDATION_FAILED",
                "--partitions requires a dataset family argument.",
                details={"scope": "global"},
            )
        if temporal_selection.mode != "all":
            raise CliCommandError(
                "VALIDATION_FAILED",
                "Temporal flags require a dataset family argument.",
                details={"scope": "global", "selection_mode": temporal_selection.mode},
            )
        return compute_global_stats(**common_kwargs).to_dict()

    if args.partitions:
        result = list_dataset_partitions(args.dataset, selection=temporal_selection, **common_kwargs)
    elif args.profile:
        result = profile_dataset(args.dataset, selection=temporal_selection, **common_kwargs)
    else:
        result = compute_dataset_stats(args.dataset, selection=temporal_selection, **common_kwargs)

    payload = result.to_dict()
    if args.partitions and args.profile:
        payload["profile"] = profile_dataset(args.dataset, selection=temporal_selection, **common_kwargs).profile
    return payload


def _handle_update(args: argparse.Namespace) -> object:
    """@notice Execute the Phase 3 `update` CLI subcommand."""

    temporal_selection = parse_temporal_selection(
        year=args.year,
        month=args.month,
        slice_value=args.slice_value,
        latest=bool(args.latest),
    )
    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    common_kwargs = {
        "selection": temporal_selection,
        "refresh_changed": bool(args.refresh_changed),
        "force_full": bool(args.force_full),
        "validate": bool(args.validate),
        "validation_schema_path": None if args.schema_path is None else Path(args.schema_path),
        "dry_run": bool(args.dry_run),
        "registry": DATASET_FAMILY_REGISTRY,
    }
    if args.dataset is None:
        return run_global_update(
            client,
            db_path=args.effective_paths.warehouse_db_path,
            raw_dir=args.effective_paths.raw_dir,
            schemas_dir=args.effective_paths.schemas_dir,
            dictionaries_dir=args.effective_paths.dictionaries_dir,
            output_dir=args.effective_paths.raw_dir,
            warehouse_dir=args.effective_paths.warehouse_dir,
            vocabulary_index_path=args.effective_paths.vocabulary_index_path,
            **common_kwargs,
        )
    return run_dataset_update(
        args.dataset,
        client,
        output_dir=args.effective_paths.raw_dir,
        schemas_dir=args.effective_paths.schemas_dir,
        warehouse_dir=args.effective_paths.warehouse_dir,
        vocabulary_index_path=args.effective_paths.vocabulary_index_path,
        **common_kwargs,
    )


def _handle_drop(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the Phase 3 `drop` CLI subcommand."""

    temporal_selection = parse_temporal_selection(
        year=args.year,
        month=args.month,
        slice_value=args.slice_value,
        latest=bool(args.latest),
    )
    resource_ids = _parse_drop_resource_ids(args.resource_ids)
    plan = DATASET_FAMILY_REGISTRY.build_drop_plan(
        args.dataset,
        scope=temporal_selection,
        layers=args.layer,
        warehouse_dir=args.effective_paths.warehouse_dir,
        resource_ids=resource_ids,
    )
    if not args.dry_run and not args.yes:
        raise CliCommandError(
            "VALIDATION_FAILED",
            "Drop requires --yes unless --dry-run is used.",
            details={
                "dataset": args.dataset,
                "layer": args.layer,
                "dry_run": False,
                "confirmation_required": True,
            },
        )
    applied = []
    if not args.dry_run:
        applied = DATASET_FAMILY_REGISTRY.apply_drop_plan(
            args.dataset,
            plan=plan,
            warehouse_dir=args.effective_paths.warehouse_dir,
        )
    payload = plan.to_dict()
    payload["applied"] = [target.to_dict() for target in applied]
    payload["dry_run"] = bool(args.dry_run)
    return payload


def _resolve_schema_cli_target(args: argparse.Namespace) -> str | None:
    """@notice Normalize the shared temporal flags into one schema target token."""

    selection = parse_temporal_selection(
        year=args.year,
        month=args.month,
        slice_value=args.slice_value,
    )
    if selection.mode == "all":
        return None
    if selection.mode == "slice":
        if len(selection.slices) != 1:
            raise CliCommandError(
                "VALIDATION_FAILED",
                "schema accepts exactly one --slice target unless --diff is used.",
                details={"dataset": args.dataset, "requested_slices": selection.slices},
            )
        return selection.slices[0]
    if selection.mode == "range":
        if args.year not in (None, "") and args.month in (None, ""):
            if len(selection.years) != 1:
                raise CliCommandError(
                    "VALIDATION_FAILED",
                    "schema accepts exactly one target year unless --diff is used.",
                    details={"dataset": args.dataset, "requested_years": selection.years},
                )
            return f"{selection.years[0]:04d}"
        if len(selection.slices) != 1:
            raise CliCommandError(
                "VALIDATION_FAILED",
                "schema accepts exactly one targeted slice unless --diff is used.",
                details={"dataset": args.dataset, "requested_slices": selection.slices},
            )
        return selection.slices[0]
    raise CliCommandError(
        "VALIDATION_FAILED",
        f"Unsupported schema selection mode {selection.mode!r}.",
        details={"dataset": args.dataset, "selection_mode": selection.mode},
    )


def _parse_drop_resource_ids(raw_values: list[str] | None) -> list[str] | None:
    """@notice Normalize repeated or comma-separated `--resource-id` values."""

    if not raw_values:
        return None
    resource_ids = sorted({part.strip() for value in raw_values for part in str(value).split(",") if part.strip()})
    return None if not resource_ids else resource_ids


def _schema_has_temporal_flags(args: argparse.Namespace) -> bool:
    """@notice Report whether the schema command received any explicit target-selection flags."""

    return any(value not in (None, "") for value in (args.year, args.month, args.slice_value))


def _resolve_stats_selection(args: argparse.Namespace):
    """@notice Normalize the shared temporal flags into one stats selection object."""

    return parse_temporal_selection(
        year=args.year,
        month=args.month,
        slice_value=args.slice_value,
        latest=bool(args.latest),
    )


def _stats_common_kwargs(args: argparse.Namespace) -> dict[str, object]:
    """@notice Build the shared backend arguments for local stats helpers."""

    return {
        "db_path": args.effective_paths.warehouse_db_path,
        "raw_dir": args.effective_paths.raw_dir,
        "schemas_dir": args.effective_paths.schemas_dir,
        "dictionaries_dir": args.effective_paths.dictionaries_dir,
        "vocabulary_index_path": args.effective_paths.vocabulary_index_path,
        "registry": DATASET_FAMILY_REGISTRY,
    }


def _handle_inspect_csv_schema(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `inspect-csv-schema` CLI subcommand."""

    schema_mapping = map_csv_schema(
        Path(args.csv_path),
        delimiter=args.delimiter,
        encoding=args.encoding,
        sample_limit=args.sample_limit,
    )
    return schema_mapping.to_dict()


def _handle_download_cig_sample(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `download-cig-sample` CLI subcommand."""

    temporal_selection = parse_temporal_selection(year=str(args.year), month=str(args.month))
    requested_periods = temporal_selection.to_period_identifiers()
    if len(requested_periods) != 1:
        raise ValueError("download-cig-sample requires exactly one normalized monthly slice.")
    year_text, month_text = requested_periods[0].split("_", 1)

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    sample = download_cig_monthly_sample(
        client,
        year=int(year_text),
        month=int(month_text),
        output_dir=Path(args.output_dir),
    )
    return sample.to_dict()


def _handle_download_dataset_csv(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `download-dataset-csv` CLI subcommand."""

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    resource = download_dataset_csv_resource(
        client,
        dataset_id=args.dataset_id,
        preferred_resource_name=args.resource_name,
        output_dir=Path(args.output_dir),
    )
    return resource.to_dict()


def _handle_download_dataset_resource(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `download-dataset-resource` CLI subcommand."""

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    resource = download_dataset_resource(
        client,
        dataset_id=args.dataset_id,
        preferred_resource_name=args.resource_name,
        preferred_format=args.resource_format,
        output_dir=Path(args.output_dir),
    )
    return resource.to_dict()


def _handle_download_dataset_to_parquet(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `download-dataset-to-parquet` CLI subcommand."""

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    family = DATASET_FAMILY_REGISTRY.resolve_family_for_dataset_id(args.dataset_id)
    if family is None:
        return download_dataset_to_parquet(
            client,
            dataset_id=args.dataset_id,
            output_dir=Path(args.output_dir),
            schemas_dir=Path(args.schemas_dir),
            warehouse_dir=Path(args.warehouse_dir),
            preferred_resource_name=args.resource_name,
            schema_path=None if args.schema_path is None else Path(args.schema_path),
            vocabulary_index_path=Path(args.vocabulary_index_path),
            delimiter=args.delimiter,
            encoding=args.encoding,
            schema_sample_limit=args.schema_sample_limit,
            keep_materialized=args.keep_materialized,
            register_crosswalks=not args.skip_crosswalks,
        ).to_dict()
    return DATASET_FAMILY_REGISTRY.download_to_parquet(
        family.dataset,
        client,
        dataset_id=args.dataset_id,
        output_dir=Path(args.output_dir),
        schemas_dir=Path(args.schemas_dir),
        warehouse_dir=Path(args.warehouse_dir),
        preferred_resource_name=args.resource_name,
        schema_path=None if args.schema_path is None else Path(args.schema_path),
        vocabulary_index_path=Path(args.vocabulary_index_path),
        delimiter=args.delimiter,
        encoding=args.encoding,
        schema_sample_limit=args.schema_sample_limit,
        keep_materialized=args.keep_materialized,
        register_crosswalks=not args.skip_crosswalks,
    ).to_dict()


def _handle_sync_cig_periods(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `sync-cig-periods` CLI subcommand."""

    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    return DATASET_FAMILY_REGISTRY.update(
        "cig",
        client,
        dataset_id=args.dataset_id,
        output_dir=Path(args.output_dir),
        schemas_dir=Path(args.schemas_dir),
        warehouse_dir=Path(args.warehouse_dir),
        periods=args.period,
        period_start=args.from_period,
        period_end=args.to_period,
        vocabulary_index_path=Path(args.vocabulary_index_path),
        delimiter=args.delimiter,
        encoding=args.encoding,
        schema_sample_limit=args.schema_sample_limit,
        keep_materialized=args.keep_materialized,
        register_crosswalks=not args.skip_crosswalks,
        refresh_changed=args.refresh_changed,
    ).to_dict()


def _handle_compare_schema_files(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `compare-schema-files` CLI subcommand."""

    left_schema = load_schema_mapping(Path(args.left_schema_path))
    right_schema = load_schema_mapping(Path(args.right_schema_path))
    return compare_schema_mappings(left_schema, right_schema)


def _handle_build_vocabulary_crosswalks(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `build-vocabulary-crosswalks` CLI subcommand."""

    dataset_ids = args.dataset_ids or list(VOCABULARY_DATASET_CONFIGS)
    client = CkanClient(
        base_url=args.base_url,
        timeout=args.timeout,
        user_agent=args.user_agent,
        accept_language=args.accept_language,
        referer=args.referer,
        proxy_url=args.proxy_url,
        transport=args.transport,
    )
    return build_vocabulary_crosswalks(
        client,
        dataset_ids=dataset_ids,
        data_dir=Path(args.data_dir),
        schemas_dir=Path(args.schemas_dir),
        output_dir=Path(args.output_dir),
    )


def _handle_build_data_dictionary(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `build-data-dictionary` CLI subcommand."""

    return build_cig_data_dictionary(
        schema_path=Path(args.schema_path),
        comparison_path=Path(args.comparison_path),
        vocabulary_index_path=Path(args.vocabulary_index_path),
        vocabulary_dir=Path(args.vocabulary_dir),
        output_dir=Path(args.output_dir),
    )


def _handle_parse_resource(args: argparse.Namespace) -> dict[str, object] | CommandOutput:
    """@notice Execute the `parse-resource` CLI subcommand."""

    encoding = args.encoding or ("utf-8-sig" if args.format == "csv" else "utf-8")
    if args.format == "csv":
        parsed_resource = parse_csv_resource(
            Path(args.resource_path),
            delimiter=args.delimiter,
            encoding=encoding,
            row_limit=args.record_limit,
        )
        return CommandOutput(
            data=parsed_resource,
            truncated=len(parsed_resource.rows) < parsed_resource.row_count,
        )

    parsed_resource = parse_json_resource(
        Path(args.resource_path),
        encoding=encoding,
        item_limit=args.record_limit,
    )
    item_count = parsed_resource.item_count
    return CommandOutput(
        data=parsed_resource,
        truncated=item_count is not None and len(parsed_resource.sample_items) < item_count,
    )


def _handle_clean_resource(args: argparse.Namespace) -> dict[str, object] | CommandOutput:
    """@notice Execute the `clean-resource` CLI subcommand."""

    encoding = args.encoding or ("utf-8-sig" if args.format == "csv" else "utf-8")
    if args.format == "csv":
        parsed_resource = parse_csv_resource(
            Path(args.resource_path),
            delimiter=args.delimiter,
            encoding=encoding,
            row_limit=args.record_limit,
        )
        schema_mapping = None if args.schema_path is None else load_schema_mapping(Path(args.schema_path))
        return CommandOutput(
            data=clean_csv_resource(parsed_resource, schema_mapping=schema_mapping),
            truncated=len(parsed_resource.rows) < parsed_resource.row_count,
        )

    parsed_resource = parse_json_resource(
        Path(args.resource_path),
        encoding=encoding,
        item_limit=args.record_limit,
    )
    return CommandOutput(data=clean_json_resource(parsed_resource))


def _handle_load_downloaded_resource(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `load-downloaded-resource` CLI subcommand."""

    return load_downloaded_resource(
        Path(args.manifest_path),
        schema_path=None if args.schema_path is None else Path(args.schema_path),
        warehouse_dir=Path(args.warehouse_dir),
        delimiter=args.delimiter,
        encoding=args.encoding,
    ).to_dict()


def _handle_query_local_data(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `query-local-data` CLI subcommand."""

    return run_local_query(
        Path(args.db_path),
        args.sql_query,
        row_limit=args.row_limit,
    ).to_dict()


def _handle_validate_local_data_integrity(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `validate-local-data-integrity` CLI subcommand."""

    return validate_local_data_integrity(
        Path(args.db_path),
        dataset_type=args.dataset_type,
        schema_path=Path(args.schema_path),
        vocabulary_index_path=Path(args.vocabulary_index_path),
    ).to_dict()


if __name__ == "__main__":
    raise SystemExit(main())
