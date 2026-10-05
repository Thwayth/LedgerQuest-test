import asyncio
import io
import json
import random
from types import SimpleNamespace as NS

from PIL import Image

from signalbot.commands import handle_updates
from signalbot.config import Config
from signalbot.service import Service
from signalbot.stickers import SITUATIONS, StickerCatalog, StickerDescriber, emoji_tags
from signalbot.storage import Storage
from tests.test_service import FakeEx, FakePub
from tests.test_writer_caption import FakeClient


def png_bytes(color="red"):
    b = io.BytesIO()
    Image.new("RGBA", (64, 64), color).save(b, "PNG")
    return b.getvalue()


def sticker(uid, emoji="🔥", set_name="packA", kind="regular", thumb=True, animated=False):
    return NS(file_id=f"fid_{uid}", file_unique_id=uid, emoji=emoji, set_name=set_name, type=kind, is_animated=animated, is_video=False,
              thumbnail=NS(file_id=f"th_{uid}") if thumb else None)


class FakeBot:
    def __init__(self, sets, fail=()):
        self.sets, self.fail, self.downloads = sets, set(fail), []

    async def get_sticker_set(self, name):
        if name in self.fail:
            raise RuntimeError("not found")
        return self.sets[name]

    async def download(self, file_id):
        self.downloads.append(file_id)
        if file_id == "th_broken":
            raise RuntimeError("gone")
        return io.BytesIO(png_bytes())


def make_set(name, stickers, kind="regular", title=None):
    return NS(name=name, title=title or name.upper(), sticker_type=kind, stickers=stickers)


def described_reply(items):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"items": items}))])


def test_emoji_tags_baseline():
    assert "loss" in emoji_tags("😭") and "morning" in emoji_tags("☀️") and "laugh" in emoji_tags("🤣")
    assert emoji_tags(None) == [] and emoji_tags("🦄") == []
    assert set(sum((emoji_tags(e) for e in "🔥🚀😭☀🤔"), [])) <= set(SITUATIONS)


def test_describer_sends_images_and_filters_unknown_tags():
    cl = FakeClient(described_reply([{"index": 0, "description": "лев ухмыляется", "tags": ["laugh", "bogus", "hype"]},
                                     {"index": 1, "description": "грустный кот", "tags": ["loss"]}]))
    res = StickerDescriber(client=cl).describe([png_bytes("red"), png_bytes("blue")])
    assert res == {0: ("лев ухмыляется", ["laugh", "hype"]), 1: ("грустный кот", ["loss"])}
    kw = cl.calls[0]
    blocks = kw["messages"][0]["content"]
    assert sum(1 for b in blocks if b["type"] == "image") == 2 and kw["model"] == "claude-sonnet-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema" and "temperature" not in kw
    assert StickerDescriber(client=FakeClient(RuntimeError("x"))).describe([png_bytes()]) is None
    assert StickerDescriber(client=FakeClient(NS(stop_reason="refusal", content=[]))).describe([png_bytes()]) is None


def test_sync_pulls_sets_tags_by_emoji_then_claude_refines_once():
    db = Storage()
    db.add_sticker_set("packA"); db.add_sticker_set("packB"); db.add_sticker_set("emojis"); db.add_sticker_set("ghost")
    bot = FakeBot({
        "packA": make_set("packA", [sticker("a1", "🔥", "packA"), sticker("a2", "😭", "packA"), sticker("a3", "🦄", "packA", thumb=False, animated=True)]),
        "packB": make_set("packB", [sticker("b1", "☀️", "packB"), sticker("broken", "😂", "packB")]),
        "emojis": make_set("emojis", [sticker("e1", "🔥", "emojis", kind="custom_emoji")], kind="custom_emoji"),
    }, fail=["ghost"])
    bot.sets["packB"].stickers[1].thumbnail = NS(file_id="th_broken")
    cl = FakeClient(described_reply([{"index": 0, "description": "огонь", "tags": ["hype", "bullish"]},
                                     {"index": 1, "description": "слёзы", "tags": ["loss"]},
                                     {"index": 2, "description": "рассвет", "tags": []}]))
    cat = StickerCatalog(db, StickerDescriber(client=cl))
    res = asyncio.run(cat.sync(bot))
    assert res["added"] == 5 and res["described"] == 3
    assert db.sticker_sets(only_unsynced=True) == ["ghost"]  # недоступный пак повторится в следующий раз
    by = {s["unique_id"]: s for s in db.stickers()}
    assert "e1" not in by  # пак кастомных эмодзи пропущен
    assert by["a1"]["tags"] == ["hype", "bullish"] and by["a1"]["described"]
    assert by["a3"]["tags"] == [] and not by["a3"]["described"]  # анимированный без превью: останется на тегах по эмодзи
    assert by["b1"]["tags"] == [] and by["b1"]["described"]  # Claude решил: ни к чему не подходит
    assert by["broken"]["described"] and by["broken"]["tags"] == ["laugh"]  # превью недоступно -> остаётся разметка по эмодзи
    n_calls = len(cl.calls)
    asyncio.run(cat.sync(bot))
    assert len(cl.calls) == n_calls  # повторно ничего не размечаем и не тратим деньги
    assert asyncio.run(StickerCatalog(Storage()).sync(FakeBot({}))) == {"added": 0, "described": 0}


def test_sync_without_claude_still_usable_via_emoji_tags():
    db = Storage()
    db.add_sticker_set("packA")
    bot = FakeBot({"packA": make_set("packA", [sticker("a1", "😭"), sticker("a2", "☀️")])})
    cat = StickerCatalog(db, describer=None)
    assert asyncio.run(cat.sync(bot))["described"] == 0
    assert cat.pick("loss") == "fid_a1" and cat.pick("morning") == "fid_a2" and cat.pick("think") is None


