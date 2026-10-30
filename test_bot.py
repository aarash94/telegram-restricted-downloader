from bot import parse

assert parse("https://t.me/c/1234567/89") == (-1001234567, 89)
assert parse("https://t.me/c/1234567/5/89") == (-1001234567, 89)  # topic/thread link
assert parse("https://t.me/somechannel/89?single") == ("somechannel", 89)
assert parse("t.me/b/somebot/89") == ("somebot", 89)
assert parse("https://t.me/cats/7") == ("cats", 7)  # username starting with c
assert parse("https://t.me/+AbCdEf") is None
assert parse("hello") is None
assert parse(None) is None

from telethon.tl.types import (RichMessage, PageBlockParagraph, PageBlockPhoto, PageBlockList, PageListItemText,
                               PageCaption, TextPlain, TextBold, TextConcat, TextEmpty)
from bot import rich_walk
rm = RichMessage(blocks=[
    PageBlockPhoto(photo_id=7, caption=PageCaption(text=TextPlain("cap"), credit=TextEmpty())),
    PageBlockParagraph(text=TextConcat(texts=[TextPlain("a "), TextBold(text=TextPlain("b"))])),
    PageBlockList(items=[PageListItemText(text=TextPlain("x")), PageListItemText(text=TextPlain("y"))]),
], photos=[], documents=[])
out, ids = [], []
rich_walk(rm, out, ids)
assert ids == [7], ids
assert "".join(out).split() == ["cap", "a", "b", "x", "y"], out

import glob, json, os
en = json.load(open("i18n/en.json", encoding="utf-8"))
for f in glob.glob("i18n/*.json"):
    d = json.load(open(f, encoding="utf-8"))
    assert d.keys() == en.keys(), f"{f}: keys differ from en.json"
    assert "{}" in d["private"], f"{f}: private must contain {{}}"
from bot import ig_parse
assert ig_parse("https://www.instagram.com/p/C1a2B3c4D5e/") == "C1a2B3c4D5e"
assert ig_parse("https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=abc") == "C1a2B3c4D5e"
assert ig_parse("instagram.com/reels/C1a2B3c4D5e") == "C1a2B3c4D5e"
assert ig_parse("https://www.instagram.com/someuser/p/C1a2B3c4D5e/") == "C1a2B3c4D5e"
assert ig_parse("https://www.instagram.com/stories/someuser/123/") is None
assert ig_parse("https://t.me/cats/7") is None

# A saved Instagram login must download photos, reels and mixed albums even when
# the anonymous GraphQL endpoint rejects the request. Only network I/O is replaced.
import copy
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
import instaloader
import requests
import bot

media = {
    "pk": "4009085925228593700", "code": "DejImhvRMIk", "media_type": 1,
    "taken_at": 1730000000, "caption": {"text": "A caption #with_tags"},
    "has_liked": False, "like_count": 3, "comment_count": 0,
    "image_versions2": {"candidates": [{"url": "https://cdn.example/photo.jpg"}]},
    "user": {"pk": "123", "username": "example", "is_private": False,
             "full_name": "Example", "profile_pic_url": "https://cdn.example/avatar.jpg"},
}
reel = {**media, "media_type": 2, "video_duration": 3.0, "view_count": 1,
        "video_versions": [{"url": "https://cdn.example/video.mp4"}]}
album = {**media, "media_type": 8, "carousel_media": [media, reel]}
blocked = instaloader.ConnectionException(
    'JSON Query to graphql/query: 401 Unauthorized - "fail" status, '
    'message "Please wait a few minutes before you try again."')

def ig_response(url):
    assert url in ("https://cdn.example/photo.jpg", "https://cdn.example/video.mp4"), url
    response = requests.Response()
    response.status_code = 200
    response.raw = BytesIO(b"downloaded media")
    return response

old_cwd = os.getcwd()
with TemporaryDirectory() as tmp:
    session = os.path.join(tmp, "instagram.session")
    saved = instaloader.Instaloader(quiet=True)
    saved.load_session("example", {"csrftoken": "test", "sessionid": "test", "ds_user_id": "123"})
    saved.save_session_to_file(session)
    saved.close()
    try:
        os.chdir(tmp)
        for data, extensions in ((media, [".jpg"]), (reel, [".mp4"]), (album, [".jpg", ".mp4"])):
            loader = instaloader.Instaloader(dirname_pattern="dl/{target}", save_metadata=False,
                                            download_video_thumbnails=False, post_metadata_txt_pattern="", quiet=True)
            def ig_info(path, params):
                assert path == "api/v1/media/4009085925228593700/info/", path
                assert params == {}, params
                assert loader.context.is_logged_in, "media query must use the saved login"
                return {"items": [copy.deepcopy(data)]}

            with patch.object(bot, "IG_LOADER", loader), patch.object(bot, "HERE", tmp), \
                    patch.dict(os.environ, IG_USERNAME="example", IG_SESSIONFILE="instagram.session"), \
                    patch.object(loader.context, "doc_id_graphql_query", side_effect=blocked), \
                    patch.object(loader.context, "get_iphone_json", side_effect=ig_info), \
                    patch.object(loader.context, "get_raw", side_effect=ig_response):
                try:
                    folder, caption = bot.ig_download("DejImhvRMIk")
                except instaloader.ConnectionException as e:
                    raise AssertionError("Saved Instagram login still uses the rejected GraphQL query") from e
                assert caption == "A caption #with_tags", caption
                files = sorted(os.listdir(folder))
                assert sorted(os.path.splitext(f)[1] for f in files) == extensions, files
                assert all(open(os.path.join(folder, f), "rb").read() == b"downloaded media" for f in files)
                import shutil
                shutil.rmtree(folder)
            loader.close()
    finally:
        os.chdir(old_cwd)

