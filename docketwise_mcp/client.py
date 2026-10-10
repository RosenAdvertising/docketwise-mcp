"""Thin asynchronous HTTP client for the documented DocketWise API."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import anyio
import httpx

from docketwise_mcp.config import Settings

MAX_REQUESTS_PER_WINDOW = 120
RATE_LIMIT_WINDOW_SECONDS = 60.0
VENDOR_PAGE_SIZE = 200
MAX_TOOL_LIMIT = 1_000

JsonObject = dict[str, Any]
Sleep = Callable[[float], Awaitable[None]]


class DocketWiseAPIError(RuntimeError):
    """Surface a DocketWise HTTP error, including the vendor's response message."""

    def __init__(self, status_code: int, vendor_message: str) -> None:
        self.status_code = status_code
        self.vendor_message = vendor_message
        super().__init__(f"DocketWise API request failed with HTTP {status_code}: {vendor_message}")


class DocketWiseResponseError(RuntimeError):
    """Report a successful response that does not match the documented shape."""


class AsyncSlidingWindowLimiter:
    """Keep requests within the documented 120 requests per minute limit."""

    def __init__(
        self,
        *,
        maximum: int = MAX_REQUESTS_PER_WINDOW,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
        sleep: Sleep = anyio.sleep,
    ) -> None:
        self._maximum = maximum
        self._window_seconds = window_seconds
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = anyio.Lock()

    async def acquire(self) -> None:
        """Wait until one request slot is available."""

        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self._window_seconds
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._maximum:
                    self._timestamps.append(now)
                    return
                delay = max(0.0, self._timestamps[0] + self._window_seconds - now)
            await self._sleep(delay)


class DocketWiseClient:
    """Call DocketWise with bearer auth, bounded retries, and pagination."""

    def __init__(
        self,
        settings: Settings,
        *,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        timeout_seconds: float = 30.0,
        sleep: Sleep = anyio.sleep,
        limiter: AsyncSlidingWindowLimiter | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._limiter = limiter or AsyncSlidingWindowLimiter(sleep=sleep)
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=settings.base_url,
            headers={
                "Authorization": f"Bearer {settings.access_token}",
                "Accept": "application/json",
                "User-Agent": "docketwise-mcp/0.1.0",
            },
            timeout=timeout_seconds,
        )

    async def __aenter__(self) -> DocketWiseClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    async def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        """Send a GET request and decode its JSON response."""

        return await self._request_json("GET", path, params=params)

    async def post(self, path: str, *, json_body: Mapping[str, Any]) -> Any:
        """Send a JSON POST request and decode its response."""

        return await self._request_json("POST", path, json=json_body)

    async def paginate(
        self,
        path: str,
        *,
        page: int = 1,
        limit: int = VENDOR_PAGE_SIZE,
        params: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        """Collect documented 200-record pages up to a bounded total limit."""

        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= limit <= MAX_TOOL_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_TOOL_LIMIT}")

        items: list[Any] = []
        current_page = page
        pages_fetched = 0
        has_more = False
        next_page: int | None = None
        truncated_within_page = False

        while len(items) < limit:
            query = dict(params or {})
            query["page"] = current_page
            payload = await self.get(path, params=query)
            if not isinstance(payload, list):
                raise DocketWiseResponseError(
                    f"DocketWise returned a non-list response for the {path} index endpoint."
                )

            pages_fetched += 1
            remaining = limit - len(items)
            items.extend(payload[:remaining])

            if len(payload) > remaining:
                has_more = True
                truncated_within_page = True
                break
            if len(payload) < VENDOR_PAGE_SIZE:
                break
            if len(items) >= limit:
                has_more = True
                next_page = current_page + 1
                break
            current_page += 1

        return {
            "items": items,
            "pagination": {
                "start_page": page,
                "pages_fetched": pages_fetched,
                "returned": len(items),
                "limit": limit,
                "has_more": has_more,
                "next_page": next_page,
                "truncated_within_page": truncated_within_page,
            },
        }

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        normalized_path = path.lstrip("/")
        response: httpx.Response | None = None

        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            response = await self._http_client.request(method, normalized_path, **kwargs)
            if response.status_code != 429 and not 500 <= response.status_code <= 599:
                break
            if attempt == self._max_retries:
                break
            await self._sleep(self._retry_delay(response, attempt))

        assert response is not None  # The bounded loop always runs at least once.
        if response.is_error:
            raise DocketWiseAPIError(
                response.status_code,
                self._vendor_error_message(response),
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        if response.status_code == 429:
            retry_header = response.headers.get("Retry-Header")
            if retry_header is not None:
                try:
                    return max(0.0, float(retry_header))
                except ValueError:
                    pass
        return self._backoff_seconds * (2**attempt)

    @staticmethod
    def _vendor_error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or response.reason_phrase

        if isinstance(payload, dict):
            for key in ("message", "error", "errors"):
                if key in payload:
                    value = payload[key]
                    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        return json.dumps(payload, sort_keys=True)
