"""Personal Telegram bot: send it a t.me link, get the message back (restricted or not).
Send it an Instagram post or reel link, get every photo and video in it.
Send it a YouTube link, pick video quality or audio only, get the file back.
Spotify track links return matching YouTube audio; X links return photos, videos/GIFs and text.

Your own account (user session) fetches + downloads; the bot re-sends to you.
First run asks for your phone + login code once, then sessions are saved to *.session files.
"""
import asyncio, json, os, re, shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from html import unescape
import instaloader
import yt_dlp
from yt_dlp.extractor.twitter import TwitterIE
from PIL import Image
from telethon import Button, TelegramClient, events, utils
from telethon.tl.types import Document, DocumentAttributeAudio, MessageMediaWebPage

try:
    import imageio_ffmpeg  # ships an ffmpeg binary in site-packages, so hosts need no separate ffmpeg install
except ImportError:
    imageio_ffmpeg = None

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
# youtube.com/watch?v=ID, youtu.be/ID, /shorts/ID, /live/ID, /embed/ID; ?t= &list= trailing junk ok
YT = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^&\s]*&)*v=|shorts/|live/|embed/|v/)|youtu\.be/)([\w-]{11})")
# Only recognize real hosts and complete IDs, then discard untrusted query/path suffixes.
SPOTIFY = re.compile(r"(?<![\w./:-])(?:https?://open\.spotify\.com/(?:intl-[a-z-]+/)?track/|spotify:track:)([A-Za-z0-9]{22})(?=$|[?\s/#<>])")
X = re.compile(r"(?<![\w./:-])(?:https?://)?(?:(?:www|m|mobile)\.)?(?:x|twitter)\.com/(?:[\w]+|i/web)/status/(\d+)(?=$|[?\s/#<>])")
IG_LOADER = instaloader.Instaloader(dirname_pattern="dl/{target}", save_metadata=False, download_video_thumbnails=False,
                                    post_metadata_txt_pattern="", quiet=True, max_connection_attempts=1,
                                    request_timeout=30, fatal_status_codes=[401, 403, 429])
YT_MAX_H = int(os.environ.get("YT_MAX_H", 1080))  # highest video button offered
YT_AUDIO_KBPS = [int(x) for x in os.environ.get("YT_AUDIO_KBPS", "128,192,320").split(",") if x.strip()]
# yt-dlp picks tv_downgraded whenever cookies are present, and YouTube currently rejects that client with
# "The page needs to be reloaded" (yt-dlp#16212, #17389). Naming the working clients sidesteps it.
YT_CLIENTS = [c.strip() for c in os.environ.get("YT_CLIENTS", "default,-tv_downgraded,web_embedded").split(",") if c.strip()]
YT_PENDING = {}  # callback token -> (chat_id, video_id, title, choices); one entry per open choice keyboard
SPOTIFY_CLIENT = None  # initialized only on the first track; reused under main's download lock


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
    """Blocking: download every photo/video of an Instagram post into dl/<code>/; return (folder, caption)."""
    username = os.environ.get("IG_USERNAME", "").strip()
    if username and not IG_LOADER.context.is_logged_in:
        session = os.environ.get("IG_SESSIONFILE", "").strip().strip('"')
        session = os.path.join(HERE, os.path.expanduser(session)) if session else None
        try:
            IG_LOADER.load_session_from_file(username, session)
        except FileNotFoundError as e:
            raise RuntimeError(T['ig_session_missing']) from e
    if IG_LOADER.context.is_logged_in:
        # Use the authenticated media endpoint, avoiding the GraphQL query that
        # Instagram refuses with 401 even for some signed-in sessions.
        mediaid = instaloader.Post.shortcode_to_mediaid(code)
        data = IG_LOADER.context.get_iphone_json(f"api/v1/media/{mediaid}/info/", {})
        items = data.get("items") or []
        if not items:
            raise instaloader.BadResponseException("Instagram returned no media for this post.")
        post = instaloader.Post.from_iphone_struct(IG_LOADER.context, items[0])
    else:
        post = instaloader.Post.from_shortcode(IG_LOADER.context, code)
    IG_LOADER.download_post(post, target=code)
    folder = os.path.join("dl", code)
    for f in os.listdir(folder):  # Instagram serves newer pictures as webp, which Telegram shows as a sticker/file
        if f.endswith(".webp"):
            Image.open(os.path.join(folder, f)).convert("RGB").save(os.path.join(folder, f[:-5] + ".jpg"), quality=95)
            os.remove(os.path.join(folder, f))
    return folder, post.caption or ""