# Missing session files must offer setup instructions without making anonymous requests.
with patch.dict(os.environ, IG_USERNAME="example", IG_SESSIONFILE="/missing/instagram.session"):
    try:
        bot.ig_download("DejImhvRMIk")
        raise AssertionError("A configured but missing Instagram session must be reported")
    except RuntimeError as e:
        assert "instaloader --login" in str(e) and "IG_SESSIONFILE" in str(e), e

# Without a configured session, public posts still use the anonymous path.
with patch.dict(os.environ, IG_USERNAME="", IG_SESSIONFILE=""), \
        patch.object(bot.IG_LOADER.context, "doc_id_graphql_query", side_effect=blocked):
    try:
        bot.ig_download("DejImhvRMIk")
        raise AssertionError("The rejected anonymous query must fail")
    except instaloader.ConnectionException as e:
        message = bot.ig_error(e)
        assert "IG_USERNAME" in message and "graphql/query" not in message, message
assert bot.ig_error(ValueError("unrelated failure")) == "ValueError: unrelated failure"
assert "IG_USERNAME" in bot.ig_error(instaloader.QueryReturnedForbiddenException("403 Forbidden"))
assert "IG_USERNAME" in bot.ig_error(instaloader.AbortDownloadException("feedback_required"))

# Refused requests must abort on the first response, without sleeping through retries.
for status in (401, 403, 429):
    response = requests.Response()
    response.status_code = status
    response._content = b'{"status":"fail","message":"Please wait a few minutes before you try again."}'
    with patch.object(bot.IG_LOADER.context._session, "get", return_value=response) as get, \
            patch.object(bot.IG_LOADER.context, "do_sleep"), \
            patch.object(bot.IG_LOADER.context._rate_controller, "wait_before_query"):
        try:
            bot.IG_LOADER.context.get_json("graphql/query", {})
            raise AssertionError(f"HTTP {status} should stop Instagram requests")
        except instaloader.AbortDownloadException as e:
            assert "IG_USERNAME" in bot.ig_error(e), e
        assert get.call_count == 1, get.call_count

