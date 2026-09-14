# telegram-restricted-downloader

Personal bot: send it a `t.me/...` message link, it sends the message back, even from copy-protected chats.
Your own account does the fetching, so it must already be a member of private chats.

## Setup (Windows VPS, ~150 MB disk + room for the largest file you fetch, 2 GB max)

1. Install Python 3.10+ from python.org (tick "Add to PATH"), then `pip install telethon cryptg`
2. Get `API_ID` / `API_HASH` at https://my.telegram.org, `BOT_TOKEN` from @BotFather
3. Save them once as user env vars:
   ```
   setx API_ID 12345
   setx API_HASH abcdef...
   setx BOT_TOKEN 123:abc...
   ```
4. Open a new terminal, run `python bot.py`. First run asks your phone number + login code (+ 2FA password). Done.

Only the logged-in account can use the bot; anyone else is ignored.

## Fetch with a second account, send links from your main one

Log the user session in with the second account (it must be a member of the private channels you fetch from).
Then tell the bot which account is allowed to talk to it: message @userinfobot from your main account to get your id, and

```
setx OWNER 123456789
```

## Keep it running after reboot

```
schtasks /create /tn tgsave /sc onstart /ru %USERNAME% /rp * /tr "cmd /c cd /d C:\path\to\telegram-restricted-downloader && python bot.py >> bot.log 2>&1"
```

## Test

`python test_bot.py`
