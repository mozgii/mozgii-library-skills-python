---
title: XPath and CSS selectors for lxml, BeautifulSoup and selectolax
author: MOZGII, Inc.
lang: en
---

# XPath and CSS selectors for lxml, BeautifulSoup and selectolax

How to write selectors that find the right elements and keep finding them after a site changes: XPath and CSS cheat sheets, which selector features each library supports (measured with lxml 6.1.3 and cssselect 1.6.0, beautifulsoup4 4.15.0 with soupsieve, selectolax 1.0.2), selecting values by their label, and testing selectors against saved pages.

## Contents

This reference has 7 sections.

1. XPath cheat sheet
2. CSS selector cheat sheet
3. Selector support by library
4. Selecting a value by its label
5. The same query in three libraries
6. Testing selectors against saved pages
7. Gotchas

## 1. XPath cheat sheet

XPath is the query language of lxml (`el.xpath(...)`); lxml implements XPath 1.0, so XPath 2.0 functions such as `matches()` and `lower-case()` are missing. A path starting with `//` searches the whole document even when called on an element; start with `.//` to search inside the element.

| Goal | XPath |
|---|---|
| all `li` anywhere | `//li` |
| `li` inside this element | `.//li` |
| direct children only | `./li` |
| attribute value | `//a/@href` |
| element with an attribute | `//a[@href]` |
| exact attribute | `//div[@id='main']` |
| one class among several | `//li[contains(concat(' ', normalize-space(@class), ' '), ' new ')]` |
| text equals, whitespace ignored | `//dt[normalize-space()='Price:']` |
| text contains | `//p[contains(., 'refund')]` |
| starts with | `//a[starts-with(@href, '/docs/')]` |
| the next sibling of a kind | `//dt[.='Price:']/following-sibling::dd[1]` |
| nearest ancestor | `//span/ancestor::li[1]` |
| position | `(//li)[1]`, `//li[last()]` (note `//li[1]` is every first child) |
| negation | `//li[not(@class)]` |
| union | `//h1 | //h2` |
| count, string results | `count(//li)`, `string(//title)`, `normalize-space(//h1)` |

`contains(@class, 'new')` also matches `class="renew"`; the `concat(' ', ...)` form above matches whole class names only.

## 2. CSS selector cheat sheet

CSS selectors are shorter than XPath for structure and attributes, and every library here supports them. They cannot select text nodes, attributes or parents; return to XPath (lxml) or the element API for those.

| Goal | CSS |
|---|---|
| tag, class, id | `li`, `.item`, `#main` |
| several classes | `li.item.new` |
| child, descendant | `ul > li`, `div li` |
| next sibling, any later sibling | `dt + dd`, `h2 ~ p` |
| attribute present, equal, prefix, suffix, contains | `[href]`, `[type="email"]`, `[href^="/"]`, `[src$=".png"]`, `[href*="utm_"]` |
| one word of a space-separated attribute | `[class~="new"]` |
| position | `li:nth-child(2)`, `li:last-child`, `li:first-of-type` |
| negation, either | `li:not(.new)`, `li:is(.new, .hot)` |
| has a descendant | `li:has(a)` |

## 3. Selector support by library

The three CSS engines agree on standard selectors and differ on text matching. This table was measured on one document with each library.

| Selector | BeautifulSoup (soupsieve) | lxml + cssselect | selectolax (Lexbor) |
|---|---|---|---|
| classes, ids, attributes, combinators | yes | yes | yes |
| `:nth-child`, `:last-child`, `:first-of-type` | yes | yes | yes |
| `:not()`, `:is()`, `:has()` | yes | yes | yes |
| text: `:-soup-contains('B')` | yes | error | error |
| text: `:contains('B')` | yes, with a `FutureWarning` | yes | error |
| text in selectolax | | | `tree.select("li").text_contains("B").matches` |

`cssselect` turns CSS into XPath, which helps when you want to see or extend what a CSS selector does:

```python
from cssselect import HTMLTranslator

print(HTMLTranslator().css_to_xpath("li.item > a"))
# descendant-or-self::li[@class and contains(@class, 'item') and contains(concat(' ', normalize-space(@class), ' '), ' item ')]/a
```

## 4. Selecting a value by its label

Values in definition lists, tables and forms are best found by their visible label, which changes less often than layout classes. XPath's `following-sibling` reads "the `dd` after the `dt` that says Price".

