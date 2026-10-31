---
name: python-html-xml-parsing
description: Parses HTML and XML in Python correctly and safely - choosing among lxml, BeautifulSoup with an explicit parser, selectolax and ElementTree, CSS selectors and XPath, extracting clean text and tables, XML namespaces, streaming huge files with iterparse, defusedxml and safe lxml settings for untrusted XML (XXE, billion laughs), encoding detection from bytes, and scraping politely with robots.txt, rate limits, a real user agent and caching. Use when scraping a web page, pulling data out of HTML, reading an XML feed, sitemap or export, parsing XML from users, handling namespaces or a file too large for memory, or fixing garbled characters from a page.
metadata:
  tags:
  - python
  - html
  - xml
  - lxml
  - beautifulsoup
  - selectolax
  - xpath
  - scraping
  - defusedxml
---

# Parsing HTML and XML in Python

HTML on the web is broken more often than not, and XML from outside can attack the parser. Good parsing code picks a parser that repairs HTML the way browsers do, selects data with selectors that survive small layout changes, feeds the parser bytes so it can find the encoding, and refuses dangerous XML features. Fetching pages is the `python-http-clients` skill; general input hardening is the `python-security` skill.

## Step 0: read the installed versions

Parser behaviour and APIs changed in recent releases (selectolax 1.0 removed its old engine), so read the versions first.

```bash
uv run python -c "import sys, pyexpat; print(sys.version_info[:3], pyexpat.EXPAT_VERSION)"
uv pip list 2>/dev/null | grep -iE "^(lxml|beautifulsoup4|selectolax|defusedxml|cssselect|charset-normalizer|html5lib) "
```

Current releases (October 2026): lxml 6.1.3, beautifulsoup4 4.15.0, selectolax 1.0.2, cssselect 1.6.0, defusedxml 0.7.1, charset-normalizer 3.5.2. Python 3.14 wheels exist for all of them.

## Choosing a parser

The parser decides what tree broken HTML becomes; pick by input and speed, and use the same one everywhere.

| Input | Use | Why |
|---|---|---|
| HTML, general scraping | `lxml.html` | fast C parser, XPath and CSS selectors (with `cssselect`) |
| HTML, convenient navigation | BeautifulSoup with `"lxml"` | friendly API over lxml; slower |
| HTML, millions of pages | selectolax (`LexborHTMLParser`) | fastest; HTML5 parsing; CSS only |
| HTML exactly as a browser parses it | BeautifulSoup with `"html5lib"` | spec-exact, very slow |
| XML you produced or trust | `xml.etree.ElementTree` or `lxml.etree` | stdlib, or lxml for XPath and speed |
| XML from users or the internet | `defusedxml`, or lxml with safe settings | blocks entity and DTD attacks |

## HTML with lxml.html

`lxml.html.fromstring` parses a fragment or a page and repairs it; `xpath()` and `cssselect()` (needs the `cssselect` package) select elements.

```python
import lxml.html

page = """<html><body>
<div class="product" data-sku="A1"><h2>Lamp</h2><span class="price">€ 49,00</span>
<a href="/p/a1">details</a></div>
<div class="product" data-sku="B2"><h2>Desk <small>oak</small></h2><span class="price">€ 310,00</span>
<a href="/p/b2">details</a></div>
</body></html>"""

doc = lxml.html.fromstring(page)
doc.make_links_absolute("https://shop.example.com/")          # relative hrefs become absolute
for card in doc.cssselect("div.product"):
    print(card.get("data-sku"),
          card.findtext("h2"),                                  # text before the first child only: "Desk "
          card.xpath("normalize-space(h2)"),                    # all text, whitespace collapsed: "Desk oak"
          card.cssselect("a")[0].get("href"))
```

- `el.text` is only the text before the first child; `el.text_content()` or XPath `normalize-space()` gives all of it.
- `lxml.html.clean` moved to the separate `lxml_html_clean` package in lxml 5.2; for sanitising HTML for display, use `nh3`.
- Pass bytes, not `str`, when the page came from the network, so lxml can read the `<meta charset>`.

## BeautifulSoup with an explicit parser

BeautifulSoup wraps a parser; always name it, because without one it picks the best installed parser and the tree changes from machine to machine.