def ig_error(e):
    msg = str(e)
    if isinstance(e, (instaloader.ConnectionException, instaloader.LoginRequiredException,
                      instaloader.AbortDownloadException, instaloader.QueryReturnedForbiddenException)) and any(
            s in msg.lower() for s in ("401", "403", "429", "please wait", "login", "checkpoint", "challenge", "feedback_required")):
        return T['ig_blocked']
    return f"{type(e).__name__}: {msg[:3000]}"


def yt_parse(text):
    m = YT.search(text or "")
    return m and m.group(1)


def human_size(n):
    """1234567 -> '1.2 MB'; None/0 -> '?', so an unknown YouTube size never shows as a bare 0 B."""
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if n and n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} B" if n else "?"


def human_time(s):
    """213 -> '3:33'; None -> '', so a video without a duration just shows no length."""
    if not s:
        return ""
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def yt_name(title, ext):
    """A safe, readable file name: strip what Windows/macOS forbid, keep the extension as chosen.

    % goes too, because the name is handed to yt-dlp as an output template where % is a placeholder.
    """
    title = re.sub(r'[/\\:*?"<>|\x00-\x1f%]', "", title or "").strip()[:80] or "video"
    return f"{title}.{ext}"


def yt_choices(info):
    """Blocking-free: turn yt-dlp's format list into the buttons the user picks from.

    Each choice is (label, format selector, postprocessors, expected bytes). Video is mp4 only, so the
    menu stays small and the merged file plays everywhere; audio is the best m4a re-encoded to mp3.

    Selectors are height-bound filters rather than the format ids seen here, because the download
    re-extracts and YouTube may hand back a different list than the one this menu was built from.
    """
    fmts = [f for f in info.get("formats") or [] if f.get("ext") != "mhtml"]  # drop storyboard thumbnails
    audio = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    video = [f for f in fmts if f.get("vcodec") not in (None, "none") and f.get("acodec") == "none"]
    best_a = max((f for f in audio if f.get("ext") == "m4a" and f.get("protocol") == "https"),
                 key=lambda f: f.get("abr") or 0, default=None) or max(audio, key=lambda f: f.get("abr") or 0, default=None)
    out, a_size = [], (best_a.get("filesize") or best_a.get("filesize_approx")) if best_a else 0
    for h in sorted({f["height"] for f in video if f.get("height") and f["height"] <= YT_MAX_H}, reverse=True):
        same = [f for f in video if f["height"] == h and f.get("protocol") == "https" and f.get("ext") == "mp4"]
        # avc1/H.264 plays on every client; av01 only where the platform has a decoder
        v = max((f for f in same if (f.get("vcodec") or "").startswith("avc1")), key=lambda f: f.get("tbr") or 0, default=None) \
            or max(same, key=lambda f: f.get("tbr") or 0, default=None)
        if not v or not best_a:  # no separate audio stream means nothing to merge, so mp4 video is unplayable
            continue
        # <= rather than == so a re-extraction without that exact height still resolves; vcodec^=avc1
        # keeps the delivered file the H.264 one the size on the button was worked out from
        sel = (f"bv*[vcodec^=avc1][height<={h}][ext=mp4]+ba[ext=m4a]"
               f"/bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]")
        out.append((f"🎬 {h}p · mp4 · {human_size((v.get('filesize') or v.get('filesize_approx') or 0) + (a_size or 0))}",
                    sel, None, (v.get("filesize") or v.get("filesize_approx") or 0) + (a_size or 0)))
    dur = info.get("duration") or 0
    if best_a:
        for kbps in sorted(YT_AUDIO_KBPS, reverse=True):  # every configured tier is offered, even above
            size = int(dur * kbps * 125)  # the source's own bitrate, which is what the owner asked to pick
            out.append((f"🎵 audio · mp3 {kbps}kbps · {human_size(size)}", "ba[ext=m4a]/ba/b",
                        [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": kbps}], size))
    return out


def yt_info(vid):
    """Blocking: probe a video's formats without downloading; yt-dlp picks the player client for us."""
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as y:
        return y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)


YT_BROWSERS = ("brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale")


def yt_cookies(setting="YT_COOKIES"):
    """YouTube walls off some addresses with "Sign in to confirm you're not a bot". Cookies from a
    browser that is signed in to YouTube are the documented way past it.

    YT_COOKIES is a path to a cookies.txt file, or a browser yt-dlp can read, optionally as
    firefox:container. A file is the dependable choice on Windows: Chromium cookies are sealed with
    DPAPI, which only opens for the same Windows account that owns the browser profile.
    """
    v = os.environ.get(setting, "").strip().strip('"')
    if not v:
        return {}
    if os.path.isfile(v):
        return {"cookiefile": v}
    browser, _, profile = v.partition(":")
    browser = browser.strip().lower()
    if browser not in YT_BROWSERS:  # a path that does not exist is far likelier than a typo'd browser
        raise RuntimeError(f"{setting}: {v!r} is neither a readable file nor one of {', '.join(YT_BROWSERS)}")
    return {"cookiesfrombrowser": (browser, profile.strip() or None, None, None)}


def yt_has_runtime():
    return bool(os.environ.get("YT_JS_RUNTIME", "").strip()) or shutil.which("deno") or shutil.which("node")


def yt_js_runtime():
    """YouTube only serves playable streams when a JavaScript runtime can solve its signature cipher, and
    yt-dlp needs deno or node for that. PATH is the usual source; YT_JS_RUNTIME names the executable
    directly, which spares anyone the business of editing PATH.
    """
    p = os.environ.get("YT_JS_RUNTIME", "").strip().strip('"')
    if not p:
        return {}
    if not os.path.isfile(p):
        raise RuntimeError(f"YT_JS_RUNTIME: {p!r} is not a file. Point it at deno.exe or node.exe.")
    return {"js_runtimes": {"deno" if "deno" in os.path.basename(p).lower() else "node": {"path": p}}}


def yt_opts():
    """Settings shared by both yt-dlp calls: no playlist, no chatter, the cookies if configured, the
    player clients that currently work, and where the JavaScript runtime lives."""
    return {"quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 30, "retries": 1, "fragment_retries": 1,
            "concurrent_fragment_downloads": 1,
            "extractor_args": {"youtube": {"player_client": YT_CLIENTS}}, **yt_cookies(), **yt_js_runtime()}


def yt_error(e):
    """yt-dlp's own message, minus the terminal colours, plus the fix when the failure has one."""
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e)).strip()
    if "confirm you" in msg and "bot" in msg:
        return T['yt_botcheck']
    if "DPAPI" in msg:  # Windows sealed the browser's cookies for a different account than this one runs as
        return T['yt_dpapi']
    if not yt_has_runtime() and (
            "needs to be reloaded" in msg or "Requested format is not available" in msg):
        # YouTube's clients only sign their streams when a JS runtime can solve the cipher, and yt-dlp
        # reports the missing runtime as an unplayable page or an empty format list
        return T['yt_noruntime']
    if "needs to be reloaded" in msg:  # yt-dlp's cookie path lands on a player YouTube is refusing
        return T['yt_stale']
    if "challenge solving failed" in msg:
        return T['yt_noejs']
    return f"{type(e).__name__}: {msg[:400]}"


