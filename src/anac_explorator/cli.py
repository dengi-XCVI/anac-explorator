"""@notice Command-line entry points for the ANAC explorator project.

@dev The current CLI covers the completed Phase 1 workflow:
1. query live CKAN package metadata
2. download and extract one monthly CIG CSV sample
3. inspect a local CSV and map its schema
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
from anac_explorator.sample import SampleDownloadError, download_cig_monthly_sample
from anac_explorator.schema import map_csv_schema


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


def _handle_compare_schema_files(args: argparse.Namespace) -> dict[str, object]:
    """@notice Execute the `compare-schema-files` CLI subcommand."""

    left_schema = load_schema_mapping(Path(args.left_schema_path))
    right_schema = load_schema_mapping(Path(args.right_schema_path))
    return compare_schema_mappings(left_schema, right_schema)
