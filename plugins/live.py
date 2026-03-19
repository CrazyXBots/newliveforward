# ============================================================
#  📡 LIVE FORWARD  — Real-time channel-to-channel forwarding
#  UI matches screenshots provided by user
# ============================================================

import re
import asyncio
import logging
from database import db
from config import Config, temp
from pyrogram import Client, filters, enums
from pyrogram.handlers import MessageHandler
from pyrogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
)
from pyrogram.errors import FloodWait
from .test import get_client, CLIENT as ClientClass, parse_buttons
from .regix import custom_caption, media

logger = logging.getLogger(__name__)

DEFAULT_LIVE_FILTERS = {
    "text": True, "photo": True, "video": True, "document": True,
    "audio": True, "voice": True, "animation": True, "sticker": True,
    "poll": True, "keep_fwd_tag": False, "protect": False,
}

# ─────────────────────────────────────────────────────────────
#  /live command
# ─────────────────────────────────────────────────────────────

@Client.on_message(filters.private & filters.command("live"))
async def live_cmd(bot, message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    cfg = await db.get_live_config(user_id)
    await message.reply_text(
        _live_main_text(cfg, user_id),
        reply_markup=_live_main_markup(),
    )


# ─────────────────────────────────────────────────────────────
#  All live# callbacks in one handler
# ─────────────────────────────────────────────────────────────

@Client.on_callback_query(filters.regex(r'^live#'))
async def live_handler(bot, query):
    user_id = query.from_user.id
    action  = query.data.split('#', 1)[1]
    cfg     = await db.get_live_config(user_id)

    if action == 'main':
        await query.message.edit_text(
            _live_main_text(cfg, user_id), reply_markup=_live_main_markup())

    # ──────────────────────────────── DESTINATION
    elif action == 'destination':
        dest = cfg.get('destination')
        dest_title = cfg.get('destination_title', '')
        text = (
            f"<b>📥 DESTINATION CHANNELS</b>  ({'1' if dest else '0'})\n\n"
            "<i>SOURCE CHANNEL KE MESSAGE IN SAB CHANNELS MEIN BHEJE JAAYENGE.\n"
            "BOT/USERBOT KO HAR DESTINATION KA ADMIN HONA CHAHIYE.</i>"
        )
        btns = []
        if dest:
            btns.append([InlineKeyboardButton(f"🗑 REMOVE: {dest_title[:30]}", callback_data="live#clear_destination")])
        btns.append([InlineKeyboardButton("➕ ADD DESTINATION", callback_data="live#set_destination")])
        btns.append([InlineKeyboardButton("↩️ BACK", callback_data="live#main")])
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))

    elif action == 'set_destination':
        await query.message.delete()
        resp = await bot.ask(user_id,
            "<b>📥 SET DESTINATION CHANNEL</b>\n\nForward any message from your destination channel.\n\n/cancel — cancel")
        if resp.text and resp.text.startswith('/'):
            return await resp.reply("Cancelled.")
        if not resp.forward_from_chat:
            return await resp.reply("❌ Please forward a message from a channel.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#destination")]]))
        await db.update_live_config(user_id, {
            'destination': resp.forward_from_chat.id,
            'destination_title': resp.forward_from_chat.title,
        })
        await resp.reply(f"<b>✅ Destination set:</b> <code>{resp.forward_from_chat.title}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#destination")]]))

    elif action == 'clear_destination':
        await db.update_live_config(user_id, {'destination': None, 'destination_title': ''})
        await query.answer("Destination removed.", show_alert=False)
        btns = [
            [InlineKeyboardButton("➕ ADD DESTINATION", callback_data="live#set_destination")],
            [InlineKeyboardButton("↩️ BACK", callback_data="live#main")],
        ]
        await query.message.edit_text(
            "<b>📥 DESTINATION CHANNELS</b>  (0)\n\n"
            "<i>SOURCE CHANNEL KE MESSAGE IN SAB CHANNELS MEIN BHEJE JAAYENGE.\n"
            "BOT/USERBOT KO HAR DESTINATION KA ADMIN HONA CHAHIYE.</i>",
            reply_markup=InlineKeyboardMarkup(btns))

    # ──────────────────────────────── SOURCES
    elif action == 'sources':
        await _show_sources(query, cfg)

    elif action == 'add_source':
        await query.message.delete()
        resp = await bot.ask(user_id,
            "<b>📤 SET SOURCE CHANNEL</b>\n\n"
            "Forward the last message from the source channel,\nOR send its message link.\n\n"
            "<i>Private channel → userbot must be a member.</i>\n\n/cancel — cancel")
        if resp.text and resp.text.startswith('/'):
            return await resp.reply("Cancelled.")
        chat_id = None
        if resp.text and not resp.forward_date:
            link = resp.text.strip().replace("?single", "")
            pm = re.search(r"t\.me/c/(\d+)/(\d+)", link)
            pu = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", link)
            if pm:   chat_id = int("-100" + pm.group(1))
            elif pu: chat_id = pu.group(1)
            else:    return await resp.reply("❌ Invalid Telegram link.")
        elif resp.forward_from_chat and resp.forward_from_chat.type in [enums.ChatType.CHANNEL, enums.ChatType.SUPERGROUP]:
            chat_id = resp.forward_from_chat.username or resp.forward_from_chat.id
        else:
            return await resp.reply("❌ Invalid input. Forward a message or send a link.")
        try:    title = (await bot.get_chat(chat_id)).title
        except: title = str(chat_id)
        sources = cfg.get('sources', [])
        already = any(str(s['chat_id']) == str(chat_id) for s in sources)
        if not already:
            sources.append({'chat_id': chat_id, 'title': title})
            await db.update_live_config(user_id, {'sources': sources})
            msg = f"<b>✅ Source added:</b> <code>{title}</code>"
        else:
            msg = f"<b>⚠️ Already added:</b> <code>{title}</code>"
        await resp.reply(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#sources")]]))

    elif action.startswith('del_src_'):
        try:
            idx = int(action.split('_')[-1])
            sources = cfg.get('sources', [])
            if 0 <= idx < len(sources):
                removed = sources.pop(idx)
                await db.update_live_config(user_id, {'sources': sources})
                await query.answer(f"Removed: {removed['title'][:25]}", show_alert=False)
        except Exception:
            await query.answer("Error removing.", show_alert=True)
        cfg = await db.get_live_config(user_id)
        await _show_sources(query, cfg)

    # ──────────────────────────────── FILTERS
    elif action == 'filters':
        await query.message.edit_text(
            "<b>🎛 FILTERS</b>\n\n<i>SOURCE CHANNEL SE KON SE MESSAGE FORWARD\nHONGE CHUNEN.</i>",
            reply_markup=_live_filters_markup(cfg))

    elif action.startswith('toggle_'):
        key = action[len('toggle_'):]
        lf  = cfg.get('filters', DEFAULT_LIVE_FILTERS.copy())
        lf[key] = not lf.get(key, DEFAULT_LIVE_FILTERS.get(key, True))
        await db.update_live_config(user_id, {'filters': lf})
        cfg['filters'] = lf
        await query.message.edit_text(
            "<b>🎛 FILTERS</b>\n\n<i>SOURCE CHANNEL SE KON SE MESSAGE FORWARD\nHONGE CHUNEN.</i>",
            reply_markup=_live_filters_markup(cfg))

    # ──────────────────────────────── MANAGE BOT/USERBOT
    elif action == 'manage_bot':
        await _show_manage_bot(query, user_id)

    elif action == 'add_bot_token':
        await query.message.delete()
        result = await ClientClass().add_bot(bot, query)
        if result is True:
            await bot.send_message(user_id, "<b>✅ Bot token added!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#manage_bot")]]))

    elif action == 'add_session_str':
        await query.message.delete()
        result = await ClientClass().add_session(bot, query)
        if result is True:
            await bot.send_message(user_id, "<b>✅ Userbot session added!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#manage_bot")]]))

    elif action == 'add_phone':
        await query.message.delete()
        result = await ClientClass().add_session(bot, query)
        if result is True:
            await bot.send_message(user_id, "<b>✅ Userbot added via phone!</b>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#manage_bot")]]))

    elif action == 'remove_bot':
        await db.remove_bot(user_id)
        await query.answer("Bot removed.", show_alert=False)
        await _show_manage_bot(query, user_id)

    elif action == 'remove_userbot':
        await db.remove_userbot(user_id)
        await query.answer("Userbot removed.", show_alert=False)
        await _show_manage_bot(query, user_id)

    # ──────────────────────────────── START
    elif action == 'start':
        if not cfg.get('destination'):
            return await query.answer("❌ Set a DESTINATION CHANNEL first!", show_alert=True)
        if not cfg.get('sources'):
            return await query.answer("❌ Add at least one SOURCE CHANNEL first!", show_alert=True)
        _bot_data = await db.get_bot(user_id) or await db.get_userbot(user_id)
        if not _bot_data:
            return await query.answer("❌ Add a BOT or USERBOT first!", show_alert=True)
        live_clients = getattr(temp, 'LIVE_CLIENTS', {})
        if user_id in live_clients:
            return await query.answer("⚠️ Live forward is already ACTIVE!", show_alert=True)
        await query.message.edit_text("<code>⏳ Starting live forward, please wait...</code>")
        ok, err = await _start_live(user_id, cfg, _bot_data)
        if ok:
            await db.update_live_config(user_id, {'is_active': True})
            cfg = await db.get_live_config(user_id)
            await query.message.edit_text(_live_main_text(cfg, user_id), reply_markup=_live_main_markup())
            await bot.send_message(user_id, "<b>📡 Live Forward is now ACTIVE! 🟢</b>")
        else:
            await query.message.edit_text(
                f"<b>❌ Failed to start:</b>\n<code>{err}</code>\n\n"
                "Check that your bot/userbot can access source and is admin in destination.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ BACK", callback_data="live#main")]]))

    # ──────────────────────────────── STOP
    elif action == 'stop':
        live_clients = getattr(temp, 'LIVE_CLIENTS', {})
        if user_id not in live_clients:
            return await query.answer("No active live forward running.", show_alert=True)
        await _stop_live(user_id)
        await db.update_live_config(user_id, {'is_active': False})
        cfg = await db.get_live_config(user_id)
        await query.answer("🛑 Live forward stopped!", show_alert=True)
        await query.message.edit_text(_live_main_text(cfg, user_id), reply_markup=_live_main_markup())