from bot import yt_parse, yt_choices, yt_name, human_size, human_time
assert yt_parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
assert yt_parse("https://youtu.be/dQw4w9WgXcQ?t=43") == "dQw4w9WgXcQ"
assert yt_parse("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
assert yt_parse("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=2") == "dQw4w9WgXcQ"
assert yt_parse("https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
assert yt_parse("https://www.instagram.com/p/C1a2B3c4D5e/") is None
assert yt_parse("https://t.me/cats/7") is None
assert yt_parse("hello") is None

assert human_size(0) == "?" and human_size(None) == "?"
assert human_size(512) == "512 B" and human_size(2048) == "2.0 kB"
assert human_size(1536 * 1024) == "1.5 MB" and human_size(3 * 1024 ** 3) == "3.0 GB"
assert human_time(213) == "3:33" and human_time(None) == ""
assert yt_name('a/b:c*d?"e', "mp4") == "abcde.mp4"  # forbidden characters dropped, extension kept
assert yt_name("", "mp3") == "video.mp3"
assert yt_name("100% hit", "mp4") == "100 hit.mp4"  # % is a placeholder in yt-dlp's output template

# choices are built offline from a captured format list: mp4 video up to YT_MAX_H, then mp3 audio
INFO = {"duration": 213, "formats": [
    {"format_id": "sb0", "ext": "mhtml", "height": 180, "vcodec": "none", "acodec": "none", "protocol": "mhtml"},
    {"format_id": "160", "ext": "mp4", "height": 144, "vcodec": "avc1.4D400C", "acodec": "none", "protocol": "https", "filesize": 2000, "tbr": 77},
    {"format_id": "313", "ext": "webm", "height": 2160, "vcodec": "vp9", "acodec": "none", "protocol": "https", "filesize": 999, "tbr": 9999},
    {"format_id": "399", "ext": "mp4", "height": 1080, "vcodec": "av01.0.08M.08", "acodec": "none", "protocol": "https", "filesize": 300, "tbr": 1142},
    {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1.640028", "acodec": "none", "protocol": "https", "filesize": 1000, "tbr": 3038},
    {"format_id": "137h", "ext": "mp4", "height": 1080, "vcodec": "avc1.640028", "acodec": "none", "protocol": "m3u8_native", "filesize": 9000, "tbr": 9000},
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "protocol": "https", "filesize": 100, "abr": 130},
    {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "protocol": "https", "filesize": 100, "abr": 129},
]}
choices = yt_choices(INFO)
assert [c[0] for c in choices] == ["🎬 1080p · mp4 · 1.1 kB", "🎬 144p · mp4 · 2.1 kB",
                                  "🎵 audio · mp3 320kbps · 8.1 MB", "🎵 audio · mp3 192kbps · 4.9 MB",
                                  "🎵 audio · mp3 128kbps · 3.3 MB"], [c[0] for c in choices]
assert [c[3] for c in choices[-3:]] == [213 * k * 125 for k in (320, 192, 128)]  # seconds x kbps over 8
assert choices[0][1].startswith("bv*[vcodec^=avc1][height<=1080][ext=mp4]+ba[ext=m4a]")  # H.264, as sized
assert "137" not in choices[0][1] and "140" not in choices[0][1]  # never bake in ids from the menu's extraction
assert choices[0][3] == 1100  # 1000 video + 100 audio
assert choices[-1][2] == [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": 128}]
assert yt_choices({"formats": [{"format_id": "x", "ext": "mhtml", "vcodec": "none", "acodec": "none"}]}) == []

from bot import yt_cookies, yt_error, T
import os
os.environ.pop("YT_COOKIES", None)
assert yt_cookies() == {}
os.environ["YT_COOKIES"] = "firefox"  # browser name
assert yt_cookies() == {"cookiesfrombrowser": ("firefox", None, None, None)}
os.environ["YT_COOKIES"] = "firefox:work"  # optional firefox container
assert yt_cookies() == {"cookiesfrombrowser": ("firefox", "work", None, None)}
open("cookies.txt", "w").write("# Netscape HTTP Cookie File\n")
os.environ["YT_COOKIES"] = "cookies.txt"  # an exported file wins over being read as a browser name
assert yt_cookies() == {"cookiefile": "cookies.txt"}
os.remove("cookies.txt")
# a Windows path that does not exist must be reported, not mistaken for a browser named "C"
os.environ["YT_COOKIES"] = r"C:\tgsave\cookies.txt"
try:
    yt_cookies()
    raise AssertionError("a bad path should raise")
except RuntimeError as e:
    assert "neither a readable file nor one of" in str(e), e
os.environ.pop("YT_COOKIES", None)

# yt-dlp colours its errors for a terminal; the owner should see plain words and the fix
assert yt_error(Exception("ERROR: \x1b[0;31m[x] \x1b[0m [youtube] abc: Sign in to confirm you\u2019re not a bot.")) == T['yt_botcheck']
assert yt_error(ValueError("no such format")) == "ValueError: no such format"
assert yt_error(Exception("ERROR: Failed to decrypt with DPAPI. See ... for more info")) == T['yt_dpapi']

# the choice keyboard has to be inline buttons with callback data: Button.text is a reply-keyboard button
# that takes no data at all, and callback_data is capped at 64 bytes
from telethon import Button
from telethon.tl.types import KeyboardInlineButton
row = [Button.inline(c[0], data=f"yt:{'t' * 12}:{i}") for i, c in enumerate(yt_choices(INFO))]
assert all(isinstance(b, KeyboardInlineButton) for b in row), "choices must be inline buttons"
assert all(b.type.data == f"yt:{'t' * 12}:{i}".encode() for i, b in enumerate(row)), "callback data must round-trip"
assert all(len(b.type.data) <= 64 for b in row), "callback data over Telegram's 64-byte cap"

# Telethon hands callback data over as bytes, not str: matching and parsing both need to cope with that,
# and a str-only filter raises inside Telethon's func check so the handler silently never fires
assert (b"yt:abc:0".startswith(b"yt:")), "callback filter must match bytes"
assert b"yt:abc:0".decode().split(":") == ["yt", "abc", "0"]
assert not (b"other:abc:0".startswith(b"yt:"))
if os.environ.get("IG_TEST"):  # IG_TEST=<shortcode> hits Instagram for real; off by default
    import shutil
    from bot import ig_download
    folder, caption = ig_download(os.environ["IG_TEST"])
    files = os.listdir(folder)
    assert files and all(f.endswith((".jpg", ".mp4")) for f in files), files
    assert isinstance(caption, str) and caption, caption  # every test post above has one
    shutil.rmtree(folder)
print("ok")
