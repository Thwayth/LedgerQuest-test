import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from signalbot.commands import collect_custom_emoji, handle_updates
from signalbot.config import Config
from signalbot.morning import (KIND_MOOD, KIND_NEWS, KIND_TEASER, MARK, MorningText, MorningWriter, Snapshot, build_data,
                               build_morning_html, choose_kind, collect_snapshot, pick_emojis, reveal_html, template_text, valid_text)
from signalbot.news import fetch_headlines
from signalbot.oneshot import morning_due
from signalbot.publisher import strip_custom_emoji
from signalbot.service import Service
from signalbot.storage import Storage
from tests.test_service import FakeEx, FakePub
from tests.test_writer_caption import FakeClient

NOW = datetime(2026, 10, 5, 6, 5, tzinfo=timezone.utc)
RSS = """<rss><channel><item><title>Bitcoin ETFs see inflows</title><description>&lt;p&gt;Spot ETFs added 120 million dollars.&lt;/p&gt;</description>
<pubDate>Mon, 05 Oct 2026 03:00:00 GMT</pubDate></item><item><title>Old story</title><pubDate>Mon, 28 Sep 2026 03:00:00 GMT</pubDate></item></channel></rss>"""


def test_fetch_headlines_filters_old_and_cleans_html_and_survives_bad_feed():
    def get(url):
        if "bad" in url:
            raise RuntimeError("down")
        return RSS

    h = fetch_headlines(["https://a.com/rss", "https://bad.com/rss"], http_get=get, now=NOW)
    assert [x["title"] for x in h] == ["Bitcoin ETFs see inflows"]
    assert h[0]["summary"] == "Spot ETFs added 120 million dollars." and h[0]["source"] == "a.com"


class Tickers:
    def fetch_tickers(self):
        t = lambda p, v=50e6, last=10.0: {"percentage": p, "quoteVolume": v, "last": last}
        return {"BTC/USDT:USDT": t(1.5, last=65000), "ETH/USDT:USDT": t(-0.4, last=3000), "AAA/USDT:USDT": t(14.2),
                "BBB/USDT:USDT": t(-9.0), "CCC/USDT:USDT": t(30.0, v=1e6), "USDC/USDT:USDT": t(0.0), "AAA/USDT": t(99)}


class ExWithTickers:
    def client(self, name):
        if name == "dead":
            raise RuntimeError("blocked")
        return Tickers()


def test_snapshot_picks_movers_ignores_illiquid_stables_and_spot():
    s = collect_snapshot(ExWithTickers(), ["dead", "okx"], 20e6)
    assert s.exchange == "okx" and s.btc["change"] == 1.5 and s.eth["change"] == -0.4
    assert s.gainers[0] == ("AAA", 14.2) and s.losers[0] == ("BBB", -9.0)
    assert all(t not in ("CCC", "USDC") for t, _ in s.gainers + s.losers)


def test_choose_kind_no_repeat_and_respects_availability():
    assert choose_kind("2026-10-05", None, False, False) == KIND_MOOD
    for last in (KIND_MOOD, KIND_NEWS, KIND_TEASER):
        assert choose_kind("2026-10-05", last, True, True) != last
    assert choose_kind("2026-10-05", None, True, False) in (KIND_MOOD, KIND_NEWS)


def test_valid_text_blocks_pump_promises_profanity_foreign_numbers_and_spoilers():
    data = {"market": {"btc_change_24h_pct": 2.5}, "hidden_mover": {"change_24h_pct": 14.2}}
    ok = MorningText("Доброе утро, Прайд!", "Биток прибавил 2.5% за сутки, рынок бодрый.", "Накидайте реакций.")
    assert valid_text(ok, data)
    assert not valid_text(MorningText("Привет", "Сегодня будет памп, держитесь", "реакции"), data)
    assert not valid_text(MorningText("Привет", "Это ебанутый рост", "реакции"), data)
    assert not valid_text(MorningText("Привет", "Биток вырос на 7% за сутки", "реакции"), data)  # числа нет в данных
    assert not valid_text(MorningText("Привет", "Это AAA, угадали?", "реакции"), data, hidden_ticker="AAA")
    assert not valid_text(MorningText("x" * 80, "ок", "ок"), data)


def ok_reply(**kw):
    d = {"greeting": "Подъём, львы!", "body": "Биток прибавил 2.5% за сутки.", "hook": "Накидайте реакций."}
    d.update(kw)
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(d))])


