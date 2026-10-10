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
                                  "🎵 audio · mp3 128kbps · 3.3 MB"], [c[0] for c in choices]
assert choices[0][1] == "137+140"  # avc1 picked over the smaller av01; m3u8 and >1080p skipped
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
if os.environ.get("IG_TEST"):  # IG_TEST=<shortcode> hits Instagram for real; off by default
    import shutil
    from bot import ig_download
    folder, caption = ig_download(os.environ["IG_TEST"])
    files = os.listdir(folder)
    assert files and all(f.endswith((".jpg", ".mp4")) for f in files), files
    assert isinstance(caption, str) and caption, caption  # every test post above has one
    shutil.rmtree(folder)
print("ok")
