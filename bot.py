# -*- coding: utf-8 -*-
"""
Forge Telegram Bot (Python)
Запуск:
  pip install -r requirements.txt
  python bot.py
Нужен serviceAccount.json из Firebase (Project settings → Service accounts).
"""
from __future__ import annotations

import json
import logging
import os
import random
import string
from datetime import datetime, timedelta, timezone
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("forge-bot")

BASE = Path(__file__).resolve().parent
FILES = BASE / "files"
SESSIONS: dict[int, dict] = {}

PLANS = [
    {"id": "1m", "title": "Forge Plus · 1 месяц", "months": 1, "stars": 150},
    {"id": "3m", "title": "Forge Plus · 3 месяца", "months": 3, "stars": 350},
    {"id": "12m", "title": "Forge Plus · 12 месяцев", "months": 12, "stars": 999},
]


def init_fb():
    sa = BASE / "serviceAccount.json"
    if not sa.exists():
        raise SystemExit(
            "Нет serviceAccount.json рядом с bot.py\n"
            "Firebase Console → Project settings → Service accounts → Generate new private key"
        )
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(str(sa)))
    return firestore.client()


db = None  # set in main


def is_admin(uid: int) -> bool:
    return int(uid) in config.ADMIN_TG_IDS


def main_kb():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⭐ Подписка Forge Plus", callback_data="menu:plus")],
            [InlineKeyboardButton("🎁 NFT за Stars", callback_data="menu:nft")],
            [InlineKeyboardButton("⬇️ Скачать обновление", callback_data="menu:update")],
            [InlineKeyboardButton("🎫 Активировать чек", callback_data="menu:check")],
            [InlineKeyboardButton("❤️ Донат разработчикам", callback_data="menu:donate")],
            [InlineKeyboardButton("🛠 Техподдержка", callback_data="menu:support")],
            [InlineKeyboardButton("ℹ️ Помощь", callback_data="menu:help")],
        ]
    )


def sess(chat_id: int) -> dict:
    return SESSIONS.setdefault(chat_id, {})


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    payload = text.split(maxsplit=1)[1] if " " in text else ""
    chat_id = update.effective_chat.id
    SESSIONS[chat_id] = {}
    if payload.startswith("chk_"):
        code = payload[4:]
        s = sess(chat_id)
        s["wait"] = "check_nick"
        s["check_code"] = code
        await update.message.reply_text(
            f"🎫 Чек `{code}`\n\nВведи ник Forge:\n`Имя#1234`",
            parse_mode="Markdown",
        )
        return
    await update.message.reply_text(
        "🔥 *Forge Bot*\n\nPlus и NFT за Stars · обновления · чеки · поддержка.",
        parse_mode="Markdown",
        reply_markup=main_kb(),
    )


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    open_ = db.collection("supportTickets").where("status", "==", "open").limit(15).stream()
    n = 0
    for d in open_:
        n += 1
        t = d.to_dict() or {}
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Принять", callback_data=f"adm:accept:{d.id}"),
                    InlineKeyboardButton("❌ Закрыть", callback_data=f"adm:close:{d.id}"),
                ]
            ]
        )
        await update.message.reply_text(
            f"🎫 `{d.id}`\nТип: {t.get('type')}\nОт: {t.get('fromTg')}\n{t.get('text')}",
            parse_mode="Markdown",
            reply_markup=kb,
        )
    if not n:
        await update.message.reply_text(
            "Открытых тикетов нет.\nЧеки: `/check plus 1m` · `/check nft ID`",
            parse_mode="Markdown",
        )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args or []
    if not args or args[0] not in ("plus", "nft"):
        await update.message.reply_text(
            "Чеки:\n`/check plus 1m`\n`/check plus 3m 10`\n`/check nft COLLECTION_ID`",
            parse_mode="Markdown",
        )
        return
    code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=8))
    kind = args[0]
    max_uses = 1
    doc = {
        "code": code,
        "kind": kind,
        "used": 0,
        "usedBy": [],
        "active": True,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "createdBy": update.effective_user.id,
    }
    if kind == "plus":
        plan_id = args[1] if len(args) > 1 else "1m"
        plan = next((p for p in PLANS if p["id"] == plan_id), PLANS[0])
        if len(args) > 2 and args[2].isdigit():
            max_uses = max(1, int(args[2]))
        doc.update({"planId": plan["id"], "months": plan["months"], "maxUses": max_uses})
        title = f"⭐ Чек Forge Plus · {plan['months']} мес."
    else:
        nft_id = args[1] if len(args) > 1 else ""
        if not nft_id:
            await update.message.reply_text("Нужен ID коллекции")
            return
        if len(args) > 2 and args[2].isdigit():
            max_uses = max(1, int(args[2]))
        col = db.collection("nftCollections").document(nft_id).get()
        name = (col.to_dict() or {}).get("name", nft_id) if col.exists else nft_id
        doc.update({"nftId": nft_id, "nftName": name, "maxUses": max_uses})
        title = f"🎁 Чек NFT · {name}"

    db.collection("starChecks").document(code).set(doc)
    body = (
        f"{title}\n\nКод: `{code}`\n"
        f"Нажми «Активировать» → ник `Имя#1234` → награда в Forge.\n"
        f"Использований: 0 / {max_uses}"
    )
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Активировать", callback_data=f"chk:{code}")]]
    )
    await update.message.reply_text(body, parse_mode="Markdown", reply_markup=kb)
    ch = config.CHECKS_CHANNEL
    if ch:
        try:
            msg = await context.bot.send_message(ch, body, parse_mode="Markdown", reply_markup=kb)
            db.collection("starChecks").document(code).update(
                {"channelId": msg.chat_id, "channelMsgId": msg.message_id}
            )
        except Exception as e:
            await update.message.reply_text(f"В канал не отправилось: {e}")
    else:
        await update.message.reply_text(
            "Чтобы чек шёл в канал: задай CHECKS_CHANNEL=@канал (бот — админ канала)"
        )


