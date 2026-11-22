import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
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
SESSION_STRING = os.getenv("SESSION_STRING")

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
        if rule.get("source") == source_id and rule.get("enabled", True):
            return rule
    return None

# ---------- Clients ----------
if SESSION_STRING:
    reader = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    reader = TelegramClient(SESSION_PATH, API_ID, API_HASH)

app = Application.builder().token(BOT_TOKEN).build()

# ---------- Copy + React logic (SEPARATE) ----------
async def handle_message(event, rule):
    try:
        copied = None
        copy_on = rule.get("copy_enabled", True)
        react_on = rule.get("reaction_enabled", True)

        # 1. COPY (optional)
        if copy_on:
            copied = await app.bot.copy_message(
                chat_id=rule["destination"],
                from_chat_id=event.chat_id,
                message_id=event.message.id,
            )
            log.info(f"📋 [{rule['name']}] copied msg #{event.message.id}")

        # 2. REACTION (optional, independent)
        if react_on and rule.get("reaction"):
            emoji = rule["reaction"]
            try:
                if copied:
                    await app.bot.set_message_reaction(
                        chat_id=rule["destination"],
                        message_id=copied.message_id,
                        reaction=emoji,
                    )
                    log.info(f"💫 [{rule['name']}] reacted on copy ({emoji})")
                else:
                    await event.message.react(emoji)
                    log.info(f"💫 [{rule['name']}] reacted on source ({emoji})")
            except Exception as e:
                log.warning(f"Reaction failed [{rule['name']}]: {e}")

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

# ---------- Main menu ----------
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Channel", callback_data="menu:add")],
        [InlineKeyboardButton("📋 My Channels", callback_data="menu:list")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
    ])

async def send_main_menu(target, edit=False):
    text = (
        "👋 *Telegram Copier Bot*\n\n"
        "📋 Copy aur 💫 Reaction — dono alag-alag control karo!\n"
        "Neeche buttons se manage karo — kuch type nahi karna. 🎯"
    )
    if edit:
        await target.edit_message_text(text, reply_markup=main_menu(), parse_mode="Markdown")
    else:
        await target.reply_text(text, reply_markup=main_menu(), parse_mode="Markdown")

# ---------- /start ----------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await update.message.reply_text("⛔ Access denied.")
    ctx.user_data.clear()
    await send_main_menu(update.message)

# ---------- Channel list builder ----------
def build_list_view():
    channels = load_channels()
    if not channels:
        txt = "📭 *Koi channel nahi hai.*\n\n➕ Add Channel dabao."
        kb = [[InlineKeyboardButton("➕ Add Channel", callback_data="menu:add")],
              [InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return txt, InlineKeyboardMarkup(kb)

    lines = ["📋 *Aapke Channels:*\n"]
    kb = []
    for i, c in enumerate(channels):
        st = "🟢" if c.get("enabled", True) else "🔴"
        copy_st = "✅" if c.get("copy_enabled", True) else "❌"
        react_st = "✅" if c.get("reaction_enabled", True) else "❌"
        lines.append(
            f"{i+1}. {st} *{c['name']}*\n"
            f"   📋 Copy {copy_st} | 💫 React {react_st} {c.get('reaction','')}\n"
            f"   `{c['source']}` → `{c['destination']}`"
        )
        kb.append([
            InlineKeyboardButton(
                f"📋 Copy {'✅' if c.get('copy_enabled', True) else '❌'}",
                callback_data=f"ch:copy:{i}"
            ),
            InlineKeyboardButton(
                f"💫 React {'✅' if c.get('reaction_enabled', True) else '❌'}",
                callback_data=f"ch:react:{i}"
            ),
        ])
        kb.append([
            InlineKeyboardButton(
                f"{'🔴 Disable' if c.get('enabled', True) else '🟢 Enable'} — {c['name'][:15]}",
                callback_data=f"ch:toggle:{i}"
            ),
            InlineKeyboardButton("🗑️ Remove", callback_data=f"ch:remove:{i}"),
        ])
    kb.append([InlineKeyboardButton("➕ Add More", callback_data="menu:add")])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="menu:main")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)

