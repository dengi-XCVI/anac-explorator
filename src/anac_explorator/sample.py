"""@notice Helpers for resolving and downloading CKAN CSV resources.

@dev This module now supports both completed Phase 1 workflows:
1. monthly CIG sample download and extraction
2. reference-data CSV download for controlled vocabulary datasets
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shutil import copyfile
from urllib import request
from zipfile import ZipFile, is_zipfile

from anac_explorator.browser import BrowserFetchError, PlaywrightFetcher
from anac_explorator.ckan import CkanClient, CkanClientError
from anac_explorator.models import CkanResource


class SampleDownloadError(RuntimeError):
    """@notice Report a failure while resolving or downloading a sample."""


@dataclass(slots=True)
class DownloadedCsvResource:
    """@notice Capture the local outputs of one downloaded CKAN CSV resource.

    @param dataset_id CKAN dataset identifier used for resolution.
    @param resource_name CKAN resource name that was selected.
    @param resource_url Direct resource URL.
    @param archive_path Local path of the downloaded ZIP archive, when applicable.
    @param csv_path Local path of the extracted CSV file.
    """

    dataset_id: str
    resource_name: str
    resource_url: str
    archive_path: str | None
    csv_path: str

    def to_dict(self) -> dict[str, str]:
        """@notice Convert the downloaded resource to a JSON-serializable dictionary."""

        payload = {
            "dataset_id": self.dataset_id,
            "resource_name": self.resource_name,
            "resource_url": self.resource_url,
            "csv_path": self.csv_path,
        }
        if self.archive_path is not None:
            payload["archive_path"] = self.archive_path
            payload["zip_path"] = self.archive_path
        return payload


DownloadedSample = DownloadedCsvResource


def select_csv_resource(
    resource_list: list[CkanResource],
    *,
    preferred_name: str | None = None,
) -> CkanResource:
    """@notice Select a downloadable CSV resource from CKAN package metadata.

    @param resource_list Resource list from the CKAN dataset metadata.
    @param preferred_name Exact resource name to prefer when present.
    @return Matching CSV resource.
    @raises SampleDownloadError If no suitable CSV resource is present.
    """

    if preferred_name is not None:
        for resource in resource_list:
            if resource.name == preferred_name:
                return resource
        raise SampleDownloadError(f"Could not find CSV resource {preferred_name!r}.")

    csv_resources = [
        resource
        for resource in resource_list
        if resource.format.upper() == "CSV" and "logcsv" not in resource.name.lower()
    ]
    if not csv_resources:
        raise SampleDownloadError("Could not find a downloadable CSV resource.")

    csv_resources.sort(key=lambda resource: (resource.name.count("_"), resource.name))
    return csv_resources[0]


def select_cig_monthly_resource(resource_list: list[CkanResource], year: int, month: int) -> CkanResource:
    """@notice Select the monthly CSV CIG resource for a given year and month.

    @param resource_list Resource list from the CKAN dataset metadata.
    @param year Dataset year.
    @param month Dataset month between 1 and 12.
    @return Matching CSV resource.
    @raises SampleDownloadError If the monthly resource is not present.
    """

    expected_name = f"cig_csv_{year}_{month:02d}"
    return select_csv_resource(resource_list, preferred_name=expected_name)


def download_dataset_csv_resource(
    client: CkanClient,
    *,
    dataset_id: str,
    output_dir: str | Path,
    preferred_resource_name: str | None = None,
) -> DownloadedCsvResource:
    """@notice Resolve, download, and materialize one CKAN CSV resource.

    @param client Configured CKAN client used for dataset metadata resolution.
    @param dataset_id CKAN dataset slug.
    @param output_dir Base output directory for the downloaded archive and CSV.
    @param preferred_resource_name Exact resource name to choose when the dataset has multiple CSV resources.
    @return Local artifact paths for the downloaded CSV resource.
    @raises SampleDownloadError If metadata, download, or extraction fails.
    """

    try:
        package = client.package_show(dataset_id)
    except CkanClientError as exc:
        raise SampleDownloadError(str(exc)) from exc

    resource = select_csv_resource(package.resources, preferred_name=preferred_resource_name)
    base_output_dir = Path(output_dir) / dataset_id / resource.name

    if resource.url.lower().endswith(".zip"):
        archive_path = base_output_dir / f"{resource.name}.zip"
        extracted_dir = base_output_dir / "extracted"
        cached_csv_path = extracted_dir / f"{resource.name}.csv"
        if cached_csv_path.exists():
            return DownloadedCsvResource(
                dataset_id=dataset_id,
                resource_name=resource.name,
                resource_url=resource.url,
                archive_path=str(archive_path) if archive_path.exists() else None,
                csv_path=str(cached_csv_path),
            )
        _download_resource(client, resource.url, archive_path)
        csv_path = _materialize_csv(archive_path, extracted_dir)
        return DownloadedCsvResource(
            dataset_id=dataset_id,
            resource_name=resource.name,
            resource_url=resource.url,
            archive_path=str(archive_path),
            csv_path=str(csv_path),
        )

    csv_path = base_output_dir / f"{resource.name}.csv"
    if csv_path.exists():
        return DownloadedCsvResource(
            dataset_id=dataset_id,
            resource_name=resource.name,
            resource_url=resource.url,
            archive_path=None,
            csv_path=str(csv_path),
        )
    _download_resource(client, resource.url, csv_path)
    return DownloadedCsvResource(
        dataset_id=dataset_id,
        resource_name=resource.name,
        resource_url=resource.url,
        archive_path=None,
        csv_path=str(csv_path),
    )


def download_cig_monthly_sample(
    client: CkanClient,
    *,
    year: int,
    month: int,
    output_dir: str | Path,
) -> DownloadedCsvResource:
    """@notice Resolve, download, and extract one monthly CIG CSV sample.

    @param client Configured CKAN client used for dataset metadata resolution.
    @param year Dataset year.
    @param month Dataset month between 1 and 12.
    @param output_dir Base output directory for the archive and extracted CSV.
    @return Local artifact paths for the downloaded sample.
    @raises SampleDownloadError If metadata, download, or extraction fails.
    """

    dataset_id = f"cig-{year}"
    preferred_resource_name = f"cig_csv_{year}_{month:02d}"
    return download_dataset_csv_resource(
        client,
        dataset_id=dataset_id,
        output_dir=output_dir,
        preferred_resource_name=preferred_resource_name,
    )


def _download_resource(client: CkanClient, url: str, destination: Path) -> None:
    """@notice Download a sample resource with the client's configured transport."""

    if client.transport in {"auto", "playwright"}:
        try:
            PlaywrightFetcher(timeout_ms=client.timeout * 1_000).download_file(
                url,
                destination,
                user_agent=client.user_agent,
                accept_language=client.accept_language,
                referer=client.referer,
                proxy_url=client.proxy_url,
            )
            return
        except BrowserFetchError as exc:
            raise SampleDownloadError(f"Failed to download resource via Playwright: {exc}") from exc

    req = request.Request(url, headers=client._request_headers(), method="GET")
    try:
        with client._build_opener().open(req, timeout=client.timeout) as response:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.read())
    except OSError as exc:
        raise SampleDownloadError(f"Failed to download resource via HTTP: {exc}") from exc


def _materialize_csv(download_path: Path, output_dir: Path) -> Path:
    """@notice Materialize a CSV from a downloaded CKAN resource path.

    @dev Some CKAN resources are named like ZIP archives but actually contain a
    plain CSV body. This helper handles both the real ZIP case and the mislabeled
    plain-CSV case using the same output convention.
    """

    if is_zipfile(download_path):
        return _extract_first_csv(download_path, output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{download_path.stem}.csv"
    copyfile(download_path, csv_path)
    return csv_path


def _extract_first_csv(zip_path: Path, output_dir: Path) -> Path:
    """@notice Extract the first CSV member from a ZIP archive."""

    output_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as archive:
        csv_members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_members:
            raise SampleDownloadError(f"No CSV members were found in {zip_path}.")

        member = csv_members[0]
        extracted_path = output_dir / Path(member).name
        with archive.open(member) as source, extracted_path.open("wb") as target:
            target.write(source.read())
    return extracted_path