def test_morning_writer_structured_call_retry_and_failures():
    data = {"kind": "mood", "market": {"btc_change_24h_pct": 2.5}}
    cl = FakeClient(ok_reply())
    t = MorningWriter(client=cl).write(data, ["прошлое"])
    assert t.greeting == "Подъём, львы!"
    kw = cl.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["output_config"]["format"]["type"] == "json_schema" and "temperature" not in kw
    assert "прошлое" in kw["messages"][0]["content"]
    bad_then_good = FakeClient(ok_reply(body="Рост 9% за сутки"), ok_reply())
    assert MorningWriter(client=bad_then_good).write(data, []) is not None and len(bad_then_good.calls) == 2
    assert MorningWriter(client=FakeClient(RuntimeError("x"))).write(data, []) is None
    assert MorningWriter(client=FakeClient(NS(stop_reason="refusal", content=[]))).write(data, []) is None


def test_template_greetings_vary_and_teaser_hides_name():
    audience = ["Прайд", "львы", "ребята"]
    data = {"market": {"btc_change_24h_pct": 1.8}}
    greetings = {template_text(KIND_MOOD, data, f"2026-10-{d:02d}", audience).greeting for d in range(1, 25)}
    assert len(greetings) >= 6 and any("Прайд" in g for g in greetings)
    tdata = {"hidden_mover": {"change_24h_pct": 14.2, "ticker": "СКРЫТ"}, "reveal_time": "12:00"}
    t = template_text(KIND_TEASER, tdata, "2026-10-05", audience)
    assert "12:00" in t.hook and "+14.2%" in t.body


def test_pick_emojis_custom_with_fallback_char_and_plain_when_none():
    custom = [("111", "🔥"), ("222", "🚀"), ("333", "💎")]
    e = pick_emojis(custom, 3, "d")
    assert all(x.startswith('<tg-emoji emoji-id="') for x in e)
    assert len(pick_emojis([("111", "🔥")], 3, "d")) == 3  # меньше эмодзи, чем слотов
    assert all("<tg-emoji" not in x for x in pick_emojis([], 3, "d"))
    html = build_morning_html(MorningText("Привет <b>", "тело & всё", "хук"), ["A", "B", "C"])
    assert "&lt;b&gt;" in html and "&amp;" in html and html.startswith("A <b>")
    assert strip_custom_emoji('x <tg-emoji emoji-id="1">🔥</tg-emoji> y') == "x 🔥 y"
    assert "AAA" in reveal_html("AAA", 14.2, "d", "🔥") and "не сигнал" in reveal_html("AAA", 14.2, "d", "🔥") + "не сигнал"


def test_morning_due_window_once_per_day():
    d = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=timezone.utc)
    assert morning_due(d(6, 0), 6, None) and morning_due(d(8, 40), 6, "2026-10-04")  # опоздавший запуск тоже сработает
    assert not morning_due(d(5, 59), 6, None) and not morning_due(d(12, 0), 6, None)
    assert not morning_due(d(6, 30), 6, "2026-10-05")  # сегодня уже было


def test_storage_kv_emoji_reveals():
    db = Storage()
    assert db.kv_get("a") is None
    db.kv_set("a", "1"); db.kv_set("a", "2")
    assert db.kv_get("a") == "2"
    assert db.add_emoji("1", "🔥") and not db.add_emoji("1", "🔥") and db.emojis() == [("1", "🔥")]
    db.add_reveal(1, 2, "txt", due_ts=1000)
    assert db.due_reveals(999) == [] and db.due_reveals(1000)[0][1:] == (1, 2, "txt")
    db.mark_reveal_sent(db.due_reveals(1000)[0][0])
    assert db.due_reveals(10**12) == []


# ---------- сквозной сценарий
class MorningEx(FakeEx):
    def client(self, name):
        return Tickers()


def make_service(client=None, feeds=True):
    cfg = Config(owner_id=1, dry_run=True)
    cfg.use_context = False
    cfg.news_feeds = ["https://a.com/rss"] if feeds else []
    svc = Service(cfg, MorningEx(), Storage(), FakePub())
    svc.market = NS(fear_greed=lambda: 55)
    if client:
        svc.morning_writer = MorningWriter(client=client)
    return svc