def yt_ffmpeg():
    """ffmpeg is what merges the two streams into one mp4 and builds the mp3, so yt-dlp must find it.

    An ffmpeg on PATH wins; otherwise use the binary imageio-ffmpeg installed into site-packages.
    """
    if shutil.which("ffmpeg"):
        return {}
    if imageio_ffmpeg:
        return {"ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe()}  # the bundled file has a version in its name
    raise RuntimeError("ffmpeg is missing. Install it on PATH, or run: pip install imageio-ffmpeg")


def yt_info(vid):
    """Blocking: probe a video's formats without downloading; yt-dlp picks the player client for us."""
    with yt_dlp.YoutubeDL(yt_opts()) as y:
        return y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)


def yt_download(vid, choice, folder, title):
    """Blocking: download one already-chosen format into folder/ and return the resulting file path.

    The file is written under the video's title, because Telethon's send_file has no file_name
    parameter and Telegram therefore takes the name off the path it is given.
    """
    _, fmt, post, _ = choice
    opts = {**yt_opts(), "noprogress": True, "format": fmt,
            "outtmpl": os.path.join(folder, yt_name(title, "%(ext)s")),
            "merge_output_format": "mp4", **yt_ffmpeg()}
    if post:
        opts["postprocessors"] = post
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([f"https://www.youtube.com/watch?v={vid}"])
    files = [os.path.join(folder, f) for f in os.listdir(folder) if not f.endswith((".part", ".ytdl", ".temp"))]
    if not files:
        raise RuntimeError("yt-dlp produced no file")
    return max(files, key=os.path.getsize)


