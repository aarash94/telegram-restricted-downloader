# telegram-restricted-downloader

Personal bot: send it a `t.me/...` message link, it sends the message back, even from copy-protected chats.
The logged-in account does the fetching, so it must already be a member of private chats.

## Setup (Windows VPS, ~150 MB disk + room for the largest file you fetch, 2 GB max)

1. Install Python 3.10+ from python.org (tick "Add to PATH"), then `pip install telethon cryptg`
2. Get `API_ID` / `API_HASH` at https://my.telegram.org, `BOT_TOKEN` from @BotFather
3. Create `C:\tgsave\.env` (`notepad .env` in that folder) with:
   ```
   API_ID=12345
   API_HASH=abcdef...
   BOT_TOKEN=123:abc...
   OWNER=123456789,987654321
   ```
   `OWNER` is the comma-separated list of Telegram account ids allowed to use the bot.
   Anyone else who messages the bot is told it is private and shown their own id, so that is how people get their id.
   Leave `OWNER` out to allow only the logged-in account.
4. `python bot.py`. First run asks your phone number + login code (+ 2FA password). Done.

## Update

```
git pull
python bot.py
```

Sessions live in `user.session` / `bot.session`; no login prompt again unless you delete `user.session` (that is how you switch the fetching account).

## Keep it running after reboot

```
schtasks /create /tn tgsave /sc onstart /ru %USERNAME% /rp * /tr "cmd /c cd /d C:\tgsave && python bot.py >> bot.log 2>&1"
```

## Test

`python test_bot.py`
