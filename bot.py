import asyncio
import html
import io
import os
import re
import time
import tempfile
import shutil
import math
from urllib.parse import urlparse
from collections import defaultdict, deque
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ChatMemberUpdated, ChatJoinRequest, BotCommand, BotCommandScopeAllChatAdministrators, ReplyParameters
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB = os.getenv("MONGO_DB", "group_help_bot")
START_PHOTO = os.getenv("START_PHOTO", "https://kommodo.ai/i/ynjRa4bLTdAC3ddCj3l5")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/")
OWNER_URL = os.getenv("OWNER_URL", "https://t.me/")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/")

DEFAULT_WELCOME = (
    "💐 ᴡᴇʟᴄᴏᴍᴇ ᴛᴏ ᴏᴜʀ ɢᴄ ᴍᴀᴋᴇ ꜰʀɪᴇɴᴅꜱ, ꜱʜᴀʀᴇ ᴠɪʙᴇꜱ, ᴀɴᴅ ᴇɴᴊᴏʏ ᴛʜᴇ ᴄʜᴀᴛꜱ..ʀᴇꜱᴘᴇᴄᴛ ᴇᴠᴇʀʏᴏɴᴇ & ᴋᴇᴇᴘ ᴛʜᴇ ᴘᴏꜱɪᴛɪᴠɪᴛʏ ᴀʟɪᴠᴇ..⭐️🍃\n\n"
    "𝆯 𝐍ᴀᴍᴇ :- ㅤㅤ{name} 🌷\n"
    "𝆯 𝐔ꜱᴇʀɪᴅ :- {id}\n"
    "𝆯 𝐔ꜱᴇʀɴᴀᴍᴇ :- ㅤ {username}\n\n"
    "𔘓⁩⁩ 𖹬 թ꧊꘍𐫶𖾝 ꮗᛧ꧊̵𖾝𖾖𖾝 ઠ𖾓៵៱᎔ᏽ𖾔𖾖𖾗 ᭔𖾝𐫶૰𖾛𖾔\n"
    "᭔𖾝𖾗𖾓 բ𖾖ᛧ𖾔᎔𝛛𖾗....🍾💐\n\n"
    "𝁘ꠋ 𐍰૰𖾗𖾓 ꧊ꠋ᎔ Ⲋᛧ꧊𖾝᎔⁠⁠⁠𐫶𖾝 ғ૰᎑᎔𝚍 ꧊ꠋ᎔ թ꘍ᛧ᎔⭐️🌷𓂃\n"
    "❍════════════════❍\n"
    "[˹ ⋏ ᑯ ᥲ ┋ 𐌼 ᥙ s ɪ ᴄ ✞](buttonurl:http://t.me/Adamusiicbot?startgroup=s&admin=delete_messages+manage_video_chats+pin_messages+invite_users)"
)

# Telegram does not expose custom background colours for inline keyboard buttons.
# The style number controls the welcome-card accent colours instead.
WELCOME_BUTTON_RE = re.compile(r"\[([^\]]+)\]\(buttonurl:([^\)\s]+)\)", re.IGNORECASE)


# Optional NSFW moderation. Create keys with a moderation provider and set these in Railway.
SIGHTENGINE_USER = os.getenv("SIGHTENGINE_API_USER", "")
SIGHTENGINE_SECRET = os.getenv("SIGHTENGINE_API_SECRET", "")
NSFW_THRESHOLD = float(os.getenv("NSFW_THRESHOLD", "0.80"))
NSFW_REMOVE_PARTIAL = os.getenv("NSFW_REMOVE_PARTIAL", "false").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")
if not OWNER_ID:
    raise RuntimeError("OWNER_ID is required")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is required")

mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo[MONGO_DB]
groups = db.groups
locks = db.locks
filters = db.filters
filter_pending = db.filter_pending
banwords = db.banwords
users = db.users
warnings = db.warnings
fedbans = db.fedbans
chat_counts = db.ranking_counts

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

flood = defaultdict(deque)
ranking_blocked_until = {}
welcome_recent = {}


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


async def is_admin_for_chat(chat_id: int, uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    try:
        m = await bot.get_chat_member(chat_id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


async def get_member(message: Message, uid=None):
    uid = uid or (message.from_user.id if message.from_user else 0)
    try:
        return await bot.get_chat_member(message.chat.id, uid)
    except Exception:
        return None


async def is_admin(message: Message, uid=None):
    uid = uid or (message.from_user.id if message.from_user else 0)
    if uid == OWNER_ID:
        return True
    m = await get_member(message, uid)
    return bool(m and m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR))


async def can_restrict_members(message: Message):
    uid = message.from_user.id if message.from_user else 0
    if uid == OWNER_ID:
        return True
    m = await get_member(message, uid)
    if not m:
        return False
    if m.status == ChatMemberStatus.CREATOR:
        return True
    return bool(m.status == ChatMemberStatus.ADMINISTRATOR and getattr(m, "can_restrict_members", False))


async def target_is_admin(message: Message, user_id: int) -> bool:
    m = await get_member(message, user_id)
    return bool(m and m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR))


async def exempt(chat_id, uid):
    # Group admins/owner are always exempt from automatic moderation.
    if uid == OWNER_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, uid)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return True
    except Exception:
        pass
    x = await users.find_one({"chat_id": chat_id, "user_id": uid})
    return bool(x and (x.get("free") or x.get("approved")))


def mention(user):
    return f'<a href="tg://user?id={user.id}">{html.escape(user.full_name or "User")}</a>'


def args(message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def target(message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user
    a = args(message)
    if a.isdigit():
        try:
            return (await bot.get_chat_member(message.chat.id, int(a))).user
        except Exception:
            return None
    username = a.split()[0].lstrip("@").lower()
    if username:
        row = await users.find_one({"chat_id": message.chat.id, "username": {"$regex": f"^{re.escape(username)}$", "$options": "i"}})
        if row:
            try:
                return (await bot.get_chat_member(message.chat.id, int(row["user_id"]))).user
            except Exception:
                return None
    return None


async def delete_safe(message):
    try:
        await message.delete()
    except Exception:
        pass


async def notify_admins(message: Message, reason: str):
    """Notify current group admins for automatic moderation events."""
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        tags = [mention(m.user) for m in admins if not m.user.is_bot]
        if not tags:
            return
        who = mention(message.from_user) if message.from_user else "User"
        await message.answer(
            f"🛡️ <b>𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ 𝐀ʟᴇʀᴛ</b>\n"
            f"👤 {who}\n"
            f"⚠️ <b>𝐑ᴇᴀsᴏɴ:</b> {html.escape(reason)}\n\n"
            f"👮 <b>𝐀ᴅᴍɪɴs:</b> " + " ".join(tags)
        )
    except Exception as e:
        print("Admin notification error:", e)


async def report_admins(message: Message, title: str = "📣 𝐀ᴅᴍɪɴ 𝐑ᴇᴘᴏʀᴛ"):
    """Send an explicit report to all current human group admins."""
    try:
        admins = await bot.get_chat_administrators(message.chat.id)
        tags = [mention(m.user) for m in admins if not m.user.is_bot]
        if not tags:
            return
        who = mention(message.from_user) if message.from_user else "User"
        body = (
            f"{title}\n"
            f"👤 <b>𝐅ʀᴏᴍ:</b> {who}\n"
            f"🆔 <b>𝐈ᴅ:</b> <code>{message.from_user.id if message.from_user else 0}</code>\n"
            f"💬 <b>𝐑ᴇǫᴜᴇsᴛ:</b> {html.escape(message.text or message.caption or '')}\n\n"
            + "👮 <b>𝐀ᴅᴍɪɴs:</b> " + " ".join(tags)
        )
        await message.answer(body)
    except Exception as e:
        print("Admin report error:", e)

async def delete_and_alert(message: Message, reason: str):
    """Delete a violating message and notify admins; never bans the sender."""
    deleted = False
    try:
        await message.delete()
        deleted = True
    except Exception as e:
        print("Delete error:", e)
    if deleted:
        await notify_admins(message, reason)
    return deleted


HELP_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛡️ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ", callback_data="h:mod"), InlineKeyboardButton(text="🔒 𝐋ᴏᴄᴋs", callback_data="h:lock")],
    [InlineKeyboardButton(text="🔞 𝐍sғᴡ", callback_data="h:nsfw"), InlineKeyboardButton(text="🏷️ 𝐅ɪʟᴛᴇʀs", callback_data="h:filter")],
    [InlineKeyboardButton(text="👋 𝐖ᴇʟᴄᴏᴍᴇ", callback_data="h:welcome"), InlineKeyboardButton(text="🧹 𝐏ᴜʀɢᴇ", callback_data="h:purge")],
    [InlineKeyboardButton(text="🏆 𝐑ᴀɴᴋɪɴɢ", callback_data="h:rank"), InlineKeyboardButton(text="⚙️ 𝐂ᴏɴғɪɢ", callback_data="h:config")],
    [InlineKeyboardButton(text="📥 𝐉ᴏɪɴ 𝐑ᴇǫᴜᴇsᴛs", callback_data="h:requests"), InlineKeyboardButton(text="📢 𝐁ʀᴏᴀᴅᴄᴀsᴛ", callback_data="h:broadcast")],
    [InlineKeyboardButton(text="📖 𝐇ᴇʟᴘ", callback_data="back"), InlineKeyboardButton(text="💬 𝐒ᴜᴘᴘᴏʀᴛ", url=SUPPORT_URL)],
    [InlineKeyboardButton(text="👑 𝐎ᴡɴᴇʀ", url=OWNER_URL), InlineKeyboardButton(text="📢 𝐂ʜᴀɴɴᴇʟ", url=CHANNEL_URL)]
])