```python
from bs4 import BeautifulSoup

broken = "<table><tr><td>a<td>b</table><p>one<p>two"
print(BeautifulSoup(broken, "html.parser").decode())    # td and p nested inside each other: not what browsers do
print(BeautifulSoup(broken, "lxml").decode())           # <td>a</td><td>b</td> ... <p>one</p><p>two</p>

soup = BeautifulSoup("<p class='lead'>Hello <b>you</b></p><a href='/x'>x</a>", "lxml")
lead = soup.select_one("p.lead")                         # CSS selectors through soupsieve
print(lead.get_text(" ", strip=True))                    # 'Hello you'; without " " it is 'Helloyou'
print([a["href"] for a in soup.select("a[href]")])
```

- `"html.parser"` needs no dependency but repairs unclosed tags differently from browsers; prefer `"lxml"`.
- `select` and `select_one` take CSS; `find` and `find_all` take names and attributes. Use `string=` (the old `text=` keyword is deprecated).
- BeautifulSoup is 5 to 20 times slower than lxml or selectolax; switch when parsing time shows up in a profile.

## selectolax for speed

selectolax binds the Lexbor HTML5 engine and is the fastest choice for large crawls. Since 1.0 the old Modest engine is gone: `from selectolax.parser import HTMLParser` raises `ImportError`; use `LexborHTMLParser`.

```python
from selectolax.lexbor import LexborHTMLParser

tree = LexborHTMLParser("<article><h1>Title</h1><p>First <em>para</em>.</p><script>x=1</script></article>")
tree.strip_tags(["script", "style"])                      # drop code before extracting text
print(tree.css_first("h1").text(strip=True))              # 'Title'
print(tree.css_first("article").text(separator=" ", strip=True))   # 'Title First para .'
print([n.attributes.get("href") for n in tree.css("a[href]")])
```

`text(strip=True)` strips each fragment and joins them with no separator, so `First <em>para</em>` becomes `Firstpara`; pass `separator=" "`.

## Extracting clean text and tables

Clean text means scripts and styles removed, block elements separated, and whitespace collapsed. `text_content()` glues adjacent cells together (`RegionSales`); join `itertext()` with spaces instead. Tables become lists of dicts keyed by the header row.

```python
import re

import lxml.html

doc = lxml.html.fromstring("""<div><style>p{}</style><p>Q3   report</p>
<table><tr><th>Region</th><th>Sales</th></tr><tr><td>EU</td><td>1 200</td></tr>
<tr><td>US</td><td>980</td></tr></table></div>""")

for bad in doc.xpath("//script|//style|//noscript"):
    bad.drop_tree()                                           # removes the element, keeps its tail text
text = re.sub(r"\s+", " ", " ".join(doc.itertext())).strip()   # 'Q3 report Region Sales EU 1 200 US 980'


def table_rows(table: lxml.html.HtmlElement) -> list[dict[str, str]]:
    rows = table.xpath(".//tr")
    headers = [c.text_content().strip() for c in rows[0].xpath("./th|./td")]
    return [dict(zip(headers, (c.text_content().strip() for c in r.xpath("./th|./td")), strict=False))
            for r in rows[1:]]


print(table_rows(doc.xpath("//table")[0]))   # [{'Region': 'EU', 'Sales': '1 200'}, {'Region': 'US', 'Sales': '980'}]
```

`pandas.read_html` turns every `<table>` into a DataFrame in one call when pandas is already a dependency; it needs lxml or html5lib and does not handle `rowspan` layouts perfectly.

## XML with ElementTree and lxml

`xml.etree.ElementTree` reads trusted XML with no dependency; `lxml.etree` has the same API plus full XPath, schema validation and speed.

```python
import xml.etree.ElementTree as ET

xml = b"""<orders><order id="7" status="paid"><total currency="EUR">49.00</total></order>
<order id="8" status="open"><total currency="EUR">12.50</total></order></orders>"""

root = ET.fromstring(xml)
for order in root.iterfind("order[@status='paid']"):        # ElementTree's small XPath subset
    print(order.get("id"), float(order.findtext("total", default="0")))
```

ElementTree supports paths, `[@attr='v']`, `[tag]` and positions, but not functions such as `contains()`; use lxml's `xpath()` for those.

## XML namespaces

A namespaced element's real name is `{uri}local`; prefixes in the document are only shorthand. Map your own prefixes to the URIs when searching, including for the default namespace (which has no prefix in the document but still needs one in the query).

```python
import xml.etree.ElementTree as ET

from lxml import etree

feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><title>Hello</title><media:thumbnail url="https://x.example/t.jpg"/></entry></feed>"""
NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}

root = ET.fromstring(feed)
print(root.findtext("atom:entry/atom:title", namespaces=NS))     # 'Hello'
print(root.find(".//{*}thumbnail").get("url"))                     # {*} matches any namespace (3.8+)

tree = etree.fromstring(feed)
print(tree.xpath("//atom:entry/media:thumbnail/@url", namespaces=NS))
```

