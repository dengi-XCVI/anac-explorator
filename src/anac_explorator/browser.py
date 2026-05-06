"""@notice Playwright-backed network helpers for WAF-protected endpoints.

@dev This module is the working access path for ANAC from this runtime. It is
imported lazily so the rest of the package remains usable even when browser
automation is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass


class BrowserFetchError(RuntimeError):
    """@notice Report a browser-backed fetch failure."""


@dataclass(slots=True)
class PlaywrightFetcher:
    """@notice Fetch remote content through a headless Playwright browser.

    @param timeout_ms Page and fetch timeout in milliseconds.
    @param headless Whether Chromium should run in headless mode.
    """

    timeout_ms: int = 30_000
    headless: bool = True

    def fetch_text(
        self,
        url: str,
        *,
        user_agent: str,
        accept_language: str,
        referer: str,
        proxy_url: str | None = None,
    ) -> str:
        """@notice Fetch a URL after letting the browser satisfy JS/WAF checks.

        @param url Target URL to fetch.
        @param user_agent Browser user agent for the Chromium context.
        @param accept_language Accept-Language header and browser locale hint.
        @param referer Portal entry page opened before the target fetch.
        @param proxy_url Optional proxy server used by Chromium.
        @return Response body text fetched from the browser page context.
        @raises BrowserFetchError If Playwright is unavailable or the browser flow fails.
        """

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise BrowserFetchError(
                "Playwright is not installed. Install the package and browser runtime first."
            ) from exc

        launch_options: dict[str, object] = {"headless": self.headless}
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(
                    user_agent=user_agent,
                    locale=_locale_from_accept_language(accept_language),
                    extra_http_headers={
                        "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
                        "Accept-Language": accept_language,
                    },
                )
                page = context.new_page()
                page.goto(referer, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(3_000)

                result = page.evaluate(
                    """
                    async ({ targetUrl, acceptLanguage }) => {
                      const response = await fetch(targetUrl, {
                        method: 'GET',
                        credentials: 'include',
                        headers: {
                          'Accept': 'application/json, text/plain;q=0.9, */*;q=0.8',
                          'Accept-Language': acceptLanguage
                        }
                      });
                      return {
                        status: response.status,
                        contentType: response.headers.get('content-type'),
                        body: await response.text()
                      };
                    }
                    """,
                    {
                        "targetUrl": url,
                        "acceptLanguage": accept_language,
                    },
                )
                browser.close()
        except PlaywrightTimeoutError as exc:
            raise BrowserFetchError(f"Playwright timed out while loading {referer}.") from exc
        except PlaywrightError as exc:
            raise BrowserFetchError(f"Playwright failed while fetching {url}: {exc}") from exc

        if not isinstance(result, dict) or "body" not in result:
            raise BrowserFetchError("Playwright did not return a valid response payload.")
        return str(result["body"])

    def download_file(
        self,
        url: str,
        destination: str | Path,
        *,
        user_agent: str,
        accept_language: str,
        referer: str,
        proxy_url: str | None = None,
    ) -> Path:
        """@notice Download a binary resource through a headless Playwright browser.

        @param url Target file URL.
        @param destination Local destination path for the downloaded file.
        @param user_agent Browser user agent for the Chromium context.
        @param accept_language Accept-Language header and browser locale hint.
        @param referer Portal entry page opened before the file request.
        @param proxy_url Optional proxy server used by Chromium.
        @return Final destination path of the downloaded file.
        @raises BrowserFetchError If Playwright cannot retrieve the file.
        """

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise BrowserFetchError(
                "Playwright is not installed. Install the package and browser runtime first."
            ) from exc

        launch_options: dict[str, object] = {"headless": self.headless}
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}

        output_path = Path(destination)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(
                    accept_downloads=True,
                    user_agent=user_agent,
                    locale=_locale_from_accept_language(accept_language),
                    extra_http_headers={
                        "Accept": "application/octet-stream,application/zip,*/*;q=0.8",
                        "Accept-Language": accept_language,
                    },
                )
                page = context.new_page()
                page.goto(referer, wait_until="domcontentloaded", timeout=self.timeout_ms)
                page.wait_for_timeout(3_000)

                try:
                    with page.expect_download(timeout=self.timeout_ms) as download_info:
                        page.evaluate("(targetUrl) => { window.location.href = targetUrl; }", url)
                    download = download_info.value
                    download.save_as(str(output_path))
                except PlaywrightTimeoutError:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                    if response is None:
                        raise BrowserFetchError(f"Playwright did not receive a response for {url}.")
                    output_path.write_bytes(response.body())
                browser.close()
        except PlaywrightTimeoutError as exc:
            raise BrowserFetchError(f"Playwright timed out while downloading {url}.") from exc
        except PlaywrightError as exc:
            raise BrowserFetchError(f"Playwright failed while downloading {url}: {exc}") from exc

        return output_path 


def _locale_from_accept_language(accept_language: str) -> str:
    """@notice Derive a browser locale from an Accept-Language header."""

    first_language = accept_language.split(",")[0].strip()
    return first_language or "it-IT"
