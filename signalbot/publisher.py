"""Отправка сообщений в Telegram (aiogram)."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile

log = logging.getLogger(__name__)


class Publisher(Protocol):
    async def post_photo(self, png: bytes, caption: str, name: str) -> tuple[int, int]: ...
    async def reply(self, chat_id: int, message_id: int, text: str) -> None: ...
    async def send(self, chat_id: int | str, text: str) -> None: ...


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
        await self._retry(
            lambda: self.bot.send_message(chat_id, text, parse_mode="HTML", reply_to_message_id=message_id)
        )

    async def send(self, chat_id: int | str, text: str) -> None:
        await self._retry(lambda: self.bot.send_message(chat_id, text, parse_mode="HTML"))
