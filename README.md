# hermes-omniroute-web

REST web-search and page-extraction backend for [Hermes Agent](https://github.com/NousResearch/hermes-agent), routed through [OmniRoute](https://github.com/diegosouzapw/OmniRoute).

It keeps Hermes' built-in `web_search` and `web_extract` tools and only replaces their backend:

- `web_search` → `POST /api/v1/search`
- `web_extract` → `POST /api/v1/web/fetch`

No Hermes private helper modules are imported here: only the public `agent.web_search_provider.WebSearchProvider` class is used.

## Install

```bash
hermes plugins install cudatuda/hermes-omniroute-web
```

Or install from a local checkout:

```bash
hermes plugins install .
```

- `OMNIROUTE_API_URL` and `OMNIROUTE_API_KEY` are resolved through Hermes' public provider environment helper, so profile `.env` values and scoped gateway credentials work without importing private plugin helpers.

```text
OMNIROUTE_API_URL=https://your-omniroute-host/v1
OMNIROUTE_API_KEY=your-omniroute-key
```

The plugin also accepts a URL ending in `/api/v1`; it normalizes either form to the current OmniRoute web endpoints.

Select the provider in `config.yaml`:

```yaml
web:
  search_backend: omniroute
  extract_backend: omniroute
```

If the optional pins are omitted, OmniRoute selects the configured provider and applies its own fallback policy. To pin upstream providers:

```text
OMNIROUTE_SEARCH_PROVIDER=serper-search
OMNIROUTE_FETCH_PROVIDER=firecrawl
```

## Response and failure behavior

The adapter maps OmniRoute search results to Hermes' `{title, url, description, position}` contract and maps fetched content to Hermes' `{url, title, content, raw_content, metadata}` contract. Fetches are performed per URL, so a failure for one URL does not discard the other results.

Missing configuration, connection failures, non-JSON responses, HTTP errors, and empty fetches are returned as normal Hermes tool errors. Availability checks are offline and never probe the gateway.

## Verify

```bash
hermes plugins validate .
python -m unittest discover -s tests -v
python -m py_compile provider.py __init__.py
```

The source repository is a fork of the original implementation at [skynight137/hermes-omniroute-web](https://github.com/skynight137/hermes-omniroute-web).