def test_pick_alternates_packs_and_avoids_recent():
    db = Storage()
    for i in range(3):
        db.upsert_sticker(f"a{i}", f"fa{i}", "packA", "☀️", ["morning"], None)
        db.upsert_sticker(f"b{i}", f"fb{i}", "packB", "☀️", ["morning"], None)
    cat = StickerCatalog(db)
    rng = random.Random(7)
    picks = [cat.pick("morning", rng) for _ in range(6)]
    assert len(set(picks)) == 6  # пока есть неиспользованные, повторов нет
    sets = [p[1] for p in picks]  # 'a' или 'b' из file_id
    switches = sum(1 for x, y in zip(sets, sets[1:]) if x != y)
    assert switches >= 3  # паки чередуются, а не идут подряд


def test_pick_prefers_claude_described():
    db = Storage()
    db.upsert_sticker("x", "fx", "p", "🔥", ["hype"], None)
    db.upsert_sticker("y", "fy", "p", "🔥", ["hype"], None)
    db.set_sticker_description("y", "огонь", ["hype"])
    wins = {"fx": 0, "fy": 0}
    for seed in range(300):
        db.kv_set("sticker_recent", "[]")
        wins[StickerCatalog(db).pick("hype", random.Random(seed))] += 1
    assert wins["fy"] > wins["fx"] * 1.5


class Msg:
    def __init__(self, chat_id, st=None, text=None):
        self.chat, self.sticker, self.text, self.entities = NS(id=chat_id, type="private", username=None), st, text, None
        self.caption = self.caption_entities = None
        self.sent = []

    async def answer(self, t, **kw):
        self.sent.append(t)


class UpdBot:
    def __init__(self, ups):
        self.ups = ups

    async def get_updates(self, **kw):
        return self.ups if "offset" not in kw else []


def test_owner_sticker_is_remembered_and_others_ignored():
    cfg = Config(owner_id=1)
    svc = NS(db=Storage(), stickers=NS(stats=lambda: "Паков: 1"))
    s1, s2 = Msg(1, sticker("s1", "🔥", "pack1")), Msg(1, sticker("s2", "😂", "pack1"))
    emo = Msg(1, sticker("c1", "🔥", None, kind="custom_emoji"))
    stranger = Msg(99, sticker("z", "🔥", "packZ"))
    stats = Msg(1, text="/stickers")
    ups = [NS(update_id=i, message=m, channel_post=None) for i, m in enumerate([s1, s2, emo, stranger, stats], 1)]
    asyncio.run(handle_updates(UpdBot(ups), cfg, svc))
    assert svc.db.sticker_sets() == ["pack1"]
    assert "Запомнил стикерпак «pack1»" in s1.sent[0] and "уже в моей коллекции" in s2.sent[0]
    assert "кастомные эмодзи" in emo.sent[0] and stranger.sent == []
    assert {s["unique_id"] for s in svc.db.stickers()} == {"s1", "s2"}
    assert stats.sent == ["Паков: 1"]


def make_service(chance=None, stickers=True):
    cfg = Config(owner_id=1, dry_run=True)
    cfg.use_context = False
    cfg.sticker_chance = chance or {}
    cfg.stickers_enabled = stickers
    svc = Service(cfg, FakeEx(), Storage(), FakePub())
    svc.db.upsert_sticker("l1", "fid_l1", "packA", "😂", ["laugh"], None)
    svc.db.upsert_sticker("p1", "fid_p1", "packB", "💰", ["profit"], None)
    return svc


def test_maybe_sticker_chance_reply_and_failure_isolation():
    svc = make_service({"laugh": 0.0, "profit": 1.0})
    assert not asyncio.run(svc.maybe_sticker("laugh"))  # вероятность 0
    assert asyncio.run(svc.maybe_sticker("laugh", force=True))
    assert asyncio.run(svc.maybe_sticker("profit", reply_to=(5, 6)))
    assert ("reply", 5, 6, "fid_p1") in svc.pub.stickers and ("post", "fid_l1") in svc.pub.stickers
    assert not asyncio.run(svc.maybe_sticker("think", force=True))  # для ситуации стикеров нет
    assert not asyncio.run(make_service(stickers=False).maybe_sticker("laugh", force=True))

    async def boom(*a):
        raise RuntimeError("telegram down")

    svc.pub.post_sticker = boom
    assert asyncio.run(svc.maybe_sticker("laugh", force=True)) is False  # сбой не ломает пост


def test_fun_and_signal_events_use_situational_stickers():
    svc = make_service({"laugh": 1.0})
    asyncio.run(svc.fun_post("joke"))
    assert ("post", "fid_l1") in svc.pub.stickers  # после шутки — стикер «смех»


def test_sticker_preview_labels_each_situation():
    svc = make_service()
    pub = FakePub()
    assert asyncio.run(svc.sticker_preview(pub)) == 2
    assert any("<b>laugh</b>" in t for t in pub.texts) and len(pub.stickers) == 2
    empty = Service(Config(owner_id=1), FakeEx(), Storage(), FakePub())
    pub2 = FakePub()
    assert asyncio.run(empty.sticker_preview(pub2)) == 0 and "пришлите" in pub2.texts[0]


def test_storage_sticker_upsert_keeps_claude_tags():
    db = Storage()
    assert db.upsert_sticker("u", "f1", "p", "🔥", ["hype"], "t1")
    db.set_sticker_description("u", "огонь", ["bullish"])
    assert not db.upsert_sticker("u", "f2", "p", "🔥", ["hype"], None)  # тот же стикер, новый file_id
    s = db.stickers()[0]
    assert s["file_id"] == "f2" and s["tags"] == ["bullish"] and s["described"]
    assert db.pending_stickers(10) == []
