import asyncio
import html
import io
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
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
banwords = db.banwords
users = db.users
warnings = db.warnings
fedbans = db.fedbans
chat_counts = db.chat_counts

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

flood = defaultdict(deque)
ranking_blocked_until = {}

async def is_admin(message, uid=None):
    uid = uid or (message.from_user.id if message.from_user else 0)
    if uid == OWNER_ID:
        return True
    try:
        m = await bot.get_chat_member(message.chat.id, uid)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False

def is_owner(uid):
    return uid == OWNER_ID

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
    [InlineKeyboardButton(text="🛡️ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ", callback_data="h:mod"),
     InlineKeyboardButton(text="🔒 𝐋ᴏᴄᴋs", callback_data="h:lock")],
    [InlineKeyboardButton(text="🔞 𝐍sғᴡ", callback_data="h:nsfw"),
     InlineKeyboardButton(text="🏷️ 𝐅ɪʟᴛᴇʀs", callback_data="h:filter")],
    [InlineKeyboardButton(text="👋 𝐖ᴇʟᴄᴏᴍᴇ", callback_data="h:welcome"),
     InlineKeyboardButton(text="🧹 𝐏ᴜʀɢᴇ", callback_data="h:purge")],
    [InlineKeyboardButton(text="🏆 𝐑ᴀɴᴋɪɴɢ", callback_data="h:rank"),
     InlineKeyboardButton(text="⚙️ 𝐂ᴏɴғɪɢ", callback_data="h:config")],
    [InlineKeyboardButton(text="📖 𝐇ᴇʟᴘ", callback_data="back"),
     InlineKeyboardButton(text="💬 𝐒ᴜᴘᴘᴏʀᴛ", url=SUPPORT_URL)],
    [InlineKeyboardButton(text="👑 𝐎ᴡɴᴇʀ", url=OWNER_URL),
     InlineKeyboardButton(text="📢 𝐂ʜᴀɴɴᴇʟ", url=CHANNEL_URL)]
])

@router.message(CommandStart())
async def start(message: Message):
    text = ("<b>𝐖ᴇʟᴄᴏᴍᴇ 𝐓ᴏ 𝐆ʀᴏᴜᴘ 𝐇ᴇʟᴘ</b>\n\n"
            "🛡️ 𝐆ʀᴏᴜᴘ 𝐌ᴏᴅᴇʀᴀᴛɪᴏɴ\n🔒 𝐋ᴏᴄᴋs & 𝐅ɪʟᴛᴇʀs\n"
            "🏆 𝐂ʜᴀᴛ 𝐑ᴀɴᴋɪɴɢ\n\nUse /help to open the menu.")
    if START_PHOTO:
        try:
            await message.answer_photo(START_PHOTO, caption=text, reply_markup=HELP_KB)
            return
        except Exception:
            pass
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
        "nsfw": "<b>🔞 𝐍sғᴡ</b>\n/nsfw on|off",
        "filter": "<b>🏷️ 𝐅ɪʟᴛᴇʀs</b>\n/filter word\n/filters\n/stopfilter word\n/clearfilters\n/banword word\n/freeword word",
        "welcome": "<b>👋 𝐖ᴇʟᴄᴏᴍᴇ</b>\n/setwelcome text\n/delwelcome\n/welcome on|off\n{mention} {id} {group} {count}",
        "purge": "<b>🧹 𝐏ᴜʀɢᴇ</b>\nReply to a message with /purge.",
        "rank": "<b>🏆 𝐑ᴀɴᴋɪɴɢ</b>\n/rank today\n/rank week\n/rank overall",
        "config": "<b>⚙️ 𝐂ᴏɴғɪɢ</b>\n/config"
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ 𝐁ᴀᴄᴋ", callback_data="back")]])
    await call.message.edit_text(data.get(cat, "Unknown"), reply_markup=kb)
    await call.answer()

@router.message(Command("config"))
async def config(message: Message):
    if not await is_admin(message):
        return
    g = await groups.find_one({"chat_id": message.chat.id}) or {}
    active_locks = await locks.find({"chat_id": message.chat.id, "enabled": True}).to_list(20)
    text = (
        "<b>⚙️ 𝐆ʀᴏᴜᴘ 𝐂ᴏɴғɪɢ</b>\n\n"
        f"👋 𝐖ᴇʟᴄᴏᴍᴇ: {bool(g.get('welcome_on', True))}\n"
        f"✏️ 𝐄ᴅɪᴛ 𝐃ᴇʟᴇᴛᴇ: {bool(g.get('editdelete', False))}\n"
        f"🔞 𝐍sғᴡ: {bool(g.get('nsfw', False))}\n"
        f"🔒 𝐋ᴏᴄᴋs: {', '.join(x['kind'] for x in active_locks) or 'None'}"
    )
    await message.answer(text)