async def send_start_photo(message: Message, caption: str):
    if not START_PHOTO:
        return False
    try:
        if START_PHOTO.startswith(("http://", "https://")):
            timeout = aiohttp.ClientTimeout(total=25)
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(START_PHOTO, allow_redirects=True) as r:
                    if r.status != 200:
                        return False
                    content_type = (r.headers.get("Content-Type") or "").lower()
                    if content_type.startswith("image/"):
                        data = await r.read()
                        await message.answer_photo(BufferedInputFile(data, filename="start.jpg"), caption=caption, reply_markup=HELP_KB)
                        return True
                    # Support image-hosting pages by reading their og:image preview.
                    page = await r.text(errors="ignore")
                    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I)
                    if not m:
                        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page, re.I)
                    if not m:
                        return False
                    image_url = m.group(1)
                    if image_url.startswith('/'):
                        from urllib.parse import urljoin
                        image_url = urljoin(str(r.url), image_url)
                    async with session.get(image_url, allow_redirects=True) as ir:
                        if ir.status != 200 or not (ir.headers.get("Content-Type") or "").lower().startswith("image/"):
                            return False
                        data = await ir.read()
                        await message.answer_photo(BufferedInputFile(data, filename="start.jpg"), caption=caption, reply_markup=HELP_KB)
                        return True
        await message.answer_photo(START_PHOTO, caption=caption, reply_markup=HELP_KB)
        return True
    except Exception as e:
        print(f"START_PHOTO error: {e}")
        return False


@router.message(CommandStart())
async def start(message: Message):
    # Keep the /start message compact; the full command list is exposed through Telegram's command menu.
    text = ("🛡️ <b>𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ & 𝐒ᴀғᴇᴛʏ</b>\n"
            "⚡ 𝐒ᴍᴀʀᴛ 𝐆ʀᴏᴜᴘ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ\n"
            "🔒 𝐋ᴏᴄᴋs • 𝐅ɪʟᴛᴇʀs • 𝐖ᴇʟᴄᴏᴍᴇ\n"
            "🛡️ 𝐍sғᴡ 𝐑ᴇᴍᴏᴠᴇʀ • 𝐀ɴᴛɪ-𝐒ᴘᴀᴍ\n"
            "🤖 𝐏ᴏᴡᴇʀᴇᴅ ʙʏ - <a href='https://t.me/JP_NETWORK'>@JP_NETWORK</a>\n\n"
            "👉 𝐔sᴇ /help ᴛᴏ ᴏᴘᴇɴ ᴛʜᴇ ғᴜʟʟ 𝐌ᴇɴᴜ.")
    if not await send_start_photo(message, text):
        await message.answer(text, reply_markup=HELP_KB)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("<b>𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ 𝐌ᴇɴᴜ</b>\n\nChoose a category.", reply_markup=HELP_KB)


async def _show_help_message(call: CallbackQuery, text: str, markup):
    """Show help content safely for both photo/caption messages and normal messages."""
    try:
        if call.message and call.message.photo:
            await call.message.edit_caption(caption=text, reply_markup=markup)
        else:
            await call.message.edit_text(text, reply_markup=markup)
        return
    except Exception as e:
        # A fresh message is a reliable fallback when the original /start message is a photo.
        print("Help menu edit failed, sending fresh message:", e)
        try:
            await call.message.answer(text, reply_markup=markup)
        except Exception as e2:
            print("Help menu fallback failed:", e2)


@router.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await _show_help_message(call, "<b>𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ 𝐌ᴇɴᴜ</b>\n\nChoose a category.", HELP_KB)
    await call.answer()


@router.callback_query(F.data.startswith("h:"))
async def category(call: CallbackQuery):
    cat = call.data[2:]
    data = {
        "mod": "<b>🛡️ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ</b>\n/ban /unban /mute /unmute /kick /warn /warnings /dmute /dban /fedban",
        "lock": "<b>🔒 𝐋ᴏᴄᴋs & 𝐔ɴʟᴏᴄᴋs</b>\n/lock sticker|gif|emoji|photo|video|link\n/unlock sticker|gif|emoji|photo|video|link\n/approve <code>user_id</code>\n/free <code>user_id</code>",
        "nsfw": "<b>🔞 𝐍sғᴡ</b>\n/nsfw on|off\nAutomatic explicit-content moderation and admin alerts.",
        "filter": "<b>🏷️ 𝐅ɪʟᴛᴇʀs</b>\n/filter word → reply to media/text and save it in one command\n/filters /stopfilter word /clearfilters",
        "welcome": "<b>👋 𝐖ᴇʟᴄᴏᴍᴇ</b>\n/setwelcome text\nExample: /setwelcome Welcome [Join Channel](buttonurl:https://t.me/yourchannel)\n/welcome on|off\n{name} {username} {mention} {id} {group} {count} {first} {last}\nAdd unlimited URL buttons using [Button Name](buttonurl:https://link)",
        "purge": "<b>🧹 𝐏ᴜʀɢᴇ</b>\nReply to the first message with /purge.",
        "rank": "<b>🏆 𝐑ᴀɴᴋɪɴɢ</b>\n/rank today\n/rank week\n/rank overall\n5 messages in 1 second = 10-minute ranking block.",
        "config": "<b>⚙️ 𝐂ᴏɴғɪɢ</b>\n/config",
        "requests": "<b>📥 𝐉ᴏɪɴ 𝐑ᴇǫᴜᴇsᴛs</b>\nNew join requests are posted in the group with inline <b>Accept</b> and <b>Decline</b> buttons.\nOnly group admins/owner can use them.",
        "broadcast": "<b>📢 𝐁ʀᴏᴀᴅᴄᴀsᴛ</b>\nOnly the bot owner can use /broadcast.\nUse /broadcast text or reply to a message with /broadcast."
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ 𝐁ᴀᴄᴋ", callback_data="back")]])
    await _show_help_message(call, data.get(cat, "Unknown"), kb)
    await call.answer()


@router.message(Command("config"))
async def config(message: Message):
    if not await is_admin(message): return
    g = await groups.find_one({"chat_id": message.chat.id}) or {}
    active_locks = await locks.find({"chat_id": message.chat.id, "enabled": True}).to_list(20)
    text = ("<b>⚙️ 𝐆ʀᴏᴜᴘ 𝐂ᴏɴғɪɢ</b>\n\n"
            f"👋 𝐖ᴇʟᴄᴏᴍᴇ: {bool(g.get('welcome_on', True))}\n"
            f"✏️ 𝐄ᴅɪᴛ 𝐃ᴇʟᴇᴛᴇ: {bool(g.get('editdelete', False))}\n"
            f"🔞 𝐍sғᴡ: {bool(g.get('nsfw', False))}\n"
            f"🔒 𝐋ᴏᴄᴋs: {', '.join(x['kind'] for x in active_locks) or 'None'}\n"
            f"🧠 𝐍sғᴡ 𝐀ᴘɪ: {'Configured' if SIGHTENGINE_USER and SIGHTENGINE_SECRET else 'Not configured'}")
    await message.answer(text)


@router.message(Command("setwelcome"))
async def setwelcome(message: Message):
    if not await is_admin(message):
        return

    text = args(message).strip()
    reply = message.reply_to_message
    photo_id = None

    # Keep the old behavior: an admin can reply to a photo and use /setwelcome text.
    if reply and reply.photo:
        photo_id = reply.photo[-1].file_id

    # Or send a photo with /setwelcome in its caption.
    if message.photo and message.caption:
        parts = message.caption.split(maxsplit=1)
        text = parts[1].strip() if len(parts) > 1 else ""
        photo_id = message.photo[-1].file_id

    if not text:
        text = DEFAULT_WELCOME

    await groups.update_one(
        {"chat_id": message.chat.id},
        {"$set": {
            "welcome": text,
            "welcome_on": True,
            "welcome_photo": photo_id
        }},
        upsert=True
    )
    await delete_safe(message)
    await message.answer(
        "✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐒ᴀᴠᴇᴅ</b>\n"
        "🖼️ 𝐖ᴇʟᴄᴏᴍᴇ 𝐂ᴀʀᴅ: 𝐔sᴇʀ 𝐃ᴘ 𝐂ɪʀᴄʟᴇ\n"
        "🔘 𝐁ᴜᴛᴛᴏɴs: <code>[Name](buttonurl:https://link)</code>\n"
        "💡 𝐘ᴏᴜ ᴄᴀɴ ᴀᴅᴅ ᴀs ᴍᴀɴʏ ᴡᴇʟᴄᴏᴍᴇ ʙᴜᴛᴛᴏɴs ᴀs ᴛʜᴇ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇssᴀɢᴇ ʟɪᴍɪᴛ ᴀʟʟᴏᴡs."
    )


@router.message(F.photo, F.caption)
async def setwelcome_photo_caption(message: Message):
    caption = message.caption or ""
    if not caption.lower().startswith("/setwelcome"):
        return
    # This handler is kept for clients that deliver the command as a photo caption.
    # The main /setwelcome handler above also handles the same case.
    return


@router.message(Command("delwelcome"))
async def delwelcome(message: Message):
    if not await is_admin(message): return
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"welcome": ""}}, upsert=True)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐃ᴇʟᴇᴛᴇᴅ</b>")


