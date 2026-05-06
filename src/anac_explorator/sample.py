"""@notice Helpers for resolving and downloading CKAN resources.

@dev Phase 1 already needed monthly CIG and vocabulary CSV downloads. Phase 2
extends that baseline with:
1. persistent download manifests
2. smarter local cache reuse
3. restart-safe downloads
4. resumable HTTP downloads when the transport allows it
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from shutil import copyfile
from urllib import request
from zipfile import ZipFile, is_zipfile

from anac_explorator.browser import BrowserFetchError, PlaywrightFetcher
from anac_explorator.ckan import CkanClient, CkanClientError
from anac_explorator.models import (
    CkanResource,
    DownloadManifest,
    DownloadedCsvResource,
    DownloadedResourceArtifact,
)


class SampleDownloadError(RuntimeError):
    """@notice Report a failure while resolving or downloading a resource."""


DownloadedSample = DownloadedCsvResource


def select_resource(
    resource_list: list[CkanResource],
    *,
    preferred_name: str | None = None,
    preferred_format: str = "CSV",
) -> CkanResource:
    """@notice Select a downloadable resource by preferred name or format."""

    expected_format = preferred_format.upper()
    if preferred_name is not None:
        for resource in resource_list:
            if resource.name == preferred_name:
                if not _resource_matches_format(resource, expected_format):
                    raise SampleDownloadError(
                        f"Resource {preferred_name!r} does not match expected format {expected_format!r}."
                    )
                return resource
        raise SampleDownloadError(f"Could not find resource {preferred_name!r}.")

    matching_resources = [
        resource
        for resource in resource_list
        if _resource_matches_format(resource, expected_format)
    ]
    if not matching_resources:
        raise SampleDownloadError(f"Could not find a downloadable {expected_format} resource.")

    matching_resources.sort(key=lambda resource: (resource.name.count("_"), resource.name))
    return matching_resources[0]


def select_csv_resource(
    resource_list: list[CkanResource],
    *,
    preferred_name: str | None = None,
) -> CkanResource:
    """@notice Select a downloadable CSV resource from CKAN package metadata."""

    return select_resource(resource_list, preferred_name=preferred_name, preferred_format="CSV")


def select_cig_monthly_resource(resource_list: list[CkanResource], year: int, month: int) -> CkanResource:
    """@notice Select the monthly CSV CIG resource for a given year and month."""

    expected_name = f"cig_csv_{year}_{month:02d}"
    return select_csv_resource(resource_list, preferred_name=expected_name)


def download_dataset_resource(
    client: CkanClient,
    *,
    dataset_id: str,
    output_dir: str | Path,
    preferred_resource_name: str | None = None,
    preferred_format: str = "CSV",
) -> DownloadedResourceArtifact:
    """@notice Resolve, download, materialize, and manifest one CKAN resource."""

    output_path = Path(output_dir)
    cached = _load_cached_artifact(
        output_path / dataset_id / preferred_resource_name if preferred_resource_name else None,
        expected_format=preferred_format,
    )
    if cached is not None:
        return cached
    legacy_cached = _recover_legacy_cached_artifact(
        output_path / dataset_id / preferred_resource_name if preferred_resource_name else None,
        dataset_id=dataset_id,
        resource_name=preferred_resource_name,
        resource_format=preferred_format.upper(),
        expected_extension=_format_extension(preferred_format),
    )
    if legacy_cached is not None:
        return legacy_cached

    try:
        package = client.package_show(dataset_id)
    except CkanClientError as exc:
        raise SampleDownloadError(str(exc)) from exc

    resource = select_resource(
        package.resources,
        preferred_name=preferred_resource_name,
        preferred_format=preferred_format,
    )
    expected_extension = _format_extension(preferred_format)
    base_output_dir = output_path / dataset_id / resource.name
    cached = _load_cached_artifact(base_output_dir, expected_format=preferred_format, expected_url=resource.url)
    if cached is not None:
        return cached
    legacy_cached = _recover_legacy_cached_artifact(
        base_output_dir,
        dataset_id=dataset_id,
        resource_name=resource.name,
        resource_format=resource.format,
        expected_extension=expected_extension,
        resource_id=resource.id or None,
        resource_url=resource.url,
        source_size=resource.size,
        source_last_modified=resource.last_modified,
    )
    if legacy_cached is not None:
        return legacy_cached

    if resource.url.lower().endswith(".zip"):
        archive_path = base_output_dir / f"{resource.name}.zip"
        cache_status, resume_supported = _download_resource(client, resource.url, archive_path)
        materialized_path = _materialize_resource(archive_path, base_output_dir / "extracted", expected_extension)
        archive_str = str(archive_path)
    else:
        materialized_path = base_output_dir / f"{resource.name}.{expected_extension}"
        cache_status, resume_supported = _download_resource(client, resource.url, materialized_path)
        archive_str = None

    manifest_path = base_output_dir / "manifest.json"
    manifest = DownloadManifest(
        dataset_id=dataset_id,
        resource_id=resource.id or None,
        resource_name=resource.name,
        resource_format=resource.format,
        resource_url=resource.url,
        transport=client.transport,
        archive_path=archive_str,
        materialized_path=str(materialized_path),
        materialized_kind=expected_extension,
        cache_status=cache_status,
        resume_supported=resume_supported,
        source_size=resource.size,
        source_last_modified=resource.last_modified,
        downloaded_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_manifest(manifest, manifest_path)
    return DownloadedResourceArtifact(manifest=manifest, manifest_path=str(manifest_path))


def download_dataset_csv_resource(
    client: CkanClient,
    *,
    dataset_id: str,
    output_dir: str | Path,
    preferred_resource_name: str | None = None,
) -> DownloadedCsvResource:
    """@notice Resolve, download, and materialize one CKAN CSV resource."""

    artifact = download_dataset_resource(
        client,
        dataset_id=dataset_id,
        output_dir=output_dir,
        preferred_resource_name=preferred_resource_name,
        preferred_format="CSV",
    )
    return DownloadedCsvResource(artifact=artifact)


def download_cig_monthly_sample(
    client: CkanClient,
    *,
    year: int,
    month: int,
    output_dir: str | Path,
) -> DownloadedCsvResource:
    """@notice Resolve, download, and extract one monthly CIG CSV sample."""

    dataset_id = f"cig-{year}"
    preferred_resource_name = f"cig_csv_{year}_{month:02d}"
    return download_dataset_csv_resource(
        client,
        dataset_id=dataset_id,
        output_dir=output_dir,
        preferred_resource_name=preferred_resource_name,
    )


def _download_resource(client: CkanClient, url: str, destination: Path) -> tuple[str, bool]:
    """@notice Download a resource using restart-safe or resumable behavior."""

    if client.transport == "http":
        return _download_resource_over_http(client, url, destination)
    return _download_resource_via_playwright(client, url, destination)


def _download_resource_via_playwright(client: CkanClient, url: str, destination: Path) -> tuple[str, bool]:
    """@notice Download a resource through Playwright with restart-safe temp files."""

    partial_path = _partial_path_for(destination)
    cache_status = "fresh"
    if partial_path.exists():
        partial_path.unlink()
        cache_status = "restarted"
    partial_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        PlaywrightFetcher(timeout_ms=client.timeout * 1_000).download_file(
            url,
            partial_path,
            user_agent=client.user_agent,
            accept_language=client.accept_language,
            referer=client.referer,
            proxy_url=client.proxy_url,
        )
    except BrowserFetchError as exc:
        raise SampleDownloadError(f"Failed to download resource via Playwright: {exc}") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path.replace(destination)
    return cache_status, False


def _download_resource_over_http(client: CkanClient, url: str, destination: Path) -> tuple[str, bool]:
    """@notice Download a resource over HTTP with partial-file resume support."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = _partial_path_for(destination)
    resume_offset = partial_path.stat().st_size if partial_path.exists() else 0

    headers = client._request_headers()
    if resume_offset > 0:
        headers["Range"] = f"bytes={resume_offset}-"

    req = request.Request(url, headers=headers, method="GET")
    try:
        with client._build_opener().open(req, timeout=client.timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            if resume_offset > 0 and status_code != 206:
                partial_path.unlink(missing_ok=True)
                return _download_resource_over_http(client, url, destination)

            mode = "ab" if resume_offset > 0 and status_code == 206 else "wb"
            with partial_path.open(mode) as handle:
                handle.write(response.read())
    except OSError as exc:
        raise SampleDownloadError(f"Failed to download resource via HTTP: {exc}") from exc

    partial_path.replace(destination)
    if resume_offset > 0:
        return "resumed", True
    return "fresh", True


def _materialize_resource(download_path: Path, output_dir: Path, expected_extension: str) -> Path:
    """@notice Materialize a working file from a downloaded CKAN resource path."""

    if is_zipfile(download_path):
        return _extract_first_matching_member(download_path, output_dir, expected_extension)

    output_dir.mkdir(parents=True, exist_ok=True)
    materialized_path = output_dir / f"{download_path.stem}.{expected_extension}"
    copyfile(download_path, materialized_path)
    return materialized_path 


def _materialize_csv(download_path: Path, output_dir: Path) -> Path:
    """@notice Materialize a CSV from a downloaded CKAN resource path."""

    return _materialize_resource(download_path, output_dir, "csv")


def _extract_first_matching_member(zip_path: Path, output_dir: Path, expected_extension: str) -> Path:
    """@notice Extract the first member whose extension matches the expected format."""

    output_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as archive:
        matching_members = [
            name for name in archive.namelist() if name.lower().endswith(f".{expected_extension.lower()}")
        ]
        if not matching_members:
            raise SampleDownloadError(f"No .{expected_extension} members were found in {zip_path}.")

        member = matching_members[0]
        extracted_path = output_dir / Path(member).name
        with archive.open(member) as source, extracted_path.open("wb") as target:
            target.write(source.read())
    return extracted_path


def _extract_first_csv(zip_path: Path, output_dir: Path) -> Path:
    """@notice Extract the first CSV payload from a ZIP archive."""

    return _extract_first_matching_member(zip_path, output_dir, "csv")


def _load_cached_artifact(
    base_output_dir: Path | None,
    *,
    expected_format: str,
    expected_url: str | None = None,
) -> DownloadedResourceArtifact | None:
    """@notice Load a cached resource artifact when its manifest and files are intact."""

    if base_output_dir is None:
        return None
    manifest_path = base_output_dir / "manifest.json"
    if not manifest_path.exists():
        return None

    manifest = DownloadManifest.from_dict(_load_json_object(manifest_path))
    if manifest.materialized_kind.casefold() != _format_extension(expected_format):
        return None
    if expected_url is not None and manifest.resource_url != expected_url:
        return None
    if not Path(manifest.materialized_path).exists():
        return None
    if manifest.archive_path is not None and not Path(manifest.archive_path).exists():
        return None

    manifest.cache_status = "cache_hit"
    return DownloadedResourceArtifact(manifest=manifest, manifest_path=str(manifest_path))


def _recover_legacy_cached_artifact(
    base_output_dir: Path | None,
    *,
    dataset_id: str,
    resource_name: str | None,
    resource_format: str,
    expected_extension: str,
    resource_id: str | None = None,
    resource_url: str = "",
    source_size: int | None = None,
    source_last_modified: str | None = None,
) -> DownloadedResourceArtifact | None:
    """@notice Adopt the Phase 1 cache layout by writing a manifest around existing files."""

    if base_output_dir is None or resource_name is None:
        return None

    extracted_path = base_output_dir / "extracted" / f"{resource_name}.{expected_extension}"
    direct_path = base_output_dir / f"{resource_name}.{expected_extension}"
    archive_path = base_output_dir / f"{resource_name}.zip"

    if extracted_path.exists():
        materialized_path = extracted_path
    elif direct_path.exists():
        materialized_path = direct_path
    else:
        return None

    manifest = DownloadManifest(
        dataset_id=dataset_id,
        resource_id=resource_id,
        resource_name=resource_name,
        resource_format=resource_format,
        resource_url=resource_url,
        transport="cache",
        archive_path=str(archive_path) if archive_path.exists() else None,
        materialized_path=str(materialized_path),
        materialized_kind=expected_extension,
        cache_status="legacy_cache_hit",
        resume_supported=False,
        source_size=source_size,
        source_last_modified=source_last_modified,
        downloaded_at=datetime.now(timezone.utc).isoformat(),
    )
    manifest_path = base_output_dir / "manifest.json"
    _write_manifest(manifest, manifest_path)
    return DownloadedResourceArtifact(manifest=manifest, manifest_path=str(manifest_path))


def _write_manifest(manifest: DownloadManifest, manifest_path: Path) -> None:
    """@notice Persist a download manifest next to the materialized resource."""

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, object]:
    """@notice Load a JSON object from disk and fail loudly on invalid shapes."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object at {path}.")
    return payload


def _partial_path_for(destination: Path) -> Path:
    """@notice Build the temp-file path used for restart-safe downloads."""

    return destination.with_suffix(destination.suffix + ".part")


def _resource_matches_format(resource: CkanResource, expected_format: str) -> bool:
    """@notice Return whether a CKAN resource matches the expected high-level format."""

    normalized_format = resource.format.upper()
    if expected_format == "CSV":
        return normalized_format == "CSV" and "logcsv" not in resource.name.lower()
    if expected_format == "JSON":
        return "JSON" in normalized_format
    return normalized_format == expected_format


def _format_extension(resource_format: str) -> str:
    """@notice Convert a CKAN format label into a working file extension."""

    normalized = resource_format.casefold()
    if normalized == "csv":
        return "csv"
    if "json" in normalized:
        return "json"
    raise ValueError(f"Unsupported resource format {resource_format!r}.")