def find_update(kind: str) -> Path | None:
    FILES.mkdir(exist_ok=True)
    prefer = config.UPDATE_EXE if kind == "pc" else config.UPDATE_APK
    p = FILES / prefer
    if p.is_file():
        return p
    ext = ".exe" if kind == "pc" else ".apk"
    files = sorted(
        [f for f in FILES.iterdir() if f.suffix.lower() == ext and f.is_file()],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


async def send_update(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str):
    chat_id = update.effective_chat.id
    # Большие файлы — ссылка (лимит Telegram ~50 МБ)
    if kind == "pc" and getattr(config, "UPDATE_PC_URL", ""):
        url = config.UPDATE_PC_URL
        await context.bot.send_message(
            chat_id,
            "💻 *Forge PC (Windows)*\n\n"
            "Скачай установщик по ссылке (файл большой, поэтому не вложением):\n\n"
            f"[⬇️ Скачать Forge Setup]({url})\n\n"
            "Если браузер откроет просмотр Dropbox — нажми Download.",
            parse_mode="Markdown",
            disable_web_page_preview=False,
            reply_markup=main_kb(),
        )
        return
    if kind == "apk" and getattr(config, "UPDATE_APK_URL", ""):
        url = config.UPDATE_APK_URL
        await context.bot.send_message(
            chat_id,
            "📱 *Forge Android*\n\n"
            f"[⬇️ Скачать APK]({url})",
            parse_mode="Markdown",
            reply_markup=main_kb(),
        )
        return
    path = find_update(kind)
    if not path:
        await context.bot.send_message(
            chat_id,
            "❌ Файл не найден.\nПоложи "
            + (".exe" if kind == "pc" else ".apk")
            + f" в папку:\n`{FILES}`",
            parse_mode="Markdown",
        )
        return
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > 49:
        await context.bot.send_message(
            chat_id,
            f"Файл {path.name} слишком большой ({size_mb:.1f} МБ). Лимит бота ≈ 50 МБ.",
        )
        return
    await context.bot.send_message(chat_id, f"⬇️ Отправляю *{path.name}* ({size_mb:.1f} МБ)…", parse_mode="Markdown")
    caption = "Forge PC · Windows (.exe)" if kind == "pc" else "Forge Mobile · Android (.apk)"
    with path.open("rb") as f:
        await context.bot.send_document(chat_id, document=f, filename=path.name, caption=caption)


async def find_user_by_nick(nick: str):
    handle = nick.strip().lstrip("@").lower()
    if not handle:
        return None
    if "#" in handle:
        hs = db.collection("handles").document(handle).get()
        if hs.exists:
            uid = hs.to_dict().get("uid")
            us = db.collection("users").document(uid).get()
            if us.exists:
                return {"uid": uid, **(us.to_dict() or {})}
    q = db.collection("users").where("handle", "==", handle).limit(1).stream()
    for d in q:
        return {"uid": d.id, **(d.to_dict() or {})}
    return None


def extend_plus(uid: str, months: int) -> bool:
    ref = db.collection("users").document(uid)
    snap = ref.get()
    if not snap.exists:
        return False
    data = snap.to_dict() or {}
    cur = data.get("plusUntil")
    now = datetime.now(timezone.utc)
    if hasattr(cur, "timestamp"):
        base = datetime.fromtimestamp(cur.timestamp(), tz=timezone.utc)
        base = max(base, now)
    else:
        base = now
    until = base + timedelta(days=30 * months)
    ref.update({"plus": True, "plusUntil": until, "plusSource": "telegram_stars"})
    return True


def grant_nft(uid: str, nft_id: str):
    col = db.collection("nftCollections").document(nft_id).get()
    if not col.exists:
        return None
    c = col.to_dict() or {}
    serial = int(c.get("sold") or 0) + 1
    db.collection("users").document(uid).collection("inventory").add(
        {
            "type": "nft",
            "collectionId": nft_id,
            "collectionName": c.get("name"),
            "serial": serial,
            "model": {
                "name": c.get("name"),
                "rarity": "common",
                "video": c.get("baseVideo") or "",
                "id": "base",
            },
            "background": {
                "id": "lavender",
                "name": "Lavender",
                "color": "#8b6bc7",
                "pattern": "stars",
            },
            "emoji": {"name": "spark", "rarity": "common"},
            "bgColor": "#8b6bc7",
            "upgraded": False,
            "source": "telegram_bot",
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
    )
    db.collection("nftCollections").document(nft_id).update({"sold": firestore.Increment(1)})
    return c.get("name"), serial


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    chat_id = q.message.chat_id
    s = sess(chat_id)

    if data == "menu:home" or data == "menu:help":
        txt = (
            "⭐ Plus/NFT — Stars.\n⬇️ Обновление — .exe / .apk из папки files.\n"
            "🎫 Чеки — кнопка «Активировать».\nПоддержка — тикет админу."
            if data == "menu:help"
            else "Выбери действие:"
        )
        await q.message.reply_text(txt, reply_markup=main_kb())
        return

    if data == "menu:update":
        await q.message.reply_text(
            "⬇️ *Обновление Forge*\nВыбери платформу:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("💻 ПК (Windows .exe)", callback_data="upd:pc")],
                    [InlineKeyboardButton("📱 Телефон (Android .apk)", callback_data="upd:apk")],
                    [InlineKeyboardButton("« Назад", callback_data="menu:home")],
                ]
            ),
        )
        return
    if data == "upd:pc":
        await send_update(update, context, "pc")
        return
    if data == "upd:apk":
        await send_update(update, context, "apk")
        return

    if data == "menu:plus":
        rows = [
            [InlineKeyboardButton(f"{p['title']} — {p['stars']} ⭐", callback_data=f"plan:{p['id']}")]
            for p in PLANS
        ]
        rows.append([InlineKeyboardButton("« Назад", callback_data="menu:home")])
        await q.message.reply_text(
            "Выбери срок *Forge Plus*:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows)
        )
        return
    if data.startswith("plan:"):
        s["wait"] = "nick_plus"
        s["plan_id"] = data[5:]
        await q.message.reply_text("Ник Forge:\n`Имя#1234`", parse_mode="Markdown")
        return

    if data == "menu:nft":
        docs = list(db.collection("nftCollections").where("published", "==", True).limit(20).stream())
        if not docs:
            await q.message.reply_text("Нет NFT в продаже.", reply_markup=main_kb())
            return
        rows = []
        for d in docs:
            c = d.to_dict() or {}
            stars = int(c.get("priceStars") or max(50, round(float(c.get("priceTon") or 5) * 40)))
            rows.append(
                [InlineKeyboardButton(f"{c.get('name')} — {stars} ⭐", callback_data=f"nft:{d.id}")]
            )
        rows.append([InlineKeyboardButton("« Назад", callback_data="menu:home")])
        await q.message.reply_text("🎁 NFT (Stars):", reply_markup=InlineKeyboardMarkup(rows))
        return
    if data.startswith("nft:"):
        s["wait"] = "nick_nft"
        s["nft_id"] = data[4:]
        await q.message.reply_text("Ник Forge для NFT:\n`Имя#1234`", parse_mode="Markdown")
        return

    if data == "menu:donate":
        await q.message.reply_text(
            "❤️ *Поддержка Forge*\nЗвёзды на сервера, рекламу и развитие:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("50 ⭐", callback_data="don:50"),
                        InlineKeyboardButton("100 ⭐", callback_data="don:100"),
                    ],
                    [
                        InlineKeyboardButton("250 ⭐", callback_data="don:250"),
                        InlineKeyboardButton("500 ⭐", callback_data="don:500"),
                    ],
                    [InlineKeyboardButton("« Назад", callback_data="menu:home")],
                ]
            ),
        )
        return
    if data.startswith("don:"):
        stars = max(1, int(data[4:] or 50))
        await context.bot.send_invoice(
            chat_id=chat_id,
            title="Донат Forge",
            description="Поддержка разработки и рекламы",
            payload=json.dumps({"kind": "donate", "stars": stars}),
            currency="XTR",
            prices=[LabeledPrice("Донат", stars)],
            provider_token="",
        )
        return

    if data == "menu:check":
        s["wait"] = "check_code"
        await q.message.reply_text("Пришли код чека (8 символов) или нажми «Активировать» под постом.")
        return

    if data.startswith("chk:"):
        code = data[4:]
        s["wait"] = "check_nick"
        s["check_code"] = code
        try:
            await context.bot.send_message(
                q.from_user.id,
                f"🎫 Чек `{code}`\n\nВведи ник Forge:\n`Имя#1234`",
                parse_mode="Markdown",
            )
        except Exception:
            await q.message.reply_text("Сначала открой бота в личке (/start), потом активируй чек.")
        return

    if data == "menu:support":
        await q.message.reply_text(
            "Тип тикета:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🚫 Разбан / бан", callback_data="sup:ban")],
                    [InlineKeyboardButton("⚠️ Жалоба", callback_data="sup:report")],
                    [InlineKeyboardButton("💬 Другое", callback_data="sup:other")],
                    [InlineKeyboardButton("« Назад", callback_data="menu:home")],
                ]
            ),
        )
        return
    if data.startswith("sup:"):
        s["wait"] = "ticket_text"
        s["ticket_type"] = data[4:]
        await q.message.reply_text("Опиши проблему:")
        return

    if data.startswith("adm:accept:") and is_admin(q.from_user.id):
        tid = data.replace("adm:accept:", "")
        db.collection("supportTickets").document(tid).set(
            {"status": "accepted", "adminTg": q.from_user.id}, merge=True
        )
        s["wait"] = "admin_reply"
        s["ticket_id"] = tid
        t = db.collection("supportTickets").document(tid).get().to_dict() or {}
        await q.message.reply_text("Тикет принят. Пиши ответ — уйдёт пользователю.")
        if t.get("fromTg"):
            await context.bot.send_message(t["fromTg"], "✅ Админ принял тикет.")
        return
    if data.startswith("adm:close:") and is_admin(q.from_user.id):
        tid = data.replace("adm:close:", "")
        db.collection("supportTickets").document(tid).set({"status": "closed"}, merge=True)
        await q.message.reply_text(f"Закрыт {tid}")
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    s = sess(chat_id)

    if s.get("wait") == "check_code":
        code = "".join(c for c in text.upper() if c.isalnum())
        s["check_code"] = code
        s["wait"] = "check_nick"
        await update.message.reply_text(
            f"Чек `{code}`\nТеперь ник Forge:\n`Имя#1234`", parse_mode="Markdown"
        )
        return

    if s.get("wait") == "check_nick" and s.get("check_code"):
        code = s.pop("check_code")
        s["wait"] = None
        await redeem_check(update, context, code, text)
        return

    if s.get("wait") == "nick_plus" and s.get("plan_id"):
        user = await find_user_by_nick(text)
        if not user:
            await update.message.reply_text("❌ Аккаунт не найден. `Имя#1234`", parse_mode="Markdown")
            return
        plan = next((p for p in PLANS if p["id"] == s["plan_id"]), None)
        s["wait"] = None
        if not plan:
            return
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=plan["title"],
            description=f"Plus на {plan['months']} мес. для {text}",
            payload=json.dumps(
                {"kind": "plus", "planId": plan["id"], "uid": user["uid"], "months": plan["months"]}
            ),
            currency="XTR",
            prices=[LabeledPrice(plan["title"], plan["stars"])],
            provider_token="",
        )
        return

    if s.get("wait") == "nick_nft" and s.get("nft_id"):
        user = await find_user_by_nick(text)
        if not user:
            await update.message.reply_text("❌ Аккаунт не найден. `Имя#1234`", parse_mode="Markdown")
            return
        col = db.collection("nftCollections").document(s["nft_id"]).get()
        if not col.exists:
            await update.message.reply_text("NFT не найден")
            return
        c = col.to_dict() or {}
        stars = int(c.get("priceStars") or max(50, round(float(c.get("priceTon") or 5) * 40)))
        s["wait"] = None
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=f"NFT: {c.get('name')}",
            description=f"«{c.get('name')}» → {text}",
            payload=json.dumps({"kind": "nft", "nftId": s["nft_id"], "uid": user["uid"]}),
            currency="XTR",
            prices=[LabeledPrice(c.get("name") or "NFT", stars)],
            provider_token="",
        )
        return

    if s.get("wait") == "ticket_text" and s.get("ticket_type"):
        ref = db.collection("supportTickets").document()
        ref.set(
            {
                "type": s["ticket_type"],
                "text": text[:2000],
                "fromTg": update.effective_user.id,
                "fromUsername": update.effective_user.username or "",
                "status": "open",
                "createdAt": firestore.SERVER_TIMESTAMP,
            }
        )
        s["wait"] = None
        await update.message.reply_text(f"✅ Тикет `{ref.id}` создан.", parse_mode="Markdown", reply_markup=main_kb())
        for aid in config.ADMIN_TG_IDS:
            try:
                await context.bot.send_message(
                    aid,
                    f"🆕 Тикет `{ref.id}`\n{s.get('ticket_type')}\nTG: {update.effective_user.id}\n{text}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton("✅ Принять", callback_data=f"adm:accept:{ref.id}"),
                                InlineKeyboardButton("❌ Закрыть", callback_data=f"adm:close:{ref.id}"),
                            ]
                        ]
                    ),
                )
            except Exception:
                pass
        return

    if s.get("wait") == "admin_reply" and s.get("ticket_id") and is_admin(update.effective_user.id):
        tid = s["ticket_id"]
        ts = db.collection("supportTickets").document(tid).get()
        if not ts.exists:
            s["wait"] = None
            await update.message.reply_text("Тикет не найден")
            return
        t = ts.to_dict() or {}
        db.collection("supportTickets").document(tid).collection("messages").add(
            {"from": "admin", "text": text, "at": firestore.SERVER_TIMESTAMP}
        )
        if t.get("fromTg"):
            await context.bot.send_message(t["fromTg"], f"💬 *Ответ поддержки:*\n{text}", parse_mode="Markdown")
        await update.message.reply_text("Отправлено.")
        return

    await update.message.reply_text("Выбери действие:", reply_markup=main_kb())


