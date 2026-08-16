import asyncio
import html
import io
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
MONGO_URI = os.getenv("MONGO_URI", "")
MONGO_DB = os.getenv("MONGO_DB", "group_help_bot")
START_PHOTO = os.getenv("START_PHOTO", "")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/")
OWNER_URL = os.getenv("OWNER_URL", "https://t.me/")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/")

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


def is_owner(uid: int) -> bool:
    return uid == OWNER_ID


async def is_admin(message: Message, uid=None):
    uid = uid or (message.from_user.id if message.from_user else 0)
    if uid == OWNER_ID:
        return True
    try:
        m = await bot.get_chat_member(message.chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False


async def exempt(chat_id, uid):
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
    return None


async def delete_safe(message):
    try:
        await message.delete()
    except Exception:
        pass


HELP_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🛡️ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ", callback_data="h:mod"), InlineKeyboardButton(text="🔒 𝐋ᴏᴄᴋs", callback_data="h:lock")],
    [InlineKeyboardButton(text="🔞 𝐍sғᴡ", callback_data="h:nsfw"), InlineKeyboardButton(text="🏷️ 𝐅ɪʟᴛᴇʀs", callback_data="h:filter")],
    [InlineKeyboardButton(text="👋 𝐖ᴇʟᴄᴏᴍᴇ", callback_data="h:welcome"), InlineKeyboardButton(text="🧹 𝐏ᴜʀɢᴇ", callback_data="h:purge")],
    [InlineKeyboardButton(text="🏆 𝐑ᴀɴᴋɪɴɢ", callback_data="h:rank"), InlineKeyboardButton(text="⚙️ 𝐂ᴏɴғɪɢ", callback_data="h:config")],
    [InlineKeyboardButton(text="📢 𝐁ʀᴏᴀᴅᴄᴀsᴛ", callback_data="h:broadcast")],
    [InlineKeyboardButton(text="📖 𝐇ᴇʟᴘ", callback_data="back"), InlineKeyboardButton(text="💬 𝐒ᴜᴘᴘᴏʀᴛ", url=SUPPORT_URL)],
    [InlineKeyboardButton(text="👑 𝐎ᴡɴᴇʀ", url=OWNER_URL), InlineKeyboardButton(text="📢 𝐂ʜᴀɴɴᴇʟ", url=CHANNEL_URL)]
])


async def send_start_photo(message: Message, caption: str):
    if not START_PHOTO:
        return False
    try:
        if START_PHOTO.startswith(("http://", "https://")):
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(START_PHOTO, allow_redirects=True) as r:
                    if r.status != 200:
                        return False
                    data = await r.read()
                    if not data or not (r.headers.get("Content-Type", "").lower().startswith("image/")):
                        return False
                    await message.answer_photo(BufferedInputFile(data, filename="start.jpg"), caption=caption, reply_markup=HELP_KB)
                    return True
        await message.answer_photo(START_PHOTO, caption=caption, reply_markup=HELP_KB)
        return True
    except Exception as e:
        print(f"START_PHOTO error: {e}")
        return False


@router.message(CommandStart())
async def start(message: Message):
    text = ("<b>𝐖ᴇʟᴄᴏᴍᴇ 𝐓ᴏ 𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ</b>\n\n"
            "🛡️ 𝐆ʀᴏᴜᴘ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ\n🔒 𝐋ᴏᴄᴋs & 𝐅ɪʟᴛᴇʀs\n🏆 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ\n\nUse /help to open the menu.")
    if not await send_start_photo(message, text):
        await message.answer(text, reply_markup=HELP_KB)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer("<b>𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ 𝐌ᴇɴᴜ</b>\n\nChoose a category.", reply_markup=HELP_KB)


@router.callback_query(F.data == "back")
async def back(call: CallbackQuery):
    await call.message.edit_text("<b>𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ 𝐌ᴇɴᴜ</b>\n\nChoose a category.", reply_markup=HELP_KB)
    await call.answer()


