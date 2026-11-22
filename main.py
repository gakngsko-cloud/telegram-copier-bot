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

# ---------- Copy + React logic ----------
async def handle_message(event, rule):
    try:
        copied = None
        copy_on = rule.get("copy_enabled", True)
        react_on = rule.get("reaction_enabled", True)

        # 1. COPY
        if copy_on:
            try:
                if str(event.chat_id).startswith("-100"):
                    copied = await app.bot.copy_message(
                        chat_id=rule["destination"],
                        from_chat_id=event.chat_id,
                        message_id=event.message.id,
                    )
                else:
                    copied = await event.message.copy_to(rule["destination"])
                log.info(f"📋 [{rule['name']}] copied msg #{event.message.id}")
            except Exception as e:
                log.error(f"Copy failed via Bot, trying Telethon... [{rule['name']}]")
                copied = await event.message.copy_to(rule["destination"])

        # 2. REACTION (via User Account for multiple support)
        if react_on and rule.get("reaction"):
            emojis = [e.strip() for e in rule["reaction"].split(",") if e.strip()]
            if emojis:
                try:
                    if copied:
                        # Destination mein message fetch karke apne account se react karo
                        try:
                            dest_msg = await reader.get_messages(rule["destination"], ids=copied.id)
                            if dest_msg:
                                if isinstance(dest_msg, list): dest_msg = dest_msg[0]
                                await dest_msg.react(emojis)
                                log.info(f"💫 [{rule['name']}] reacted on copy via User Account ({emojis})")
                        except Exception as e:
                            log.warning(f"Telethon react on copy failed, trying bot: {e}")
                            # Fallback to bot (bot sirf ek emoji support karta hai)
                            await app.bot.set_message_reaction(
                                chat_id=rule["destination"],
                                message_id=copied.id,
                                reaction=emojis[0]
                            )
                    else:
                        # React on source message via User Account
                        await event.message.react(emojis)
                        log.info(f"💫 [{rule['name']}] reacted on source via User Account ({emojis})")
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
        [InlineKeyboardButton("➕ Add Source", callback_data="menu:add")],
        [InlineKeyboardButton("📋 My Sources", callback_data="menu:list")],
        [InlineKeyboardButton("❓ Help", callback_data="menu:help")],
    ])