async def redeem_check(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str, nick: str):
    ref = db.collection("starChecks").document(code)
    snap = ref.get()
    if not snap.exists or not (snap.to_dict() or {}).get("active"):
        await update.message.reply_text("❌ Чек не найден или неактивен.")
        return
    c = snap.to_dict() or {}
    if int(c.get("used") or 0) >= int(c.get("maxUses") or 1):
        await update.message.reply_text("❌ Чек уже использован.")
        return
    uid_tg = update.effective_user.id
    if uid_tg in (c.get("usedBy") or []):
        await update.message.reply_text("❌ Ты уже активировал этот чек.")
        return
    user = await find_user_by_nick(nick)
    if not user:
        await update.message.reply_text("❌ Аккаунт не найден. `Имя#1234`", parse_mode="Markdown")
        return
    label = ""
    if c.get("kind") == "plus":
        if not extend_plus(user["uid"], int(c.get("months") or 1)):
            await update.message.reply_text("Ошибка выдачи Plus")
            return
        label = f"Forge Plus на {c.get('months')} мес."
    elif c.get("kind") == "nft":
        r = grant_nft(user["uid"], c.get("nftId"))
        if not r:
            await update.message.reply_text("Ошибка NFT")
            return
        label = f"NFT {r[0]} #{r[1]}"
    else:
        await update.message.reply_text("Неизвестный чек")
        return
    ref.update(
        {
            "used": firestore.Increment(1),
            "usedBy": firestore.ArrayUnion([uid_tg]),
            "lastRedeemNick": nick,
            "lastRedeemUid": user["uid"],
        }
    )
    await update.message.reply_text(
        f"✅ Чек активирован!\n*{label}* → `{nick}`",
        parse_mode="Markdown",
        reply_markup=main_kb(),
    )
    for aid in config.ADMIN_TG_IDS:
        try:
            await context.bot.send_message(
                aid, f"🔔 Чек `{code}` → {nick}\n{label}", parse_mode="Markdown"
            )
        except Exception:
            pass


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    try:
        payload = json.loads(sp.invoice_payload or "{}")
    except Exception:
        payload = {}
    db.collection("starPayments").add(
        {
            "payload": payload,
            "stars": sp.total_amount,
            "tgUser": update.effective_user.id,
            "at": firestore.SERVER_TIMESTAMP,
        }
    )
    chat_id = update.effective_chat.id
    if payload.get("kind") == "donate":
        db.collection("starDonations").add(
            {
                "stars": sp.total_amount,
                "tgUser": update.effective_user.id,
                "at": firestore.SERVER_TIMESTAMP,
            }
        )
        for aid in config.ADMIN_TG_IDS:
            try:
                await context.bot.send_message(
                    aid, f"❤️ Донат {sp.total_amount}⭐ от TG {update.effective_user.id}"
                )
            except Exception:
                pass
        await update.message.reply_text("Спасибо за поддержку Forge! ❤️", reply_markup=main_kb())
        return
    if payload.get("kind") == "plus" and payload.get("uid") and payload.get("months"):
        ok = extend_plus(payload["uid"], int(payload["months"]))
        await update.message.reply_text(
            f"✅ Plus на {payload['months']} мес. активирован!" if ok else "⚠️ Оплата есть, аккаунт не найден.",
            reply_markup=main_kb(),
        )
        return
    if payload.get("kind") == "nft" and payload.get("uid") and payload.get("nftId"):
        r = grant_nft(payload["uid"], payload["nftId"])
        await update.message.reply_text(
            f"✅ NFT {r[0]} #{r[1]} выдан!" if r else "Ошибка выдачи NFT",
            reply_markup=main_kb(),
        )
        return
    await update.message.reply_text("Оплата получена.", reply_markup=main_kb())


def main():
    global db
    db = init_fb()
    FILES.mkdir(exist_ok=True)
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Forge bot started · admins %s · files %s", config.ADMIN_TG_IDS, FILES)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