# ---------- Add wizard: config screen ----------
async def show_config_screen(q, ctx):
    copy_on = ctx.user_data.get("copy_enabled", True)
    react_on = ctx.user_data.get("reaction_enabled", True)
    emoji = ctx.user_data.get("reaction", "👍")

    warn = ""
    if not copy_on and not react_on:
        warn = "\n⚠️ *Dono OFF hain!* Kam se kam ek ON karo.\n"

    txt = (
        "⚙️ *Final Config*\n\n"
        f"💫 Reaction emoji: {emoji}\n"
        f"📋 Copy messages: {'✅ ON' if copy_on else '❌ OFF'}\n"
        f"💫 Auto reaction: {'✅ ON' if react_on else '❌ OFF'}\n"
        f"{warn}\n"
        "_Buttons dabao to toggle karne ke liye._"
    )
    kb = [
        [InlineKeyboardButton(
            f"📋 Copy: {'✅ ON' if copy_on else '❌ OFF'}",
            callback_data="cfg:copy"
        )],
        [InlineKeyboardButton(
            f"💫 Reaction: {'✅ ON' if react_on else '❌ OFF'}",
            callback_data="cfg:react"
        )],
    ]
    if copy_on or react_on:
        kb.append([InlineKeyboardButton("✅ Done — Add Channel", callback_data="cfg:done")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")])
    await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

# ---------- Callback router ----------
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return await q.answer("Access denied", show_alert=True)
    await q.answer()
    data = q.data

    if data == "menu:main":
        ctx.user_data.clear()
        return await send_main_menu(q, edit=True)

    if data == "menu:help":
        txt = (
            "❓ *Help*\n\n"
            "*➕ Add Channel*\n"
            "1. Source channel se message forward karo\n"
            "2. Destination channel se message forward karo\n"
            "3. Emoji chuno\n"
            "4. Copy / Reaction alag-alag ON-OFF karo\n"
            "5. Done — bas! ✅\n\n"
            "*📋 My Channels*\n"
            "Har channel par 4 buttons:\n"
            "• 📋 Copy ✅/❌\n"
            "• 💫 React ✅/❌\n"
            "• 🟢/🔴 Enable/Disable\n"
            "• 🗑️ Remove\n\n"
            "*Modes:*\n"
            "• Copy ON + React ON → copy + react\n"
            "• Copy ON + React OFF → sirf copy\n"
            "• Copy OFF + React ON → sirf source par react\n"
            "• Copy OFF + React OFF → allow nahi\n\n"
            "⚠️ *Zaroori:* Bot destination channel ka admin hona chahiye (jab copy ON ho)."
        )
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if data == "menu:add":
        ctx.user_data.clear()
        ctx.user_data["step"] = "source"
        txt = (
            "➕ *New Channel — Step 1/3*\n\n"
            "📥 *Source channel* ka koi message yahan **forward** karo.\n"
            "_(Jahan se copy/reaction karna hai)_\n\n"
            "⚠️ Channel ka message forward karo, user ka nahi."
        )
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if data == "menu:list":
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:copy:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["copy_enabled"] = not ch[idx].get("copy_enabled", True)
            save_channels(ch)
            st = "ON" if ch[idx]["copy_enabled"] else "OFF"
            await q.answer(f"📋 Copy: {st}")
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:react:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["reaction_enabled"] = not ch[idx].get("reaction_enabled", True)
            save_channels(ch)
            st = "ON" if ch[idx]["reaction_enabled"] else "OFF"
            await q.answer(f"💫 Reaction: {st}")
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:toggle:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["enabled"] = not ch[idx].get("enabled", True)
            save_channels(ch)
            st = "ON" if ch[idx]["enabled"] else "OFF"
            await q.answer(f"Channel: {st}")
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:remove:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            removed = ch.pop(idx)
            save_channels(ch)
            await q.answer(f"🗑️ Removed: {removed['name']}", show_alert=True)
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data == "cfg:copy":
        ctx.user_data["copy_enabled"] = not ctx.user_data.get("copy_enabled", True)
        return await show_config_screen(q, ctx)

    if data == "cfg:react":
        ctx.user_data["reaction_enabled"] = not ctx.user_data.get("reaction_enabled", True)
        return await show_config_screen(q, ctx)

    if data == "cfg:done":
        copy_on = ctx.user_data.get("copy_enabled", True)
        react_on = ctx.user_data.get("reaction_enabled", True)
        if not copy_on and not react_on:
            return await q.answer("⚠️ Kam se kam ek ON karo!", show_alert=True)

        src = ctx.user_data.get("source")
        dst = ctx.user_data.get("destination")
        src_name = ctx.user_data.get("source_name", "Channel")
        emoji = ctx.user_data.get("reaction", "👍")

        if not src or not dst:
            return await q.edit_message_text("❌ Something went wrong. /start se dobara try karo.")

        new_rule = {
            "name": src_name[:40],
            "source": src,
            "destination": dst,
            "reaction": emoji,
            "copy_enabled": copy_on,
            "reaction_enabled": react_on,
            "enabled": True,
        }
        ch = load_channels()
        ch.append(new_rule)
        save_channels(ch)
        ctx.user_data.clear()

        mode = []
        if copy_on:
            mode.append("📋 Copy")
        if react_on:
            mode.append("💫 React")
        txt = (
            "🎉 *Channel Added!*\n\n"
            f"📌 Name: *{new_rule['name']}*\n"
            f"📥 Source: `{src}`\n"
            f"📤 Destination: `{dst}`\n"
            f"💫 Reaction: {emoji}\n"
            f"⚙️ Features: {' + '.join(mode)}\n\n"
            "Bas! Ab source mein message aayega toh yeh features trigger honge. ✅"
        )
        kb = [
            [InlineKeyboardButton("➕ Add Another", callback_data="menu:add")],
            [InlineKeyboardButton("📋 My Channels", callback_data="menu:list")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
        ]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if data == "wizard:cancel":
        ctx.user_data.clear()
        return await send_main_menu(q, edit=True)

# ---------- Emoji choice → config screen ----------
async def handle_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != ADMIN_ID:
        return await q.answer("Access denied", show_alert=True)
    await q.answer()
    emoji = q.data.split(":", 1)[1]
    ctx.user_data["reaction"] = emoji
    ctx.user_data["copy_enabled"] = True
    ctx.user_data["reaction_enabled"] = True
    await show_config_screen(q, ctx)

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

    # Naya forward_origin check karo (PTB v21+)
    if getattr(msg, "forward_origin", None):
        if msg.forward_origin.type == "channel":
            chat_id = msg.forward_origin.chat.id
            title = msg.forward_origin.chat.title or "Channel"
        elif msg.forward_origin.type == "user":
            return await msg.reply_text("❌ User ka message forward mat karo. Sirf *Channel* ka message forward karo.", parse_mode="Markdown")

    # Agar purane tareeke se forward hua hai (backup)
    elif getattr(msg, "forward_from_chat", None):
        chat_id = msg.forward_from_chat.id
        title = msg.forward_from_chat.title or "Channel"

    if not chat_id:
        return await msg.reply_text("❌ Channel ka message forward karo (user ka nahi).")

    if step == "source":
        ctx.user_data["source"] = chat_id
        ctx.user_data["source_name"] = title
        ctx.user_data["step"] = "destination"
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")]]
        await msg.reply_text(
            f"✅ Source: *{title}*\n`{chat_id}`\n\n"
            "📤 *Step 2/3* — Ab *destination channel* ka message forward karo.\n"
            "_(Jahan copy bhejni hai — agar sirf reaction chahiye, koi bhi channel forward kar do)_",
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
        if not rule.get("enabled", True):
            continue
        if not rule.get("copy_enabled", True):
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
app.add_handler(CallbackQueryHandler(handle_emoji, pattern="^emoji:"))
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