@router.message(Command("setwelcome"))
async def setwelcome(message: Message):
    if not await is_admin(message): return
    text = args(message)
    if not text:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /setwelcome text")
    await groups.update_one({"chat_id": message.chat.id},
        {"$set": {"welcome": text, "welcome_on": True}}, upsert=True)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐒ᴀᴠᴇᴅ</b>")

@router.message(Command("delwelcome"))
async def delwelcome(message: Message):
    if not await is_admin(message): return
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"welcome": ""}}, upsert=True)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐃ᴇʟᴇᴛᴇᴅ</b>")

@router.message(Command("welcome"))
async def welcome_toggle(message: Message):
    if not await is_admin(message): return
    v=args(message).lower()
    if v not in ("on","off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /welcome on|off")
    await groups.update_one({"chat_id": message.chat.id}, {"$set": {"welcome_on": v=="on"}}, upsert=True)
    await message.answer("✅ <b>𝐖ᴇʟᴄᴏᴍᴇ 𝐔ᴘᴅᴀᴛᴇᴅ</b>")

@router.message(Command("filter"))
async def add_filter(message: Message):
    if not await is_admin(message): return
    word=args(message).lower()
    if not word: return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /filter word")
    await filters.update_one({"chat_id":message.chat.id,"word":word},{"$set":{"word":word}},upsert=True)
    await message.answer("✅ <b>𝐅ɪʟᴛᴇʀ 𝐀ᴅᴅᴇᴅ</b>")

@router.message(Command("stopfilter"))
async def stopfilter(message: Message):
    if not await is_admin(message): return
    await filters.delete_one({"chat_id":message.chat.id,"word":args(message).lower()})
    await message.answer("✅ <b>𝐅ɪʟᴛᴇʀ 𝐑ᴇᴍᴏᴠᴇᴅ</b>")

@router.message(Command("filters"))
async def listfilters(message: Message):
    rows=await filters.find({"chat_id":message.chat.id}).sort("word",1).to_list(200)
    await message.answer("<b>🏷️ 𝐅ɪʟᴛᴇʀs</b>\n"+("\n".join("• "+html.escape(x["word"]) for x in rows) or "None"))

@router.message(Command("clearfilters"))
async def clearfilters(message: Message):
    if not await is_admin(message): return
    await filters.delete_many({"chat_id":message.chat.id})
    await message.answer("✅ <b>𝐅ɪʟᴛᴇʀs 𝐂ʟᴇᴀʀᴇᴅ</b>")

@router.message(Command("banword"))
async def banword(message: Message):
    if not await is_admin(message): return
    word=args(message).lower()
    if not word: return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /banword word")
    await banwords.update_one({"chat_id":message.chat.id,"word":word},{"$set":{"word":word}},upsert=True)
    await message.answer("✅ <b>𝐁ᴀɴ 𝐖ᴏʀᴅ 𝐀ᴅᴅᴇᴅ</b>")

@router.message(Command("freeword"))
async def freeword(message: Message):
    if not await is_admin(message): return
    await banwords.delete_one({"chat_id":message.chat.id,"word":args(message).lower()})
    await message.answer("✅ <b>𝐖ᴏʀᴅ 𝐑ᴇʟᴇᴀsᴇᴅ</b>")

LOCK_TYPES={"sticker","gif","emoji","photo","video","link"}

@router.message(Command("lock"))
async def lock(message: Message):
    if not await is_admin(message): return
    kind=args(message).lower()
    if kind not in LOCK_TYPES:
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /lock sticker|gif|emoji|photo|video|link")
    await locks.update_one({"chat_id":message.chat.id,"kind":kind},{"$set":{"enabled":True}},upsert=True)
    await message.answer(f"🔒 <b>𝐋ᴏᴄᴋᴇᴅ</b>: {html.escape(kind)}")