`root.findall("entry")` returns nothing on this feed: `entry` is in the Atom namespace. This is the most common XML bug.

## Untrusted XML: defusedxml and safe lxml settings

XML from outside can define entities that expand to gigabytes (billion laughs, quadratic blowup) or read local files and URLs (XXE). Parse it with `defusedxml`, which refuses entity declarations, or give lxml a parser that resolves nothing.

```python
import defusedxml.ElementTree as SafeET
from defusedxml import DTDForbidden, EntitiesForbidden
from lxml import etree

evil = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
try:
    SafeET.fromstring(evil)
except (EntitiesForbidden, DTDForbidden) as exc:
    print("refused:", type(exc).__name__)                  # refused: EntitiesForbidden

SAFE = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
print(etree.fromstring(evil, SAFE).text)                     # None: the entity is not expanded
```

- Python's bundled Expat 2.7.2+ (check `pyexpat.EXPAT_VERSION`) refuses billion laughs on its own, and ElementTree never fetches external entities; a Python built against an older system Expat does not have this protection. Use defusedxml anyway for input from outside.
- lxml resolves only internal entities by default, but `iterparse` resolved external ones until 6.1.0 (CVE-2026-41066) and external parameter entities slipped through until 6.1.3. Require `lxml>=6.1.3` and still pass the explicit settings.
- `defusedxml.lxml` is deprecated; use the lxml parser settings above. Decompressing gzipped XML needs its own size limit.
- Never parse untrusted XML with `xml.dom.minidom` plus a DTD, `xmlrpc`, or `lxml` with `load_dtd=True`.

## Huge XML files with iterparse

`iterparse` streams a file element by element so memory stays flat; clear each record after use or the tree grows to the full file anyway.

```python
import io
import xml.etree.ElementTree as ET

data = io.BytesIO(b"<rows>" + b"".join(b'<row id="%d"><v>%d</v></row>' % (i, i * i) for i in range(100_000)) + b"</rows>")

total = 0
for event, elem in ET.iterparse(data, events=("end",)):
    if elem.tag == "row":
        total += int(elem.findtext("v"))
        elem.clear()                                          # free the row's children and text
print(total)
```

With lxml, `etree.iterparse(f, tag="row", events=("end",))` filters in C, and deleting already-processed siblings (`while elem.getprevious() is not None: del elem.getparent()[0]`) also frees the empty shells the root keeps. Pass a file path or a binary file, never a decoded string.

## Encoding detection

An encoding is chosen in this order: the HTTP `Content-Type` charset, a byte-order mark, the document's own `<meta charset>` or XML declaration, then a guess. Give parsers bytes and they handle the middle two; guess only for plain text with no declaration.

```python
from charset_normalizer import from_bytes

raw = "Grüße aus Köln, café".encode("cp1252")
best = from_bytes(raw).best()
print(best.encoding if best else None, str(best) if best else None)   # a cp125x guess; short samples are ambiguous
```

- `lxml.html.fromstring(raw_bytes)` and `BeautifulSoup(raw_bytes, "lxml")` read `<meta charset>`; `str` input skips that and trusts whoever decoded it.
- httpx `response.text` uses the HTTP charset and falls back to a guess; for HTML, pass `response.content` to the parser instead.
- `bs4.UnicodeDammit(raw).unicode_markup` tries declared encodings and then charset-normalizer when installed.

## Scraping politely

Polite scraping reads `robots.txt`, identifies itself, waits between requests, and avoids downloading what has not changed. `urllib.robotparser` answers "may I fetch this URL" and reads `Crawl-delay`.

```python
from urllib.robotparser import RobotFileParser

rules = RobotFileParser()
rules.parse("""User-agent: *
Disallow: /private/
Crawl-delay: 2
""".splitlines())                                   # in production: the lines of robots.txt fetched with your client
agent = "AcmeBot/1.2 (+https://acme.example/bot)"
print(rules.can_fetch(agent, "https://site.example/private/a"), rules.crawl_delay(agent))   # False 2
```

- Send a `User-Agent` that names the bot and a contact URL; never pretend to be a browser to get around a block.
- One request at a time per host, with the `Crawl-delay` or at least about one second between requests; back off on 429 and 503, honouring `Retry-After`.
- Cache responses and send `If-None-Match` / `If-Modified-Since`; a `304` costs the site almost nothing.
- Check the site's terms and the data's licence; robots.txt is a request, not a permission.
- A complete rate-limited, caching fetcher is in [scraping-politely.md](references/scraping-politely.md).

