---
title: Scraping politely in Python
author: MOZGII, Inc.
lang: en
---

# Scraping politely in Python

A polite scraper fetches only what a site allows, as slowly as the site asks, says who it is, and does not download pages that have not changed. This reference builds such a fetcher with httpx and the standard library (robots.txt, per-host rate limits, conditional requests, retries on 429 and 503), reads sitemaps, and stores raw pages so parsing can be rerun without fetching again. HTTP client basics (timeouts, connection reuse) are the `python-http-clients` skill.

## Contents

This reference has 7 sections.

1. Rules a polite scraper follows
2. robots.txt with urllib.robotparser
3. A rate-limited, caching fetcher
4. Retries on 429 and 503 with Retry-After
5. Sitemaps and sitemap indexes
6. Storing raw pages and parsing later
7. Gotchas

## 1. Rules a polite scraper follows

Polite scraping is a set of habits that keep a site's operators from blocking you and keep your load on their servers small.

| Rule | How |
|---|---|
| ask first when there is an API or a data export | use it; it is faster and stable |
| obey robots.txt | `RobotFileParser.can_fetch` before each URL |
| identify yourself | `User-Agent: AcmeBot/1.2 (+https://acme.example/bot)` with a contact page |
| one request at a time per host | a per-host lock and a minimum interval |
| slow down when asked | honour `Crawl-delay`, `Retry-After`, 429 and 503 |
| do not fetch unchanged pages | `If-None-Match` and `If-Modified-Since` |
| fetch only what you need | skip images, scripts and duplicate URLs; normalise URLs |
| respect the content | check the terms of use, licences and personal-data law before storing |

## 2. robots.txt with urllib.robotparser

`urllib.robotparser.RobotFileParser` parses a robots.txt file and answers `can_fetch(agent, url)`; it also reads `Crawl-delay` and `Request-rate`. Fetch the file with your own HTTP client (so it gets your timeouts and User-Agent) and feed the lines to `parse()`.

```python
from urllib.robotparser import RobotFileParser

ROBOTS = """User-agent: AcmeBot
Disallow: /search
Crawl-delay: 5

User-agent: *
Disallow: /private/
"""

rules = RobotFileParser()
rules.parse(ROBOTS.splitlines())
for url in ("https://site.example/search?q=x", "https://site.example/private/a", "https://site.example/blog/1"):
    print(url, rules.can_fetch("AcmeBot/1.2", url), rules.can_fetch("OtherBot", url))
print(rules.crawl_delay("AcmeBot/1.2"), rules.crawl_delay("OtherBot"))      # 5 None
```

