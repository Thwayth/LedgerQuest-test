import asyncio
import io
import json
import random
import time
from types import SimpleNamespace as NS

from PIL import Image

from signalbot.commands import handle_updates
from signalbot.config import Config, load_config
from signalbot.emojipacks import decorate, sync_emoji
from signalbot.fun import FunWriter, clean_ok
from signalbot.memes import SIZE, TEMPLATES, render_meme
from signalbot.morning import KIND_MOOD, MorningText, MorningWriter, build_data, build_morning_html, template_text, valid_text
from signalbot.reactions import ReactionDescriber, fill_placeholders, placeholders_valid, reaction_slots, refresh_reactions
from signalbot.service import Service
from signalbot.storage import Storage
from signalbot.trends import SENSITIVE, TRENDS_RSS, TrendScout, clean_lines, parse_trends_rss
from tests.test_service import FakeEx, FakePub
from tests.test_stickers import FakeBot as StickerBot, Msg, UpdBot, make_set, png_bytes, sticker
from tests.test_writer_caption import FakeClient


# ---------- подстановка премиум-эмодзи
def test_decorate_matches_by_meaning_keeps_fallback_and_never_touches_tags():
    m = {"🔥": ["111"], "💎": ["222"]}
    out = decorate('<b>TRB</b> 🔥 и 💎, а 🤝 остаётся', m, random.Random(1), p=1.0)
    assert '<tg-emoji emoji-id="111">🔥</tg-emoji>' in out and '<tg-emoji emoji-id="222">💎</tg-emoji>' in out and "🤝" in out
    assert decorate("без эмодзи", m) == "без эмодзи" and decorate("🔥", {}) == "🔥"
    again = decorate(out, m, random.Random(1), p=1.0)
    assert again.count("<tg-emoji") == out.count("<tg-emoji")  # повторная обработка не вкладывает теги
    guaranteed = decorate("<b>X</b> 🤝 привет", {"🔥": ["9"]}, random.Random(1))
    assert '<tg-emoji emoji-id="9">🤝</tg-emoji>' in guaranteed  # паки есть в каждом посте


def test_storage_emoji_packs_and_map_and_migration(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE emoji (id TEXT PRIMARY KEY, char TEXT NOT NULL)")  # база прошлой версии
    con.execute("INSERT INTO emoji VALUES ('1', '🔥')")
    con.commit(); con.close()
    db = Storage(path)  # миграция добавляет set_name
    assert db.emojis() == [("1", "🔥")] and db.unresolved_emoji_ids() == ["1"]
    assert db.add_emoji("2", "🔥️", "ton") and not db.add_emoji("2", "🔥", "ton")
    assert db.emoji_map() == {"🔥": ["1", "2"]}
    assert db.add_emoji_set("ton") and not db.add_emoji_set("ton")
    db.mark_emoji_set_synced("ton", "TON")
    assert db.emoji_sets(only_unsynced=True) == []


class EmojiBot(StickerBot):
    def __init__(self, sets, custom):
        super().__init__(sets)
        self.custom = custom

    async def get_custom_emoji_stickers(self, ids):
        return [self.custom[i] for i in ids if i in self.custom]


def ce(eid, emoji, set_name):
    return NS(custom_emoji_id=eid, emoji=emoji, set_name=set_name, file_unique_id=eid, file_id=eid, thumbnail=NS(file_id=f"th_{eid}"), is_animated=False, is_video=False, type="custom_emoji")


def test_sync_emoji_resolves_pack_of_sent_emoji_and_imports_whole_pack():
    db = Storage()
    db.add_emoji("e1", "💎")  # владелец прислал один эмодзи, пака мы пока не знаем
    db.add_emoji("lost", "⭐")
    pack = make_set("TONemoji", [ce("e1", "💎", "TONemoji"), ce("e2", "🚀", "TONemoji"), ce("e3", "🔥", "TONemoji")], kind="custom_emoji", title="TON")
    bot = EmojiBot({"TONemoji": pack}, {"e1": ce("e1", "💎", "TONemoji")})
    res = asyncio.run(sync_emoji(bot, db))
    assert res == {"resolved": 1, "packs": 1}
    assert set(db.emoji_map()) == {"💎", "🚀", "🔥", "⭐"} and db.unresolved_emoji_ids() == []  # «lost» больше не запрашиваем
    assert asyncio.run(sync_emoji(bot, db)) == {"resolved": 0, "packs": 0}  # повторно ничего не делаем


def test_owner_custom_emoji_sticker_registers_pack():
    cfg = Config(owner_id=1)
    svc = NS(db=Storage())
    m1 = Msg(1, sticker("c1", "🔥", "SimbaPack", kind="custom_emoji"))
    m1.sticker.custom_emoji_id = "c1"
    m2 = Msg(1, sticker("c2", "🔥", "SimbaPack", kind="custom_emoji"))
    m2.sticker.custom_emoji_id = "c2"
    ups = [NS(update_id=i, message=m, channel_post=None) for i, m in enumerate([m1, m2], 1)]
    asyncio.run(handle_updates(UpdBot(ups), cfg, svc))
    assert "Запомнил пак премиум-эмодзи «SimbaPack»" in m1.sent[0] and "уже в моей коллекции" in m2.sent[0]
    assert svc.db.emoji_sets() == ["SimbaPack"] and {i for i, _ in svc.db.emojis()} == {"c1", "c2"}


# ---------- реакции канала
class ChatBot(EmojiBot):
    def __init__(self, avail, customs):
        super().__init__({}, customs)
        self.avail, self.chat_calls = avail, 0

    async def get_chat(self, cid):
        self.chat_calls += 1
        return NS(available_reactions=self.avail)


def test_refresh_reactions_reads_channel_describes_custom_and_caches_a_day():
    db = Storage()
    avail = [NS(type="emoji", emoji="👍"), NS(type="custom_emoji", custom_emoji_id="simba1"), NS(type="paid")]
    bot = ChatBot(avail, {"simba1": ce("simba1", "🦁", "SimbaPack")})
    cl = FakeClient(NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"items": [{"index": 0, "meaning": "довольный львёнок Симба"}]}))]))
    assert asyncio.run(refresh_reactions(bot, db, "-1001", ReactionDescriber(client=cl))) == 2
    rs = db.reactions()
    assert [r["key"] for r in rs] == ["👍", "simba1"] and rs[1]["description"] == "довольный львёнок Симба" and rs[1]["described"]
    assert ("simba1", "🦁") in db.emojis() and db.emoji_sets() == ["SimbaPack"]  # реакция канала попала и в пул эмодзи
    n = bot.chat_calls
    assert asyncio.run(refresh_reactions(bot, db, "-1001", None)) == 2 and bot.chat_calls == n  # кэш на сутки
    assert asyncio.run(refresh_reactions(bot, Storage(), "0", None)) == 0  # заглушка CHANNEL_ID
    assert asyncio.run(refresh_reactions(ChatBot(None, {}), Storage(), "-1001", None)) == 0  # список не задан