@router.callback_query(F.data.startswith("h:"))
async def category(call: CallbackQuery):
    cat = call.data[2:]
    data = {
        "mod": "<b>🛡️ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ</b>\n/ban /unban /mute /unmute /kick /warn /warnings /dmute /dban /fedban",
        "lock": "<b>🔒 𝐋ᴏᴄᴋs</b>\n/lock sticker|gif|emoji|photo|video|link\n/free sticker|gif|emoji|photo|video|link",
        "nsfw": "<b>🔞 𝐍sғᴡ</b>\n/nsfw on|off\nRequires SIGHTENGINE_API_USER + SIGHTENGINE_API_SECRET.",
        "filter": "<b>🏷️ 𝐅ɪʟᴛᴇʀs</b>\n/filter word → then send the reply media/text\n/filters /stopfilter word /clearfilters",
        "welcome": "<b>👋 𝐖ᴇʟᴄᴏᴍᴇ</b>\n/setwelcome any text/design\n/welcome on|off\n{mention} {id} {group} {count}",
        "purge": "<b>🧹 𝐏ᴜʀɢᴇ</b>\nReply to the first message with /purge.",
        "rank": "<b>🏆 𝐑ᴀɴᴋɪɴɢ</b>\n/rank today\n/rank week\n/rank overall\n5 messages in 1 second = 10-minute ranking block.",
        "config": "<b>⚙️ 𝐂ᴏɴғɪɢ</b>\n/config",
        "broadcast": "<b>📢 𝐁ʀᴏᴀᴅᴄᴀsᴛ</b>\nOnly the bot owner can use /broadcast.\nUse /broadcast text or reply to a message with /broadcast."
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ 𝐁ᴀᴄᴋ", callback_data="back")]])
    await call.message.edit_text(data.get(cat, "Unknown"), reply_markup=kb)
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
    if not await is_admin(message): return
    text = args(message)
    if not text:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /setwelcome your custom message")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"welcome": text, "welcome_on": True}}, upsert=True)
    await delete_safe(message)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐒ᴀᴠᴇᴅ</b>")


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


# FILTERS: /filter trigger, then send the response message. It supports text/photo/video/sticker/animation/document/audio/voice.
@router.message(Command("filter"))
async def add_filter(message: Message):
    if not await is_admin(message): return
    word = args(message).lower().strip()
    if not word:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /filter jpexo")
    await filter_pending.update_one({"chat_id": message.chat.id, "admin_id": message.from_user.id},
                                    {"$set": {"word": word, "created_at": time.time()}}, upsert=True)
    await message.answer(f"✅ <b>𝐅ɪʟᴛᴇʀ 𝐊ᴇʏ 𝐒ᴇᴛ</b>: <code>{html.escape(word)}</code>\nNow send the text/photo/video/sticker you want when users type it.")


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
    kind = row.get("kind")
    if kind == "text": await message.answer(row.get("text", "")); return
    if kind == "photo": await message.answer_photo(row["file_id"], caption=row.get("caption") or None); return
    if kind == "video": await message.answer_video(row["file_id"], caption=row.get("caption") or None); return
    if kind == "animation": await message.answer_animation(row["file_id"], caption=row.get("caption") or None); return
    if kind == "sticker": await message.answer_sticker(row["file_id"]); return
    if kind == "document": await message.answer_document(row["file_id"], caption=row.get("caption") or None); return
    if kind == "audio": await message.answer_audio(row["file_id"], caption=row.get("caption") or None); return
    if kind == "voice": await message.answer_voice(row["file_id"], caption=row.get("caption") or None); return


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


@router.message(Command("free"))
async def free(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
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
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"free": False}}, upsert=True)
        await message.answer("✅ <b>𝐅ʀᴇᴇ 𝐒ᴛᴀᴛᴜs 𝐑ᴇᴍᴏᴠᴇᴅ</b>")


async def restrict(message, action):
    if not await is_admin(message): return
    t = await target(message)
    if not t: return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ</b>")
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
        await users.update_one({"chat_id": message.chat.id, "user_id": t.id}, {"$set": {"approved": True}}, upsert=True)
        await message.answer("✅ <b>𝐔sᴇʀ 𝐀ᴘᴘʀᴏᴠᴇᴅ</b>")


@router.message(Command("unapprove"))
async def unapprove(message: Message):
    if not await is_admin(message): return
    t = await target(message)
    if t:
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


