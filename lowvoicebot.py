#!/usr/bin/env python3

import logging
import sys
import os
from collections import namedtuple
import re
import asyncio
from time import time as now

from async_lru import alru_cache
from aiohttp import ClientSession as HTTPClientSession
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import (
    InlineQuery,
    InputTextMessageContent,
    InlineQueryResultArticle,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.utils.exceptions import InvalidQueryID
from aiogram.types.message import ParseMode
from aiogram.dispatcher.filters.builtin import CommandStart
from aiogram.dispatcher.handler import SkipHandler

types.User.__repr__ = lambda u: f"User({u.mention})"

logger = logging.getLogger("lowvoicebot")


class BotTokenUnspecified(Exception):
    pass


class ReadableException(Exception):
    content = "❌ Unspecified Error ❌"

    def __init__(self, content, *args, **kwargs):
        self.content = content
        super().__init__(*args, **kwargs)


REGEX_RESOLVE_USER = re.compile(r'<div\ class="tgme_page_title">(.+?)</div>')
HTTP_SESSION = None


@alru_cache(maxsize=1024)
async def resolve_user(username):
    global HTTP_SESSION
    if not HTTP_SESSION:
        HTTP_SESSION = HTTPClientSession()
    try:
        async with HTTP_SESSION.get(f"https://t.me/{username}") as response:
            assert response.status == 200
            return REGEX_RESOLVE_USER.search(await response.text()).group(1)
    except Exception as e:
        logger.debug(f"Failed to resolve {username} with error {e}")
        return None


WhisperEntry = namedtuple("WhisperEntry", ("sender", "recipient", "content"))

whispers = dict()

expiring_tasks = dict()

def expire_whisper(id, in_seconds=0):
    if id in expiring_tasks:
        expiring_tasks[id].cancel()
        del expiring_tasks[id]
    async def _expire_whisper(id, in_seconds):
        await asyncio.sleep(in_seconds)
        expired_whisper = whispers.pop(id)
        logger.debug(f"Message expired: {expired_whisper!s}")
    return _expire_whisper(id, in_seconds)


bot_token = os.environ.get("BOT_TOKEN")
if not bot_token:
    with open("./.BOT_TOKEN") as f:
        bot_token = f.read().strip()
    if not bot_token:
        raise BotTokenUnspecified

bot = Bot(token=bot_token)
dispatcher = Dispatcher(bot)


# Due to the design limitation of aiogram, the precedence of handler cannot be specified explicitly and only one
# handler can be triggered per message (unless `raise SkipHandler`).
# Therefore, this more generic handler should be loaded after the above so that the above won't be shaded.
@dispatcher.message_handler(commands=["start"])
async def start_handler(message: types.Message):
    logger.debug(f"start_handler:{message.text}")
    args = message.get_args()
    if not args:
        await message.reply(
            "Low Voice Bot helps send *private ️messages* in public groups.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton("Start", switch_inline_query="")]]
            ),
        )
    else:
        raise SkipHandler


@dispatcher.message_handler(CommandStart(re.compile(r"SAVE\_(?P<whisper_id>.+)")))
async def start_save_handler(message: types.Message, deep_link: re.Match):
    logger.debug(f"start_save_handler:{deep_link}")
    try:
        try:
            whisper_id = deep_link.group("whisper_id")
        except IndexError:
            raise ReadableException("❌ Malformed arguments")
        whisper = whispers.get(whisper_id)
        if whisper is None:
            logger.debug(f"start_handler:Invalid whisper id: {whisper_id}")
            raise ReadableException("⏲️🔔/❌ The message is expired or non-existent.")
        if (
            message.from_user.username != whisper.recipient
            and message.from_user != whisper.sender
        ):
            raise ReadableException("🚫 You are neither the sender nor recipient.")
        await message.answer(
            f"_Message from_ {whisper.sender.mention} _to_ @{whisper.recipient}:\n\n{whisper.content}",
            parse_mode=ParseMode.MARKDOWN,
        )
    except ReadableException as e:
        await message.reply(e.content, parse_mode=ParseMode.MARKDOWN)


@dispatcher.message_handler(commands=["ping"])
async def ping_handler(message: types.Message):
    await message.reply("Pong")


