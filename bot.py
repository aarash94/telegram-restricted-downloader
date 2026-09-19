"""Personal Telegram bot: send it a t.me link, get the message back (restricted or not).
Send it an Instagram post or reel link, get every photo and video in it.

Your own account (user session) fetches + downloads; the bot re-sends to you.
First run asks for your phone + login code once, then sessions are saved to *.session files.
"""
import asyncio, json, os, re, shutil
import instaloader
from PIL import Image
from telethon import TelegramClient, events
from telethon.tl.types import Document, MessageMediaWebPage

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(HERE, ".env")):  # KEY=VALUE lines next to bot.py; simpler than setx on Windows
    for line in open(os.path.join(HERE, ".env"), encoding="utf-8-sig"):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#"):
            os.environ[k.strip()] = v.strip()

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNERS = [int(x) for x in os.environ.get("OWNER", "").split(",") if x.strip()]  # allowed account ids; empty = the logged-in account
BOT_LANG = os.environ.get("BOT_LANG", "en")  # any file name in i18n/
T = json.load(open(os.path.join(HERE, "i18n", f"{BOT_LANG}.json"), encoding="utf-8"))

# t.me/c/<chat>/<id>, t.me/c/<chat>/<topic>/<id>, t.me/<user>/<id>, t.me/b/<bot>/<id>, trailing ?single ok
LINK = re.compile(r"t\.me/(c/|b/)?(\w+)(?:/\d+)?/(\d+)")
# instagram.com/p/<code>, /reel/<code>, /reels/<code>, /tv/<code>, optionally under /<username>/; ?igsh=... ok
IG = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reels?|tv)/([\w-]+)")
IG_LOADER = instaloader.Instaloader(dirname_pattern="dl/{target}", save_metadata=False, download_video_thumbnails=False,
                                    post_metadata_txt_pattern="", quiet=True)  # media files only, nothing else


def rich_walk(node, out, media):
    """Flatten a Telegram rich message (page blocks) into text lines + media ids, in order."""
    n = type(node).__name__
    if n == "TextPlain":
        return out.append(node.text)
    for attr in ("photo_id", "video_id", "audio_id", "document_id"):
        if hasattr(node, attr):
            media.append(getattr(node, attr))
    for attr in ("text", "texts", "items", "blocks", "cover", "caption"):
        v = getattr(node, attr, None)
        for x in (v if isinstance(v, list) else [v]):
            if x is not None and not isinstance(x, str):
                rich_walk(x, out, media)
    if n.startswith(("PageBlock", "PageListItem", "PageListOrderedItem")):
        out.append("\n")


def parse(text):
    m = LINK.search(text or "")
    if not m:
        return None
    kind, chat, mid = m.groups()
    return (int("-100" + chat) if kind == "c/" else chat), int(mid)


def ig_parse(text):
    m = IG.search(text or "")
    return m and m.group(1)


def ig_download(code):
    """Blocking: download every photo/video of an Instagram post into dl/<code>/ and return that folder."""
    IG_LOADER.download_post(instaloader.Post.from_shortcode(IG_LOADER.context, code), target=code)
    folder = os.path.join("dl", code)
    for f in os.listdir(folder):  # Instagram serves newer pictures as webp, which Telegram shows as a sticker/file
        if f.endswith(".webp"):
            Image.open(os.path.join(folder, f)).convert("RGB").save(os.path.join(folder, f[:-5] + ".jpg"), quality=95)
            os.remove(os.path.join(folder, f))
    return folder


async def main():
    user = await TelegramClient("user", API_ID, API_HASH).start()
    bot = await TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
    me = await user.get_me()
    os.makedirs("dl", exist_ok=True)
    allowed = OWNERS or [me.id]

    @bot.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id not in allowed))
    async def deny(ev):
        await ev.respond(T['private'].format(ev.sender_id))

    @bot.on(events.NewMessage(from_users=allowed))
    async def save(ev):
        link, code = parse(ev.raw_text), ig_parse(ev.raw_text)
        if not link and not code:
            return await ev.respond(T['welcome'] if ev.raw_text.startswith("/start") else T['bad_link'])
        try:
            if code:  # Instagram: all photos/videos of the post as one album (Telethon splits past 10)
                note = await ev.reply(T['downloading'])
                folder = await asyncio.to_thread(ig_download, code)
                try:
                    # instaloader names carousel items _1.._N; sort by length first so _10 comes after _9
                    files = [os.path.join(folder, f) for f in sorted(os.listdir(folder), key=lambda f: (len(f), f))]
                    await bot.send_file(ev.chat_id, files, supports_streaming=True)
                finally:
                    shutil.rmtree(folder)
                    await note.delete()
                return await ev.respond(T['next'])
            chat, mid = link
            msg = await user.get_messages(chat, ids=mid)
            if msg is None:
                return await ev.reply(T['not_found'])
            rm = getattr(msg, "rich_message", None)
            if rm and not msg.media and not msg.message:  # new block-based post format
                out, ids = [], []
                rich_walk(rm, out, ids)
                text = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
                files = {m.id: m for m in rm.photos + rm.documents}
                for i in ids:
                    if i in files and (path := await user.download_media(files[i], "dl/")):
                        try:
                            await bot.send_file(ev.chat_id, path, attributes=getattr(files[i], "attributes", None))
                        finally:
                            os.remove(path)
                for i in range(0, len(text), 4000):
                    await ev.reply(text[i:i + 4000])
                return await ev.respond(T['next'])
            media = msg.media
            if isinstance(media, MessageMediaWebPage):  # link preview: media only when the post has no text
                wp = media.webpage
                media = None if msg.message else (getattr(wp, "document", None) or getattr(wp, "photo", None))
            if not media:
                info = f"{type(msg).__name__}/{type(msg.media).__name__}"
                if isinstance(msg.media, MessageMediaWebPage):
                    info += f"/{type(msg.media.webpage).__name__}"
                if not msg.message:
                    return await ev.reply(f"{T['empty']} {info}\n\n{msg.stringify()[:3000]}")
                await ev.reply(msg.message, formatting_entities=msg.entities)
            else:
                note = await ev.reply(T['downloading'])
                path = await user.download_media(msg, "dl/")
                if not path:
                    return await note.edit(T['unsupported'])
                try:
                    doc = msg.document or (media if isinstance(media, Document) else None)
                    fits = len(msg.message or "") <= 1024  # bot caption limit; Premium posts can carry 2048
                    await bot.send_file(ev.chat_id, path, caption=msg.message if fits else None,
                                        formatting_entities=msg.entities if fits else None,
                                        attributes=doc.attributes if doc else None)
                    if not fits:
                        await ev.reply(msg.message, formatting_entities=msg.entities)
                finally:
                    os.remove(path)
                    await note.delete()
            await ev.respond(T['next'])
        except Exception as e:  # surface every failure to the owner instead of only the log
            await ev.reply(f"{T['error']}\n{type(e).__name__}: {e}")

    print("bot running as", me.first_name, "| allowed ids:", allowed)
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
