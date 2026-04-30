"""@notice Helpers for resolving and downloading monthly CIG sample files.

@dev This module implements the completed Phase 1 sample workflow: choose one
monthly CIG CSV resource from live CKAN metadata, download its ZIP archive,
and extract the CSV payload locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib import request
from zipfile import ZipFile

from anac_explorator.browser import BrowserFetchError, PlaywrightFetcher
from anac_explorator.ckan import CkanClient, CkanClientError
from anac_explorator.models import CkanResource


class SampleDownloadError(RuntimeError):
    """@notice Report a failure while resolving or downloading a sample."""


@dataclass(slots=True)
class DownloadedSample:
    """@notice Capture the local outputs of one downloaded monthly CIG sample.

    @param dataset_id CKAN dataset identifier used for resolution.
    @param resource_name CKAN resource name that was selected.
    @param resource_url Direct resource URL.
    @param zip_path Local path of the downloaded ZIP archive.
    @param csv_path Local path of the extracted CSV file.
    """

    dataset_id: str
    resource_name: str
    resource_url: str
    zip_path: str
    csv_path: str

    def to_dict(self) -> dict[str, str]:
        """@notice Convert the downloaded sample to a JSON-serializable dictionary."""

        return {
            "dataset_id": self.dataset_id,
            "resource_name": self.resource_name,
            "resource_url": self.resource_url,
            "zip_path": self.zip_path,
            "csv_path": self.csv_path,
        }


def select_cig_monthly_resource(resource_list: list[CkanResource], year: int, month: int) -> CkanResource:
    """@notice Select the monthly CSV CIG resource for a given year and month.

    @param resource_list Resource list from the CKAN dataset metadata.
    @param year Dataset year.
    @param month Dataset month between 1 and 12.
    @return Matching CSV resource.
    @raises SampleDownloadError If the monthly resource is not present.
    """

    expected_name = f"cig_csv_{year}_{month:02d}"
    for resource in resource_list:
        if resource.name == expected_name:
            return resource
    raise SampleDownloadError(f"Could not find monthly resource {expected_name!r}.")


def download_cig_monthly_sample(
    client: CkanClient,
    *,
    year: int,
    month: int,
    output_dir: str | Path,
) -> DownloadedSample:
    """@notice Resolve, download, and extract one monthly CIG CSV sample.

    @param client Configured CKAN client used for dataset metadata resolution.
    @param year Dataset year.
    @param month Dataset month between 1 and 12.
    @param output_dir Base output directory for the archive and extracted CSV.
    @return Local artifact paths for the downloaded sample.
    @raises SampleDownloadError If metadata, download, or extraction fails.
    """

    dataset_id = f"cig-{year}"
    try:
        package = client.package_show(dataset_id)
    except CkanClientError as exc:
        raise SampleDownloadError(str(exc)) from exc

    resource = select_cig_monthly_resource(package.resources, year, month)
    base_output_dir = Path(output_dir) / dataset_id / resource.name
    zip_path = base_output_dir / f"{resource.name}.zip"

    _download_resource(client, resource.url, zip_path)
    csv_path = _extract_first_csv(zip_path, base_output_dir / "extracted")
    return DownloadedSample(
        dataset_id=dataset_id,
        resource_name=resource.name,
        resource_url=resource.url,
        zip_path=str(zip_path),
        csv_path=str(csv_path),
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
