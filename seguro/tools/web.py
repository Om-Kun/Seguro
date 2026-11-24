"""Web reading without a browser: page text fetch and news search via public RSS feeds."""
import email.utils
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import httpx

from seguro.tools.registry import registry

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SeguroAgent/2"}


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        self._in_title = tag == "title"

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


@registry.register(
    name="web_fetch",
    description="Downloads a web page and returns its title and readable text (no browser window).",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http(s) URL"},
            "max_chars": {"type": "integer", "description": "Text limit (default 4000)"}
        },
        "required": ["url"]
    }
)
def web_fetch(url: str, max_chars: int = 4000) -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers=UA)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "url": url}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "url": str(r.url)}
    p = _Text()
    p.feed(r.text)
    text = " ".join(p.parts)
    return {"ok": True, "url": str(r.url), "title": p.title, "text": text[:max_chars], "chars": len(text)}


def _bing(query: str) -> list:
    url = f"https://www.bing.com/news/search?q={urllib.parse.quote_plus(query)}&format=rss"
    r = httpx.get(url, timeout=20, follow_redirects=True, headers=UA)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        source = next((c.text for c in it if c.tag.endswith("}Source") or c.tag == "Source"), None)
        link = it.findtext("link") or ""
        real = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("url")
        if real:  # bing.com/news/apiclick.aspx?...&url=<article> -> the article itself
            link = real[0]
        if not source:
            source = urllib.parse.urlparse(link).netloc.replace("www.", "") or "unknown"
        out.append({"title": _strip_html(it.findtext("title")), "source": source.strip(),
                    "summary": _strip_html(it.findtext("description"))[:400], "link": link,
                    "published": it.findtext("pubDate") or ""})
    return out


def _google(query: str) -> list:
    url = (f"https://news.google.com/rss/search?q={urllib.parse.quote_plus(query + ' when:3d')}"
           f"&hl=en-US&gl=US&ceid=US:en")
    r = httpx.get(url, timeout=20, follow_redirects=True, headers=UA)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        src = it.find("source")
        title = _strip_html(it.findtext("title"))
        source = src.text.strip() if src is not None and src.text else "unknown"
        if title.endswith(" - " + source):
            title = title[: -len(" - " + source)]
        out.append({"title": title, "source": source, "summary": "", "link": it.findtext("link") or "",
                    "published": it.findtext("pubDate") or ""})
    return out


def _ts(item) -> float:
    try:
        return email.utils.parsedate_to_datetime(item["published"]).timestamp()
    except Exception:
        return 0.0


@registry.register(
    name="news_search",
    description=("Finds the latest news articles for a topic (Bing News RSS, Google News fallback). Returns "
                 "articles newest first and a ready 'table' with header [Title, Source, Summary, Link, Published]."),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic, e.g. 'artificial intelligence'"},
            "count": {"type": "integer", "description": "How many articles (default 5)"}
        },
        "required": ["query"]
    }
)
def news_search(query: str, count: int = 5) -> dict:
    errors, items, provider = [], [], None
    for name, fn in (("bing", _bing), ("google_news", _google)):
        try:
            items = fn(query)
            if items:
                provider = name
                break
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
    if not items:
        return {"ok": False, "error": "No news feed answered. " + "; ".join(errors)}
    seen, uniq = set(), []
    for it in sorted(items, key=_ts, reverse=True):
        key = it["title"].lower()
        if key and key not in seen:
            seen.add(key)
            uniq.append(it)
    top = uniq[: max(1, int(count))]
    table = [["Title", "Source", "Summary", "Link", "Published"]] + \
            [[a["title"], a["source"], a["summary"] or a["title"], a["link"], a["published"]] for a in top]
    return {"ok": True, "provider": provider, "count": len(top), "articles": top, "table": table}