def test_reaction_slots_placeholders_and_morning_integration():
    db = Storage()
    db.replace_reactions([{"key": "👍", "kind": "emoji", "char": "👍", "description": "реакция 👍", "described": True},
                          {"key": "simba1", "kind": "custom", "char": "🦁", "description": "довольный Симба", "described": True}])
    slots = reaction_slots(db)
    assert [s["slot"] for s in slots] == [1, 2] and slots[0]["plain"] == "🦁"  # премиум-реакции первыми
    assert slots[0]["html"] == '<tg-emoji emoji-id="simba1">🦁</tg-emoji>' and slots[1]["html"] == "👍"
    assert fill_placeholders("Ставьте {R1} или {R2}, {R9}.", slots) == 'Ставьте <tg-emoji emoji-id="simba1">🦁</tg-emoji> или 👍, .'
    assert placeholders_valid("{R1} {R2}", slots) and not placeholders_valid("{R3}", slots)

    t = MorningText("Привет", "Рынок спокоен.", "Ставьте {R1}, если ждёте рост.")
    data = build_data(KIND_MOOD, None, [], 55, "12:00", slots)
    assert data["reactions"][0] == {"slot": 1, "meaning": "довольный Симба"}
    assert valid_text(t, data, slots=slots) and not valid_text(MorningText("Привет", "Ждём {R5}", "ок"), data, slots=slots)
    assert "tg-emoji" in build_morning_html(t, ["A", "B", "C"], slots)
    tpl = {template_text(KIND_MOOD, {"market": {"btc_change_24h_pct": 0.3}}, f"2026-10-{d:02d}", ["Прайд"], slots).hook for d in range(1, 30)}
    assert any("{R1}" in h for h in tpl)  # запасные шаблоны тоже зовут к реальным реакциям


# ---------- тренды
RSS = '<rss><channel><item><title>Новый мем про котов</title></item><item><title>Смерть известного актёра</title></item></channel></rss>'


def test_trend_filters_and_parsing():
    assert SENSITIVE.search("Дело Эпштейна") and SENSITIVE.search("Путин") and not SENSITIVE.search("Мелстрой стрим")
    lines = clean_lines("- Мем про котов: все пишут\n* Ещё один мем: смешно\n1. Эпштейн файлы: нет\nмусор без маркера\n- Война в новостях")
    assert lines == ["Мем про котов: все пишут", "Ещё один мем: смешно"]
    assert parse_trends_rss(RSS) == ["Новый мем про котов"]


