---
title: Logging HTTP requests with httpx event hooks
author: MOZGII, Inc.
lang: en
---

# Logging HTTP requests with httpx event hooks

httpx event hooks run once per request and once per response, which suits a log line with method, URL, status and duration. The URL is logged without its query string, because queries often carry tokens or personal data, and no headers or bodies are logged.

```python
import logging
import time

import httpx

log = logging.getLogger("my_app.http")


def log_request(request: httpx.Request) -> None:
    request.extensions["start"] = time.monotonic()


def log_response(response: httpx.Response) -> None:
    start = response.request.extensions.get("start", time.monotonic())
    log.info("%s %s -> %d in %.0f ms", response.request.method, response.request.url.copy_with(query=None),
             response.status_code, (time.monotonic() - start) * 1000)


client = httpx.Client(event_hooks={"request": [log_request], "response": [log_response]})
```

A hook fires for each hop of a redirect, so a followed 302 logs two lines. Set the `httpx` logger to `WARNING` beside this hook: its own `INFO` line prints the full URL, query included.