```python
import lxml.html

doc = lxml.html.fromstring("""<dl><dt>Price:</dt><dd>49</dd><dt>Stock:</dt><dd>3</dd></dl>
<table><tr><th>SKU</th><td>A1</td></tr><tr><th>Weight</th><td>2 kg</td></tr></table>""")


def labelled(doc: lxml.html.HtmlElement, label: str) -> str | None:
    hits = doc.xpath(
        "//dt[normalize-space()=$l]/following-sibling::dd[1] | //th[normalize-space()=$l]/following-sibling::td[1]",
        l=label,                                              # XPath variable: no string building, no injection
    )
    return hits[0].text_content().strip() if hits else None


print(labelled(doc, "Price:"), labelled(doc, "Weight"))     # 49 2 kg
```

Pass values as XPath variables (`$l` with `l=...`) instead of formatting them into the expression; a label containing a quote would otherwise break or change the query.

## 5. The same query in three libraries

The same extraction in each library shows the API differences: lxml returns elements and strings from XPath, BeautifulSoup returns `Tag` objects, selectolax returns nodes with `attributes` and `text()`.

```python
import lxml.html
from bs4 import BeautifulSoup
from selectolax.lexbor import LexborHTMLParser

html = """<ul><li class="item new">A <span>1</span></li><li class="item">B</li>
<li class="item"><a href="/c">C</a></li></ul>"""

doc = lxml.html.fromstring(html)
print([li.text_content().strip() for li in doc.cssselect("li.item")], doc.xpath("//li/a/@href"))

soup = BeautifulSoup(html, "lxml")
print([li.get_text(" ", strip=True) for li in soup.select("li.item")], [a["href"] for a in soup.select("li a[href]")])

tree = LexborHTMLParser(html)
print([li.text(separator=" ", strip=True) for li in tree.css("li.item")],
      [a.attributes["href"] for a in tree.css("li a[href]")])
```

| Operation | lxml.html | BeautifulSoup | selectolax |
|---|---|---|---|
| all matches | `el.cssselect(css)`, `el.xpath(xp)` | `el.select(css)` | `node.css(css)` |
| first match | `next(iter(el.cssselect(css)), None)` | `el.select_one(css)` | `node.css_first(css)` |
| attribute | `el.get("href")` | `tag.get("href")` or `tag["href"]` | `node.attributes.get("href")` |
| all text | `el.text_content()` | `tag.get_text(" ", strip=True)` | `node.text(separator=" ", strip=True)` |
| remove an element | `el.drop_tree()` | `tag.decompose()` | `node.decompose()` |
| serialise | `lxml.html.tostring(el, encoding="unicode")` | `str(tag)` | `node.html` |

## 6. Testing selectors against saved pages

Scrapers break when sites change, so keep a few real pages as test fixtures and assert on the extracted data. A failing test then tells you the site changed before bad data reaches users.

```python
import tempfile
from pathlib import Path

import lxml.html


def extract_product(html: bytes) -> dict[str, str]:
    doc = lxml.html.fromstring(html)
    return {
        "name": doc.xpath("normalize-space(//h1)"),
        "price": doc.xpath("normalize-space(//*[@data-testid='price'])"),
    }


def test_product_page(tmp_path: Path) -> None:
    page = tmp_path / "product.html"                         # real tests: tests/fixtures/product-2026-10.html
    page.write_bytes(b"<h1> Desk lamp </h1><span data-testid='price'>EUR 49.00</span>")
    assert extract_product(page.read_bytes()) == {"name": "Desk lamp", "price": "EUR 49.00"}


test_product_page(Path(tempfile.mkdtemp()))                  # pytest passes tmp_path itself
print("passed")
```

- Save fixtures as bytes exactly as downloaded, so encoding handling is tested too.
- When a selector must change, add the new page as a second fixture; keep the old one while both layouts are live.
- In production, alert when a required field comes back empty for more than a few pages in a row.

## 7. Gotchas

These are the selector mistakes seen most.

1. **A selector copied from browser devtools matches nothing.** The browser inserted `<tbody>` or ran JavaScript that built the element. Check the raw HTML (view-source), drop `tbody` from the path, or find the JSON the page loads.
2. **`//li[1]` returns several elements.** It means "every `li` that is the first child of its parent". Use `(//li)[1]` for the first in the document.
3. **`el.xpath("//a")` returns links from the whole page.** `//` starts at the document root. Use `.//a`.
4. **`:contains()` warns in BeautifulSoup and fails in selectolax.** It is not standard CSS. Use `:-soup-contains()` in BeautifulSoup, `text_contains` in selectolax, XPath `contains()` in lxml.
5. **`contains(@class, 'item')` matches `item-list` too.** Partial string match. Use the `concat(' ', normalize-space(@class), ' ')` form or CSS `.item`.
6. **`XPathEvalError: Unregistered function`.** An XPath 2.0 function such as `lower-case()`. Use `translate()` for case folding, or filter in Python.
