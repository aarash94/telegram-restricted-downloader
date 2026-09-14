"""Personal Telegram bot: send it a t.me link, get the message back (restricted or not).

Your own account (user session) fetches + downloads; the bot re-sends to you.
First run asks for your phone + login code once, then sessions are saved to *.session files.
"""
import os, re
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaWebPage

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNERS = [int(x) for x in os.environ.get("OWNER", "").split(",") if x.strip()]  # allowed account ids; empty = the logged-in account

WELCOME = "سلام! 👋\nلینک پیام تلگرام را اینجا بفرستید تا همان پیام را برایتان بفرستم.\nحتی از کانال‌هایی که ذخیره و فوروارد در آن‌ها بسته است."
BAD_LINK = "لینک معتبر نیست. یک لینک پیام تلگرام بفرستید، مثلاً:\nhttps://t.me/channel/123"
NOT_FOUND = "پیام پیدا نشد. آیا اکانت عضو آن چت است؟"
DOWNLOADING = "در حال دانلود… ⏳"
UNSUPPORTED = "این نوع پیام پشتیبانی نمی‌شود (نظرسنجی، موقعیت مکانی، مخاطب و…)."
EMPTY = "(پیام خالی)"
NEXT = "انجام شد ✅\nلینک پیام بعدی را بفرستید."
ERROR = "خطا ❌"
PRIVATE = "این ربات خصوصی است.\nشناسه شما: {}"

# t.me/c/<chat>/<id>, t.me/c/<chat>/<topic>/<id>, t.me/<user>/<id>, t.me/b/<bot>/<id>, trailing ?single ok
LINK = re.compile(r"t\.me/(c/|b/)?(\w+)(?:/\d+)?/(\d+)")


def parse(text):
    m = LINK.search(text or "")
    if not m:
        return None
    kind, chat, mid = m.groups()
    return (int("-100" + chat) if kind == "c/" else chat), int(mid)


async def main():
    user = await TelegramClient("user", API_ID, API_HASH).start()
    bot = await TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
    me = await user.get_me()
    os.makedirs("dl", exist_ok=True)
    allowed = OWNERS or [me.id]

    @bot.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id not in allowed))
    async def deny(ev):
        await ev.respond(PRIVATE.format(ev.sender_id))

    @bot.on(events.NewMessage(from_users=allowed))
    async def save(ev):
        link = parse(ev.raw_text)
        if not link:
            return await ev.respond(WELCOME if ev.raw_text.startswith("/start") else BAD_LINK)
        chat, mid = link
        try:
            msg = await user.get_messages(chat, ids=mid)
            if msg is None:
                return await ev.reply(NOT_FOUND)
            if not msg.media or isinstance(msg.media, MessageMediaWebPage):
                await ev.reply(msg.message or EMPTY, formatting_entities=msg.entities)
            else:
                note = await ev.reply(DOWNLOADING)
                path = await user.download_media(msg, "dl/")
                if not path:
                    return await note.edit(UNSUPPORTED)
                try:
                    await bot.send_file(ev.chat_id, path, caption=msg.message, formatting_entities=msg.entities,
                                        attributes=msg.document.attributes if msg.document else None)
                finally:
                    os.remove(path)
                    await note.delete()
            await ev.respond(NEXT)
        except Exception as e:  # surface every failure to the owner instead of only the log
            await ev.reply(f"{ERROR}\n{type(e).__name__}: {e}")

    print("bot running as", me.first_name, "| allowed ids:", allowed)
    await bot.run_until_disconnected()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
