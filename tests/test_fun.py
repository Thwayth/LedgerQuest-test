import asyncio
import base64
import io
import json
import random
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from signalbot.config import Config, load_config
from signalbot.fun import MARK, FunWriter, clean_ok, pick_unused
from signalbot.funbank import FACTS, JOKES, MEMES
from signalbot.memes import SIZE, TEMPLATES, prepare_user_image, render_card, render_meme
from signalbot.morning import PROFANITY
from signalbot.oneshot import fun_due
from signalbot.service import Service
from signalbot.storage import Storage
from tests.test_service import FakeEx, FakePub
from tests.test_writer_caption import FakeClient


def png_size(b):
    return Image.open(io.BytesIO(b)).size


def test_every_template_renders_square_png_with_cyrillic():
    for t in TEMPLATES:
        b = render_meme(t, "Купил на хаях", "Продал на лоях. Всё по плану", seed=3)
        assert b.startswith(b"\x89PNG") and png_size(b) == (SIZE, SIZE)
    assert png_size(render_card("ФАКТ", "Текст факта " * 12, seed=1)) == (SIZE, SIZE)
    assert render_meme("lion", "x", "y", mascot_path="/no/such.png").startswith(b"\x89PNG")  # без талисмана не падает


def test_all_bank_memes_render_and_banks_are_clean():
    for tpl, top, bottom in MEMES:
        assert tpl in TEMPLATES and len(top) <= 70 and len(bottom) <= 70
        render_meme(tpl, top, bottom, seed=1)
    for text in [j for j in JOKES] + [f[1] for f in FACTS] + [m[1] + " " + m[2] for m in MEMES]:
        assert not PROFANITY.search(text)
    assert len({f[0] for f in FACTS}) == len(FACTS)
    assert all(len(f[1]) <= 210 for f in FACTS)  # влезает в карточку


def test_clean_ok_rules():
    assert clean_ok("Рынок опять нас проверяет", 70)
    assert not clean_ok("Это ебанутый рост", 70) and not clean_ok("Будет памп", 70) and not clean_ok("Путин вернулся", 70)
    assert not clean_ok("a" * 80, 70) and not clean_ok("<b>x</b>", 70)
    assert not clean_ok("Выросло на 50%", 70)  # чисел нет в разрешённых
    assert clean_ok("В 2010 году купил пиццу", 70, allowed_numbers={"2010"})
    assert clean_ok("", 70, allow_empty=True) and not clean_ok("", 70)


def test_pick_unused_cycles_without_repeats():
    rng = random.Random(1)
    used, seen = [], []
    for _ in range(5):
        i, used = pick_unused(5, used, rng)
        seen.append(i)
    assert sorted(seen) == [0, 1, 2, 3, 4]
    j, used2 = pick_unused(5, used, rng)  # всё использовано: круг заново
    assert used2 == [j]


def reply(**d):
    return NS(stop_reason="end_turn", content=[NS(type="text", text=json.dumps(d))])


def test_fun_writer_meme_fact_joke_and_validation():
    w = FunWriter(client=FakeClient(reply(template="lion", top="Я после стопа", bottom="Рынок: бывает", caption="Узнали себя")))
    m = w.meme([])
    assert m.template == "lion" and m.top == "Я после стопа"
    kw = w._client.calls[0]
    assert kw["model"] == "claude-sonnet-5-5" and kw["output_config"]["format"]["type"] == "json_schema" and "temperature" not in kw
    assert FunWriter(client=FakeClient(reply(template="lion", top="Будет памп", bottom="b", caption=""), reply(template="lion", top="ок", bottom="ок", caption=""))).meme([]) is not None
    assert FunWriter(client=FakeClient(RuntimeError("x"))).meme([]) is None

    fact = "22 мая 2010 года программист купил две пиццы за 10 000 BTC."
    ok = FunWriter(client=FakeClient(reply(card="В 2010 году купили две пиццы за 10 000 BTC.", comment="Самая дорогая пицца в истории.")))
    assert ok.fact(fact, []) is not None
    bad = FunWriter(client=FakeClient(reply(card="В 2011 году две пиццы за 5 BTC.", comment="ок"), reply(card="Пицца за 99 BTC", comment="ок")))
    assert bad.fact(fact, []) is None  # выдуманные числа не проходят

    assert FunWriter(client=FakeClient(reply(joke="Трейдер вошёл в позицию и вышел из себя."))).joke([]) == "Трейдер вошёл в позицию и вышел из себя."
    assert FunWriter(client=FakeClient(NS(stop_reason="refusal", content=[]))).joke([]) is None


def make_service(writer=None, memes_dir="/nonexistent"):
    cfg = Config(owner_id=1, dry_run=True, memes_dir=memes_dir)
    cfg.use_context = False
    svc = Service(cfg, FakeEx(), Storage(), FakePub())
    svc.fun_writer = writer
    return svc


