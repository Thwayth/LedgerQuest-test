"""Свежие заголовки из открытых RSS крипто-СМИ (без ключей)."""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlparse

log = logging.getLogger(__name__)
MAX_BYTES = 2_000_000


def _http_get(url: str) -> str:
    import requests

    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; signalbot)"})
    r.raise_for_status()
    return r.text[:MAX_BYTES]


def parse_feed(xml_text: str, source: str, now: datetime, max_age_h: float) -> list[dict]:
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        if not title:
            continue
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))).strip()[:220]
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now - ts > timedelta(hours=max_age_h) or ts > now + timedelta(hours=1):
            continue
        out.append({"source": source, "title": title, "summary": summary, "ts": ts})
    return out


def fetch_headlines(
    feeds: list[str], http_get: Callable[[str], str] = _http_get, now: datetime | None = None,
    max_age_h: float = 36, per_feed: int = 2, limit: int = 6,
) -> list[dict]:
    """До per_feed свежих заголовков с каждой ленты, новее первыми. Сбой ленты не ломает остальные."""
    now = now or datetime.now(timezone.utc)
    items: list[dict] = []
    seen: set[str] = set()
    for url in feeds:
        try:
            host = urlparse(url).netloc.removeprefix("www.")
            got = sorted(parse_feed(http_get(url), host, now, max_age_h), key=lambda i: i["ts"], reverse=True)
        except Exception as e:
            log.warning("лента %s недоступна: %s", url, e)
            continue
        for i in got[:per_feed]:
            key = i["title"].lower()
            if key not in seen:
                seen.add(key)
                items.append(i)
    items.sort(key=lambda i: i["ts"], reverse=True)
    return [{k: v for k, v in i.items() if k != "ts"} for i in items[:limit]]
