"""Актуальные мемы и тренды рунета: Claude с веб-поиском (раз в сутки) + запасная лента Google Trends.

Из результата вырезаются чувствительные темы (смерти, насилие, война, политика, преступления, сексуальные скандалы).
"""
from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from typing import Callable

log = logging.getLogger(__name__)
TRENDS_RSS = "https://trends.google.com/trending/rss?geo=RU"
SENSITIVE = re.compile(
    r"(эпштейн|epstein|смерт|умер|умира|убий|убит|убийств|погиб|жертв|теракт|взрыв|войн|обстрел|фронт|насили|изнасил|педофил|"
    r"суицид|самоубий|авари|катастроф|пожар|трамп|путин|зеленск|байден|мобилиз|выбор|санкци|арест|суд\b|приговор|"
    r"covid|ковид|теракт|секс|порно|18\+)", re.I)

PROMPT = """Сегодня {day}. Найди в интернете, какие мемы и тренды сейчас популярны в русскоязычном интернете и в TikTok (за последние дни).
Нужны только безобидные: мем-форматы, крылатые фразы, вирусные тренды, популярные стримеры и блогеры в юмористическом контексте.
Не включай: смерти, насилие, войну, политику и политиков, преступления и аресты, сексуальные скандалы, трагедии.
Верни ровно 6 строк, каждая вида: «- название: суть в одной фразе (до 120 символов)». Больше ничего не пиши."""


def clean_lines(text: str, limit: int = 6) -> list[str]:
    out = []
    for ln in text.splitlines():
        m = re.match(r"^\s*(?:[-•*]|\d+[.)])\s*(.+)$", ln)
        if not m:
            continue
        item = m.group(1).strip()[:160]
        if item and not SENSITIVE.search(item) and item not in out:
            out.append(item)
    return out[:limit]


def parse_trends_rss(xml_text: str, limit: int = 8) -> list[str]:
    items = []
    for it in ET.fromstring(xml_text).iter("item"):
        t = html.unescape((it.findtext("title") or "").strip())
        if t and not SENSITIVE.search(t) and t not in items:
            items.append(t)
    return items[:limit]


def _http_get(url: str) -> str:
    import requests

    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; signalbot)"})
    r.raise_for_status()
    return r.text[:1_000_000]


class TrendScout:
    def __init__(self, model: str = "claude-sonnet-5-5", client=None, http_get: Callable[[str], str] = _http_get, use_claude: bool = False):
        self.model, self._client, self._get, self.use_claude = model, client, http_get, use_claude

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=180.0, max_retries=2)
        return self._client

    def _web_search(self, day: str) -> list[str]:
        messages = [{"role": "user", "content": PROMPT.format(day=day)}]
        for _ in range(4):  # длинный поиск Claude может приостановиться (pause_turn): продолжаем
            resp = self._cl().messages.create(
                model=self.model, max_tokens=4000, messages=messages,
                tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 4}],
            )
            if resp.stop_reason == "pause_turn":
                messages = [messages[0], {"role": "assistant", "content": resp.content}]
                continue
            if resp.stop_reason == "refusal":
                return []
            return clean_lines("".join(b.text for b in resp.content if b.type == "text"))
        return []

    def fetch(self, day: str) -> list[str]:
        """Тренды дня: Claude с веб-поиском (если есть ключ), иначе или при сбое — лента Google Trends."""
        if self.use_claude:
            try:
                got = self._web_search(day)
                if got:
                    return got
            except Exception as e:
                log.warning("поиск трендов Claude: %s", e)
        try:
            return parse_trends_rss(self._get(TRENDS_RSS))
        except Exception as e:
            log.warning("лента трендов недоступна: %s", e)
            return []