## Verify

Prove XML safety with attack cases that must be refused, and prove selectors against a saved page rather than the live site.

```bash
uv pip list 2>/dev/null | grep -iE "^lxml "             # expect 6.1.3 or newer
uv run python - <<'PY'
import defusedxml.ElementTree as SafeET
from defusedxml import DTDForbidden, EntitiesForbidden
from lxml import etree

xxe = b'<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
laughs = b'<!DOCTYPE l [<!ENTITY a "aa"><!ENTITY b "&a;&a;&a;&a;">]><l>&b;</l>'
for attack in (xxe, laughs):
    try:
        SafeET.fromstring(attack)
    except (EntitiesForbidden, DTDForbidden) as exc:
        print("refused:", type(exc).__name__)           # expect refused: EntitiesForbidden, twice
    else:
        raise SystemExit("defusedxml accepted an attack")

SAFE = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
assert etree.fromstring(xxe, SAFE).text is None         # the file was never read
PY
uv run pytest tests/test_selectors.py                   # selectors against pages saved in the repo
grep -rnE "BeautifulSoup\([^,)]*\)" src                  # expect nothing: every call names its parser
```

## Anti-patterns

| Instead of | Do |
|---|---|
| regular expressions over HTML | a parser and a selector |
| `BeautifulSoup(page)` with no parser | name the parser, usually `"lxml"` |
| `response.text` passed to the parser | `response.content`, so the parser reads the declared encoding |
| `div > div:nth-child(3)` | ids, `data-*` attributes or semantic classes |
| `ET.fromstring` on XML from outside | defusedxml, or lxml with the safe settings |
| `ET.parse` on a file of several GB | `iterparse` with `clear()` |
| a fake browser User-Agent | an honest bot name with a contact URL |

## Pre-merge checklist

Parsing code is ready when every item holds.

- [ ] One parser is chosen and named explicitly (`"lxml"` in BeautifulSoup; `LexborHTMLParser` in selectolax).
- [ ] Parsers get bytes from the network; text extraction uses a separator and collapses whitespace.
- [ ] Selectors use stable hooks (ids, `data-*`, semantic classes), not deep positional paths.
- [ ] Namespaced XML is queried with a prefix map or `{*}`.
- [ ] Untrusted XML goes through defusedxml or a lxml parser with `resolve_entities=False, no_network=True`.
- [ ] Files over a few hundred MB are streamed with `iterparse` and elements are cleared.
- [ ] Scrapers check robots.txt, send an honest User-Agent, rate-limit per host and cache.

## Gotchas

These are the parsing bugs seen most.

1. **`findall("item")` returns `[]` on a feed that has items.** The items are in a namespace. Use a prefix map or `{*}item`.
2. **Text comes out as `Helloyou`.** `get_text(strip=True)` or selectolax `text(strip=True)` join fragments with nothing. Pass a `" "` separator.
3. **`ImportError: Modest backend is deprecated since selectolax 1.0`.** `selectolax.parser.HTMLParser` was removed. Import `LexborHTMLParser` from `selectolax.lexbor`.
4. **`Ã©` instead of `é`.** UTF-8 bytes were decoded as Latin-1 (or the reverse) before parsing. Pass bytes and let the parser read the declaration.
5. **The scraper works locally but gets 403 in production.** The site blocks unknown agents or the data-centre IP range. Ask for an API or permission; do not rotate disguises.
6. **Memory explodes on a 2 GB XML export.** `ET.parse` builds the whole tree. Use `iterparse` and `clear()`.
7. **A selector stops matching after a site redesign.** It depended on layout (`div > div:nth-child(3)`). Anchor on ids, `data-*` attributes or text labels, and test against saved pages.

## References

The reference files hold the longer recipes and selector tables.

- [selectors-xpath-css.md](references/selectors-xpath-css.md): XPath and CSS selector cheat sheets, the same query in lxml, BeautifulSoup and selectolax, and testing selectors against saved pages
- [scraping-politely.md](references/scraping-politely.md): a rate-limited, caching, robots-aware fetcher with httpx, sitemaps, retries on 429 and 503, and storing raw pages

## See also

- `python-http-clients`: fetching pages with timeouts and retries
- `python-security`: input hardening and output sanitising
- `python-text-unicode-regex`: encodings and cleaning extracted text
- `python-serialization`: JSON, TOML and other data formats