@router.message(Command("welcome"))
async def welcome_toggle(message: Message):
    if not await is_admin(message): return
    v = args(message).lower()
    if v not in ("on", "off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /welcome on|off")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"welcome_on": v == "on"}}, upsert=True)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐔ᴘᴅᴀᴛᴇᴅ</b>")




async def save_filter_response(message: Message, word: str):
    data = {"chat_id": message.chat.id, "word": word, "source_chat_id": message.chat.id, "source_message_id": message.message_id}
    if message.text:
        data.update({"kind": "text", "text": message.text})
    elif message.photo:
        data.update({"kind": "photo", "file_id": message.photo[-1].file_id, "caption": message.caption or ""})
    elif message.video:
        data.update({"kind": "video", "file_id": message.video.file_id, "caption": message.caption or ""})
    elif message.animation:
        data.update({"kind": "animation", "file_id": message.animation.file_id, "caption": message.caption or ""})
    elif message.sticker:
        data.update({"kind": "sticker", "file_id": message.sticker.file_id})
    elif message.document:
        data.update({"kind": "document", "file_id": message.document.file_id, "caption": message.caption or ""})
    elif message.audio:
        data.update({"kind": "audio", "file_id": message.audio.file_id, "caption": message.caption or ""})
    elif message.voice:
        data.update({"kind": "voice", "file_id": message.voice.file_id, "caption": message.caption or ""})
    else:
        return False
    await filters.update_one({"chat_id": message.chat.id, "word": word}, {"$set": data}, upsert=True)
    return True


async def send_filter_response(message: Message, row):
    # Always send the saved filter as a reply to the trigger message.
    rp = message.as_reply_parameters()
    kind = row.get("kind")
    if kind == "text":
        await message.answer(row.get("text", ""), reply_parameters=rp)
    elif kind == "photo":
        await message.answer_photo(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    elif kind == "video":
        await message.answer_video(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    elif kind == "animation":
        await message.answer_animation(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    elif kind == "sticker":
        await message.answer_sticker(row["file_id"], reply_parameters=rp)
    elif kind == "document":
        await message.answer_document(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    elif kind == "audio":
        await message.answer_audio(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    elif kind == "voice":
        await message.answer_voice(row["file_id"], caption=row.get("caption") or None, reply_parameters=rp)
    else:
        raise ValueError("Unknown filter kind")


@router.message(Command("stopfilter"))
async def stopfilter(message: Message):
    if not await is_admin(message): return
    await filters.delete_one({"chat_id": message.chat.id, "word": args(message).lower().strip()})
    await message.answer("✅ <b>𝐅ɪʟᴛᴇʀ 𝐑ᴇᴍᴏᴠᴇᴅ</b>")


@router.message(Command("filters"))
async def listfilters(message: Message):
    rows = await filters.find({"chat_id": message.chat.id}).sort("word", 1).to_list(200)
    await message.answer("<b>🏷️ 𝐅ɪʟᴛᴇʀs</b>\n" + ("\n".join("• " + html.escape(x["word"]) for x in rows) or "None"))


@router.message(Command("clearfilters"))
async def clearfilters(message: Message):
    if not await is_admin(message): return
    await filters.delete_many({"chat_id": message.chat.id})
    await message.answer("✅ <b>𝐅ɪʟᴛᴇʀs 𝐂ʟᴇᴀʀᴇᴅ</b>")


@router.message(Command("banword"))
async def banword(message: Message):
    if not await is_admin(message): return
    word = args(message).lower().strip()
    if not word: return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /banword word")
    await banwords.update_one({"chat_id": message.chat.id, "word": word}, {"$set": {"word": word}}, upsert=True)
    await message.answer("✅ <b>𝐁ᴀɴ 𝐖ᴏʀᴅ 𝐀ᴅᴅᴇᴅ</b>")


@router.message(Command("freeword"))
async def freeword(message: Message):
    if not await is_admin(message): return
    await banwords.delete_one({"chat_id": message.chat.id, "word": args(message).lower().strip()})
    await message.answer("✅ <b>𝐖ᴏʀᴅ 𝐑ᴇʟᴇᴀsᴇᴅ</b>")


LOCK_TYPES = {"sticker", "gif", "emoji", "photo", "video", "link"}


@router.message(Command("lock"))
async def lock(message: Message):
    if not await is_admin(message): return
    kind = args(message).lower().strip()
    if kind not in LOCK_TYPES:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /lock sticker|gif|emoji|photo|video|link")
    await locks.update_one({"chat_id": message.chat.id, "kind": kind}, {"$set": {"enabled": True}}, upsert=True)
    await message.answer(f"🔒 <b>𝐋ᴏᴄᴋᴇᴅ</b>: {html.escape(kind)}")


@router.message(Command("id"))
async def show_id(message: Message):
    t = await target(message)
    if t:
        return await message.answer(
            f"🆔 <b>User ID</b>\n👤 {mention(t)}\n<code>{t.id}</code>"
        )
    # In a private chat /id with no target returns the sender's own ID.
    if message.chat.type == ChatType.PRIVATE and message.from_user:
        return await message.answer(f"🆔 <b>Your User ID:</b> <code>{message.from_user.id}</code>")
    await message.answer("❌ <b>Reply to a user's message or use /id @username /id user_id</b>")


@router.message(Command("free"))
async def free(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
        if await target_is_admin(message, t.id):
            return await message.answer("ℹ️ <b>𝐀ᴅᴍɪɴs ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ 𝐄xᴇᴍᴘᴛ</b>")
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"free": True}}, upsert=True)
        return await message.answer(f"🆓 <b>𝐅ʀᴇᴇ 𝐔sᴇʀ</b>: {mention(t)}")
    kind = args(message).lower().strip()
    if kind in LOCK_TYPES:
        await locks.update_one({"chat_id": message.chat.id, "kind": kind}, {"$set": {"enabled": False}}, upsert=True)
        await message.answer(f"✅ <b>𝐔ɴʟᴏᴄᴋᴇᴅ</b>: {html.escape(kind)}")
    else:
        await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴀ ʟᴏᴄᴋ ᴛʏᴘᴇ</b>")


@router.message(Command("unfree"))
async def unfree(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
        if await target_is_admin(message, t.id):
            return await message.answer("ℹ️ <b>𝐀ᴅᴍɪɴs ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ 𝐄xᴇᴍᴘᴛ</b>")
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"free": False}}, upsert=True)
        await message.answer("✅ <b>𝐅ʀᴇᴇ 𝐒ᴛᴀᴛᴜs 𝐑ᴇᴍᴏᴠᴇᴅ</b>")


async def restrict(message, action):
    if not await is_admin(message): return
    if action in {"ban", "unban", "mute", "unmute", "kick"} and not await can_restrict_members(message):
        return await message.answer("❌ <b>𝐘ᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ 𝐁ᴀɴ/𝐌ᴜᴛᴇ 𝐏ᴇʀᴍɪssɪᴏɴ</b>")
    t = await target(message)
    if not t: return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴀ ᴜsᴇʀ_ɪᴅ/username</b>")
    if await target_is_admin(message, t.id):
        return await message.answer("❌ <b>𝐀ᴅᴍɪɴs ᴄᴀɴɴᴏᴛ ʙᴇ 𝐌ᴜᴛᴇᴅ/𝐁ᴀɴɴᴇᴅ ʙʏ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ</b>")
    try:
        if action == "ban": await bot.ban_chat_member(message.chat.id, t.id)
        elif action == "unban": await bot.unban_chat_member(message.chat.id, t.id, only_if_banned=True)
        elif action == "kick":
            await bot.ban_chat_member(message.chat.id, t.id)
            await bot.unban_chat_member(message.chat.id, t.id, only_if_banned=True)
        elif action == "mute":
            from aiogram.types import ChatPermissions
            await bot.restrict_chat_member(message.chat.id, t.id, ChatPermissions(can_send_messages=False))
        elif action == "unmute":
            from aiogram.types import ChatPermissions
            await bot.restrict_chat_member(message.chat.id, t.id, ChatPermissions(can_send_messages=True))
        await message.answer(f"✅ <b>𝐔sᴇʀ {action.upper()}</b>\n👤 {mention(t)}")
    except Exception as e:
        print(f"restrict error: {e}")
        await message.answer("❌ <b>𝐏ᴇʀᴍɪssɪᴏɴ 𝐄ʀʀᴏʀ</b>")


def make_restrict_handler(action):
    async def handler(message: Message): await restrict(message, action)
    return handler


for _cmd, _action in [("ban", "ban"), ("unban", "unban"), ("mute", "mute"), ("unmute", "unmute"), ("kick", "kick")]:
    router.message.register(make_restrict_handler(_action), Command(_cmd))


@router.message(Command("dmute"))
async def dmute(message: Message):
    if not await is_admin(message): return
    if message.reply_to_message: await delete_safe(message.reply_to_message)
    await restrict(message, "mute")


@router.message(Command("dban"))
async def dban(message: Message):
    if not await is_admin(message): return
    if message.reply_to_message: await delete_safe(message.reply_to_message)
    await restrict(message, "ban")


@router.message(Command("approve"))
async def approve(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
        if await target_is_admin(message, t.id):
            return await message.answer("ℹ️ <b>𝐀ᴅᴍɪɴs ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ 𝐀ᴘᴘʀᴏᴠᴇᴅ</b>")
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"approved": True}}, upsert=True)
        await message.answer("✅ <b>𝐔sᴇʀ 𝐀ᴘᴘʀᴏᴠᴇᴅ</b>")