# ─────────────────────────────────────────────────────────────
#  UI builders
# ─────────────────────────────────────────────────────────────

def _live_main_text(cfg: dict, user_id: int) -> str:
    live_clients = getattr(temp, 'LIVE_CLIENTS', {})
    is_active    = cfg.get('is_active', False) and (user_id in live_clients)
    dest         = cfg.get('destination')
    dest_title   = cfg.get('destination_title', '')
    sources      = cfg.get('sources', [])
    return (
        "<b>📡 LIVE FORWARD SETTINGS</b>\n\n"
        f"<b>STATUS :</b> {'🟢' if is_active else '🔴'} {'ACTIVE' if is_active else 'INACTIVE'}\n"
        f"<b>DESTINATION :</b> {'✅ ' + dest_title if dest else '❌ NOT SET'}\n"
        f"<b>SOURCE CHANNELS :</b> {len(sources)}\n\n"
        "<i>NEW MESSAGES FROM ALL SOURCE CHANNELS WILL BE\n"
        "FORWARDED TO DESTINATION IN REAL TIME.</i>"
    )

def _live_main_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 DESTINATION CHANNEL",  callback_data="live#destination")],
        [InlineKeyboardButton("📤 SOURCE CHANNELS",      callback_data="live#sources")],
        [InlineKeyboardButton("🎛 FILTERS",              callback_data="live#filters")],
        [InlineKeyboardButton("🤖 MANAGE BOT / USERBOT", callback_data="live#manage_bot")],
        [InlineKeyboardButton("▶️ START LIVE", callback_data="live#start"),
         InlineKeyboardButton("⏹ STOP LIVE",  callback_data="live#stop")],
        [InlineKeyboardButton("↩️ BACK", callback_data="settings#main")],
    ])

