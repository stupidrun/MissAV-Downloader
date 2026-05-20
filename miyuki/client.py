"""HTTP client with anti-bot bypass (TLS fingerprint impersonation via curl_cffi)."""

import logging
import time

from curl_cffi.requests import Session

logger = logging.getLogger("miyuki")

MISSAV_DOMAIN = "https://missav.live"
COVER_URL_PREFIX = "https://fourhoi.com/"
VIDEO_M3U8_PREFIX = "https://surrit.com/"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": f"{MISSAV_DOMAIN}/",
}

DEFAULT_RETRY = 5
DEFAULT_DELAY = 2
DEFAULT_TIMEOUT = 10


class MiyukiClient:
    """HTTP client that handles TLS fingerprint impersonation and retry logic."""

    def __init__(self, proxy: str | None = None):
        self.session = Session(
            impersonate="chrome131",
            verify=False,
            headers=DEFAULT_HEADERS,
        )
        if proxy:
            self.session.proxies = {
                "http": f"http://{proxy}",
                "https": f"http://{proxy}",
            }

    def get(self, url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> "Response":
        """Single GET request."""
        return self.session.get(url=url, timeout=timeout, **kwargs)

    def post(self, url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> "Response":
        """Single POST request."""
        return self.session.post(url=url, timeout=timeout, **kwargs)

    def get_with_retry(
        self,
        url: str,
        retry: int = DEFAULT_RETRY,
        delay: int = DEFAULT_DELAY,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> bytes | None:
        """GET request with retry, returns response content bytes or None on failure."""
        for attempt in range(retry):
            try:
                response = self.session.get(url=url, timeout=timeout)
                return response.content
            except Exception:
                if attempt < retry - 1:
                    time.sleep(delay)
        return None
