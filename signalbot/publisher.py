"""Отправка сообщений в Telegram (aiogram)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

log = logging.getLogger(__name__)


class Publisher(Protocol):
    async def post_photo(self, png: bytes, caption: str, name: str) -> tuple[int, int]: ...
    async def reply(self, chat_id: int, message_id: int, text: str) -> None: ...
    async def send(self, chat_id: int | str, text: str) -> None: ...
    async def post_text(self, text: str) -> tuple[int, int]: ...
    async def post_sticker(self, file_id: str) -> None: ...
    async def reply_sticker(self, chat_id: int, message_id: int, file_id: str) -> None: ...


_CUSTOM_EMOJI = re.compile(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", re.S)


def strip_custom_emoji(text: str) -> str:
    """Кастомные эмодзи -> обычные символы внутри тега (если Telegram их не принял)."""
    return _CUSTOM_EMOJI.sub(r"\1", text)


class TelegramPublisher:
    def __init__(self, bot: Bot, target: int | str):
        self.bot, self.target = bot, target

    async def _retry(self, coro_fn, attempts: int = 4):
        for i in range(attempts):
            try:
                return await coro_fn()
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramNetworkError:
                if i == attempts - 1:
                    raise
                await asyncio.sleep(2**i)
        raise RuntimeError("Telegram: исчерпаны попытки")

    async def post_photo(self, png: bytes, caption: str, name: str = "idea") -> tuple[int, int]:
        msg = await self._retry(
            lambda: self.bot.send_photo(
                self.target, BufferedInputFile(png, filename=f"{name}.png"), caption=caption, parse_mode="HTML"
            )
        )
        return msg.chat.id, msg.message_id

    async def reply(self, chat_id: int, message_id: int, text: str) -> None:
        await self._send_html(chat_id, text, reply_to=message_id)

    async def _send_html(self, chat_id: int | str, text: str, reply_to: int | None = None):
        async def go(t: str):
            return await self._retry(lambda: self.bot.send_message(chat_id, t, parse_mode="HTML", reply_to_message_id=reply_to))

        try:
            return await go(text)
        except TelegramBadRequest as e:
            # боту могут быть недоступны премиум-эмодзи: тогда отправляем обычные
            if "<tg-emoji" in text and any(w in str(e).lower() for w in ("emoji", "entit")):
                log.warning("Telegram не принял кастомные эмодзи, отправляю обычные: %s", e)
                return await go(strip_custom_emoji(text))
            raise

    async def send(self, chat_id: int | str, text: str) -> None:
        await self._send_html(chat_id, text)

    async def post_sticker(self, file_id: str) -> None:
        await self._retry(lambda: self.bot.send_sticker(self.target, file_id))

    async def reply_sticker(self, chat_id: int, message_id: int, file_id: str) -> None:
        await self._retry(lambda: self.bot.send_sticker(chat_id, file_id, reply_to_message_id=message_id))

    async def post_text(self, text: str) -> tuple[int, int]:
        msg = await self._send_html(self.target, text)
        return msg.chat.id, msg.message_id