@router.message(Command("free"))
async def free(message: Message):
    if not await is_admin(message): return
    t=await target(message)
    if t:
        await users.update_one({"chat_id":message.chat.id,"user_id":t.id},
            {"$set":{"free":True}},upsert=True)
        return await message.answer(f"🆓 <b>𝐅ʀᴇᴇ 𝐔sᴇʀ</b>: {mention(t)}")
    kind=args(message).lower()
    if kind in LOCK_TYPES:
        await locks.update_one({"chat_id":message.chat.id,"kind":kind},{"$set":{"enabled":False}},upsert=True)
        await message.answer(f"✅ <b>𝐔ɴʟᴏᴄᴋᴇᴅ</b>: {html.escape(kind)}")
    else:
        await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴜsᴇ ᴀ ʟᴏᴄᴋ ᴛʏᴘᴇ</b>")

@router.message(Command("unfree"))
async def unfree(message: Message):
    if not await is_admin(message): return
    t=await target(message)
    if t:
        await users.update_one({"chat_id":message.chat.id,"user_id":t.id},{"$set":{"free":False}},upsert=True)
        await message.answer("✅ <b>𝐅ʀᴇᴇ 𝐒ᴛᴀᴛᴜs 𝐑ᴇᴍᴏᴠᴇᴅ</b>")

async def restrict(message, action):
    if not await is_admin(message): return
    t=await target(message)
    if not t: return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ</b>")
    try:
        if action=="ban": await bot.ban_chat_member(message.chat.id,t.id)
        elif action=="unban": await bot.unban_chat_member(message.chat.id,t.id,only_if_banned=True)
        elif action=="kick":
            await bot.ban_chat_member(message.chat.id,t.id)
            await bot.unban_chat_member(message.chat.id,t.id,only_if_banned=True)
        elif action=="mute":
            from aiogram.types import ChatPermissions
            await bot.restrict_chat_member(message.chat.id,t.id,ChatPermissions(can_send_messages=False))
        elif action=="unmute":
            from aiogram.types import ChatPermissions
            await bot.restrict_chat_member(message.chat.id,t.id,ChatPermissions(can_send_messages=True))
        await message.answer(f"✅ <b>𝐔sᴇʀ {action.upper()}</b>\n👤 {mention(t)}")
    except Exception:
        await message.answer("❌ <b>𝐏ᴇʀᴍɪssɪᴏɴ 𝐄ʀʀᴏʀ</b>")

def make_restrict_handler(action):
    async def handler(message: Message):
        await restrict(message, action)
    return handler

for _cmd,_action in [("ban","ban"),("unban","unban"),("mute","mute"),("unmute","unmute"),("kick","kick")]:
    router.message.register(make_restrict_handler(_action), Command(_cmd))

@router.message(Command("dmute"))
async def dmute(message: Message):
    if not await is_admin(message): return
    if message.reply_to_message: await delete_safe(message.reply_to_message)
    await restrict(message,"mute")

@router.message(Command("dban"))
async def dban(message: Message):
    if not await is_admin(message): return
    if message.reply_to_message: await delete_safe(message.reply_to_message)
    await restrict(message,"ban")

@router.message(Command("approve"))
async def approve(message: Message):
    if not await is_admin(message): return
    t=await target(message)
    if not t: return
    await users.update_one({"chat_id":message.chat.id,"user_id":t.id},{"$set":{"approved":True}},upsert=True)
    await message.answer("✅ <b>𝐔sᴇʀ 𝐀ᴘᴘʀᴏᴠᴇᴅ</b>")

@router.message(Command("unapprove"))
async def unapprove(message: Message):
    if not await is_admin(message): return
    t=await target(message)
    if t:
        await users.update_one({"chat_id":message.chat.id,"user_id":t.id},{"$set":{"approved":False}},upsert=True)
    await message.answer("✅ <b>𝐀ᴘᴘʀᴏᴠᴀʟ 𝐑ᴇᴍᴏᴠᴇᴅ</b>")

@router.message(Command("purge"))
async def purge(message: Message):
    if not await is_admin(message): return
    if not message.reply_to_message:
        return await message.answer("❌ <b>𝐑ᴇᴘʟʏ ᴛᴏ 𝐓ʜᴇ 𝐅ɪʀsᴛ 𝐌ᴇssᴀɢᴇ</b>")
    start=message.reply_to_message.message_id
    await delete_safe(message)
    for mid in range(start, message.message_id):
        try: await bot.delete_message(message.chat.id,mid)
        except Exception: pass
        if mid % 50 == 0: await asyncio.sleep(.2)

@router.message(Command("warn"))
async def warn(message: Message):
    if not await is_admin(message): return
    t=await target(message)
    if not t: return
    x=await warnings.find_one_and_update({"chat_id":message.chat.id,"user_id":t.id},
        {"$inc":{"count":1}},upsert=True,return_document=True)
    count=(x or {}).get("count",1)
    await message.answer(f"⚠️ <b>𝐖ᴀʀɴɪɴɢ</b>\n👤 {mention(t)}\n⚠️ 𝐖ᴀʀɴs: {count}")

