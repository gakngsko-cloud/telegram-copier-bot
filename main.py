import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ---------- Load env ----------
load_dotenv()

API_ID = int(os.getenv("TG_API_ID"))
API_HASH = os.getenv("TG_API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_NAME = os.getenv("SESSION_NAME", "copier_session")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# ---------- Paths ----------
DATA_DIR = "/data" if os.path.isdir("/data") else "."
SESSION_PATH = os.path.join(DATA_DIR, SESSION_NAME)
CHANNELS_PATH = os.path.join(DATA_DIR, "channels.json")

if DATA_DIR == "/data" and not os.path.exists(CHANNELS_PATH):
    if os.path.exists("channels.json"):
        shutil.copy("channels.json", CHANNELS_PATH)

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

# ---------- Storage ----------
def load_channels():
    if not os.path.exists(CHANNELS_PATH):
        return []
    with open(CHANNELS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_channels(channels):
    with open(CHANNELS_PATH, "w", encoding="utf-8") as f:
        json.dump(channels, f, indent=2, ensure_ascii=False)

def find_rule_by_source(source_id):
    for rule in load_channels():
        if rule.get("source") == source_id and rule.get("enabled"):
            return rule
    return None

# ---------- Clients ----------
reader = TelegramClient(SESSION_PATH, API_ID, API_HASH)
app = Application.builder().token(BOT_TOKEN).build()

# ---------- Copy logic ----------
async def handle_message(event, rule):
    try:
        copied = await app.bot.copy_message(
            chat_id=rule["destination"],
            from_chat_id=event.chat_id,
            message_id=event.message.id,
        )
        if rule.get("reaction"):
            try:
                await app.bot.set_message_reaction(
                    chat_id=rule["destination"],
                    message_id=copied.message_id,
                    reaction=rule["reaction"],
                )
            except TelegramError as e:
                log.warning(f"Reaction failed [{rule['name']}]: {e}")
        log.info(f"✅ [{rule['name']}] msg #{event.message.id} copied")
        await asyncio.sleep(1.5)
    except RetryAfter as e:
        wait = e.retry_after if isinstance(e.retry_after, int) else 5
        await asyncio.sleep(wait)
        return await handle_message(event, rule)
    except Exception as e:
        log.error(f"❌ [{rule['name']}] failed: {e}")

@reader.on(events.NewMessage())
async def on_new_message(event):
    rule = find_rule_by_source(event.chat_id)
    if rule:
        await handle_message(event, rule)

# ---------- Guard ----------
def is_admin(update: Update):
    u = update.effective_user
    return u and u.id == ADMIN_ID

# ---------- Menus ----------
def main_menu():
    kb = [
        [InlineKeyboardButton("➕ Add Channel", callback_data="menu:add")],
        [InlineKeyboardButton("📋 My Channels", callback_data="menu:list")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(kb)

async def send_main_menu(update_or_msg, edit=False):
    text = (
        "👋 *Telegram Copier Bot*\n\n"
        "Neeche buttons se sab kuch karo — kuch type nahi karna! 🎯"
    )
    kb = main_menu()
    if edit and hasattr(update_or_msg, "edit_message_text"):
        await update_or_msg.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update_or_msg.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ---------- /start ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await update.message.reply_text("⛔ Access denied.")
    ctx.user_data.clear()
    await send_main_menu(update.message)

# ---------- Callback router ----------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return await q.answer("Access denied", show_alert=True)
    await q.answer()
    data = q.data

    # ----- Main menu -----
    if data == "menu:main":
        ctx.user_data.clear()
        return await send_main_menu(q, edit=True)

    if data == "menu:help":
        txt = (
            "❓ *Help*\n\n"
            "*➕ Add Channel*\n"
            "1. Source channel se koi message forward karo\n"
            "2. Destination channel se message forward karo\n"
            "3. Emoji chuno — bas! ✅\n\n"
            "*📋 My Channels*\n"
            "Har channel ke aage Toggle / Remove button\n\n"
            "⚠️ *Zaroori:* Bot destination channel ka admin hona chahiye."
        )
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # ----- Add wizard start -----
    if data == "menu:add":
        ctx.user_data.clear()
        ctx.user_data["step"] = "source"
        txt = (
            "➕ *New Channel — Step 1/3*\n\n"
            "📥 *Source channel* ka koi message yahan **forward** karo.\n"
            "_(Jahan se copy karna hai)_\n\n"
            "⚠️ Channel ka message forward karo, user ka nahi."
        )
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="menu:main")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # ----- List channels -----
    if data == "menu:list":
        channels = load_channels()
        if not channels:
            txt = "📭 *Koi channel nahi hai.*\n\n➕ Add Channel dabao."
            kb = [[InlineKeyboardButton("➕ Add Channel", callback_data="menu:add")],
                  [InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
            return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

        lines = ["📋 *Aapke Channels:*\n"]
        kb = []
        for i, c in enumerate(channels):
            status = "🟢" if c.get("enabled") else "🔴"
            lines.append(
                f"{i+1}. {status} *{c['name']}*\n"
                f"   `{c['source']}` → `{c['destination']}` | {c.get('reaction','—')}"
            )
            kb.append([
                InlineKeyboardButton(
                    f"{'🔴 Turn OFF' if c.get('enabled') else '🟢 Turn ON'} — {c['name'][:20]}",
                    callback_data=f"ch:toggle:{i}"
                )
            ])
            kb.append([
                InlineKeyboardButton(
                    f"🗑️ Remove — {c['name'][:20]}",
                    callback_data=f"ch:remove:{i}"
                )
            ])
        kb.append([InlineKeyboardButton("➕ Add More", callback_data="menu:add")])
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
        return await q.edit_message_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

    # ----- Toggle / Remove -----
    if data.startswith("ch:toggle:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["enabled"] = not ch[idx].get("enabled", True)
            save_channels(ch)
            state = "🟢 ON" if ch[idx]["enabled"] else "🔴 OFF"
            await q.answer(f"{state} — {ch[idx]['name']}", show_alert=True)
        # refresh list
        ctx.user_data["_refresh"] = True
        return await on_callback(
            type("U", (), {"callback_query": q, "effective_user": q.from_user, "message": None})(),
            ctx
        ) if False else await _refresh_list(q, ctx)

    if data.startswith("ch:remove:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            removed = ch.pop(idx)
            save_channels(ch)
            await q.answer(f"🗑️ Removed: {removed['name']}", show_alert=True)
        return await _refresh_list(q, ctx)

    # ----- Emoji choice -----
    if data.startswith("emoji:"):
        emoji = data.split(":", 1)[1]
        ctx.user_data["reaction"] = emoji
        # auto-add with detected names
        src = ctx.user_data.get("source")
        dst = ctx.user_data.get("destination")
        src_name = ctx.user_data.get("source_name", "Channel")
        if not src or not dst:
            await q.edit_message_text("❌ Something went wrong. /start se dobara try karo.")
            return

        new_rule = {
            "name": src_name[:40],
            "source": src,
            "destination": dst,
            "reaction": emoji,
            "enabled": True,
            "copy_reaction_to_source": False,
        }
        ch = load_channels()
        ch.append(new_rule)
        save_channels(ch)
        ctx.user_data.clear()

        txt = (
            "🎉 *Channel Added!*\n\n"
            f"📌 Name: *{new_rule['name']}*\n"
            f"📥 Source: `{src}`\n"
            f"📤 Destination: `{dst}`\n"
            f"💫 Reaction: {emoji}\n\n"
            "Ab source mein message aayega toh auto copy + reaction hoga. ✅"
        )
        kb = [
            [InlineKeyboardButton("➕ Add Another", callback_data="menu:add")],
            [InlineKeyboardButton("📋 My Channels", callback_data="menu:list")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
        ]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    # ----- Cancel wizard -----
    if data == "wizard:cancel":
        ctx.user_data.clear()
        return await send_main_menu(q, edit=True)

async def _refresh_list(q, ctx):
    # Re-build the list view
    channels = load_channels()
    if not channels:
        txt = "📭 *Koi channel nahi hai.*"
        kb = [[InlineKeyboardButton("➕ Add Channel", callback_data="menu:add")],
              [InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    lines = ["📋 *Aapke Channels:*\n"]
    kb = []
    for i, c in enumerate(channels):
        status = "🟢" if c.get("enabled") else "🔴"
        lines.append(
            f"{i+1}. {status} *{c['name']}*\n"
            f"   `{c['source']}` → `{c['destination']}` | {c.get('reaction','—')}"
        )
        kb.append([InlineKeyboardButton(
            f"{'🔴 Turn OFF' if c.get('enabled') else '🟢 Turn ON'} — {c['name'][:20]}",
            callback_data=f"ch:toggle:{i}"
        )])
        kb.append([InlineKeyboardButton(
            f"🗑️ Remove — {c['name'][:20]}",
            callback_data=f"ch:remove:{i}"
        )])
    kb.append([InlineKeyboardButton("➕ Add More", callback_data="menu:add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return await q.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )

# ---------- Forward handler (wizard) ----------
async def handle_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    step = ctx.user_data.get("step")
    if step not in ("source", "destination"):
        return

    msg = update.message
    chat_id = None
    title = "Channel"
    if msg.forward_from_chat:
        chat_id = msg.forward_from_chat.id
        title = msg.forward_from_chat.title or "Channel"
        if msg.forward_from_chat.type == "channel":
            chat_id = int(f"-100{chat_id}")
    if not chat_id:
        return await msg.reply_text("❌ Channel ka message forward karo (user ka nahi).")

    if step == "source":
        ctx.user_data["source"] = chat_id
        ctx.user_data["source_name"] = title
        ctx.user_data["step"] = "destination"
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")]]
        await msg.reply_text(
            f"✅ Source: *{title}*\n`{chat_id}`\n\n"
            "📤 *Step 2/3* — Ab *destination channel* (jahan bhejna hai) ka message forward karo.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
    elif step == "destination":
        if chat_id == ctx.user_data.get("source"):
            return await msg.reply_text("❌ Source aur destination alag hone chahiye.")
        ctx.user_data["destination"] = chat_id
        ctx.user_data["destination_name"] = title
        ctx.user_data["step"] = "reaction"

        emojis = ["👍", "🔥", "❤️", "😂", "😮", "😢", "🙏", "👏", "🎉", "💯", "⚡", "💎"]
        buttons = [
            [InlineKeyboardButton(emojis[i], callback_data=f"emoji:{emojis[i]}"),
             InlineKeyboardButton(emojis[i+1], callback_data=f"emoji:{emojis[i+1]}"),
             InlineKeyboardButton(emojis[i+2], callback_data=f"emoji:{emojis[i+2]}")]
            for i in range(0, len(emojis), 3)
        ]
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")])
        await msg.reply_text(
            f"✅ Destination: *{title}*\n`{chat_id}`\n\n"
            "💫 *Step 3/3* — Reaction emoji chuno:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown",
        )

# ---------- Ignore stray text ----------
async def ignore_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if ctx.user_data.get("step") in ("source", "destination"):
        await update.message.reply_text("⏳ Pehle channel ka message *forward* karo, text nahi.", parse_mode="Markdown")

# ---------- Verify access ----------
async def verify_access():
    channels = load_channels()
    if not channels:
        log.warning("⚠️ Koi channel nahi. Telegram par /start bhejo.")
        return
    ok, bad = 0, 0
    me = await app.bot.get_me()
    for rule in channels:
        if not rule.get("enabled"):
            continue
        try:
            member = await app.bot.get_chat_member(rule["destination"], me.id)
            if member.status in ("administrator", "creator"):
                ok += 1
            else:
                log.warning(f"⚠️ Bot admin nahi hai: {rule['name']}")
                bad += 1
        except TelegramError as e:
            log.error(f"❌ [{rule['name']}]: {e}")
            bad += 1
    log.info(f"Access: ✅ {ok} OK | ⚠️ {bad} problems")

# ---------- Register handlers ----------
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CallbackQueryHandler(on_callback))
app.add_handler(MessageHandler(filters.FORWARDED, handle_forward))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ignore_text))

# ---------- Main ----------
async def main():
    log.info("🚀 Starting Telegram Copier Bot...")
    me = await app.bot.get_me()
    log.info(f"🤖 Bot: @{me.username}")

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    log.info("✅ Bot polling started")

    await reader.start()
    log.info("✅ Reader logged in")

    await verify_access()
    log.info("🟢 Bot 24/7 chalu hai.")
    await reader.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("🛑 Stopped.")
