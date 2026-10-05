import asyncio
from types import SimpleNamespace as NS

from signalbot.commands import handle_updates
from signalbot.config import Config
from signalbot.storage import Storage


class Msg:
    def __init__(self, text, chat_id=1, chat_type="private"):
        self.text, self.chat, self.sent = text, NS(id=chat_id, type=chat_type), []

    async def answer(self, text, **kw):
        self.sent.append(text)


class FakeBot:
    def __init__(self, msgs):
        self.updates = [NS(update_id=10 + i, message=m) for i, m in enumerate(msgs)]
        self.calls = []

    async def get_updates(self, **kw):
        self.calls.append(kw)
        return self.updates if "offset" not in kw else []


class FakeSvc:
    db = Storage()

    def stats_text(self):
        return "STATS"


def run(msgs, owner=1):
    bot, cfg = FakeBot(msgs), Config(owner_id=owner)
    return asyncio.run(handle_updates(bot, cfg, FakeSvc())), bot


def test_start_replies_with_chat_id_and_confirms_updates():
    m = Msg("/start", chat_id=777)
    _, bot = run([m], owner=1)
    assert "777" in m.sent[0] and "не совпадает с OWNER_ID" in m.sent[0]
    assert bot.calls[-1]["offset"] == 11  # подтверждение: update_id + 1


def test_owner_commands_and_scan_flag():
    stats, active, scan = Msg("/stats"), Msg("/active"), Msg("/scan@mybot")
    want, _ = run([stats, active, scan])
    assert want is True
    assert stats.sent == ["STATS"] and active.sent == ["Активных идей нет."] and scan.sent


def test_non_owner_cannot_use_commands_and_groups_ignored():
    other, group = Msg("/scan", chat_id=5), Msg("/start", chat_type="supergroup")
    want, _ = run([other, group], owner=1)
    assert want is False and other.sent == [] and group.sent == []
