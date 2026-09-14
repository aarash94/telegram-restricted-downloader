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
print("ok")