@dispatcher.inline_handler()
async def whisper_inline_handler(query: InlineQuery):
    logger.debug(f"whisper_inline_handler")
    try:
        bot_username = (await bot.get_me()).username
        sender = query.from_user
        if not query.query:
            raise ReadableException(
                (
                    f"EMPTY_ARG",
                    f"ℹ️ Show Usage",
                    f"ℹ️ Usage: `@{bot_username} @RECIPIENT message)`",
                )
            )
        try:
            recipient, whisper = query.query.split(maxsplit=1)
        except ValueError:
            raise ReadableException(
                (
                    "USAGE_ERROR",
                    "❌ Usage Error",
                    f"❌ Usage Error\nUsage: `@{bot_username} @RECIPIENT message`",
                )
            )

        if recipient.startswith("@"):
            recipient = recipient[1:]
        recipient_name = await resolve_user(recipient)
        if recipient_name is None:
            raise ReadableException(
                ("INVALID_USERNAME", "❌ Invalid Username", "❌ Invalid Username")
            )
        whisper_id = f"WHISPER-{int(now()):x}-{id(whisper):x}"
        input_content = InputTextMessageContent(
            f"*Private Message*\n_To_ {recipient_name}(@{recipient}),\nexpiring in 30 minutes.",
            parse_mode=ParseMode.MARKDOWN,
        )
        button_reveal = InlineKeyboardButton(
            "🔎 Reveal", callback_data=f"REVEAL|{whisper_id}"
        )
        button_save = InlineKeyboardButton(
            "💾 Save", url=f"https://t.me/{bot_username}?start=SAVE_{whisper_id}"
        )
        button_expire = InlineKeyboardButton(
            "🛑 Expire", callback_data=f"EXPIRE|{whisper_id}"
        )
        item_default = InlineQueryResultArticle(
            id=f"{whisper_id}-1",
            # f'✉️ To {recipient_name}, ⏲️ Expire in 30 minutes',
            title=f"With Save button",
            input_message_content=input_content,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[button_reveal, button_save, button_expire]]
            ),
        )
        item_nosave = InlineQueryResultArticle(
            id=f"{whisper_id}-2",
            # f'✉️ To {recipient_name}, \n⏲️ Expire in 30 minutes, No save button',
            title=f"Without Save button",
            input_message_content=input_content,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[button_reveal, button_expire]]
            ),
        )
        try:
            if (
                await query.answer(
                    results=[item_default, item_nosave], cache_time=3, is_personal=True
                )
            ) is True:
                logger.info(
                    f"From {sender.mention} to {recipient_name}(@{recipient}): WHISPER_REDACTED"
                )
                whispers[whisper_id] = WhisperEntry(sender, recipient, whisper)
                # TODO: permenant trhu  encryption
                expiring_tasks[whisper_id] = asyncio.create_task(
                    expire_whisper(whisper_id, 15)
                )
        except InvalidQueryID as e:
            logger.debug(f"{e} query: {query}, message: {whisper}")
    except ReadableException as e:
        logger.debug(f"whisper_inline_handler:ReadableException({e.content})")
        input_content = InputTextMessageContent(e.content[2], ParseMode.MARKDOWN)
        item = InlineQueryResultArticle(
            id=e.content[0], title=e.content[1], input_message_content=input_content
        )
        try:
            await query.answer(results=[item], cache_time=3600)
        except InvalidQueryID as e:
            logger.debug(f"{e}(inside ReadableException) {query}")


@dispatcher.callback_query_handler()
async def whisper_callback_handler(query: CallbackQuery):
    logger.debug(f"whisper_reveal_handler")

    def answer_error(error_text):
        return query.answer(error_text, cache_time=1800)

    try:
        action, whisper_id = query.data.split("|", maxsplit=1)
        whisper = whispers.get(whisper_id)
        if whisper is None:
            logger.debug(
                f"whisper_reveal_handler:Invalid whisper id, data: {query.data}"
            )
            raise ReadableException("⏲️🔔/❌ The message is expired or non-existent.")
        if (
            query.from_user.username != whisper.recipient
            and query.from_user != whisper.sender
        ):
            raise ReadableException("🚫 You are neither the sender nor recipient.")
        if action == "REVEAL":
            await query.answer(
                f"From {whisper.sender.mention} to @{whisper.recipient}:\n\n{whisper.content}",
                cache_time=30,
                show_alert=True,
            )
        elif action == "EXPIRE":
            await expire_whisper(whisper_id)
            await query.answer(f"✅ Successfully expired.", cache_time=1800)
        else:
            raise ReadableException("❌ Unsupported Action ❌")
    except ReadableException as e:
        await answer_error(e.content)


def main():
    logging.basicConfig(level=logging.INFO)
    # logger.setLevel(logging.DEBUG)

    executor.start_polling(dispatcher)


if __name__ == "__main__":
    main()
