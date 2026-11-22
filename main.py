import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telegram import Bot
from telegram.error import RetryAfter, TelegramError

# ---------- Load .env ----------
load_dotenv()

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_NAME = os.getenv("SESSION_NAME", "copier_session")

# ---------- Session path (FadeHost persistent /data folder) ----------
DATA_DIR = "/data" if os.path.isdir("/data") else "."
SESSION_PATH = os.path.join(DATA_DIR, SESSION_NAME)

# ---------- Logging ----------
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("copier")

# ---------- Load channel rules ----------
def load_rules():
    with open("channels.json", "r", encoding="utf-8") as f:
        rules = json.load(f)
    active = [r for r in rules if r.get("enabled")]
    log.info(f"Loaded {len(rules)} rules, {len(active)} active")
    return active

# ---------- Clients ----------
reader = TelegramClient(SESSION_PATH, API_ID, API_HASH)
bot = Bot(token=BOT_TOKEN)

# ---------- Copy + React logic ----------
async def handle_message(event, rule):
    try:
        # 1. Copy (no "Forwarded" tag)
        copied = await bot.copy_message(
            chat_id=rule["destination"],
            from_chat_id=event.chat_id,
            message_id=event.message.id,
        )

        # 2. React on destination
        if rule.get("reaction"):
            try:
                await bot.set_message_reaction(
                    chat_id=rule["destination"],
                    message_id=copied.message_id,
                    reaction=rule["reaction"],
                )
            except TelegramError as e:
                log.warning(f"Reaction failed [{rule['name']}]: {e}")

        # 3. Optional: react on source too
        if rule.get("copy_reaction_to_source") and rule.get("reaction"):
            try:
                await bot.set_message_reaction(
                    chat_id=event.chat_id,
                    message_id=event.message.id,
                    reaction=rule["reaction"],
                )
            except TelegramError as e:
                log.warning(f"Source reaction failed [{rule['name']}]: {e}")

        log.info(f"✅ [{rule['name']}] msg #{event.message.id} copied")

        # Rate-limit protection
        await asyncio.sleep(1.5)

    except RetryAfter as e:
        wait = e.retry_after if isinstance(e.retry_after, int) else 5
        log.warning(f"⏳ Rate limit hit, waiting {wait}s...")
        await asyncio.sleep(wait)
        return await handle_message(event, rule)

    except Exception as e:
        log.error(f"❌ [{rule['name']}] failed: {e}")

# ---------- Register handlers ----------
async def register_all():
    rules = load_rules()
    if not rules:
        log.error("No active channels in channels.json — exiting.")
        sys.exit(1)

    for rule in rules:
        async def handler(event, r=rule):
            await handle_message(event, r)

        reader.add_event_handler(
            handler,
            events.NewMessage(chats=rule["source"]),
        )
        log.info(f"👀 Watching: {rule['name']} ({rule['source']})")

# ---------- Verify bot access ----------
async def verify_access():
    rules = load_rules()
    ok, bad = 0, 0
    me = await bot.get_me()
    for rule in rules:
        try:
            member = await bot.get_chat_member(rule["destination"], me.id)
            if member.status in ("administrator", "creator"):
                ok += 1
            else:
                log.warning(f"⚠️ Bot admin nahi hai: {rule['name']}")
                bad += 1
        except TelegramError as e:
            log.error(f"❌ Destination unreachable [{rule['name']}]: {e}")
            bad += 1
    log.info(f"Access check: ✅ {ok} OK | ⚠️ {bad} problems")

# ---------- Main ----------
async def main():
    log.info("🚀 Starting Telegram Copier Bot...")

    me = await bot.get_me()
    log.info(f"🤖 Bot: @{me.username}")

    # Reader login (pehli baar OTP maangega)
    await reader.start()
    log.info("✅ Reader logged in")

    await verify_access()
    await register_all()

    log.info("🟢 Bot 24/7 chalu hai.")
    await reader.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("🛑 Manually stopped.")