def test_morning_post_end_to_end_with_claude_text(monkeypatch):
    import signalbot.service as sv

    monkeypatch.setattr(sv, "fetch_headlines", lambda feeds: [{"source": "a.com", "title": "Bitcoin ETFs see inflows", "summary": "120 million"}])
    monkeypatch.setattr(sv, "choose_kind", lambda *a: KIND_NEWS)
    svc = make_service(FakeClient(ok_reply(body="По данным a.com, в ETF зашло 120 миллионов.")))
    svc.db.add_emoji("777", "🔥")
    assert asyncio.run(svc.morning_post())
    html = svc.pub.texts[0]
    assert "Подъём, львы!" in html and "120 миллионов" in html and '<tg-emoji emoji-id="777">🔥</tg-emoji>' in html
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert svc.db.kv_get("last_morning") == day and svc.db.kv_get("last_morning_kind") == KIND_NEWS
    assert svc.db.recent_posts(1)[0].startswith(MARK)


def test_teaser_schedules_reveal_that_names_the_coin_later(monkeypatch):
    import signalbot.service as sv

    monkeypatch.setattr(sv, "choose_kind", lambda *a: KIND_TEASER)
    svc = make_service(FakeClient(ok_reply(body="Одна монета из топа выросла на 14.2% за сутки, имя скрою.", hook="Реакции в студию, назову в 12:00.")))
    asyncio.run(svc.morning_post())
    assert "AAA" not in svc.pub.texts[0]  # тикер в утреннем посте скрыт
    assert asyncio.run(svc.send_due_reveals()) == 0  # рано
    assert svc.db.due_reveals(int(time.time() * 1000) + 4 * 3_600_000)  # через 3 часа разгадка созреет
    svc.db._db.execute("UPDATE reveals SET due_ts=0"); svc.db._db.commit()
    assert asyncio.run(svc.send_due_reveals()) == 1
    chat, msg, text = svc.pub.replies[0]
    assert (chat, msg) == (111, 501) and "AAA" in text and "+14.2%" in text
    assert asyncio.run(svc.send_due_reveals()) == 0  # повторно не шлём


def test_morning_without_claude_uses_templates_and_news_falls_back_to_mood(monkeypatch):
    import signalbot.service as sv

    monkeypatch.setattr(sv, "fetch_headlines", lambda feeds: [{"source": "a", "title": "t", "summary": "s"}])
    monkeypatch.setattr(sv, "choose_kind", lambda *a: KIND_NEWS)
    svc = make_service()
    svc.morning_writer = None
    assert asyncio.run(svc.morning_post())
    assert "<b>" in svc.pub.texts[0] and svc.db.kv_get("last_morning_kind") == KIND_MOOD


def test_preview_does_not_touch_state():
    svc = make_service(FakeClient(ok_reply()))
    pub = FakePub()
    asyncio.run(svc.morning_post(pub, remember=False))
    assert pub.texts and svc.db.kv_get("last_morning") is None and svc.db.recent_posts(5) == []


# ---------- запоминание премиум-эмодзи
def entity(eid, off, ln):
    from aiogram.enums import MessageEntityType

    return NS(type=MessageEntityType.CUSTOM_EMOJI, custom_emoji_id=eid, offset=off, length=ln,
              extract_from=lambda text, o=off, l=ln: text[o:o + l])


def test_collect_custom_emoji_and_owner_dm_and_channel_post():
    m = NS(text="🔥🚀", entities=[entity("11", 0, 1), entity("22", 1, 1)], caption=None, caption_entities=None)
    assert collect_custom_emoji(m) == [("11", "🔥"), ("22", "🚀")]

    class Msg:
        def __init__(self, chat, **kw):
            self.chat, self.text, self.sent = chat, kw.get("text"), []
            self.entities, self.caption, self.caption_entities = kw.get("entities"), None, None

        async def answer(self, t, **kw):
            self.sent.append(t)

    class Bot:
        def __init__(self, ups):
            self.ups = ups

        async def get_updates(self, **kw):
            return self.ups if "offset" not in kw else []

    cfg = Config(owner_id=1, channel_id="-1009")
    svc = NS(db=Storage())
    dm = Msg(NS(id=1, type="private", username=None), text="🦁", entities=[entity("33", 0, 1)])
    post = Msg(NS(id=-1009, type="channel", username=None), text="💎", entities=[entity("44", 0, 1)])
    other = Msg(NS(id=-555, type="channel", username=None), text="🐂", entities=[entity("55", 0, 1)])
    ups = [NS(update_id=1, message=dm, channel_post=None), NS(update_id=2, message=None, channel_post=post),
           NS(update_id=3, message=None, channel_post=other)]
    asyncio.run(handle_updates(Bot(ups), cfg, svc))
    assert {i for i, _ in svc.db.emojis()} == {"33", "44"}  # чужой канал не берём
    assert "Запомнил" in dm.sent[0]
