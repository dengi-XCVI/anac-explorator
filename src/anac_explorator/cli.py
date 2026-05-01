"""@notice Command-line entry points for the ANAC explorator project.

@dev The current CLI covers the completed Phase 1 workflow:
1. query live CKAN package metadata
2. download and extract one CKAN CSV resource
3. inspect a local CSV and map its schema
4. compare schema artifacts
5. build normalized vocabulary cross-reference tables
6. build a structured field dictionary for the January 2025 CIG schema
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from anac_explorator.ckan import (
    CkanClient,
    CkanClientError,
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_CKAN_BASE_URL,
    DEFAULT_REFERER,
    DEFAULT_TRANSPORT,
    DEFAULT_USER_AGENT,
)
from anac_explorator.comparison import compare_schema_mappings, load_schema_mapping
from anac_explorator.dictionary import build_cig_data_dictionary
from anac_explorator.sample import (
    SampleDownloadError,
    download_cig_monthly_sample,
    download_dataset_csv_resource,
)
from anac_explorator.schema import map_csv_schema
from anac_explorator.vocabulary import VOCABULARY_DATASET_CONFIGS, build_vocabulary_crosswalks


def build_parser() -> argparse.ArgumentParser:
    """@notice Construct the top-level CLI parser."""

    parser = argparse.ArgumentParser(prog="anac-explorator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    package_show = subparsers.add_parser(
        "package-show",
        help="Fetch CKAN metadata for one dataset identifier.",
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
        help="Resolve one dataset CSV resource, download it, and materialize the CSV locally.",
    )
    download_dataset_csv.add_argument("dataset_id", help="CKAN dataset slug to resolve.")
    download_dataset_csv.add_argument(
        "--resource-name",
        help="Exact CKAN resource name to choose when a dataset exposes multiple CSV resources.",
    )
    download_dataset_csv.add_argument(
        "--output-dir",
        default="data/raw",
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

    download_cig_sample = subparsers.add_parser(
        "download-cig-sample",
        help="Resolve one monthly CIG CSV resource, download it, and extract the CSV.",
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
        default="data/raw",
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
        help="Compare two schema JSON artifacts and report differences.",
    )
    compare_schema_files.add_argument("left_schema_path", help="Path to the left schema JSON file.")
    compare_schema_files.add_argument("right_schema_path", help="Path to the right schema JSON file.")
    compare_schema_files.set_defaults(handler=_handle_compare_schema_files)

    build_vocabularies = subparsers.add_parser(
        "build-vocabulary-crosswalks",
        help="Download configured vocabulary datasets and emit normalized cross-reference tables.",
    )
    build_vocabularies.add_argument(
        "dataset_ids",
        nargs="*",
        help="Optional subset of configured vocabulary dataset slugs to build.",
    )
    build_vocabularies.add_argument(
        "--data-dir",
        default="data/raw",
        help="Base directory used for downloaded raw dataset files.",
    )
    build_vocabularies.add_argument(
        "--schemas-dir",
        default="schemas",
        help="Directory used for raw schema artifacts.",
    )
    build_vocabularies.add_argument(
        "--output-dir",
        default="vocabularies",
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
        help="Build the January 2025 CIG data dictionary from schema and vocabulary artifacts.",
    )
    build_data_dictionary.add_argument(
        "--schema-path",
        default="schemas/cig_2025_01.schema.json",
        help="Source schema artifact for the current CIG surface.",
    )
    build_data_dictionary.add_argument(
        "--comparison-path",
        default="schemas/cig_2007_01_vs_cig_2025_01.comparison.json",
        help="Cross-year comparison artifact used for field notes.",
    )
    build_data_dictionary.add_argument(
        "--vocabulary-index-path",
        default="vocabularies/index.json",
        help="Vocabulary index artifact used for code-meaning links and gaps.",
    )
    build_data_dictionary.add_argument(
        "--vocabulary-dir",
        default="vocabularies",
        help="Directory containing generated vocabulary artifacts.",
    )
    build_data_dictionary.add_argument(
        "--output-dir",
        default="dictionaries",
        help="Directory used for generated data-dictionary artifacts.",
    )
    build_data_dictionary.set_defaults(handler=_handle_build_data_dictionary)

    inspect_csv_schema = subparsers.add_parser(
        "inspect-csv-schema",
        help="Inspect a local CSV file and emit a schema mapping as JSON.",
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """@notice Execute the CLI with the provided argument vector.

    @param argv Optional explicit argument vector.
    @return Process exit code.
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        payload = args.handler(args)
    except (CkanClientError, SampleDownloadError) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")
    except (FileNotFoundError, UnicodeDecodeError, ValueError) as exc:
        parser.exit(status=1, message=f"error: {exc}\n")

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


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
        year=args.year,
        month=args.month,
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
