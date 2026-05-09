"""@notice Tests for CKAN metadata parsing."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from anac_explorator.ckan import CkanClient, CkanClientError


class _FakeResponse:
    """@notice Minimal context-manager response used for urlopen mocking."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def read(self) -> bytes:
        """@notice Return the mocked response body."""

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        """@notice Enter the mocked response context."""

        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """@notice Exit the mocked response context without suppression."""

        return False


class _FakeRawResponse:
    """@notice Minimal raw response used to simulate non-JSON endpoint bodies."""

    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        """@notice Return the mocked raw response body."""

        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeRawResponse":
        """@notice Enter the mocked response context."""

        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        """@notice Exit the mocked response context without suppression."""

        return False


class _FakeOpener:
    """@notice Minimal opener used to capture outgoing request metadata."""

    def __init__(self, response) -> None:
        self._response = response
        self.requests = []

    def open(self, request_object, timeout: int):  # noqa: ANN001
        """@notice Record the outgoing request and return the mocked response."""

        self.requests.append((request_object, timeout))
        return self._response


class CkanClientTests(unittest.TestCase):
    """@notice Verify that CKAN payloads are normalized correctly."""

    @patch("anac_explorator.ckan.request.build_opener")
    def test_package_show_parses_resources(self, mock_build_opener) -> None:
        """@notice Parse a valid package_show payload into dataclasses."""

        mock_build_opener.return_value = _FakeOpener(
            _FakeResponse(
                {
                    "success": True,
                    "result": {
                        "id": "pkg-1",
                        "name": "cig-2025",
                        "title": "CIG 2025",
                        "notes": "Sample package",
                        "resources": [
                            {
                                "id": "res-1",
                                "name": "cig_csv_2025_01",
                                "format": "CSV",
                                "url": "https://example.invalid/sample.zip",
                                "size": "12345",
                                "last_modified": "2026-04-26T12:00:00",
                            }
                        ],
                    },
                }
            )
        )

        package = CkanClient().package_show("cig-2025")

        self.assertEqual(package.name, "cig-2025")
        self.assertEqual(len(package.resources), 1)
        self.assertEqual(package.resources[0].size, 12345)

    @patch("anac_explorator.ckan.request.build_opener")
    def test_package_show_rejects_unsuccessful_payloads(self, mock_build_opener) -> None:
        """@notice Surface CKAN failures as client errors."""

        mock_build_opener.return_value = _FakeOpener(_FakeResponse({"success": False}))

        with self.assertRaises(CkanClientError):
            CkanClient().package_show("cig-2025")

    @patch("anac_explorator.ckan.request.build_opener")
    def test_package_show_reports_html_blocks(self, mock_build_opener) -> None:
        """@notice Surface HTML block pages as explicit connectivity failures."""

        mock_build_opener.return_value = _FakeOpener(
            _FakeRawResponse("<html><head><title>Request Rejected</title></head><body>blocked</body></html>")
        )

        with self.assertRaisesRegex(CkanClientError, "blocked or filtered"):
            CkanClient(transport="http").package_show("cig-2025")

    @patch("anac_explorator.ckan.request.ProxyHandler")
    @patch("anac_explorator.ckan.request.build_opener")
    def test_package_show_forwards_proxy_and_headers(self, mock_build_opener, mock_proxy_handler) -> None:
        """@notice Forward proxy configuration and browser-like headers to the opener."""

        fake_opener = _FakeOpener(
            _FakeResponse(
                {
                    "success": True,
                    "result": {
                        "id": "pkg-1",
                        "name": "cig-2025",
                        "title": "CIG 2025",
                        "notes": "",
                        "resources": [],
                    },
                }
            )
        )
        mock_build_opener.return_value = fake_opener

        client = CkanClient(
            proxy_url="http://proxy.internal:8080",
            user_agent="Mozilla/5.0",
            accept_language="it-IT,it;q=0.9",
            referer="https://dati.anticorruzione.it/opendata/",
        )
        client.package_show("cig-2025")

        mock_proxy_handler.assert_called_once_with(
            {
                "http": "http://proxy.internal:8080",
                "https": "http://proxy.internal:8080",
            }
        )
        request_object, timeout = fake_opener.requests[0]
        self.assertEqual(timeout, 30)
        self.assertEqual(request_object.headers["User-agent"], "Mozilla/5.0")
        self.assertEqual(request_object.headers["Accept-language"], "it-IT,it;q=0.9")
        self.assertEqual(request_object.headers["Referer"], "https://dati.anticorruzione.it/opendata/")

    def test_package_show_auto_falls_back_to_playwright_on_html_block(self) -> None:
        """@notice Retry through Playwright when the HTTP transport receives HTML."""

        client = CkanClient(transport="auto")
        browser_payload = json.dumps(
            {
                "success": True,
                "result": {
                    "id": "pkg-1",
                    "name": "cig-2025",
                    "title": "CIG 2025",
                    "notes": "",
                    "resources": [],
                },
            }
        )

        with patch.object(CkanClient, "_get_text_over_http", return_value="<html>blocked</html>"), patch.object(
            CkanClient, "_get_text_via_playwright", return_value=browser_payload
        ) as mock_playwright:
            package = client.package_show("cig-2025")

        self.assertEqual(package.name, "cig-2025")
        mock_playwright.assert_called_once()