@router.message(Command("unapprove"))
async def unapprove(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
        if await target_is_admin(message, t.id):
            return await message.answer("ℹ️ <b>𝐀ᴅᴍɪɴs ᴀʀᴇ ᴀʟʀᴇᴀᴅʏ 𝐄xᴇᴍᴘᴛ</b>")
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"approved": False}}, upsert=True)
    await message.answer("✅ <b>𝐀ᴘᴘʀᴏᴠᴀʟ 𝐑ᴇᴍᴏᴠᴇᴅ</b>")


@router.message(Command("purge"))
async def purge(message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message:
        return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ 𝐓ʜᴇ 𝐅ɪʀsᴛ 𝐌ᴇssᴀɢᴇ</b>")
    start = message.reply_to_message.message_id
    await delete_safe(message)
    for mid in range(start, message.message_id):
        try: await bot.delete_message(message.chat.id, mid)
        except Exception: pass
        if mid % 50 == 0: await asyncio.sleep(.2)


@router.message(Command("warn"))
async def warn(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if not t: return
    await warnings.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$inc": {"count": 1}}, upsert=True)
    x = await warnings.find_one({"chat_id": message.chat.id, "user_id": t.id})
    await message.answer(f"⚠️ <b>𝐖ᴀʀɴɪɴɢ</b>\n👤 {mention(t)}\n⚠️ 𝐖ᴀʀɴs: {x.get('count', 0)}")


@router.message(Command("warnings"))
async def warnings_cmd(message: Message):
    t = await target(message)
    if not t: return
    x = await warnings.find_one({"chat_id": message.chat.id, "user_id": t.id})
    await message.answer(f"⚠️ <b>𝐖ᴀʀɴs</b>: {(x or {}).get('count', 0)}")


# FedBan is owner-only. /unfedban is also owner-only; group admins cannot remove it.
@router.message(Command("fedban"))
async def fedban(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    t = await target(message)
    if not t: return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ</b>")
    await fedbans.update_one({"user_id": t.id}, {"$set": {"reason": args(message), "by": OWNER_ID}}, upsert=True)
    await message.answer(f"👑 <b>𝐅ᴇᴅ𝐁ᴀɴ 𝐀ᴅᴅᴇᴅ</b>\n👤 {mention(t)}")


@router.message(Command("unfedban"))
async def unfedban(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    t = await target(message)
    if not t: return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ</b>")
    await fedbans.delete_one({"user_id": t.id})
    await message.answer(f"👑 <b>𝐅ᴇᴅ𝐁ᴀɴ 𝐑ᴇᴍᴏᴠᴇᴅ</b>\n👤 {mention(t)}")


@router.message(Command("editdelete"))
async def editdelete(message: Message):
    if not await is_admin(message): return
    v = args(message).lower()
    if v not in ("on", "off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /editdelete on|off")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"editdelete": v == "on"}}, upsert=True)
    await message.answer("✅ <b>𝐄ᴅɪᴛ 𝐃ᴇʟᴇᴛᴇ 𝐔ᴘᴅᴀᴛᴇᴅ</b>")


@router.message(Command("nsfw"))
async def nsfw(message: Message):
    if not await is_admin(message): return
    v = args(message).lower()
    if v not in ("on", "off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /nsfw on|off")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"nsfw": v == "on"}}, upsert=True)
    if v == "on" and not (SIGHTENGINE_USER and SIGHTENGINE_SECRET):
        return await message.answer("⚠️ <b>𝐍sғᴡ 𝐆ᴜᴀʀᴅ 𝐎ɴ</b> — API keys are missing, so media cannot be classified yet.")
    await message.answer(f"🔞 <b>𝐍sғᴡ 𝐆ᴜᴀʀᴅ {'𝐎ɴ' if v == 'on' else '𝐎ғғ'}</b>")


async def download_file_bytes(file_id: str):
    f = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download(f, destination=buf)
    return buf.getvalue()


async def nsfw_detect(file_id: str, filename: str, content_type: str):
    if not (SIGHTENGINE_USER and SIGHTENGINE_SECRET):
        return False
    try:
        data = await download_file_bytes(file_id)
        form = aiohttp.FormData()
        form.add_field("media", data, filename=filename, content_type=content_type)
        form.add_field("models", "nudity-2.1")
        form.add_field("api_user", SIGHTENGINE_USER)
        form.add_field("api_secret", SIGHTENGINE_SECRET)
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post("https://api.sightengine.com/1.0/check.json", data=form) as r:
                if r.status != 200:
                    print("Sightengine HTTP", r.status, await r.text())
                    return False
                result = await r.json(content_type=None)
        nudity = result.get("nudity", {}) if isinstance(result, dict) else {}
        raw = float(nudity.get("raw", 0) or 0)
        partial = float(nudity.get("partial", 0) or 0)
        return raw >= NSFW_THRESHOLD or (NSFW_REMOVE_PARTIAL and partial >= NSFW_THRESHOLD)
    except Exception as e:
        print("NSFW detection error:", e)
        return False


