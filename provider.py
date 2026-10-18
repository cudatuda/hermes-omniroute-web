"""OmniRoute web search and extraction provider for Hermes.

The provider uses OmniRoute's HTTP API and adapts its response envelopes to the
public ``WebSearchProvider`` contract.  No Hermes private helper modules are
imported here: only ``agent.web_search_provider.WebSearchProvider`` is used.

Endpoints:

* ``POST /api/v1/search``
* ``POST /api/v1/web/fetch``

OmniRoute keeps upstream provider credentials and performs its own provider
selection/fallback.  This plugin only needs the gateway URL and API key.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agent.web_search_provider import WebSearchProvider, get_provider_env

logger = logging.getLogger(__name__)

_SEARCH_LIMIT_CAP = 100
_SEARCH_DESCRIPTION_CAP = 500


def _env(name: str) -> str:
    """Read the process environment without making a network request."""
    return get_provider_env(name)


def _configured_provider(name: str) -> str:
    """Return an optional provider pin, allowing OmniRoute to auto-select otherwise."""
    return _env(name)


def _gateway_root() -> str:
    """Return OmniRoute's origin from a URL optionally ending in ``/v1``.

    OmniRoute's current web endpoints live under ``/api/v1`` while its model
    endpoint is commonly configured as ``.../v1``. Accept both ``.../v1`` and
    ``.../api/v1`` without producing a duplicated path.
    """
    url = _env("OMNIROUTE_API_URL").rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


def _api_url(path: str) -> str:
    return f"{_gateway_root()}/api/v1/{path.lstrip('/')}"


def _request_timeout(operation: str) -> float:
    """Keep search/fetch responsive while allowing slow page extraction."""
    return 60.0 if operation == "fetch" else 30.0


def _error_message(response: httpx.Response, operation: str) -> str:
    """Build a useful, bounded error without echoing credentials."""
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("type") or "")
            elif error:
                detail = str(error)
            elif payload.get("message"):
                detail = str(payload["message"])
    except (ValueError, TypeError):
        pass
    if not detail:
        detail = response.text.strip()[:300]
    suffix = f": {detail}" if detail else ""
    return f"OmniRoute {operation} returned HTTP {response.status_code}{suffix}"


def _post(path: str, payload: dict[str, Any], operation: str) -> dict[str, Any]:
    """POST JSON to OmniRoute and return a mapping response.

    ``httpx`` exceptions are intentionally converted to ``RuntimeError`` so the
    provider methods can return Hermes' normal failure envelope.
    """
    token = _env("OMNIROUTE_API_KEY")
    if not token:
        raise RuntimeError("OMNIROUTE_API_KEY is not set")
    if not _gateway_root():
        raise RuntimeError("OMNIROUTE_API_URL is not set")

    try:
        response = httpx.post(
            _api_url(path),
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "hermes-omniroute-web/2.0.0",
            },
            timeout=_request_timeout(operation),
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"Could not reach OmniRoute for {operation}: {exc}") from exc

    if response.is_error:
        raise RuntimeError(_error_message(response, operation))

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"OmniRoute {operation} returned a non-JSON response") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"OmniRoute {operation} returned an invalid response")
    return data


def _search_row(item: Any, position: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    description = str(item.get("snippet") or item.get("description") or item.get("content") or "")
    if len(description) > _SEARCH_DESCRIPTION_CAP:
        description = description[:_SEARCH_DESCRIPTION_CAP].rstrip() + "…"
    raw_position = item.get("position")
    try:
        result_position = int(raw_position) if raw_position is not None else position
    except (TypeError, ValueError):
        result_position = position
    return {
        "title": str(item.get("title") or ""),
        "url": str(item.get("url") or ""),
        "description": description,
        "position": result_position,
    }


def _extract_error(url: str, message: str) -> dict[str, Any]:
    return {
        "url": url,
        "title": "",
        "content": "",
        "raw_content": "",
        "error": message,
    }


class OmniRouteWebProvider(WebSearchProvider):
    """Search and page extraction through an OmniRoute gateway."""

    @property
    def name(self) -> str:
        return "omniroute"

    @property
    def display_name(self) -> str:
        return "OmniRoute"

    def supports_extract(self) -> bool:
        return True

    def is_available(self) -> bool:
        """Cheap offline availability check required by the provider contract."""
        return bool(_env("OMNIROUTE_API_URL") and _env("OMNIROUTE_API_KEY"))

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "Query must be a non-empty string"}

        try:
            safe_limit = max(1, min(int(limit), _SEARCH_LIMIT_CAP))
        except (TypeError, ValueError):
            safe_limit = 5

        try:
            payload: dict[str, Any] = {
                "query": query,
                "max_results": safe_limit,
                "search_type": "web",
            }
            provider = _configured_provider("OMNIROUTE_SEARCH_PROVIDER")
            if provider:
                payload["provider"] = provider
            data = _post("search", payload, "search")
        except (RuntimeError, httpx.HTTPError) as exc:
            logger.warning("OmniRoute search failed: %s", exc)
            return {"success": False, "error": str(exc)}

        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raw_results = []
        web = [
            row
            for index, item in enumerate(raw_results[:safe_limit], start=1)
            if (row := _search_row(item, index)) is not None
        ]
        return {"success": True, "data": {"web": web}}

    def extract(self, urls: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        """Fetch each URL independently so one failed page does not abort a batch."""
        if isinstance(urls, str):
            urls = [urls]
        clean_urls = [url.strip() for url in (urls or []) if isinstance(url, str) and url.strip()]
        results: list[dict[str, Any]] = []

        for url in clean_urls:
            try:
                payload: dict[str, Any] = {
                    "url": url,
                    "format": kwargs.get("format") or "markdown",
                    "include_metadata": True,
                }
                provider = _configured_provider("OMNIROUTE_FETCH_PROVIDER")
                if provider:
                    payload["provider"] = provider
                if kwargs.get("depth") is not None:
                    payload["depth"] = max(0, min(int(kwargs["depth"]), 2))
                if kwargs.get("wait_for_selector"):
                    payload["wait_for_selector"] = str(kwargs["wait_for_selector"])
                data = _post("web/fetch", payload, "fetch")
            except (RuntimeError, httpx.HTTPError) as exc:
                logger.warning("OmniRoute fetch failed for %s: %s", url, exc)
                results.append(_extract_error(url, str(exc)))
                continue

            content = str(data.get("content") or "")
            metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            title = str(metadata.get("title") or "")
            if not content.strip():
                results.append(_extract_error(url, "OmniRoute returned no content for this URL"))
                continue

            metadata = dict(metadata)
            metadata.setdefault("sourceURL", data.get("url") or url)
            metadata["provider"] = data.get("provider") or provider or "auto"
            if isinstance(data.get("links"), list):
                metadata["links"] = data["links"]
            if data.get("screenshot_url"):
                metadata["screenshot_url"] = data["screenshot_url"]
            results.append(
                {
                    "url": str(data.get("url") or url),
                    "title": title,
                    "content": content,
                    "raw_content": content,
                    "metadata": metadata,
                }
            )
        return results

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": "OmniRoute",
            "badge": "gateway",
            "tag": "Search and fetch through OmniRoute; upstream provider keys stay in the gateway.",
            "env_vars": [
                {
                    "key": "OMNIROUTE_API_URL",
                    "prompt": "OmniRoute base URL (for example https://host:20128/v1)",
                    "url": "https://github.com/diegosouzapw/OmniRoute",
                },
                {
                    "key": "OMNIROUTE_API_KEY",
                    "prompt": "OmniRoute API key",
                    "url": "https://github.com/diegosouzapw/OmniRoute",
                },
            ],
        }