- The agent is matched by the product token (`AcmeBot`), case-insensitively; the version and comment are ignored.
- A 404 for robots.txt means everything is allowed; a 401 or 403 means treat the site as disallowed; on 5xx or a timeout, wait and retry later rather than crawl blind.
- Re-read robots.txt every few hours on long crawls; sites change it.
- `crawl_delay()` returns whole seconds only: `urllib.robotparser` ignores a fractional value such as `Crawl-delay: 0.5`, so keep your own minimum interval as well.
- RFC 9309 rules (`*` and `$` wildcards, the longest matching rule wins) reached `urllib.robotparser` in a recent bugfix release: Python 3.14.8 follows them, 3.14.4 and 3.13.13 apply the first matching rule and read `*` literally. The same robots.txt can get different answers per Python version; pin the Python, test the URLs that matter, or use Protego (Scrapy's parser).

## 3. A rate-limited, caching fetcher

The fetcher keeps one robots parser and one "next allowed time" per host, sends conditional headers from a small cache, and returns cached content on `304 Not Modified`. It runs here against `httpx.MockTransport`, so the example needs no network.

```python
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

AGENT = "AcmeBot/1.2 (+https://acme.example/bot)"


@dataclass
class Cached:
    etag: str | None
    last_modified: str | None
    body: bytes


@dataclass
class PoliteFetcher:
    client: httpx.Client
    min_interval: float = 1.0
    robots: dict[str, RobotFileParser] = field(default_factory=dict)
    next_ok: dict[str, float] = field(default_factory=dict)
    cache: dict[str, Cached] = field(default_factory=dict)

    def _rules(self, host: str, scheme: str) -> RobotFileParser:
        if host not in self.robots:
            rules = RobotFileParser()
            r = self.client.get(f"{scheme}://{host}/robots.txt")
            if r.status_code in (401, 403):
                rules.disallow_all = True
            elif r.status_code == 200:
                rules.parse(r.text.splitlines())
            elif 400 <= r.status_code < 500:
                rules.allow_all = True                       # 404: no rules
            else:
                rules.disallow_all = True                    # 5xx: assume nothing is allowed (RFC 9309)
            self.robots[host] = rules
        return self.robots[host]

    def _wait_turn(self, host: str, rules: RobotFileParser) -> None:
        delay = max(self.min_interval, float(rules.crawl_delay(AGENT) or 0))
        now = time.monotonic()
        time.sleep(max(0.0, self.next_ok.get(host, now) - now))
        self.next_ok[host] = time.monotonic() + delay

    def get(self, url: str) -> bytes | None:
        parts = urlsplit(url)
        rules = self._rules(parts.netloc, parts.scheme)
        if not rules.can_fetch(AGENT, url):
            return None
        self._wait_turn(parts.netloc, rules)
        headers = {}
        if hit := self.cache.get(url):
            if hit.etag:
                headers["If-None-Match"] = hit.etag
            if hit.last_modified:
                headers["If-Modified-Since"] = hit.last_modified
        r = self.client.get(url, headers=headers)
        if r.status_code == 304 and hit:
            return hit.body
        r.raise_for_status()
        self.cache[url] = Cached(r.headers.get("ETag"), r.headers.get("Last-Modified"), r.content)
        return r.content


def site(request: httpx.Request) -> httpx.Response:            # a fake site for the example
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nDisallow: /admin\nCrawl-delay: 1\n")
    if request.headers.get("If-None-Match") == '"v1"':
        return httpx.Response(304)
    return httpx.Response(200, content=b"<h1>Hello</h1>", headers={"ETag": '"v1"'})


client = httpx.Client(transport=httpx.MockTransport(site), headers={"User-Agent": AGENT}, timeout=10)
fetcher = PoliteFetcher(client, min_interval=0.1)
start = time.monotonic()
print(fetcher.get("https://site.example/page"))               # b'<h1>Hello</h1>'
print(fetcher.get("https://site.example/page"))               # 304, served from cache
print(fetcher.get("https://site.example/admin"))              # None: disallowed
print(f"{time.monotonic() - start:.1f}s")                     # about 1.0: Crawl-delay honoured between requests
```

- The cache here is a dict; persist it (SQLite, or the `hishel` 1.x HTTP cache for httpx) so restarts do not refetch everything.
- For several hosts in parallel, run one worker per host; the per-host interval then holds without a global lock.
- In asyncio code, the same structure works with `httpx.AsyncClient`, `asyncio.sleep` and one `asyncio.Lock` per host.

## 4. Retries on 429 and 503 with Retry-After

A `429 Too Many Requests` or `503 Service Unavailable` asks the client to slow down; `Retry-After` says how long, in seconds or as an HTTP date. Wait at least that long, and widen the host's interval for the rest of the crawl.

```python
import email.utils
import time
from datetime import UTC, datetime


def retry_after_seconds(value: str | None, default: float = 30.0) -> float:
    if not value:
        return default
    if value.strip().isdigit():
        return float(value)
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


print(retry_after_seconds("120"), retry_after_seconds(None))
print(round(retry_after_seconds(email.utils.format_datetime(datetime.now(UTC), usegmt=True))))   # 0
```

- Cap the wait (for example at 15 minutes) and give up on the URL after a few attempts; log it for a later run.
- Double the host's minimum interval after each 429, and lower it again slowly after a run of successes.
- Retry network errors and 5xx with backoff and jitter (the `python-error-handling` skill); never retry 4xx other than 429.

## 5. Sitemaps and sitemap indexes

A sitemap lists a site's URLs with optional `lastmod` dates; a sitemap index lists other sitemaps. Reading them is cheaper for the site than following links, and `lastmod` tells you which pages changed. Sitemaps are XML from outside, so parse them with defusedxml.

```python
import gzip
import io

import defusedxml.ElementTree as SafeET

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
MAX_SITEMAP = 50 * 1024 * 1024                                  # the protocol's limit for uncompressed sitemaps


def read_sitemap(raw: bytes) -> tuple[list[str], list[tuple[str, str | None]]]:
    """Return (child sitemaps, [(url, lastmod)])."""
    if raw[:2] == b"\x1f\x8b":                                  # gzip magic bytes
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            raw = stream.read(MAX_SITEMAP + 1)                  # stops one byte past the limit
    if len(raw) > MAX_SITEMAP:
        raise ValueError("sitemap over 50 MB")
    root = SafeET.fromstring(raw)
    children = [loc.text.strip() for loc in root.iterfind("sm:sitemap/sm:loc", NS) if loc.text]
    urls = [(u.findtext("sm:loc", "", NS).strip(), u.findtext("sm:lastmod", None, NS))
            for u in root.iterfind("sm:url", NS)]
    return children, urls


xml = b"""<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://site.example/a</loc><lastmod>2026-10-01</lastmod></url>
<url><loc>https://site.example/b</loc></url></urlset>"""
print(read_sitemap(gzip.compress(xml)))

bomb = gzip.compress(b"\0" * (MAX_SITEMAP + 1))                  # about 50 KB that unpacks past the limit
try:
    read_sitemap(bomb)
except ValueError as exc:
    print(exc)                                                  # sitemap over 50 MB
```

- robots.txt may list sitemaps; `RobotFileParser.site_maps()` returns them.
- `gzip.decompress` unpacks everything before you can measure it, so a small file from a hostile site can fill memory. `GzipFile.read(limit + 1)` stops at the limit, which is why the size check above works on any input.
- A sitemap's `lastmod` is often wrong or always "today"; conditional requests are the reliable change check.

## 6. Storing raw pages and parsing later

Store each fetched page as raw bytes with its URL, fetch time, status and headers, then parse in a separate step. When a selector breaks or a new field is needed, you re-parse the stored pages instead of crawling again.

```python
import gzip
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def store(root: Path, url: str, status: int, headers: dict[str, str], body: bytes) -> Path:
    key = hashlib.sha256(url.encode()).hexdigest()
    folder = root / key[:2]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.html.gz").write_bytes(gzip.compress(body))
    meta = {"url": url, "status": status, "fetched": datetime.now(UTC).isoformat(),
            "content_type": headers.get("content-type")}
    path = folder / f"{key}.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


print(store(Path(tempfile.mkdtemp()), "https://site.example/a", 200, {"content-type": "text/html; charset=utf-8"}, b"<h1>A</h1>").name)
```

Keep the `Content-Type` header: its charset is the first source for decoding the stored bytes later.

## 7. Gotchas

These are the problems polite scrapers still run into.

1. **Blocked after a few hundred requests despite robots.txt allowing them.** Requests were concurrent or too fast for the site's rate limiter. One request per host at a time, with a delay of a second or more.
2. **`can_fetch` returns `False` for everything.** robots.txt was fetched with an error page (HTML login page, 403) and parsed as rules, or `read()` hit a 401/403 and set `disallow_all`. Check the status before parsing.
3. **The cache never produces a 304.** The site sends neither `ETag` nor `Last-Modified`, or the URL has a changing query parameter (session id, timestamp). Normalise URLs and fall back to content hashes to detect changes.
4. **Pages come back empty.** The content is rendered by JavaScript. Look for the JSON API the page calls, or an official feed; a headless browser is the last resort and the heaviest load.
5. **The crawl revisits the same page under many URLs.** Tracking parameters, fragments and trailing slashes. Normalise (drop `#fragment` and `utm_*`, sort query parameters) before the "seen" check.