@router.message(Command("warnings"))
async def warnings_cmd(message: Message):
    t=await target(message)
    if not t: return
    x=await warnings.find_one({"chat_id":message.chat.id,"user_id":t.id})
    await message.answer(f"⚠️ <b>𝐖ᴀʀɴs</b>: {(x or {}).get('count',0)}")

@router.message(Command("fedban"))
async def fedban(message: Message):
    if not is_owner(message.from_user.id): return
    t=await target(message)
    if t:
        await fedbans.update_one({"user_id":t.id},{"$set":{"reason":args(message)}},upsert=True)
        await message.answer("👑 <b>𝐅ᴇᴅ𝐁ᴀɴ 𝐀ᴅᴅᴇᴅ</b>")

@router.message(Command("unfedban"))
async def unfedban(message: Message):
    if not is_owner(message.from_user.id): return
    t=await target(message)
    if t:
        await fedbans.delete_one({"user_id":t.id})
        await message.answer("👑 <b>𝐅ᴇᴅ𝐁ᴀɴ 𝐑ᴇᴍᴏᴠᴇᴅ</b>")

@router.message(Command("editdelete"))
async def editdelete(message: Message):
    if not await is_admin(message): return
    v=args(message).lower()
    if v not in ("on","off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /editdelete on|off")
    await groups.update_one({"chat_id":message.chat.id},{"$set":{"editdelete":v=="on"}},upsert=True)
    await message.answer("✅ <b>𝐄ᴅɪᴛ 𝐃ᴇʟᴇᴛᴇ 𝐔ᴘᴅᴀᴛᴇᴅ</b>")

@router.message(Command("nsfw"))
async def nsfw(message: Message):
    if not await is_admin(message): return
    v=args(message).lower()
    if v not in ("on","off"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /nsfw on|off")
    await groups.update_one({"chat_id":message.chat.id},{"$set":{"nsfw":v=="on"}},upsert=True)
    await message.answer("🔞 <b>𝐍sғᴡ 𝐆ᴜᴀʀᴅ 𝐔ᴘᴅᴀᴛᴇᴅ</b>")

async def record_chat(message):
    if not message.from_user or message.from_user.is_bot: return
    uid=message.from_user.id
    now=time.monotonic()
    q=flood[(message.chat.id,uid)]
    q.append(now)
    while q and now-q[0]>1: q.popleft()
    if len(q)>=5:
        ranking_blocked_until[(message.chat.id,uid)] = now+600
        return
    if now < ranking_blocked_until.get((message.chat.id,uid),0): return
    dt=datetime.now(timezone.utc)
    today=dt.strftime("%Y-%m-%d")
    iso=dt.isocalendar()
    week=f"{iso.year}-W{iso.week:02d}"
    key={"chat_id":message.chat.id,"user_id":uid}
    await chat_counts.update_one(key, {
        "$set":{"username":message.from_user.username or "","full_name":message.from_user.full_name or "User",
                "today_key":today,"week_key":week},
        "$setOnInsert":{"overall_count":0,"today_count":0,"week_count":0},
        "$inc":{"overall_count":1,
                "today_count":1,
                "week_count":1}
    }, upsert=True)

@router.message()
async def moderation_and_count(message: Message):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP): return
    if not message.from_user or message.from_user.is_bot: return
    if await exempt(message.chat.id,message.from_user.id):
        await record_chat(message); return
    text=(message.text or message.caption or "").lower()

    frows=await filters.find({"chat_id":message.chat.id}).to_list(200)
    brows=await banwords.find({"chat_id":message.chat.id}).to_list(200)
    if text and any(x["word"] in text for x in frows+brows):
        await delete_safe(message); return

    lrows=await locks.find({"chat_id":message.chat.id,"enabled":True}).to_list(20)
    locked={x["kind"] for x in lrows}
    if "sticker" in locked and message.sticker: await delete_safe(message); return
    if "gif" in locked and message.animation: await delete_safe(message); return
    if "photo" in locked and message.photo: await delete_safe(message); return
    if "video" in locked and message.video: await delete_safe(message); return
    if "link" in locked and re.search(r"(https?://|t\.me/|www\.)",text): await delete_safe(message); return
    if "emoji" in locked and text and not re.search(r"[A-Za-z0-9\u0980-\u09ff\u0900-\u097f]",text) and len(text)<=50:
        await delete_safe(message); return
    await record_chat(message)

