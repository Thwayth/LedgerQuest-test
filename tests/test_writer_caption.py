import asyncio
import json
from types import SimpleNamespace as NS

from signalbot.caption import build_caption, levels_block, plain_summary
from signalbot.config import Config, StrategyParams
from signalbot.models import Idea, Pool, Side, Zone
from signalbot.scanner import analyze
from signalbot.service import Service
from signalbot.storage import Storage
from signalbot.synthetic import make_trb_like
from signalbot.writer import PostText, PostWriter, idea_facts
from tests.test_service import FakeEx, FakePub


def idea(side=Side.LONG):
    return Idea("TRB/USDT:USDT", "okx", "1d", side, Zone(95, 100, 3, 0), [Pool(110.0, 5), Pool(120.5, 9)], 92.0, 1.0, 1.1,
                score=66, entry_conservative=100.4, entry_aggressive=92.0, invalidation=88.25,
                extra={"rr": 2.4, "components": {"proximity": 22, "squeeze": 5, "volume": 12, "structure": 3, "touches": 9, "rr": 8}})


class FakeClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(opinion="Монета упёрлась в уровень и явно что-то задумала. Ждём пробой и закрепление.", risk="Кто готов рискнуть, может зайти по рынку и набрать сеткой."):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"opinion": opinion, "risk_line": risk}))])


def test_levels_block_has_tvh_tp_sl_with_numbers():
    b = levels_block(idea())
    assert "<b>ТВХ:</b> 100.40 (по рынку 92.000)" in b
    assert "<b>ТП1:</b> 110.00" in b and "<b>ТП2:</b> 120.50" in b
    assert "<b>СЛ:</b> 88.250" in b or "<b>СЛ:</b> 88.25" in b
    assert b.index("ТВХ") < b.index("ТП1") < b.index("ТП2") < b.index("СЛ")


def test_caption_with_claude_text_structure():
    c = build_caption(idea(), "2026-10-05", text=PostText("Моё мнение: интересно.", "Рискнуть можно, но осторожно."))
    assert c.startswith("<b>TRB</b> 🔥")
    assert "Моё мнение: интересно." in c and "<blockquote>Рискнуть можно, но осторожно.</blockquote>" in c
    assert "ТВХ" in c and "ТП1" in c and "СЛ" in c and "Не финансовый совет" not in c
    assert len(c) <= 1000


def test_caption_escapes_html_in_llm_text_and_falls_back_when_too_long():
    c = build_caption(idea(), "d", text=PostText("a <b>x</b> & y", "r"))
    assert "&lt;b&gt;" in c and "<b>x</b>" not in c
    long = build_caption(idea(), "d", text=PostText("слово " * 400, "r"))
    assert len(long) <= 1000 and "слово слово слово" not in long  # ушли на шаблон


def test_template_caption_varies_by_day_and_has_levels():
    posts = {build_caption(idea(), f"2026-10-{d:02d}") for d in range(1, 21)}
    assert len(posts) >= 8
    assert all("ТВХ" in p and "ТП1" in p and "СЛ" in p for p in posts)


def test_plain_summary_strips_tags():
    s = plain_summary("<b>TRB</b> 🔥\n\n<blockquote>a &amp; b</blockquote>")
    assert "<" not in s and "a & b" in s


def test_writer_returns_valid_text_and_uses_structured_output():
    cl = FakeClient(ok())
    t = PostWriter(client=cl).write(idea(), "2026-10-05", ["прошлый пост"])
    assert t and t.opinion.startswith("Монета упёрлась")
    kw = cl.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "low"
    assert "temperature" not in kw  # на Opus 5.5 семплинг не задаётся
    assert "прошлый пост" in kw["messages"][0]["content"] and "TRB" in kw["messages"][0]["content"]


def test_writer_rejects_digits_and_promises_then_retries_once_then_gives_up():
    cl = FakeClient(ok(opinion="Цель 5.2 почти в кармане"), ok(opinion="Это гарантированно вырастет, гарантирую"))
    assert PostWriter(client=cl).write(idea(), "d", []) is None and len(cl.calls) == 2
    cl2 = FakeClient(ok(opinion="Цель 5.2"), ok())
    assert PostWriter(client=cl2).write(idea(), "d", []) is not None  # вторая попытка прошла


def test_writer_survives_refusal_and_api_errors():
    refusal = NS(stop_reason="refusal", content=[])
    assert PostWriter(client=FakeClient(refusal)).write(idea(), "d", []) is None
    assert PostWriter(client=FakeClient(RuntimeError("api down"))).write(idea(), "d", []) is None


def test_idea_facts_have_no_prices_and_pick_strengths():
    f = idea_facts(idea())
    assert f["ticker"] == "TRB" and f["targets_count"] == 2 and "LONG" in f["direction"]
    assert "цена вплотную подошла к уровню" in f["strengths"]
    assert "110" not in json.dumps(f)  # уровни модели не передаём — их добавит бот


def test_storage_posts_and_clear_ideas():
    db = Storage()
    for i in range(7):
        db.add_post(i, f"post {i}")
    assert db.recent_posts(3) == ["post 4", "post 5", "post 6"]
    from tests.test_storage_stats_caption import tr

    db.add(tr())
    assert db.clear_ideas() == 1 and db.all() == [] and len(db.recent_posts(10)) == 7


def make_service(writer):
    cfg = Config(owner_id=1, dry_run=True)
    cfg.strategy.min_score = 0
    cfg.use_context = False
    return Service(cfg, FakeEx(), Storage(), FakePub(), writer=writer), cfg


def test_service_publishes_claude_text_and_remembers_it():
    svc, _ = make_service(PostWriter(client=FakeClient(ok(opinion="Уникальная фраза про монету."))))
    published = asyncio.run(svc.scan_once())
    assert len(published) == 1
    caption = svc.pub.posts[0][1]
    assert "Уникальная фраза про монету." in caption and "ТВХ" in caption and "СЛ" in caption
    assert "Уникальная фраза про монету." in svc.db.recent_posts(1)[0]


def test_service_falls_back_to_templates_when_claude_fails():
    svc, _ = make_service(PostWriter(client=FakeClient(RuntimeError("boom"))))
    asyncio.run(svc.scan_once())
    caption = svc.pub.posts[0][1]
    assert "ТВХ" in caption and "<blockquote>" in caption  # пост ушёл, несмотря на сбой
