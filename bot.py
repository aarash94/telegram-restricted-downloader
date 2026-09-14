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

    @bot.on(events.NewMessage(from_users=me.id, func=lambda e: parse(e.raw_text)))
    async def save(ev):
        chat, mid = parse(ev.raw_text)
        try:
            msg = await user.get_messages(chat, ids=mid)
            if msg is None:
                return await ev.reply("Not found. Is your account a member of that chat?")
            if not msg.media or isinstance(msg.media, MessageMediaWebPage):
                return await ev.reply(msg.message or "(empty message)", formatting_entities=msg.entities)
            note = await ev.reply("Downloading…")
            path = await user.download_media(msg, "dl/")
            if not path:
                return await note.edit("Unsupported media type (poll, location, contact…)")
            try:
                await bot.send_file(ev.chat_id, path, caption=msg.message, formatting_entities=msg.entities,
                                    attributes=msg.document.attributes if msg.document else None)
            finally:
                os.remove(path)
                await note.delete()
        except Exception as e:  # ponytail: surface every failure to the owner instead of only the log
            await ev.reply(f"Error: {type(e).__name__}: {e}")

    print("bot running as", me.first_name)
    await bot.run_until_disconnected()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