@router.edited_message()
async def edited(message: Message):
    if message.chat.type not in (ChatType.GROUP,ChatType.SUPERGROUP): return
    g=await groups.find_one({"chat_id":message.chat.id})
    if g and g.get("editdelete"):
        await asyncio.sleep(60)
        await delete_safe(message)

def ranking_image(title,rows):
    W,H=1200,780
    im=Image.new("RGB",(W,H),"white")
    d=ImageDraw.Draw(im)
    try:
        big=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",44)
        normal=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",27)
    except:
        big=normal=ImageFont.load_default()
    d.text((45,35),title,font=big,fill="black")
    maxv=max([x["count"] for x in rows],default=1)
    y=115
    for i,x in enumerate(rows,1):
        name=(x.get("full_name") or "User")[:26]
        count=x["count"]
        d.text((50,y),f"{i:>2}. {name}",font=normal,fill="black")
        bw=int(630*count/maxv) if maxv else 0
        d.rectangle((400,y+5,400+bw,y+38),outline="black",width=2)
        d.text((850,y),str(count),font=normal,fill="black")
        y+=58
    b=io.BytesIO(); im.save(b,"PNG"); b.seek(0); return b

@router.message(Command("rank"))
async def rank(message: Message):
    p=args(message).lower() or "overall"
    if p not in ("today","week","overall"):
        return await message.answer("❌ <b>𝐔sᴀɢᴇ</b>: /rank today|week|overall")
    dt=datetime.now(timezone.utc); today=dt.strftime("%Y-%m-%d")
    iso=dt.isocalendar(); week=f"{iso.year}-W{iso.week:02d}"
    rows=[]
    if p=="today":
        cur=chat_counts.find({"chat_id":message.chat.id,"today_key":today},{"full_name":1,"today_count":1}).sort("today_count",-1).limit(10)
        async for x in cur: rows.append({"full_name":x.get("full_name","User"),"count":x.get("today_count",0)})
        title="𝐓ᴏᴅᴀʏ 𝐑ᴀɴᴋɪɴɢ"
    elif p=="week":
        cur=chat_counts.find({"chat_id":message.chat.id,"week_key":week},{"full_name":1,"week_count":1}).sort("week_count",-1).limit(10)
        async for x in cur: rows.append({"full_name":x.get("full_name","User"),"count":x.get("week_count",0)})
        title="𝐖ᴇᴇᴋ 𝐑ᴀɴᴋɪɴɢ"
    else:
        cur=chat_counts.find({"chat_id":message.chat.id},{"full_name":1,"overall_count":1}).sort("overall_count",-1).limit(10)
        async for x in cur: rows.append({"full_name":x.get("full_name","User"),"count":x.get("overall_count",0)})
        title="𝐎ᴠᴇʀᴀʟʟ 𝐑ᴀɴᴋɪɴɢ"
    if not rows: return await message.answer("📊 <b>𝐍ᴏ 𝐑ᴀɴᴋɪɴɢ 𝐃ᴀᴛᴀ 𝐘ᴇᴛ</b>")
    await message.answer_photo(ranking_image(title,rows),caption=f"<b>🏆 {title}</b>\n📊 𝐓ᴏᴘ 10")

@router.message(F.new_chat_members)
async def welcome_member(message: Message):
    g=await groups.find_one({"chat_id":message.chat.id})
    if not g or not g.get("welcome_on",True) or not g.get("welcome"): return
    count=await bot.get_chat_member_count(message.chat.id)
    for u in message.new_chat_members:
        text=g["welcome"].replace("{mention}",mention(u)).replace("{id}",str(u.id)).replace("{group}",html.escape(message.chat.title or "Group")).replace("{count}",str(count))
        await message.answer(text)

async def main():
    await groups.create_index("chat_id",unique=True)
    await locks.create_index([("chat_id",1),("kind",1)],unique=True)
    await filters.create_index([("chat_id",1),("word",1)],unique=True)
    await banwords.create_index([("chat_id",1),("word",1)],unique=True)
    await users.create_index([("chat_id",1),("user_id",1)],unique=True)
    await warnings.create_index([("chat_id",1),("user_id",1)],unique=True)
    await chat_counts.create_index([("chat_id",1),("user_id",1)],unique=True)
    await fedbans.create_index("user_id",unique=True)
    print("MongoDB Group Help Bot started")
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
