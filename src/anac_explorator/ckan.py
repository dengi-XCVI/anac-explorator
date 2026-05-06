"""@notice CKAN client primitives for live ANAC dataset discovery.

@dev This client now powers the completed Phase 1 workflow: it resolves live
CKAN metadata and falls back to Playwright transport when direct HTTP requests
are rejected by the ANAC WAF.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import parse, request

from anac_explorator.models import CkanPackage, CkanResource

DEFAULT_CKAN_BASE_URL = "https://dati.anticorruzione.it/opendata/api/3/action"
DEFAULT_USER_AGENT = "anac-explorator/0.1.0 (+https://github.com/dengi-XCVI/anac-explorator)"
DEFAULT_ACCEPT_LANGUAGE = "it-IT,it;q=0.9,en;q=0.8"
DEFAULT_REFERER = "https://dati.anticorruzione.it/opendata/"
DEFAULT_TRANSPORT = "auto"


class CkanClientError(RuntimeError):
    """@notice Report a CKAN request or response failure."""


@dataclass(slots=True)
class CkanClient:
    """@notice Minimal CKAN API client for ANAC metadata access.

    @param base_url CKAN action API base URL.
    @param timeout Request timeout in seconds.
    @param user_agent User-Agent header sent to the remote API.
    @param accept_language Accept-Language header used to look more like a regional browser.
    @param referer Referer header used for portal-style request flows.
    @param proxy_url Optional HTTP(S) proxy URL used to route requests through an alternate network path.
    @param transport Request transport strategy: `http`, `playwright`, or `auto`.
    """

    base_url: str = DEFAULT_CKAN_BASE_URL
    timeout: int = 30
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE
    referer: str = DEFAULT_REFERER
    proxy_url: str | None = None
    transport: str = DEFAULT_TRANSPORT

    def package_show(self, dataset_id: str) -> CkanPackage:
        """@notice Fetch a CKAN package and normalize the relevant fields.

        @param dataset_id Dataset slug or package identifier.
        @return Normalized package metadata with attached resources.
        @raises CkanClientError If the HTTP request fails or CKAN reports failure.
        """

        endpoint = f"{self.base_url.rstrip('/')}/package_show"
        query_string = parse.urlencode({"id": dataset_id})
        payload = self._get_json(f"{endpoint}?{query_string}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise CkanClientError("CKAN response did not contain a valid result object.")

        raw_resources = result.get("resources", [])
        if not isinstance(raw_resources, list):
            raise CkanClientError("CKAN response contained an invalid resources collection.")

        resources = [self._parse_resource(resource) for resource in raw_resources]
        return CkanPackage(
            id=str(result.get("id", "")),
            name=str(result.get("name", dataset_id)),
            title=str(result.get("title", dataset_id)),
            notes=str(result.get("notes", "")),
            resources=resources,
        )

    def _get_json(self, url: str) -> dict[str, Any]:
        """@notice Perform a JSON GET request against the CKAN API.

        @param url Fully qualified request URL.
        @return Parsed JSON response body.
        @raises CkanClientError If the request fails or the payload is invalid.
        """

        body = self._get_text(url)
        return self._decode_json_body(body)

    def _get_text(self, url: str) -> str:
        """@notice Fetch raw response text using the configured transport."""

        if self.transport == "playwright":
            return self._get_text_via_playwright(url)

        body = self._get_text_over_http(url)
        if self.transport == "auto" and _looks_like_html(body):
            return self._get_text_via_playwright(url)
        return body

    def _get_text_over_http(self, url: str) -> str:
        """@notice Fetch raw response text with the standard HTTP client."""

        req = request.Request(
            url,
            headers=self._request_headers(),
            method="GET",
        )
        try:
            with self._build_opener().open(req, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except OSError as exc:
            raise CkanClientError(f"Failed to reach CKAN endpoint: {exc}") from exc

    def _get_text_via_playwright(self, url: str) -> str:
        """@notice Fetch raw response text through a headless Playwright browser."""

        from anac_explorator.browser import BrowserFetchError, PlaywrightFetcher

        try:
            return PlaywrightFetcher(timeout_ms=self.timeout * 1_000).fetch_text(
                url,
                user_agent=self.user_agent,
                accept_language=self.accept_language,
                referer=self.referer,
                proxy_url=self.proxy_url,
            )
        except BrowserFetchError as exc:
            raise CkanClientError(str(exc)) from exc

    def _decode_json_body(self, body: str) -> dict[str, Any]:
        """@notice Decode a CKAN JSON payload or surface a useful failure."""

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            body_preview = " ".join(body.split())[:160]
            if _looks_like_html(body):
                raise CkanClientError(
                    "CKAN endpoint returned HTML instead of JSON; remote access appears to be blocked "
                    f"or filtered. Response preview: {body_preview}"
                ) from exc
            raise CkanClientError(
                f"CKAN endpoint returned invalid JSON. Response preview: {body_preview}"
            ) from exc

        if payload.get("success") is not True:
            raise CkanClientError(f"CKAN request failed: {payload!r}")
        return payload

    def _request_headers(self) -> dict[str, str]:
        """@notice Build the base HTTP headers for CKAN requests."""

        return {
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            "Referer": self.referer,
        }

    def _build_opener(self) -> request.OpenerDirector:
        """@notice Build the HTTP opener used for CKAN requests.

        @dev Proxy support is the main escape hatch when the default runtime IP
        is blocked by the remote endpoint or its WAF.
        """

        handlers: list[Any] = []
        if self.proxy_url:
            handlers.append(
                request.ProxyHandler(
                    {
                        "http": self.proxy_url,
                        "https": self.proxy_url,
                    }
                )
            )
        return request.build_opener(*handlers)

    @staticmethod
    def _parse_resource(resource: Any) -> CkanResource:
        """@notice Normalize a raw CKAN resource object.

        @param resource Raw resource dictionary from CKAN.
        @return Parsed resource metadata.
        @raises CkanClientError If the input object is not a dictionary.
        """

        if not isinstance(resource, dict):
            raise CkanClientError("CKAN resource entry was not a dictionary.")

        return CkanResource(
            id=str(resource.get("id", "")),
            name=str(resource.get("name", "")),
            format=str(resource.get("format", "")),
            url=str(resource.get("url", "")),
            size=_coerce_int(resource.get("size")),
            last_modified=_coerce_optional_str(resource.get("last_modified")),
            description=str(resource.get("description", "")),
        )


def _coerce_int(value: Any) -> int | None:
    """@notice Best-effort integer coercion for CKAN size fields."""

    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError): 
        return None


def _coerce_optional_str(value: Any) -> str | None:
    """@notice Normalize nullable string fields from CKAN."""

    if value in (None, ""):
        return None
    return str(value)


def _looks_like_html(body: str) -> bool:
    """@notice Detect HTML responses that indicate a WAF or portal page."""

    return "<html" in body.lower()