def _live_filters_markup(cfg: dict) -> InlineKeyboardMarkup:
    f = cfg.get('filters', DEFAULT_LIVE_FILTERS.copy())
    def icon(k): return "✅" if f.get(k, DEFAULT_LIVE_FILTERS.get(k, True)) else "❌"
    rows = [
        ("✏️ TEXT",              "text"),
        ("📷 PHOTO",             "photo"),
        ("🎞 VIDEO",             "video"),
        ("📁 DOCUMENT",          "document"),
        ("🎵 AUDIO",             "audio"),
        ("🎤 VOICE",             "voice"),
        ("🎭 GIF",               "animation"),
        ("🃏 STICKER",           "sticker"),
        ("📊 POLL",              "poll"),
        ("📌 KEEP FWD TAG",      "keep_fwd_tag"),
        ("🔒 PROTECT CONTENT",   "protect"),
    ]
    btns = [[InlineKeyboardButton(lbl, callback_data="noth"),
             InlineKeyboardButton(icon(k), callback_data=f"live#toggle_{k}")] for lbl, k in rows]
    btns.append([InlineKeyboardButton("↩️ BACK", callback_data="live#main")])
    return InlineKeyboardMarkup(btns)

async def _show_sources(query, cfg: dict):
    sources = cfg.get('sources', [])
    status  = "❌ NOT SET" if not sources else f"{len(sources)} channel(s) added"
    text = (
        f"<b>📤 SOURCE CHANNEL</b>\n\n"
        f"<b>CURRENT :</b> {status}\n\n"
        "<i>SOURCE CHANNEL KE NEW MESSAGES ALL DESTINATIONS MEIN BHEJE JAAYENGE.\n"
        "PRIVATE CHANNEL KE LIYE USERBOT ZAROORI HAI JO US CHANNEL KA MEMBER HO.</i>"
    )
    btns = [[InlineKeyboardButton(f"🗑 {s['title'][:35]}", callback_data=f"live#del_src_{i}")]
            for i, s in enumerate(sources)]
    btns.append([InlineKeyboardButton("✏️ SET SOURCE", callback_data="live#add_source")])
    btns.append([InlineKeyboardButton("↩️ BACK", callback_data="live#main")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))