async def check_nsfw(message: Message, enabled: bool):
    if not enabled or not SIGHTENGINE_USER or not SIGHTENGINE_SECRET:
        return False
    if message.photo:
        return await nsfw_detect(message.photo[-1].file_id, "photo.jpg", "image/jpeg")
    if message.video:
        return await nsfw_detect(message.video.file_id, "video.mp4", "video/mp4")
    if message.animation:
        return await nsfw_detect(message.animation.file_id, "animation.mp4", "video/mp4")
    if message.sticker and not message.sticker.is_animated and not message.sticker.is_video:
        return await nsfw_detect(message.sticker.file_id, "sticker.webp", "image/webp")
    return False


async def record_chat(message):
    if not message.from_user or message.from_user.is_bot: return
    if message.text and message.text.startswith("/"): return
    uid = message.from_user.id
    now = time.monotonic()
    q = flood[(message.chat.id, uid)]
    q.append(now)
    while q and now - q[0] > 1: q.popleft()
    if len(q) >= 5:
        ranking_blocked_until[(message.chat.id, uid)] = now + 600
        return
    if now < ranking_blocked_until.get((message.chat.id, uid), 0): return
    dt = datetime.now(timezone.utc)
    today = dt.strftime("%Y-%m-%d")
    iso = dt.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    base = {"chat_id": message.chat.id, "user_id": uid}
    for period, key in (("today", today), ("week", week), ("overall", "all")):
        doc = {**base, "period": period, "period_key": key}
        await chat_counts.update_one(doc, {
            "$set": {"username": message.from_user.username or "", "full_name": message.from_user.full_name or "User"},
            "$inc": {"count": 1}
        }, upsert=True)


async def get_user_dp(user_id: int):
    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
        if not photos or not photos.photos:
            return None
        return photos.photos[0][-1].file_id
    except Exception as e:
        print("User DP error:", e)
        return None


def parse_welcome_template(template, user, group_title, count):
    username = f"@{user.username}" if user.username else "—"
    values = {
        "{mention}": mention(user),
        "{id}": str(user.id),
        "{group}": html.escape(group_title or "Group"),
        "{count}": str(count),
        "{username}": html.escape(username),
        "{first}": html.escape(user.first_name or ""),
        "{last}": html.escape(user.last_name or ""),
        "{name}": html.escape(user.full_name or "User")
    }
    for k, v in values.items():
        template = template.replace(k, v)
    return template


def extract_welcome_buttons(text):
    buttons = []
    def repl(match):
        label = html.unescape(match.group(1).strip())[:64]
        url = match.group(2).strip()
        if label and url:
            buttons.append((label, url))
        return ""
    clean = WELCOME_BUTTON_RE.sub(repl, text)
    clean = re.sub(r"[ \t]+\n", "\n", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, buttons


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


async def build_welcome_card(user, status="MEMBER", style=3):
    """Create the neon welcome card shown in the user's requested design."""
    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (10, 10, 18))
    draw = ImageDraw.Draw(img)

    # Dark neon-style background.
    for y in range(H):
        t = y / H
        base = int(13 + 18 * t)
        draw.line((0, y, W, y), fill=(base, 8, int(24 + 15 * t)))
    for x in range(0, W, 34):
        draw.line((x, 0, x, H), fill=(26, 12, 36), width=1)
    for y in range(0, H, 34):
        draw.line((0, y, W, y), fill=(26, 12, 36), width=1)

    # Neon glow helper.
    def neon_line(points, color, width=6, glow=18):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        for w, a in [(width + glow, 45), (width + 8, 80), (width, 255)]:
            ld.line(points, fill=(*color, a), width=w, joint="curve")
        img.paste(layer, (0, 0), layer)

    if style == 1:
        pink, cyan = (255, 40, 40), (255, 80, 80)
    elif style == 2:
        pink, cyan = (40, 220, 90), (90, 255, 140)
    else:
        pink, cyan = (255, 40, 40), (50, 150, 255)
    white = (245, 245, 255)
    neon_line([(45, 55), (45, H-55), (680, H-55)], pink, 4)
    neon_line([(W-50, 55), (W-50, H-55), (705, H-55)], cyan, 3)
    neon_line([(700, 225), (W-55, 225)], pink, 5)
    neon_line([(700, 455), (W-55, 455)], pink, 5)
    neon_line([(700, 575), (W-120, 575)], cyan, 4)

    # Left circular DP frame.
    cx, cy, r = 335, 360, 220
    try:
        photos = await bot.get_user_profile_photos(user_id=user.id, limit=1)
        if photos and photos.photos:
            file_id = photos.photos[0][-1].file_id
            data = await download_file_bytes(file_id)
            avatar = Image.open(io.BytesIO(data)).convert("RGB")
            side = min(avatar.size)
            left = (avatar.width - side) // 2
            top = (avatar.height - side) // 2
            avatar = avatar.crop((left, top, left + side, top + side)).resize((r*2, r*2), Image.Resampling.LANCZOS)
            mask = Image.new("L", (r*2, r*2), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, r*2-1, r*2-1), fill=255)
            img.paste(avatar, (cx-r, cy-r), mask)
    except Exception as e:
        print("Welcome card avatar error:", e)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(35, 25, 45))

    # Bright circular rings around the DP.
    neon_line([(cx+r*0.82, cy-r*0.72), (cx+r, cy-r*0.35), (cx+r*0.98, cy+r*0.35), (cx+r*0.70, cy+r*0.82)], pink, 8, 16)
    neon_line([(cx-r*0.70, cy-r*0.82), (cx-r, cy-r*0.25), (cx-r*0.92, cy+r*0.50), (cx-r*0.55, cy+r*0.85)], cyan, 6, 14)
    draw.ellipse((cx-r-5, cy-r-5, cx+r+5, cy+r+5), outline=white, width=5)

    # Fonts.
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
        label_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 38)
        value_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        welcome_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 43)
    except Exception:
        title_font = label_font = value_font = welcome_font = ImageFont.load_default()

    # Header and fields.
    draw.text((760, 85), "WELCOME", font=title_font, fill=white, anchor="mm")
    draw.text((700, 255), f"STATUS: {status}", font=label_font, fill=white)
    name = user.full_name or "User"
    if len(name) > 25:
        name = name[:24] + "…"
    draw.text((700, 330), "NAME :", font=label_font, fill=white)
    draw.text((875, 333), name, font=value_font, fill=white)
    draw.text((700, 395), f"ID: {user.id}", font=label_font, fill=white)
    draw.text((760, 620), "Welcome", font=welcome_font, fill=white, anchor="mm")

    # Small decorative stars.
    for sx, sy, sc in [(100, 120, 28), (640, 125, 23), (620, 610, 32), (90, 620, 18)]:
        pts = []
        for i in range(10):
            a = -math.pi/2 + i*math.pi/5
            rr = sc if i % 2 == 0 else sc*0.42
            pts.append((sx + math.cos(a)*rr, sy + math.sin(a)*rr))
        draw.polygon(pts, outline=pink if sx < 500 else cyan, width=4)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=94, optimize=True)
    return out.getvalue()


async def render_welcome(g, user, group_title, count):
    template = g.get("welcome") or DEFAULT_WELCOME
    template = parse_welcome_template(template, user, group_title, count)
    clean_text, button_defs = extract_welcome_buttons(template)
    return clean_text, button_defs


