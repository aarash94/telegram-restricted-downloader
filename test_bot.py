from bot import parse

assert parse("https://t.me/c/1234567/89") == (-1001234567, 89)
assert parse("https://t.me/c/1234567/5/89") == (-1001234567, 89)  # topic/thread link
assert parse("https://t.me/somechannel/89?single") == ("somechannel", 89)
assert parse("t.me/b/somebot/89") == ("somebot", 89)
assert parse("https://t.me/cats/7") == ("cats", 7)  # username starting with c
assert parse("https://t.me/+AbCdEf") is None
assert parse("hello") is None
assert parse(None) is None
print("ok")