async def _show_manage_bot(query, user_id: int):
    _bot = await db.get_bot(user_id)
    usr  = await db.get_userbot(user_id)
    text = (
        "<b>🤖 BOT / USERBOT ADD KARO</b>\n\n"
        "<i>CHANNEL MANAGEMENT CHALANE KE LIYE BOT YA\n"
        "USERBOT ZAROORI HAI.\nTEENON MEIN SE KOI EK CHUNO:</i>"
    )
    btns = []
    if _bot:
        btns.append([InlineKeyboardButton(f"✅ BOT: {_bot['name'][:25]}  (remove)", callback_data="live#remove_bot")])
    else:
        btns.append([InlineKeyboardButton("➕ ADD BOT (TOKEN)",       callback_data="live#add_bot_token")])
    if usr:
        btns.append([InlineKeyboardButton(f"✅ USERBOT: {usr['name'][:20]}  (remove)", callback_data="live#remove_userbot")])
    else:
        btns.append([InlineKeyboardButton("➕ ADD USERBOT (SESSION)", callback_data="live#add_session_str")])
        btns.append([InlineKeyboardButton("➕ LOGIN USERBOT (PHONE)", callback_data="live#add_phone")])
    btns.append([InlineKeyboardButton("↩️ BACK", callback_data="live#main")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(btns))


# ─────────────────────────────────────────────────────────────
#  Core engine
# ─────────────────────────────────────────────────────────────

async def _start_live(user_id: int, cfg: dict, _bot_data: dict):
    if not hasattr(temp, 'LIVE_CLIENTS'):
        temp.LIVE_CLIENTS = {}
    dest    = cfg['destination']
    sources = [s['chat_id'] for s in cfg.get('sources', [])]
    lf      = cfg.get('filters', DEFAULT_LIVE_FILTERS.copy())
    is_bot  = _bot_data['is_bot']
    data    = _bot_data['token'] if is_bot else _bot_data['session']
    try:
        client = await get_client(data, is_bot=is_bot)
        await client.start()
    except Exception as e:
        return False, str(e)
    try:
        t = await client.send_message(dest, "📡 <b>Live Forward Activated!</b>")
        await t.delete()
    except Exception as e:
        try: await client.stop()
        except: pass
        return False, f"Cannot post to destination: {e}"

    async def _handler(c, message):
        try:
            type_map = {
                "text": bool(message.text and not message.media),
                "photo": bool(message.photo), "video": bool(message.video),
                "document": bool(message.document), "audio": bool(message.audio),
                "voice": bool(message.voice), "animation": bool(message.animation),
                "sticker": bool(message.sticker), "poll": bool(message.poll),
            }
            for mtype, present in type_map.items():
                if present and not lf.get(mtype, True):
                    return
            keep_tag = lf.get('keep_fwd_tag', False)
            protect  = lf.get('protect', False)
            if keep_tag:
                await c.forward_messages(chat_id=dest, from_chat_id=message.chat.id,
                                         message_ids=message.id, protect_content=protect)
            else:
                configs     = await db.get_configs(user_id)
                btn_str     = configs.get('button')
                button      = parse_buttons(btn_str) if btn_str else None
                new_caption = custom_caption(message, configs.get('caption'))
                media_fid   = media(message)
                if media_fid and new_caption:
                    await c.send_cached_media(chat_id=dest, file_id=media_fid,
                        caption=new_caption, reply_markup=button, protect_content=protect)
                else:
                    await c.copy_message(chat_id=dest, from_chat_id=message.chat.id,
                        message_id=message.id, caption=new_caption,
                        reply_markup=button, protect_content=protect)
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except Exception as e:
            logger.error(f"[Live] user {user_id}: {e}")

    handler = MessageHandler(_handler, filters.chat(sources))
    client.add_handler(handler)
    temp.LIVE_CLIENTS[user_id] = {'client': client, 'handler': handler}
    logger.info(f"[Live] Started user {user_id} | {sources} → {dest}")
    return True, None

async def _stop_live(user_id: int):
    live = getattr(temp, 'LIVE_CLIENTS', {})
    if user_id not in live:
        return
    e = live[user_id]
    try:    e['client'].remove_handler(e['handler'])
    except: pass
    try:    await e['client'].stop()
    except: pass
    del live[user_id]

async def restart_live_forwards(main_bot):
    if not hasattr(temp, 'LIVE_CLIENTS'):
        temp.LIVE_CLIENTS = {}
    active_cfgs = await db.get_all_active_live_configs()
    count = 0
    async for cfg_doc in active_cfgs:
        uid  = cfg_doc['user_id']
        _bd  = await db.get_bot(uid) or await db.get_userbot(uid)
        if not _bd:
            await db.update_live_config(uid, {'is_active': False})
            continue
        ok, err = await _start_live(uid, cfg_doc, _bd)
        if ok:
            count += 1
            try:
                await main_bot.send_message(uid,
                    f"<b>📡 Live Forward resumed after restart!</b>\n\n"
                    f"📥 <code>{cfg_doc.get('destination_title','?')}</code>\n"
                    f"📤 {len(cfg_doc.get('sources',[]))} source(s) | 🟢 ACTIVE")
            except: pass
        else:
            await db.update_live_config(uid, {'is_active': False})
    logger.info(f"[Live] Resumed {count} live forward(s).")