async def send_welcome_for_user(chat_id: int, user, group_title: str, g=None, reply_to_message_id=None):
    if not user or user.is_bot:
        return
    key = (chat_id, user.id)
    now = time.monotonic()
    if now - welcome_recent.get(key, 0) < 8:
        return
    welcome_recent[key] = now
    if g is None:
        g = await groups.find_one_and_update(
            {"chat_id": chat_id},
            {"$setOnInsert": {"welcome": DEFAULT_WELCOME, "welcome_on": True, "welcome_photo": None, "welcome_style": 3, "created_at": time.time()}},
            upsert=True, return_document=True
        )
    if not g:
        g = {"welcome": DEFAULT_WELCOME, "welcome_on": True, "welcome_photo": None, "welcome_style": 3}
    if not g.get("welcome_on", True):
        return
    try:
        count = await bot.get_chat_member_count(chat_id)
    except Exception:
        count = "?"

    caption, button_defs = await render_welcome(g, user, group_title, count)
    style = int(g.get("welcome_style", 3) or 3)
    # Always render the welcome caption as Telegram quote text, like the requested design.
    if caption:
        caption = "<blockquote>" + caption + "</blockquote>"
    keyboard = None
    if button_defs:
        # Two buttons per row, preserving the order written in /setwelcome.
        rows = []
        for i in range(0, min(len(button_defs), 100), 2):
            row = [InlineKeyboardButton(text=label, url=url) for label, url in button_defs[i:i+2]]
            rows.append(row)
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)

    try:
        # Always generate the new welcome card from the joining user's own DP.
        card = await build_welcome_card(user, "MEMBER", style=style)
        await bot.send_photo(chat_id, BufferedInputFile(card, filename="welcome.jpg"), caption=caption or "👋 Welcome!", reply_markup=keyboard)
    except Exception as e:
        print("Welcome send error:", e)
        try:
            await bot.send_message(chat_id, caption or "👋 Welcome!", reply_markup=keyboard)
        except Exception:
            pass


@router.message(F.new_chat_members)
async def welcome_member(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    for user in message.new_chat_members:
        await send_welcome_for_user(message.chat.id, user, message.chat.title, reply_to_message_id=message.message_id)


@router.chat_member()
async def member_status_update(update: ChatMemberUpdated):
    """Welcome from Telegram's membership update, not the visible service message."""
    if update.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    old = update.old_chat_member.status
    new = update.new_chat_member.status
    inactive = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}
    active = {
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.RESTRICTED,
    }

    # A genuine join is a transition from a non-member state to a member state.
    # This also catches users accepted from a join request.
    if old in inactive and new in active:
        await send_welcome_for_user(
            update.chat.id,
            update.new_chat_member.user,
            update.chat.title
        )


@router.chat_join_request()
async def join_request(request: ChatJoinRequest):
    chat = request.chat
    user = request.from_user
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    text = (
        "📥 <b>𝐍ᴇᴡ 𝐉ᴏɪɴ 𝐑ᴇǫᴜᴇsᴛ</b>\n\n"
        f"👤 <b>𝐔sᴇʀ:</b> {mention(user)}\n"
        f"🆔 <b>𝐈ᴅ:</b> <code>{user.id}</code>\n"
        f"🔗 <b>𝐔sᴇʀɴᴀᴍᴇ:</b> {html.escape('@' + user.username if user.username else '—')}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ 𝐀ᴄᴄᴇᴘᴛ", callback_data=f"jr:accept:{chat.id}:{user.id}"),
        InlineKeyboardButton(text="❌ 𝐃ᴇᴄʟɪɴᴇ", callback_data=f"jr:decline:{chat.id}:{user.id}")
    ]])
    try:
        await bot.send_message(chat.id, text, reply_markup=kb)
    except Exception as e:
        print("Join request notification error:", e)


@router.callback_query(F.data.startswith("jr:"))
async def join_request_action(call: CallbackQuery):
    try:
        _, action, chat_id_s, user_id_s = call.data.split(":", 3)
        chat_id, user_id = int(chat_id_s), int(user_id_s)
    except Exception:
        return await call.answer("Invalid request.", show_alert=True)
    if not call.from_user or not await is_admin_for_chat(chat_id, call.from_user.id):
        return await call.answer("❌ Only group admins can manage join requests.", show_alert=True)
    try:
        if action == "accept":
            await bot.approve_chat_join_request(chat_id, user_id)
            result = "✅ <b>𝐀ᴄᴄᴇᴘᴛᴇᴅ</b>"
        elif action == "decline":
            await bot.decline_chat_join_request(chat_id, user_id)
            result = "❌ <b>𝐃ᴇᴄʟɪɴᴇᴅ</b>"
        else:
            return await call.answer("Unknown action.", show_alert=True)
        try:
            await call.message.edit_text((call.message.text or "") + "\n\n" + result, reply_markup=None)
        except Exception:
            pass
        await call.answer("Done")
    except Exception as e:
        print("Join request action error:", e)
        await call.answer("❌ Could not process this request. Check bot admin permissions.", show_alert=True)


@router.message(Command("broadcast"))
async def broadcast(message: Message):
    if not message.from_user or not is_owner(message.from_user.id):
        return
    source = message.reply_to_message
    text = args(message)
    if not source and not text:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /broadcast text OR reply to a message with /broadcast")
    sent = failed = 0
    cursor = groups.find({}, {"chat_id": 1})
    async for g in cursor:
        chat_id = g["chat_id"]
        try:
            if source:
                await source.copy_to(chat_id)
            else:
                await bot.send_message(chat_id, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"📢 <b>𝐁ʀᴏᴀᴅᴄᴀsᴛ 𝐂ᴏᴍᴘʟᴇᴛᴇ</b>\n✅ Sent: {sent}\n❌ Failed: {failed}")


VIDEO_LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?v=|shorts/|live/)|youtu\.be/|instagram\.com/(?:reel|p|tv)/)[^\s<>]+",
    re.IGNORECASE,
)


def extract_supported_video_url(text: str):
    m = VIDEO_LINK_RE.search(text or "")
    return m.group(0).rstrip(".,)>]}") if m else None


async def handle_video_link(message: Message) -> bool:
    """Delete supported YouTube/Instagram video links in groups. No downloading."""
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return False
    if not extract_supported_video_url(message.text or message.caption or ""):
        return False
    await delete_safe(message)
    return True



@router.message(Command("cleancommand"))
async def cleancommand_cmd(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return await message.answer("❌ This command works only in groups.")
    if not message.from_user or not await is_admin_for_chat(message.chat.id, message.from_user.id):
        return await message.answer("❌ <b>𝐀ᴅᴍɪɴ 𝐎ɴʟʏ</b>")
    arg = (message.text or "").split(maxsplit=1)[1].strip().lower() if len((message.text or "").split(maxsplit=1)) > 1 else ""
    if arg not in ("on", "off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ:</b> /cleancommand on ᴏʀ /cleancommand off")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"clean_commands": arg == "on"}}, upsert=True)
    await message.answer("✅ <b>𝐂ʟᴇᴀɴ 𝐂ᴏᴍᴍᴀɴᴅ</b> 𝐎ɴ" if arg == "on" else "🔴 <b>𝐂ʟᴇᴀɴ 𝐂ᴏᴍᴍᴀɴᴅ</b> 𝐎ғғ")