async def send_main_menu(target, edit=False):
    text = (
        "👋 *Telegram Copier Bot*\n\n"
        "📋 Copy aur 💫 Reaction — dono alag-alag control karo!\n"
        "Ab aap multiple reactions (jaise 👍,🔥) bhi daal sakte ho. 🎯"
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
        txt = "📭 *Koi source nahi hai.*\n\n➕ Add Source dabao."
        kb = [[InlineKeyboardButton("➕ Add Source", callback_data="menu:add")],
              [InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return txt, InlineKeyboardMarkup(kb)

    lines = ["📋 *Aapke Sources:*\n"]
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

# ---------- Config screen ----------
async def send_config_screen(target, ctx, edit=False):
    copy_on = ctx.user_data.get("copy_enabled", True)
    react_on = ctx.user_data.get("reaction_enabled", True)
    emoji = ctx.user_data.get("reaction", "👍")

    warn = ""
    if not copy_on and not react_on:
        warn = "\n⚠️ *Dono OFF hain!* Kam se kam ek ON karo.\n"

    txt = (
        "⚙️ *Final Config*\n\n"
        f"💫 Reaction emojis: {emoji}\n"
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
        kb.append([InlineKeyboardButton("✅ Done — Add Source", callback_data="cfg:done")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")])
    
    if edit:
        await target.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    else:
        await target.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

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
            "*➕ Add Source*\n"
            "1. Source se message forward karo\n"
            "2. Destination se message forward karo\n"
            "3. Reaction emojis type karo (comma lagakar multiple dal sakte ho, jaise `👍,🔥`)\n"
            "4. Copy / Reaction ON-OFF karo\n"
            "5. Done ✅\n\n"
            "*Note:* Reaction hamesha aapke account se lagega (multiple support ke liye)."
        )
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="menu:main")]]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if data == "menu:add":
        ctx.user_data.clear()
        ctx.user_data["step"] = "source"
        txt = (
            "➕ *New Source — Step 1/3*\n\n"
            "📥 *Source* (Channel / User / Group) ka koi message yahan **forward** karo."
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
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:react:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["reaction_enabled"] = not ch[idx].get("reaction_enabled", True)
            save_channels(ch)
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:toggle:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch[idx]["enabled"] = not ch[idx].get("enabled", True)
            save_channels(ch)
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data.startswith("ch:remove:"):
        idx = int(data.split(":")[2])
        ch = load_channels()
        if 0 <= idx < len(ch):
            ch.pop(idx)
            save_channels(ch)
        txt, kb = build_list_view()
        return await q.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

    if data == "cfg:copy":
        ctx.user_data["copy_enabled"] = not ctx.user_data.get("copy_enabled", True)
        return await send_config_screen(q, ctx, edit=True)

    if data == "cfg:react":
        ctx.user_data["reaction_enabled"] = not ctx.user_data.get("reaction_enabled", True)
        return await send_config_screen(q, ctx, edit=True)

    if data == "cfg:done":
        copy_on = ctx.user_data.get("copy_enabled", True)
        react_on = ctx.user_data.get("reaction_enabled", True)
        if not copy_on and not react_on:
            return await q.answer("⚠️ Kam se kam ek ON karo!", show_alert=True)

        src = ctx.user_data.get("source")
        dst = ctx.user_data.get("destination")
        src_name = ctx.user_data.get("source_name", "Source")
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

        txt = (
            "🎉 *Source Added!*\n\n"
            f"📌 Name: *{new_rule['name']}*\n"
            f"📥 Source: `{src}`\n"
            f"📤 Destination: `{dst}`\n"
            f"💫 Reaction: {emoji}\n\n"
            "Bas! Ab kaam karega. ✅"
        )
        kb = [
            [InlineKeyboardButton("➕ Add Another", callback_data="menu:add")],
            [InlineKeyboardButton("📋 My Sources", callback_data="menu:list")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
        ]
        return await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")

    if data == "wizard:cancel":
        ctx.user_data.clear()
        return await send_main_menu(q, edit=True)

# ---------- Forward handler (wizard) ----------
async def handle_forward(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    step = ctx.user_data.get("step")
    if step not in ("source", "destination"):
        return

    msg = update.message
    chat_id = None
    title = "Unknown"

    if getattr(msg, "forward_origin", None):
        origin = msg.forward_origin
        if origin.type == "channel":
            chat_id = origin.chat.id
            title = origin.chat.title or "Channel"
        elif origin.type == "chat":
            chat_id = origin.chat.id
            title = origin.chat.title or "Group"
        elif origin.type == "user":
            chat_id = origin.sender_user.id
            title = f"{origin.sender_user.first_name or ''} {origin.sender_user.last_name or ''}".strip() or "User"
        elif origin.type == "hidden_user":
            return await msg.reply_text("❌ Is user ne privacy on ki hai. Aap direct unki ID nahi nikal sakte.")
    elif getattr(msg, "forward_from_chat", None):
        chat_id = msg.forward_from_chat.id
        title = msg.forward_from_chat.title or "Chat"
    elif getattr(msg, "forward_from", None):
        chat_id = msg.forward_from.id
        title = msg.forward_from.first_name or "User"

    if not chat_id:
        return await msg.reply_text("❌ Kisi valid Channel, Group ya User ka message forward karo.")

    if str(chat_id).startswith("-") and not str(chat_id).startswith("-100"):
        chat_id = int(f"-100{str(chat_id).replace('-', '')}")

    if step == "source":
        ctx.user_data["source"] = chat_id
        ctx.user_data["source_name"] = title
        ctx.user_data["step"] = "destination"
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")]]
        await msg.reply_text(
            f"✅ Source: *{title}*\n`{chat_id}`\n\n"
            "📤 *Step 2/3* — Ab *Destination* (jahan bhejna hai) ka message forward karo.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )
    elif step == "destination":
        if chat_id == ctx.user_data.get("source"):
            return await msg.reply_text("❌ Source aur destination alag hone chahiye.")
        ctx.user_data["destination"] = chat_id
        ctx.user_data["destination_name"] = title
        ctx.user_data["step"] = "reaction"
        
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="wizard:cancel")]]
        await msg.reply_text(
            f"✅ Destination: *{title}*\n`{chat_id}`\n\n"
            "💫 *Step 3/3* — Reaction emojis type karke bhejo.\n"
            "Ek se zyada dalne ke liye comma `,` lagao (jaise: `👍,🔥,❤️`).\n"
            "Agar reaction nahi chahiye to `skip` likho.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown",
        )

# ---------- Text Handler (Reaction & Ignore) ----------
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    step = ctx.user_data.get("step")
    
    if step in ("source", "destination"):
        await update.message.reply_text("⏳ Pehle message *forward* karo, text nahi.", parse_mode="Markdown")
    elif step == "reaction":
        text = update.message.text.strip()
        if text.lower() == 'skip':
            ctx.user_data["reaction"] = ""
        else:
            emojis = [e.strip() for e in text.split(",") if e.strip()]
            ctx.user_data["reaction"] = ",".join(emojis)
            
        ctx.user_data["copy_enabled"] = True
        ctx.user_data["reaction_enabled"] = True
        ctx.user_data["step"] = "config"
        
        await send_config_screen(update.message, ctx, edit=False)

# ---------- Verify access ----------
async def verify_access():
    channels = load_channels()
    if not channels:
        log.warning("⚠️ Koi source nahi. Telegram par /start bhejo.")
        return
    ok, bad = 0, 0
    me = await app.bot.get_me()
    for rule in channels:
        if not rule.get("enabled", True) or not rule.get("copy_enabled", True):
            continue
        try:
            if str(rule["destination"]).startswith("-100"):
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
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

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
    log.info(