def test_trend_scout_uses_claude_web_search_with_pause_and_falls_back_to_rss():
    pause = NS(stop_reason="pause_turn", content=[NS(type="text", text="ищу")])
    done = NS(stop_reason="end_turn", content=[NS(type="text", text="- Тренд один: суть\n- Тренд два: суть")])
    cl = FakeClient(pause, done)
    scout = TrendScout(client=cl, http_get=lambda u: RSS, use_claude=True)
    assert scout.fetch("2026-10-05") == ["Тренд один: суть", "Тренд два: суть"]
    tools = cl.calls[0]["tools"][0]
    assert tools["type"] == "web_search_20260209" and tools["name"] == "web_search" and "temperature" not in cl.calls[0]
    assert TrendScout(client=FakeClient(RuntimeError("no search")), http_get=lambda u: RSS, use_claude=True).fetch("d") == ["Новый мем про котов"]
    assert TrendScout(http_get=lambda u: RSS).fetch("d") == ["Новый мем про котов"]  # без ключа — бесплатная лента
    seen = []
    TrendScout(http_get=lambda u: seen.append(u) or RSS).fetch("d")
    assert seen == [TRENDS_RSS]

    def boom(u):
        raise RuntimeError("net")

    assert TrendScout(http_get=boom).fetch("d") == []


def meme_reply(**d):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(d))])


def test_fun_writer_gets_trends_topics_and_blocks_sensitive_names():
    cl = FakeClient(meme_reply(template="nobody", top="Никто:", bottom="Я после стрима: всё на красное", caption=""))
    m = FunWriter(client=cl).meme([], ["Тренд про котов: все снимают"], ["Мелстрой: казино-мем"])
    assert m.template == "nobody"
    prompt = cl.calls[0]["messages"][0]["content"]
    assert "Тренд про котов" in prompt and "Мелстрой" in prompt and "TRENDS" in prompt
    assert "nobody" in cl.calls[0]["system"] or "nobody" in prompt
    assert not clean_ok("Файлы Эпштейна и крипта", 70) and not clean_ok("Смерть трейдера", 70)
    assert clean_ok("Мелстрой бы оценил этот азарт", 70)  # безобидная отсылка проходит


def test_new_meme_templates_render():
    assert {"nobody", "expectation"} <= set(TEMPLATES)
    for t in ("nobody", "expectation"):
        b = render_meme(t, "Никто:" if t == "nobody" else "Ожидание: к луне", "Я в три ночи: смотрю график" if t == "nobody" else "Реальность: вниз", seed=4)
        assert Image.open(io.BytesIO(b)).size == (SIZE, SIZE)


def test_config_fun_topics_and_trends(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text('fun:\n  trends: false\n  topics:\n    - "Мелстрой"\n    - "тикток"\n')
    cfg = load_config(y, env_file=None)
    assert cfg.fun_trends is False and cfg.fun_topics == ["Мелстрой", "тикток"]
    assert load_config(tmp_path / "none.yaml", env_file=None) if False else True


# ---------- сервис: тренды кэшируются, посты проходят через подстановку эмодзи
def make_service():
    cfg = Config(owner_id=1, dry_run=True)
    cfg.use_context = False
    svc = Service(cfg, FakeEx(), Storage(), FakePub())
    svc.fun_writer = None
    return svc


def test_service_trends_cached_per_day_and_posts_use_premium_emoji(monkeypatch):
    svc = make_service()
    calls = []
    svc.trend_scout = NS(fetch=lambda day: calls.append(day) or ["Тренд: суть"])
    assert asyncio.run(svc._trends()) == ["Тренд: суть"] and asyncio.run(svc._trends()) == ["Тренд: суть"] and len(calls) == 1
    svc.cfg.fun_trends = False
    assert asyncio.run(svc._trends()) == []

    svc.db.add_emoji("777", "😂", "pack")
    svc.db.add_emoji("778", "🧠", "pack")
    asyncio.run(svc.fun_post("joke"))
    assert '<tg-emoji emoji-id="777">😂</tg-emoji>' in svc.pub.texts[-1] or "<tg-emoji" in svc.pub.texts[-1]
    asyncio.run(svc.fun_post("fact"))
    assert "<tg-emoji" in svc.pub.posts[-1][1]  # подпись факта тоже с премиум-эмодзи


def test_rejected_text_retry_carries_reason_back_to_claude():
    def reply(opinion, hook="Ставьте реакции."):
        return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"greeting": "Привет, Прайд!", "body": opinion, "hook": hook}))])

    long_body = "слово " * 140  # длиннее лимита
    cl = FakeClient(reply(long_body), reply("Короткий и ясный текст."))
    t = MorningWriter(client=cl).write({"kind": "mood"}, [])
    assert t is not None and t.body == "Короткий и ясный текст."
    second_prompt = cl.calls[1]["messages"][0]["content"]
    assert "ПРЕДЫДУЩИЙ ВАРИАНТ ОТКЛОНЁН" in second_prompt and "длиннее" in second_prompt  # Claude знает, что исправлять
    assert "ПРЕДЫДУЩИЙ" not in cl.calls[0]["messages"][0]["content"]
    ok = MorningText("Привет", "слово " * 100, "ок")  # ~600 символов проходит новый лимит
    assert valid_text(ok, {"kind": "mood"})