@router.message(Command("newfeaters"))
async def newfeaters_cmd(message: Message):
    text = (
        "🆕 <b>𝐍ᴇᴡ 𝐅ᴇᴀᴛᴜʀᴇs</b>\n\n"
        "start - 🛡️ 𝐒ᴛᴀʀᴛ 𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ\n"
        "help - ⚡ 𝐒ʜᴏᴡ 𝐀ʟʟ 𝐅ᴇᴀᴛᴜʀᴇs\n"
        "id - 🆔 𝐆ᴇᴛ 𝐔sᴇʀ 𝐈ᴅ\n"
        "filter - 🔒 𝐀ᴅᴅ 𝐍ᴇᴡ 𝐅ɪʟᴛᴇʀ\n"
        "filters - 📋 𝐋ɪsᴛ 𝐅ɪʟᴛᴇʀs\n"
        "stopfilter - 🗑️ 𝐑ᴇᴍᴏᴠᴇ 𝐅ɪʟᴛᴇʀ\n"
        "clearfilters - 🧹 𝐂ʟᴇᴀʀ 𝐀ʟʟ 𝐅ɪʟᴛᴇʀs\n"
        "banword - 🚫 𝐁ᴀɴ 𝐖ᴏʀᴅ\n"
        "freeword - ♻️ 𝐔ɴʙᴀɴ 𝐖ᴏʀᴅ\n"
        "approve - ✅ 𝐀ᴘᴘʀᴏᴠᴇ 𝐔sᴇʀ\n"
        "free - 🆓 𝐅ʀᴇᴇ 𝐔sᴇʀ\n"
        "ban - 🚫 𝐁ᴀɴ 𝐔sᴇʀ\n"
        "unban - ♻️ 𝐔ɴʙᴀɴ 𝐔sᴇʀ\n"
        "mute - 🔇 𝐌ᴜᴛᴇ 𝐔sᴇʀ\n"
        "unmute - 🔊 𝐔ɴᴍᴜᴛᴇ 𝐔sᴇʀ\n"
        "kick - 👢 𝐊ɪᴄᴋ 𝐔sᴇʀ\n"
        "editdelete - ✏️ 𝐄ᴅɪᴛᴇᴅ 𝐌ᴇssᴀɢᴇ 𝐏ʀᴏᴛᴇᴄᴛɪᴏɴ\n"
        "welcome - 👋 𝐖ᴇʟᴄᴏᴍᴇ 𝐒ᴇᴛᴛɪɴɢs\n"
        "setwelcome - 📝 𝐒ᴇᴛ 𝐖ᴇʟᴄᴏᴍᴇ\n"
        "rank - 🏆 𝐒ʜᴏᴡ 𝐑ᴀɴᴋ\n"
        "lock - 🔒 𝐋ᴏᴄᴋ 𝐌ᴇᴅɪᴀ\n"
        "unlock - 🔓 𝐔ɴʟᴏᴄᴋ 𝐌ᴇᴅɪᴀ\n"
        "config - ⚙️ 𝐆ʀᴏᴜᴘ 𝐂ᴏɴғɪɢ\n"
        "warn - ⚠️ 𝐖ᴀʀɴ 𝐔sᴇʀ\n"
        "warnings - 📊 𝐂ʜᴇᴄᴋ 𝐖ᴀʀɴɪɴɢs\n"
        "purge - 🧹 𝐏ᴜʀɢᴇ 𝐌ᴇssᴀɢᴇs\n"
        "nsfw - 🛡️ 𝐍sғᴡ 𝐅ɪʟᴛᴇʀ\n"
        "fedban - 🌐 𝐅ᴇᴅᴇʀᴀᴛᴇᴅ 𝐁ᴀɴ\n"
        "unfedban - ♻️ 𝐑ᴇᴍᴏᴠᴇ 𝐅ᴇᴅᴇʀᴀᴛᴇᴅ 𝐁ᴀɴ\n"
        "cleancommand - 🧹 𝐂ʟᴇᴀɴ 𝐂ᴏᴍᴍᴀɴᴅ"
    )
    await message.answer(text)


@router.message(F.video_chat_participants_invited)
async def video_chat_participants_invited(message: Message):
    """When Telegram tells the bot that users were invited to the group call, mention them."""
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    event = message.video_chat_participants_invited
    invited = getattr(event, "users", None) or []
    if not invited:
        return
    for user in invited:
        try:
            await message.answer(f"🐥 {mention(user)}, 𝐉ᴏɪɴ ᴛʜᴇ 𝐕ᴄ ғᴀsᴛ 😼")
        except Exception as e:
            print("Video chat invite reminder error:", e)


@router.edited_message()

@router.message()
async def moderation_and_count(message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    # YouTube/Instagram video links are removed in groups. Downloader is disabled.
    # This applies to normal users, admins, and the owner.
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        if await handle_video_link(message):
            return

    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    await users.update_one(
        {"chat_id": message.chat.id, "user_id": message.from_user.id},
        {"$set": {"username": message.from_user.username or "", "full_name": message.from_user.full_name or "User"}},
        upsert=True
    )

    # @admins is an explicit report request. It does not ban or delete the user's message.
    report_text = (message.text or message.caption or "")
    if re.search(r"(?<!\w)@admins\b", report_text, re.IGNORECASE):
        await report_admins(message)

    # Optional command cleaner: when enabled, every group command message is deleted.
    # /cleancommand itself is handled above, so it can still be used to turn the system off.
    if message.text and message.text.startswith("/"):
        try:
            gcfg = await groups.find_one({"chat_id": message.chat.id}) or {}
            if gcfg.get("clean_commands", False):
                await delete_safe(message)
        except Exception as e:
            print("Clean command error:", e)
        return

    # Automatically initialize every group in MongoDB.
    await groups.update_one(
        {"chat_id": message.chat.id},
        {"$setOnInsert": {
            "welcome": DEFAULT_WELCOME,
            "welcome_on": True,
            "welcome_photo": None,
            "created_at": time.time()
        }},
        upsert=True
    )

    # FedBan is enforced only on ordinary user messages, never on commands.
    if await fedbans.find_one({"user_id": message.from_user.id}):
        try:
            await bot.ban_chat_member(message.chat.id, message.from_user.id)
            await delete_safe(message)
        except Exception as e:
            print("FedBan enforcement error:", e)
        return

    g = await groups.find_one({"chat_id": message.chat.id}) or {}
    text = re.sub(r"\s+", " ", (message.text or message.caption or "").strip()).lower()

    # FILTERS MUST RUN BEFORE the admin/free exemption so an admin can also test a filter.
    # Only a plain text/caption trigger is matched; media messages themselves do not trigger filters.
    if text and len(text) <= 100 and not text.startswith("/"):
        row = await filters.find_one({"chat_id": message.chat.id, "word": text})
        if row:
            try:
                await send_filter_response(message, row)
            except Exception as e:
                print("Filter response error:", e)
            return

    if await exempt(message.chat.id, message.from_user.id):
        await record_chat(message)
        return

    # Ban words.
    brows = await banwords.find({"chat_id": message.chat.id}).to_list(200)
    if text and any(x.get("word", "") in text for x in brows):
        await delete_and_alert(message, "𝐁ᴀɴ 𝐖ᴏʀᴅ")
        return

    # NSFW moderation.
    if await check_nsfw(message, bool(g.get("nsfw", False))):
        # Stickers are silently removed; other NSFW content still gets an admin alert.
        if message.sticker:
            await delete_safe(message)
        else:
            await delete_and_alert(message, "🔞 𝐍sғᴡ 𝐂ᴏɴᴛᴇɴᴛ")
        return

    # Locks.
    lrows = await locks.find({"chat_id": message.chat.id, "enabled": True}).to_list(20)
    locked = {x["kind"] for x in lrows}
    if "sticker" in locked and message.sticker:
        # Sticker locks delete silently; do not tag all admins.
        await delete_safe(message); return
    if "gif" in locked and message.animation:
        await delete_and_alert(message, "🔒 𝐆ɪғ 𝐋ᴏᴄᴋ"); return
    if "photo" in locked and message.photo:
        await delete_and_alert(message, "🔒 𝐏ʜᴏᴛᴏ 𝐋ᴏᴄᴋ"); return
    if "video" in locked and message.video:
        await delete_and_alert(message, "🔒 𝐕ɪᴅᴇᴏ 𝐋ᴏᴄᴋ"); return
    if "link" in locked and re.search(r"(https?://|t\.me/|www\.)", text):
        await delete_and_alert(message, "🔒 𝐋ɪɴᴋ 𝐋ᴏᴄᴋ"); return
    if "emoji" in locked and text and not re.search(r"[A-Za-z0-9\u0980-\u09ff\u0900-\u097f]", text) and len(text) <= 50:
        await delete_and_alert(message, "🔒 𝐄ᴍᴏᴊɪ 𝐋ᴏᴄᴋ"); return

    await record_chat(message)


async def edited(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not message.from_user or message.from_user.is_bot:
        return
    g = await groups.find_one({"chat_id": message.chat.id}) or {}
    if not g.get("editdelete"):
        return

    # Warn immediately, then delete the edited message after 5 minutes.
    try:
        await message.answer(
            f"⚠️ {mention(message.from_user)} <b>Your message was edited.</b>\n"
            f"This edited message will be deleted after 5 minutes."
        )
    except Exception as e:
        print("Edit warning error:", e)

    await asyncio.sleep(300)
    try:
        await message.delete()
    except Exception as e:
        print("Edit-delete error:", e)


def ranking_image(title, rows):
    W, H = 1200, 780
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    try:
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 44)
        normal = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 27)
    except Exception:
        big = normal = ImageFont.load_default()
    d.text((45, 35), title, font=big, fill="black")
    maxv = max([x["count"] for x in rows], default=1)
    y = 115
    for i, x in enumerate(rows, 1):
        name = (x.get("full_name") or "User")[:26]
        count = x["count"]
        d.text((50, y), f"{i:>2}. {name}", font=normal, fill="black")
        bw = int(630 * count / maxv) if maxv else 0
        d.rectangle((400, y + 5, 400 + bw, y + 38), outline="black", width=2)
        d.text((850, y), str(count), font=normal, fill="black")
        y += 58
    b = io.BytesIO(); im.save(b, "PNG"); b.seek(0)
    return b