@router.message(F.new_chat_members)
async def welcome_member(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    g = await groups.find_one({"chat_id": message.chat.id})
    if not g or not g.get("welcome_on", True) or not g.get("welcome"):
        return
    try:
        count = await bot.get_chat_member_count(message.chat.id)
    except Exception:
        count = "?"
    for u in message.new_chat_members:
        text = g["welcome"]
        replacements = {
            "{mention}": mention(u),
            "{id}": str(u.id),
            "{group}": html.escape(message.chat.title or "Group"),
            "{count}": str(count),
            "{username}": f"@{u.username}" if u.username else "",
            "{first}": html.escape(u.first_name or ""),
            "{last}": html.escape(u.last_name or "")
        }
        for k, v in replacements.items(): text = text.replace(k, v)
        try:
            await message.answer(text)
        except Exception as e:
            print("Welcome send error:", e)


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


@router.message()
async def moderation_and_count(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    if not message.from_user or message.from_user.is_bot: return

    # A pending filter is consumed by the admin's next non-command message.
    if not (message.text or "").startswith("/"):
        pending = await filter_pending.find_one({"chat_id": message.chat.id, "admin_id": message.from_user.id})
        if pending:
            if await is_admin(message):
                if await save_filter_response(message, pending["word"]):
                    await filter_pending.delete_one({"_id": pending["_id"]})
                    await message.answer(f"✅ <b>𝐅ɪʟᴇʀ 𝐑ᴇsᴘᴏɴsᴇ 𝐒ᴀᴠᴇᴅ</b>\nTrigger: <code>{html.escape(pending['word'])}</code>")
                    return

    if await exempt(message.chat.id, message.from_user.id):
        await record_chat(message)
        return

    # FedBan is enforced automatically in every group where the bot is admin.
    if await fedbans.find_one({"user_id": message.from_user.id}):
        try:
            await bot.ban_chat_member(message.chat.id, message.from_user.id)
            await delete_safe(message)
        except Exception as e:
            print("FedBan enforcement error:", e)
        return

    g = await groups.find_one({"chat_id": message.chat.id}) or {}
    text = (message.text or message.caption or "").lower()

    # Filter trigger: send the saved response; do not delete the trigger message.
    if text and not text.startswith("/"):
        rows = await filters.find({"chat_id": message.chat.id}).to_list(200)
        for row in rows:
            word = (row.get("word") or "").lower()
            if word and word in text:
                try: await send_filter_response(message, row)
                except Exception as e: print("Filter response error:", e)
                break

    # Ban words.
    brows = await banwords.find({"chat_id": message.chat.id}).to_list(200)
    if text and any(x.get("word", "") in text for x in brows):
        await delete_safe(message)
        return

    # NSFW moderation before lock checks.
    if await check_nsfw(message, bool(g.get("nsfw", False))):
        await delete_safe(message)
        return

    # Locks.
    lrows = await locks.find({"chat_id": message.chat.id, "enabled": True}).to_list(20)
    locked = {x["kind"] for x in lrows}
    if "sticker" in locked and message.sticker: await delete_safe(message); return
    if "gif" in locked and message.animation: await delete_safe(message); return
    if "photo" in locked and message.photo: await delete_safe(message); return
    if "video" in locked and message.video: await delete_safe(message); return
    if "link" in locked and re.search(r"(https?://|t\.me/|www\.)", text): await delete_safe(message); return
    if "emoji" in locked and text and not re.search(r"[A-Za-z0-9\u0980-\u09ff\u0900-\u097f]", text) and len(text) <= 50:
        await delete_safe(message); return

    await record_chat(message)


@router.edited_message()
async def edited(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    g = await groups.find_one({"chat_id": message.chat.id})
    if g and g.get("editdelete"):
        await asyncio.sleep(60)
        await delete_safe(message)


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
    p = args(message).lower().strip() or "overall"
    if p not in ("today", "week", "overall"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /rank today|week|overall")
    dt = datetime.now(timezone.utc)
    if p == "today":
        key = dt.strftime("%Y-%m-%d")
    elif p == "week":
        iso = dt.isocalendar(); key = f"{iso.year}-W{iso.week:02d}"
    else:
        key = "all"
    cur = chat_counts.find({"chat_id": message.chat.id, "period": p, "period_key": key}).sort("count", -1).limit(10)
    rows = []
    async for x in cur:
        rows.append({"full_name": x.get("full_name", "User"), "count": x.get("count", 0)})
    if not rows:
        return await message.answer("📊 <b>𝐍ᴏ 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ 𝐃ᴀᴛᴀ 𝐘ᴇᴛ</b>")
    title = {"today": "𝐓ᴏᴅᴀʏ 𝐑ᴀɴᴋɪɴɢ", "week": "𝐖ᴇᴇᴋ 𝐑ᴀɴᴋɪɴɢ", "overall": "𝐎ᴠᴇʀᴀʟʟ 𝐑ᴀɴᴋɪɴɢ"}[p]
    chart = ranking_image(title, rows)
    await message.answer_photo(BufferedInputFile(chart.getvalue(), filename="ranking.png"), caption=f"<b>🏆 {title}</b>\n📊 𝐓ᴏᴘ 10")


async def main():
    await groups.create_index("chat_id", unique=True)
    await locks.create_index([("chat_id", 1), ("kind", 1)], unique=True)
    await filters.create_index([("chat_id", 1), ("word", 1)], unique=True)
    await filter_pending.create_index([("chat_id", 1), ("admin_id", 1)], unique=True)
    await banwords.create_index([("chat_id", 1), ("word", 1)], unique=True)
    await users.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
    await warnings.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
    await chat_counts.create_index([("chat_id", 1), ("user_id", 1), ("period", 1), ("period_key", 1)], unique=True)
    await fedbans.create_index("user_id", unique=True)
    print("MongoDB Group Help Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