def spotify_parse(text):
    m = SPOTIFY.search(text or "")
    return f"https://open.spotify.com/track/{m.group(1)}" if m else None


def x_parse(text):
    m = X.search(text or "")
    return f"https://x.com/i/status/{m.group(1)}" if m else None


def spotify_download(url, folder):
    """Blocking: spotDL matches/tags; our downloader owns every media file for cleanup."""
    global SPOTIFY_CLIENT
    try:
        from spotdl import Spotdl
        from spotdl.utils.metadata import embed_metadata
    except ImportError as e:
        raise RuntimeError(T['spotify_install']) from e
    if SPOTIFY_CLIENT is None:
        SPOTIFY_CLIENT = Spotdl(client_id="", client_secret="", headless=True,
            downloader_settings={"format": "m4a", "bitrate": "auto", "threads": 1,
                "lyrics_providers": [], "audio_providers": ["youtube-music"],
                "ffmpeg": yt_ffmpeg().get("ffmpeg_location", "ffmpeg")})
        # Matching can probe YouTube metadata too; give those probes the same cookies/runtime.
        for provider in SPOTIFY_CLIENT.downloader.audio_providers:
            opts = {**provider.audio_handler.params, **yt_opts(), "socket_timeout": 30,
                    "retries": 1, "fragment_retries": 1, "concurrent_fragment_downloads": 1}
            provider.audio_handler.close()
            provider.audio_handler = yt_dlp.YoutubeDL(opts)
    songs = SPOTIFY_CLIENT.search([url])
    if len(songs) != 1:
        raise RuntimeError(T['spotify_none'])
    song = songs[0]
    source = SPOTIFY_CLIENT.downloader.search(song)
    vid = yt_parse(source)
    if not vid:
        raise RuntimeError(T['spotify_none'])
    artist = ", ".join(song.artists or [])
    choice = ("", "ba[ext=m4a]/ba", [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}], 0)
    path = yt_download(vid, choice, folder, f"{artist} - {song.name}")
    song.download_url = source
    embed_metadata(Path(path), song)
    return str(path), song.name, ", ".join(song.artists or []), song.duration