@router.message(Command("rank"))
async def rank(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return

    p = args(message).lower().strip() or "overall"
    if p not in ("today", "week", "overall"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /rank today|week|overall")

    dt = datetime.now(timezone.utc)
    if p == "today":
        key = dt.strftime("%Y-%m-%d")
    elif p == "week":
        iso = dt.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
    else:
        key = "all"

    try:
        cur = (
            chat_counts.find({
                "chat_id": message.chat.id,
                "period": p,
                "period_key": key
            })
            .sort("count", -1)
            .limit(10)
        )
        rows = []
        async for x in cur:
            rows.append({
                "full_name": x.get("full_name", "User"),
                "count": int(x.get("count", 0))
            })
    except Exception as e:
        print("Ranking DB error:", e)
        return await message.answer("❌ <b>𝐑ᴀɴᴋɪɴɢ 𝐃ʙ 𝐄ʀʀᴏʀ</b>")

    if not rows:
        return await message.answer(
            "📊 <b>𝐍ᴏ 𝐂ʜᴀᴛ 𝐃ᴀᴛᴀ 𝐘ᴇᴛ</b>\n"
            "Send some normal group messages first."
        )

    title = {
        "today": "𝐓ᴏᴅᴀʏ 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ",
        "week": "𝐖ᴇᴇᴋ 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ",
        "overall": "𝐎ᴠᴇʀᴀʟʟ 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ"
    }[p]

    chart = ranking_image(title, rows)
    await message.answer_photo(
        BufferedInputFile(chart.getvalue(), filename="ranking.png"),
        caption=f"<b>🏆 {title}</b>\n📊 𝐓ᴏᴘ 10 𝐂ʜᴀᴛᴛᴇʀs"
    )





async def main():
    # Explicitly request membership updates. This makes welcome independent of
    # Telegram's visible "joined via invite link" service message.
    await bot.delete_webhook(drop_pending_updates=False)
    await groups.create_index("chat_id", unique=True)
    await locks.create_index([("chat_id", 1), ("kind", 1)], unique=True)
    await filters.create_index([("chat_id", 1), ("word", 1)], unique=True)
    await filter_pending.create_index([("chat_id", 1), ("admin_id", 1)], unique=True)
    await banwords.create_index([("chat_id", 1), ("word", 1)], unique=True)
    await users.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
    await warnings.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
    await chat_counts.create_index([("chat_id", 1), ("user_id", 1), ("period", 1), ("period_key", 1)], unique=True)
    await fedbans.create_index("user_id", unique=True)
    # Force command menu for admins. Broadcast stays out of the direct menu.
    commands = [
        BotCommand(command="start", description="🛡️ 𝐒ᴛᴀʀᴛ 𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ"),
        BotCommand(command="help", description="⚡ 𝐒ʜᴏᴡ 𝐀ʟʟ 𝐅ᴇᴀᴛᴜʀᴇs"),
        BotCommand(command="newfeaters", description="🆕 𝐍ᴇᴡ 𝐅ᴇᴀᴛᴜʀᴇs"),
        BotCommand(command="id", description="🆔 𝐆ᴇᴛ 𝐔sᴇʀ 𝐈ᴅ"),
        BotCommand(command="filter", description="🔒 𝐀ᴅᴅ 𝐍ᴇᴡ 𝐅ɪʟᴛᴇʀ"),
        BotCommand(command="filters", description="📋 𝐋ɪsᴛ 𝐅ɪʟᴛᴇʀs"),
        BotCommand(command="stopfilter", description="🗑️ 𝐑ᴇᴍᴏᴠᴇ 𝐅ɪʟᴛᴇʀ"),
        BotCommand(command="clearfilters", description="🧹 𝐂ʟᴇᴀʀ 𝐀ʟʟ 𝐅ɪʟᴛᴇʀs"),
        BotCommand(command="banword", description="🚫 𝐁ᴀɴ 𝐖ᴏʀᴅ"),
        BotCommand(command="freeword", description="♻️ 𝐔ɴʙᴀɴ 𝐖ᴏʀᴅ"),
        BotCommand(command="approve", description="✅ 𝐀ᴘᴘʀᴏᴠᴇ 𝐔sᴇʀ"),
        BotCommand(command="free", description="🆓 𝐅ʀᴇᴇ 𝐔sᴇʀ"),
        BotCommand(command="ban", description="🚫 𝐁ᴀɴ 𝐔sᴇʀ"),
        BotCommand(command="unban", description="♻️ 𝐔ɴʙᴀɴ 𝐔sᴇʀ"),
        BotCommand(command="mute", description="🔇 𝐌ᴜᴛᴇ 𝐔sᴇʀ"),
        BotCommand(command="unmute", description="🔊 𝐔ɴᴍᴜᴛᴇ 𝐔sᴇʀ"),
        BotCommand(command="kick", description="👢 𝐊ɪᴄᴋ 𝐔sᴇʀ"),
        BotCommand(command="editdelete", description="✏️ 𝐄ᴅɪᴛᴇᴅ 𝐌ᴇssᴀɢᴇ 𝐏ʀᴏᴛᴇᴄᴛɪᴏɴ"),
        BotCommand(command="welcome", description="👋 𝐖ᴇʟᴄᴏᴍᴇ 𝐒ᴇᴛᴛɪɴɢs"),
        BotCommand(command="setwelcome", description="📝 𝐒ᴇᴛ 𝐖ᴇʟᴄᴏᴍᴇ"),
        BotCommand(command="rank", description="🏆 𝐒ʜᴏᴡ 𝐑ᴀɴᴋ"),
        BotCommand(command="lock", description="🔒 𝐋ᴏᴄᴋ 𝐌ᴇᴅɪᴀ"),
        BotCommand(command="unlock", description="🔓 𝐔ɴʟᴏᴄᴋ 𝐌ᴇᴅɪᴀ"),
        BotCommand(command="config", description="⚙️ 𝐆ʀᴏᴜᴘ 𝐂ᴏɴғɪɢ"),
        BotCommand(command="warn", description="⚠️ 𝐖ᴀʀɴ 𝐔sᴇʀ"),
        BotCommand(command="warnings", description="📊 𝐂ʜᴇᴄᴋ 𝐖ᴀʀɴɪɴɢs"),
        BotCommand(command="purge", description="🧹 𝐏ᴜʀɢᴇ 𝐌ᴇssᴀɢᴇs"),
        BotCommand(command="nsfw", description="🛡️ 𝐍sғᴡ 𝐅ɪʟᴛᴇʀ"),
        BotCommand(command="fedban", description="🌐 𝐅ᴇᴅᴇʀᴀᴛᴇᴅ 𝐁ᴀɴ"),
        BotCommand(command="unfedban", description="♻️ 𝐑ᴇᴍᴏᴠᴇ 𝐅ᴇᴅᴇʀᴀᴛᴇᴅ 𝐁ᴀɴ"),
        BotCommand(command="cleancommand", description="🧹 𝐂ʟᴇᴀɴ 𝐂ᴏᴍᴍᴀɴᴅ"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeAllChatAdministrators())
    await bot.set_my_commands(commands)
    print("MongoDB Group Help Bot started")
    # Do not rely on Telegram service messages. Receive chat_member updates
    # directly so welcome works in both small and large groups.
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "chat_member", "chat_join_request", "my_chat_member", "video_chat_participants_invited", "video_chat_ended"]
    )


if __name__ == "__main__":
    asyncio.run(main())


START_TEXT_FINAL = '🛡️ 𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ & 𝐒ᴀғᴇᴛʏ\n⚡ 𝐒ᴍᴀʀᴛ 𝐆ʀᴏᴜᴘ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ\n🔒 𝐋ᴏᴄᴋs • 𝐅ɪʟᴛᴇʀs • 𝐖ᴇʟᴄᴏᴍᴇ\n🛡️ 𝐍sғᴡ 𝐑ᴇᴍᴏᴠᴇʀ • 𝐀ɴᴛɪ-𝐒ᴘᴀᴍ\n🤖 𝐏ᴏᴡᴇʀᴇᴅ ʙʏ - @JP_NETWORK\n\n👉 𝐔sᴇ /help ᴛᴏ ᴏᴘᴇɴ ᴛʜᴇ ғᴜʟʟ 𝐌ᴇɴᴜ.'