def test_fun_post_all_kinds_without_claude_use_banks_and_do_not_repeat():
    svc = make_service()
    assert asyncio.run(svc.fun_post("meme")) == "meme" and svc.pub.posts[-1][0].startswith(b"\x89PNG")
    assert asyncio.run(svc.fun_post("fact")) == "fact"
    assert asyncio.run(svc.fun_post("joke")) == "joke" and svc.pub.texts
    assert asyncio.run(svc.fun_post("mix")) in ("meme", "joke")
    facts_before = json.loads(svc.db.kv_get("fun_used_facts"))
    for _ in range(4):
        asyncio.run(svc.fun_post("fact"))
    used = json.loads(svc.db.kv_get("fun_used_facts"))
    assert len(used) == len(set(used)) == len(facts_before) + 4  # без повторов
    assert all(t.startswith(MARK) for t in svc.db.recent_posts(3))


def test_fun_post_uses_claude_text():
    w = FunWriter(client=FakeClient(reply(template="two_panel", top="Входить по крику", bottom="Входить по уровню", caption="Выбирайте с умом")))
    svc = make_service(w)
    asyncio.run(svc.fun_post("meme"))
    assert "Выбирайте с умом" in svc.pub.posts[-1][1]
    w2 = FunWriter(client=FakeClient(reply(card="Факт без единой цифры.", comment="Дефицит это модно.")))
    svc2 = make_service(w2)
    asyncio.run(svc2.fun_post("fact"))
    assert "Дефицит это модно." in svc2.pub.posts[-1][1]


def test_fun_post_with_user_meme_folder_and_vision_caption(tmp_path):
    img = Image.new("RGB", (800, 600), "orange")
    img.save(tmp_path / "doge.png")
    Image.new("RGB", (50, 50)).save(tmp_path / "anim.gif")
    (tmp_path / "notes.txt").write_text("не картинка")
    w = FunWriter(client=FakeClient(reply(caption="Когда зашёл по плану")))
    svc = make_service(w, str(tmp_path))
    assert sorted(svc._user_memes()) == ["anim.gif", "doge.png"]
    for day_salt in range(40):  # файл выпадает с вероятностью 50%: прогоняем, пока не выпадет
        svc.fun_writer = FunWriter(client=FakeClient(reply(caption="Когда зашёл по плану"), reply(caption="Когда зашёл по плану"), reply(template="lion", top="a", bottom="b", caption="")))
        asyncio.run(svc.fun_post("meme"))
        if svc._used_files():
            break
        svc.db.add_post(day_salt, "salt")  # меняет зерно выбора
    assert svc._used_files(), "файл так и не выбран"
    assert any("Когда зашёл по плану" in p[1] for p in svc.pub.posts if p[2] == "meme")
    sent = svc.pub.posts[-1][0]
    assert Image.open(io.BytesIO(sent)).format == "JPEG"


def test_prepare_user_image_converts_and_limits_size(tmp_path):
    Image.new("RGBA", (4000, 3000), (10, 20, 30, 128)).save(tmp_path / "big.png")
    out = Image.open(io.BytesIO(prepare_user_image(tmp_path / "big.png")))
    assert out.format == "JPEG" and max(out.size) == 1600


def test_fun_preview_sends_three_without_state():
    svc = make_service()
    pub = FakePub()
    assert asyncio.run(svc.fun_preview(pub)) == 3
    assert len(pub.posts) == 2 and len(getattr(pub, "texts", [])) == 1
    assert svc.db.kv_get("fun_used_facts") is None and svc.db.recent_posts(5) == []


def test_fun_due_window_once_per_day_and_config_slots(tmp_path):
    d = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=timezone.utc)
    assert fun_due(d(10, 0), 10, 0, None) and fun_due(d(13, 59), 10, 0, "2026-10-04")
    assert not fun_due(d(9, 59), 10, 0, None) and not fun_due(d(14, 0), 10, 0, None) and not fun_due(d(10, 30), 10, 0, "2026-10-05")
    y = tmp_path / "c.yaml"
    y.write_text('fun:\n  slots:\n    - {time_msk: "13:00", kind: meme}\n    - {time_msk: "01:30", kind: joke}\n')
    assert load_config(y, env_file=None).fun_slots == [(10, 0, "meme"), (22, 30, "joke")]
    y.write_text('fun:\n  slots:\n    - {time_msk: "13:00", kind: bogus}\n')
    with pytest.raises(ValueError):
        load_config(y, env_file=None)


def test_fun_writer_tolerates_malformed_json_fields():
    for method, args in (("meme", ([],)), ("joke", ([],)), ("fact", ("факт", []))):
        w = FunWriter(client=FakeClient(reply(wrong="field"), reply(wrong="field")))
        assert getattr(w, method)(*args) is None  # не падает, а возвращает None -> сервис возьмёт банк