class XPostExtractor(TwitterIE):
    """Preserve long-post text that yt-dlp's video metadata conversion discards."""
    def _graphql_to_legacy(self, data, twid):
        status = super()._graphql_to_legacy(data, twid)
        result = (data.get("tweetResult") or {}).get("result") or {}
        result = result.get("tweet") or result  # visibility-wrapped posts
        note = ((result.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}
        if note.get("text"):
            status["full_text"] = note["text"]
        return status


def x_download(url, folder):
    """Blocking: fetch one X post once, keeping its text and every attached photo/video."""
    opts = {"quiet": True, "no_warnings": True, "noprogress": True,
            "noplaylist": False, "format": "b[ext=mp4]/b", "socket_timeout": 30,
            "retries": 1, "fragment_retries": 1, "concurrent_fragment_downloads": 1,
            "outtmpl": os.path.join(folder, "%(autonumber)02d-%(id)s.%(ext)s"),
            **yt_cookies("X_COOKIES"), **yt_ffmpeg()}
    with yt_dlp.YoutubeDL(opts) as y:
        extractor = XPostExtractor(y)
        extractor.initialize()
        twid = url.rsplit("/", 1)[-1]
        # The public video extractor rejects photos/text. Reuse its authenticated status fetch instead.
        status = extractor._extract_status(twid)
        caption = unescape(status.get("full_text") or status.get("text") or "")
        media = list((status.get("extended_entities") or {}).get("media") or [])
        quoted = status.get("quoted_status") or {}
        media.extend((quoted.get("extended_entities") or {}).get("media") or [])
        card = status.get("card") or {}
        bindings = card.get("binding_values") or {}
        if card.get("name", "").split(":")[-1] == "unified_card":
            unified = json.loads((bindings.get("unified_card") or {}).get("string_value") or "{}")
            media.extend((unified.get("media_entities") or {}).values())
        else:
            vmap = bindings.get("amplify_url_vmap") or bindings.get("player_stream_url") or {}
            if vmap.get("string_value"):
                formats, _ = extractor._extract_formats_from_vmap_url(vmap["string_value"], twid)
                media.append({"type": "video", "formats": formats})
        files = []
        for index, item in enumerate(media, 1):
            if item.get("type") == "photo":
                photo = item.get("media_url_https") or ""
                if not photo.startswith("https://pbs.twimg.com/"):
                    raise RuntimeError(T['x_none'])
                ext = photo.split("?", 1)[0].rsplit(".", 1)[-1].lower()
                ext = ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"
                path = os.path.join(folder, f"{index:02d}.{ext}")
                photo = yt_dlp.utils.update_url_query(photo, {"name": "orig"})
                with y.urlopen(photo) as response, open(path, "wb") as out:
                    shutil.copyfileobj(response, out, 64 * 1024)
                if not os.path.getsize(path):
                    raise RuntimeError(T['x_none'])
                if ext == "webp":  # Telegram otherwise displays a sticker/file
                    converted = os.path.join(folder, f"{index:02d}.jpg")
                    with Image.open(path) as image:
                        image.convert("RGB").save(converted, quality=95)
                    os.remove(path)
                    path = converted
                files.append(path)
            else:
                video = item.get("video_info") or {}
                formats = list(item.get("formats") or [])
                for variant in video.get("variants") or []:
                    formats.extend(extractor._extract_variant_formats(variant, twid)[0])
                if not formats:
                    raise RuntimeError(T['x_none'])
                info = y.process_ie_result({"id": f"{twid}-{index}", "title": f"X post {twid}",
                    "extractor": "twitter", "extractor_key": "Twitter", "webpage_url": url,
                    "duration": (video.get("duration_millis") or 0) / 1000, "formats": formats}, download=True)
                for download in info.get("requested_downloads") or []:
                    path = download.get("filepath")
                    if path and os.path.isfile(path) and os.path.getsize(path):
                        files.append(path)
    if not files and not caption:
        raise RuntimeError(T['x_none'])
    return files, caption


async def send_external(ev, bot, spotify, xurl):
    """Send a Spotify track or X post and remove all request files even when upload fails."""
    note = await ev.reply(T['downloading'])
    try:
        with TemporaryDirectory(prefix="spotify-" if spotify else "x-", dir="dl") as folder:
            if spotify:
                path, title, artist, duration = await asyncio.to_thread(spotify_download, spotify, folder)
                await bot.send_file(ev.chat_id, path, caption=f"{artist} - {title}"[:1024], parse_mode=None,
                    attributes=[DocumentAttributeAudio(duration=int(duration or 0), title=title,
                                                       performer=artist or None, voice=False)], mime_type="audio/mp4")
            else:
                files, caption = await asyncio.to_thread(x_download, xurl, folder)
                fits = len(caption.encode("utf-16-le")) <= 2048
                if files:
                    await bot.send_file(ev.chat_id, files, caption=caption if fits else None,
                                        parse_mode=None, supports_streaming=True)
                if caption and (not files or not fits):
                    # The fallback must end after a whole UTF-16 character, never a high surrogate.
                    for chunk, _ in utils.split_text(caption, [], limit=4000,
                                                    split_at=(r"\n", r"\s", r"[^\ud800-\udbff]")):
                        await ev.reply(chunk, parse_mode=None, link_preview=False)
    finally:
        await note.delete()
    await ev.respond(T['next'])


async def main():
    user = await TelegramClient("user", API_ID, API_HASH).start()
    bot = await TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
    me = await user.get_me()
    os.makedirs("dl", exist_ok=True)
    allowed = OWNERS or [me.id]
    downloads = asyncio.Lock()  # one active job; also protects shared Instagram and Spotify clients

    @bot.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id not in allowed))
    async def deny(ev):
        await ev.respond(T['private'].format(ev.sender_id))

    @bot.on(events.NewMessage(from_users=allowed))
    async def save(ev):
        async with downloads:
            await save_message(ev)

    async def save_message(ev):
        link, code, vid = parse(ev.raw_text), ig_parse(ev.raw_text), yt_parse(ev.raw_text)
        spotify, xurl = spotify_parse(ev.raw_text), x_parse(ev.raw_text)
        if not link and not code and not vid and not spotify and not xurl:
            return await ev.respond(T['welcome'] if ev.raw_text.startswith("/start") else T['bad_link'])
        try:
            if spotify or xurl:
                return await send_external(ev, bot, spotify, xurl)
            if vid:  # YouTube: show the quality/format menu, download whatever the owner taps
                note = await ev.reply(T['yt_fetching'])
                try:
                    info = await asyncio.to_thread(yt_info, vid)
                    choices = yt_choices(info)
                    if not choices:
                        return await note.edit(T['yt_none'])
                    token = os.urandom(6).hex()  # callback_data is capped at 64 bytes, so keep only a token
                    YT_PENDING[token] = (ev.chat_id, vid, info.get("title"), choices, info.get("duration"))
                    await note.edit(T['yt_pick'].format(yt_name(info.get("title"), "").rstrip("."),
                                                        human_time(info.get("duration")), len(choices)),
                                buttons=[[Button.inline(c[0], data=f"yt:{token}:{i}")] for i, c in enumerate(choices)],
                                parse_mode=None)  # video titles carry *stars* and _underscores_ of their own
                except Exception as e:  # drop the placeholder, then report it in plain words
                    await note.delete()
                    await ev.reply(f"{T['error']}\n{yt_error(e)}")
                    return
                return
            if code:  # Instagram: all photos/videos of the post as one album (Telethon splits past 10)
                note = await ev.reply(T['downloading'])
                folder = os.path.join("dl", code)
                try:
                    _, caption = await asyncio.to_thread(ig_download, code)
                    # instaloader names carousel items _1.._N; sort by length first so _10 comes after _9
                    files = [os.path.join(folder, f) for f in sorted(os.listdir(folder), key=lambda f: (len(f), f))]
                    fits = len(caption) <= 1024  # bot caption limit; parse_mode=None keeps #hash_tags and *stars* literal
                    await bot.send_file(ev.chat_id, files, caption=caption if fits else None, parse_mode=None,
                                        supports_streaming=True)
                    if not fits:
                        await ev.reply(caption, parse_mode=None)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)  # also clears a half-finished download
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
                text = msg.message or ""
                if len(text) <= 4096:
                    await ev.reply(text, formatting_entities=msg.entities)
                else:  # Telegram caps one message at 4096 chars; split so long posts do not crash
                    for i in range(0, len(text), 4000):
                        await ev.reply(text[i:i + 4000])
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
                        text = msg.message or ""
                        if len(text) <= 4096:
                            await ev.reply(text, formatting_entities=msg.entities)
                        else:  # the part after the 1024-char caption limit can still be huge; split it
                            for i in range(0, len(text), 4000):
                                await ev.reply(text[i:i + 4000])
                finally:
                    os.remove(path)
                    await note.delete()
            await ev.respond(T['next'])
        except Exception as e:  # surface every failure to the owner instead of only the log
            error = ig_error(e) if code else yt_error(e) if spotify else f"{type(e).__name__}: {str(e)[:3000]}"
            await ev.reply(f"{T['error']}\n{error}")

    @bot.on(events.CallbackQuery(func=lambda e: (e.data or b"").startswith(b"yt:")))
    async def yt_pick(ev):
        async with downloads:
            await download_youtube(ev)

    async def download_youtube(ev):
        try:  # callback data arrives as bytes from Telethon, unlike the message text above
            _, token, index = ev.data.decode().split(":")
            pending = YT_PENDING[token]
            chat, vid, title, choices = pending[:4]  # duration may be present in newer entries
            duration = pending[4] if len(pending) > 4 else None
        except (ValueError, KeyError):  # token expired or the menu was never registered
            return await ev.answer(T['yt_expired'], alert=True)
        if chat != ev.chat_id or ev.sender_id not in allowed:
            return await ev.answer(T['yt_expired'], alert=True)
        try:
            choice = choices[int(index)]
        except (ValueError, IndexError):
            return await ev.answer(T['yt_expired'], alert=True)
        del YT_PENDING[token]  # one choice per menu, so a second tap cannot start a second download
        await ev.answer()
        folder = os.path.join("dl", "yt", token)
        try:
            await ev.edit(T['downloading'])
            path = await asyncio.to_thread(yt_download, vid, choice, folder, title)
            is_video = os.path.splitext(path)[1].lower() in (".mp4", ".mkv", ".webm")
            is_audio = os.path.splitext(path)[1].lower() in (".mp3", ".m4a", ".ogg", ".opus", ".flac", ".wav", ".aac")
            # audio goes out with its audio attribute (duration + title) and no force_file, otherwise
            # Telegram shows a plain file with a download button instead of the inline player
            attrs = ([DocumentAttributeAudio(duration=int(duration or 0),
                                            title=(title or "").strip() or None, voice=False)]
                     if is_audio else None)
            mime = "audio/mpeg" if os.path.splitext(path)[1].lower() == ".mp3" else None
            await bot.send_file(ev.chat_id, path, caption=(title or "").strip()[:1024] or None,
                                parse_mode=None, supports_streaming=is_video,
                                attributes=attrs, mime_type=mime)
            await ev.delete()
        except Exception as e:
            await ev.edit(f"{T['error']}\n{yt_error(e)}")
        finally:
            shutil.rmtree(folder, ignore_errors=True)  # also clears a half-finished download

    print("bot running as", me.first_name, "| allowed ids:", allowed)
    await bot.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
