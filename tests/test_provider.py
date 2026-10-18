from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

import provider


class _Response:
    def __init__(self, payload, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.is_error = status_code >= 400
        self.text = text

    def json(self):
        return self._payload


class OmniRouteWebProviderTests(unittest.TestCase):
    ENV = {
        "OMNIROUTE_API_URL": "https://gateway.example/v1",
        "OMNIROUTE_API_KEY": "test-key",
    }

    def setUp(self) -> None:
        self.env = patch.dict(os.environ, self.ENV, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_search_uses_current_endpoint_and_normalizes_results(self) -> None:
        response = _Response(
            {
                "provider": "serper-search",
                "results": [
                    {
                        "title": "First",
                        "url": "https://example.com/first",
                        "snippet": "A short result",
                        "position": 0,
                    },
                    {"title": "Second", "url": "https://example.com/second"},
                ],
            }
        )

        with patch.object(provider.httpx, "post", return_value=response) as post:
            result = provider.OmniRouteWebProvider().search("test query", limit=2)

        self.assertEqual(result["success"], True)
        self.assertEqual(result["data"]["web"][0]["position"], 0)
        self.assertEqual(result["data"]["web"][1]["position"], 2)
        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://gateway.example/api/v1/search")
        self.assertEqual(kwargs["json"], {
            "query": "test query",
            "max_results": 2,
            "search_type": "web",
        })
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_optional_provider_pin_is_forwarded(self) -> None:
        response = _Response({"results": []})
        with patch.dict(os.environ, {"OMNIROUTE_SEARCH_PROVIDER": "tavily-search"}), \
             patch.object(provider.httpx, "post", return_value=response) as post:
            provider.OmniRouteWebProvider().search("test")

        self.assertEqual(post.call_args.kwargs["json"]["provider"], "tavily-search")

    def test_extract_keeps_per_url_failures_isolated(self) -> None:
        responses = [
            _Response({"url": "https://bad.example", "content": "", "metadata": {}}),
            _Response({
                "provider": "firecrawl",
                "url": "https://good.example/final",
                "content": "# Good page",
                "metadata": {"title": "Good page"},
                "links": ["https://good.example/next"],
            }),
        ]

        with patch.object(provider.httpx, "post", side_effect=responses) as post:
            result = provider.OmniRouteWebProvider().extract([
                "https://bad.example",
                "https://good.example",
            ])

        self.assertEqual(len(result), 2)
        self.assertIn("error", result[0])
        self.assertEqual(result[0]["url"], "https://bad.example")
        self.assertEqual(result[1]["content"], "# Good page")
        self.assertEqual(result[1]["raw_content"], "# Good page")
        self.assertEqual(result[1]["title"], "Good page")
        self.assertEqual(result[1]["metadata"]["links"], ["https://good.example/next"])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[1].args[0],
            "https://gateway.example/api/v1/web/fetch",
        )

    def test_http_errors_are_returned_in_hermes_shape(self) -> None:
        response = _Response(
            {"error": {"message": "provider quota exceeded"}},
            status_code=429,
        )
        with patch.object(provider.httpx, "post", return_value=response):
            result = provider.OmniRouteWebProvider().search("test")

        self.assertEqual(result["success"], False)
        self.assertIn("429", result["error"])
        self.assertIn("provider quota exceeded", result["error"])

    def test_network_errors_are_returned_in_hermes_shape(self) -> None:
        with patch.object(
            provider.httpx,
            "post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            result = provider.OmniRouteWebProvider().search("test")

        self.assertEqual(result["success"], False)
        self.assertIn("Could not reach OmniRoute", result["error"])


if __name__ == "__main__":
    unittest.main()
