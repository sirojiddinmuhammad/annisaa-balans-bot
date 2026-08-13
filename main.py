"""
Annisaa Markazi — Chek boti (yangi CRM)
========================================
Ish tartibi:
  1) Admin talaba xabarini (eslatma/chek) forward qiladi
  2) Bot forward'dagi Telegram ID orqali talabani topadi
       - topilmasa: admin ism (+ guruh nomi) matn yuboradi
       - bot ism+guruh bo'yicha qidiradi, admin tugmadan tanlaydi
       - Telegram ID bo'sh bo'lsa → forward'dan olib yozib qo'yadi
  3) Admin chekni yuboradi (rasm yoki PDF)
  4) Claude chekdan ma'lumot o'qiydi
  5) To'lovlar bazasiga yozuv qo'shiladi (Talaba avtomat bog'lanadi)
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config as C
import notion_api as N
import receipt_ai as AI
from eslatma_parser import ajrat_ism_guruh

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("chekbot")

TZ = timezone(timedelta(hours=5))       # Toshkent
PENDING: dict = {}                       # {user_id: {...}}


# ============================================================ yordamchilar
def e(s):
    return html.escape(str(s)) if s is not None else ""


def pul(n):
    return "—" if n is None else f"{int(n):,}".replace(",", " ")


def admin_mi(update: Update) -> bool:
    if not C.ADMIN_IDS:
        return True
    u = update.effective_user
    return bool(u and u.id in C.ADMIN_IDS)


def holat_p(user_id: int):
    p = PENDING.get(user_id)
    if not p:
        return None
    import time
    if time.time() - p.get("vaqt", 0) > C.PENDING_TTL:
        PENDING.pop(user_id, None)
        return None
    return p


def holat_saqla(user_id: int, **kw):
    import time
    p = PENDING.setdefault(user_id, {})
    p.update(kw)
    p["vaqt"] = time.time()
    return p


def kb(qatorlar):
    return InlineKeyboardMarkup(qatorlar)


async def javob(update: Update, matn, keyboard=None):
    if update.callback_query:
        await update.callback_query.edit_message_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)
    else:
        await update.effective_message.reply_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)


def _forward_id(update: Update):
    """Forward qilingan xabardan asl yuboruvchining Telegram ID sini oladi."""
    m = update.effective_message
    fo = getattr(m, "forward_origin", None)
    if fo is not None:
        # Yangi Telegram API — forward_origin
        user = getattr(fo, "sender_user", None)
        if user is not None:
            return user.id, getattr(user, "full_name", None)
    # Eski API — forward_from
    ff = getattr(m, "forward_from", None)
    if ff is not None:
        return ff.id, getattr(ff, "full_name", None)
    return None, None


# ============================================================ /start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    await javob(update,
                "👋 <b>Chek boti</b> (yangi CRM)\n\n"
                "1️⃣ Talaba xabarini menga <b>forward</b> qiling\n"
                "   (yoki ism + guruh nomini matn qilib yuboring)\n"
                "2️⃣ Talaba tasdiqlanadi\n"
                "3️⃣ Chekni yuboring (rasm yoki PDF)\n"
                "4️⃣ To'lov bazaga yoziladi\n\n"
                "<code>/bekor</code> — bekor qilish\n"
                "<code>/kesh</code> — ro'yxatlarni yangilash\n\n"
                f"Sizning ID: <code>{uid}</code>")


async def bekor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    PENDING.pop(update.effective_user.id, None)
    await javob(update, "🧹 Bekor qilindi.")


async def kesh_yangila(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    N.keshni_tozala()
    g = await N.guruhlar_map()
    k = await N.kartalar()
    await javob(update, f"♻️ Yangilandi: {len(g)} guruh, {len(k)} karta.")


# ============================================================ talaba topish
async def _talaba_tanlandi(update: Update, uid: int, talaba: dict,
                          yozilgan_id=None):
    """Talaba aniqlangach — chekni kutish holatiga o'tadi."""
    p = holat_saqla(uid, talaba_id=talaba["id"], talaba_nomi=talaba["ism"])

    ekstra = ""
    if yozilgan_id:
        ekstra = f"\n📝 Telegram ID saqlandi: <code>{yozilgan_id}</code>"

    # Chek allaqachon kelgan bo'lsa (chek + forward birga) → tasdiqqa
    if p.get("chek"):
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_yoki_saqla(update, uid)
        return

    holat_saqla(uid, stage="chek_kutish")
    await javob(update,
                f"✅ <b>{e(talaba['ism'])}</b>{ekstra}\n\n"
                f"📎 <b>Endi chekni yuboring</b> (rasm yoki PDF)")


async def _forward_qabul(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Forward qilingan xabar — Telegram ID orqali talaba topish."""
    uid = update.effective_user.id
    tg_id, ism = _forward_id(update)

    if not tg_id:
        # Forward ID yashiringan — admin ism yuborishi kerak
        holat_saqla(uid, stage="ism_kutish", forward_tgid=None)
        await javob(update,
                    "🔒 Forward'da Telegram ID ko'rinmadi "
                    "(talaba maxfiylikni yopgan).\n\n"
                    "✍️ Talaba <b>ism</b>ini (va guruh nomini) yozing:\n"
                    "<i>masalan: Kamila Obidova, 83-Fonetika</i>")
        return

    kutish = await update.effective_message.reply_text("🔍 Talaba izlanmoqda…")
    try:
        talaba = await N.talaba_tgid(tg_id)
    except Exception as ex:
        log.exception("Talaba izlashda xato")
        await kutish.edit_text(f"❌ Notion xatosi: <code>{e(ex)}</code>",
                              parse_mode=ParseMode.HTML)
        return
    await kutish.delete()

    if talaba:
        holat_saqla(uid, forward_tgid=tg_id)
        await _talaba_tanlandi(update, uid, talaba)
    else:
        # ID yangi — talaba bazada bor, lekin ID yozilmagan. Ism bilan qidiramiz.
        holat_saqla(uid, stage="ism_kutish", forward_tgid=tg_id, forward_ism=ism)
        qatorlar = None
        matn = ("🆕 Bu Telegram ID bazada yo'q.\n\n")
        if ism:
            matn += f"Forward'dagi ism: <b>{e(ism)}</b>\n\n"
        matn += ("✍️ Talaba <b>ism</b>ini (va guruh nomini) yozing — "
                 "topgach, ID avtomat saqlanadi:\n"
                 "<i>masalan: Kamila Obidova, 83-Fonetika</i>")
        await javob(update, matn)


async def _ism_qabul(update: Update, uid: int, matn: str):
    """Admin yozgan ism+guruh matnidan talabani qidirish."""
    r = ajrat_ism_guruh(matn)
    if not r["ism"]:
        await javob(update, "❌ Ism topilmadi. Qaytadan yozing.")
        return

    kutish = await update.effective_message.reply_text("🔍 Talaba izlanmoqda…")
    try:
        aniq, taxminlar = await N.talaba_izla(r["ism"], r["guruh"])
    except Exception as ex:
        log.exception("Talaba izlashda xato")
        await kutish.edit_text(f"❌ Notion xatosi: <code>{e(ex)}</code>",
                              parse_mode=ParseMode.HTML)
        return
    await kutish.delete()

    if not taxminlar:
        await javob(update,
                    f"❌ <b>{e(r['ism'])}</b> topilmadi.\n"
                    f"Boshqacha yozib ko'ring yoki /bekor bosing.")
        return

    holat_saqla(uid, stage="talaba_tanlash", talaba_taxmin=taxminlar,
                izlangan_guruh=r["guruh"])

    # Aniq moslik bo'lsa — darrov (lekin baribir tasdiq tugmasi bilan)
    if aniq:
        # Ro'yxatdagi indeksini topamiz
        idx = next((i for i, t in enumerate(taxminlar) if t["id"] == aniq["id"]), 0)
        g_matn = ""
        if aniq.get("guruhlar"):
            g_matn = " · " + ", ".join(aniq["guruhlar"][:2])
        qatorlar = [
            [InlineKeyboardButton(f"✅ Ha, {aniq['ism']}", callback_data=f"t:{idx}")],
            [InlineKeyboardButton("🔽 Boshqa talaba", callback_data="tlist")],
            [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
        ]
        await javob(update,
                    f"🎯 Topildi: <b>{e(aniq['ism'])}</b>{e(g_matn)}\n\n"
                    f"Shu talabami?", kb(qatorlar))
        return

    # Bir nechta nomzod — ro'yxat
    await _talaba_royxat(update, uid, taxminlar)


async def _talaba_royxat(update: Update, uid: int, taxminlar):
    qatorlar = []
    for i, t in enumerate(taxminlar):
        yorliq = t["ism"]
        if t.get("mos_guruh") and t.get("guruhlar"):
            yorliq += f" · {t['guruhlar'][0]} ✅"
        elif t.get("guruhlar"):
            yorliq += f" · {t['guruhlar'][0]}"
        qatorlar.append([InlineKeyboardButton(yorliq[:60], callback_data=f"t:{i}")])
    qatorlar.append([InlineKeyboardButton("🔍 Boshqa ism yozish", callback_data="tm")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
    await javob(update, "Quyidagilardan tanlang:", kb(qatorlar))


# ============================================================ chek
def _mime_aniqla(update: Update):
    m = update.effective_message
    if m.photo:
        return m.photo[-1], "image/jpeg", "chek.jpg"
    d = m.document
    if d:
        mime = (d.mime_type or "").lower()
        if mime.startswith("image/") or mime == "application/pdf":
            return d, mime, d.file_name or ("chek.pdf" if "pdf" in mime else "chek.jpg")
    return None, None, None


async def chek_qabul(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id

    fayl, mime, nomi = _mime_aniqla(update)
    if not fayl:
        await javob(update, "❌ Faqat rasm yoki PDF qabul qilinadi.")
        return

    # Chek forward qilingan bo'lsa — undan ham Telegram ID olishga harakat
    if update.effective_message.forward_origin or \
       getattr(update.effective_message, "forward_from", None):
        tg_id, _ = _forward_id(update)
        if tg_id and not (holat_p(uid) or {}).get("talaba_id"):
            # Chek forward'idan talabani topishga urinamiz
            try:
                talaba = await N.talaba_tgid(tg_id)
                if talaba:
                    holat_saqla(uid, talaba_id=talaba["id"],
                                talaba_nomi=talaba["ism"], forward_tgid=tg_id)
            except Exception:
                pass

    p = holat_p(uid)
    if not p or not p.get("talaba_id"):
        await javob(update,
                    "❗️ Avval talabani aniqlang.\n"
                    "Talaba xabarini forward qiling yoki ismini yozing.")
        return

    kutish = await update.effective_message.reply_text("🧾 Chek o'qilmoqda…")

    # ---- yuklab olish ----
    try:
        tg_fayl = await ctx.bot.get_file(fayl.file_id)
        baytlar = bytes(await tg_fayl.download_as_bytearray())
    except Exception as ex:
        log.exception("Fayl yuklab olinmadi")
        await kutish.edit_text(f"❌ Fayl yuklab olinmadi: <code>{e(ex)}</code>",
                              parse_mode=ParseMode.HTML)
        return

    fayl_unique = getattr(fayl, "file_unique_id", None)
    hash_ = AI.rasm_hash(baytlar, mime)

    # ---- arxiv kanalga zaxira ----
    chek_url = None
    try:
        izoh = f"{p['talaba_nomi']} | {datetime.now(TZ):%d.%m.%Y %H:%M}"
        yub = await ctx.bot.copy_message(
            chat_id=C.ARXIV_CHANNEL_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
            caption=izoh[:1000])
        ichki = str(C.ARXIV_CHANNEL_ID).replace("-100", "", 1)
        chek_url = f"https://t.me/c/{ichki}/{yub.message_id}"
    except Exception as ex:
        log.warning("Arxiv kanalga yuborilmadi: %s", ex)

    # ---- Claude o'qiydi ----
    try:
        d = await AI.chekni_oqi(baytlar, mime)
    except Exception as ex:
        log.exception("Chek o'qilmadi")
        holat_saqla(uid, stage="qolda_summa", chek_url=chek_url, hash=hash_,
                    fayl_unique=fayl_unique, fayl_nomi=nomi, mime=mime, baytlar=baytlar)
        await kutish.edit_text(
            f"❌ Chekdan ma'lumot o'qilmadi.\n<code>{e(ex)}</code>\n\n"
            f"Summani qo'lda yozing (masalan <code>270000</code>) yoki /bekor.",
            parse_mode=ParseMode.HTML)
        return

    holat_saqla(uid, chek=d, chek_url=chek_url, hash=hash_,
                fayl_unique=fayl_unique, fayl_nomi=nomi, mime=mime, baytlar=baytlar,
                stage="tasdiq")
    await kutish.delete()
    await _tasdiq_yoki_saqla(update, uid)


# ============================================================ tasdiq / saqlash
def _yosh(sana_iso):
    if not sana_iso:
        return None
    try:
        d = datetime.fromisoformat(sana_iso[:10]).date()
        return (datetime.now(TZ).date() - d).days
    except Exception:
        return None


async def _tasdiq_yoki_saqla(update: Update, uid: int):
    """
    Chek o'qilgach: kartani va sanani hal qiladi.
    Faqat quyida SO'RAYDI: karta topilmadi, sana yo'q, dublikat.
    Aks holda AVTOMAT saqlaydi.
    """
    p = holat_p(uid)
    if not p or not p.get("chek"):
        await javob(update, "⌛️ Ma'lumot yo'qolgan. Qaytadan boshlang.")
        return
    d = p["chek"]

    ogohlar = []
    shubhali = False

    # --- karta: avval raqam, keyin ism ---
    karta = p.get("karta")
    karta_ism_bilan = p.get("karta_ism_bilan", False)
    karta_hal = p.get("karta_hal", False)
    if karta is None and not karta_hal:
        karta = await N.karta_top(d.get("qabul_karta"))
        if karta is None and d.get("qabul_fio"):
            karta, koplik = await N.karta_top_ism(d["qabul_fio"])
            if karta:
                karta_ism_bilan = True
            elif koplik:
                await _karta_sorash(update, uid, d)
                return
        if karta is None:
            await _karta_sorash(update, uid, d)
            return

    karta_mos = bool(karta)
    if not karta_mos:
        shubhali = True
        ogohlar.append("🚫 Karta ro'yxatda yo'q — noma'lum deb belgilandi.")
    elif karta_ism_bilan:
        shubhali = True
        ogohlar.append(f"ℹ️ Karta ism bo'yicha topildi: <b>{e(karta['nomi'])}</b>.")
    if karta and "biriktirilmagan" in (karta.get("status") or "").lower():
        shubhali = True
        ogohlar.append(f"⚠️ Karta <b>{e(karta['nomi'])}</b> hozir faol emas.")

    # --- muvaffaqiyat ---
    if not d.get("muvaffaqiyatli"):
        shubhali = True
        ogohlar.append("🚫 Chekda tasdiq yo'q — to'lov o'tmagan bo'lishi mumkin!")

    # --- tranzaksiya ID yo'q → to'xtatmaydi, faqat shubhali ---
    if not d.get("tranzaksiya_id"):
        shubhali = True

    # --- sana ---
    yosh = _yosh(d.get("sana"))
    if not d.get("sana"):
        await _sana_sorash(update, uid, d, karta, karta_ism_bilan)
        return
    if yosh is not None and yosh > C.ESKI_CHEK_KUN:
        shubhali = True
        ogohlar.append(f"🚨 Chek {yosh} kunlik! Eski to'lov bo'lishi mumkin.")
        oxirgi = await N.oxirgi_tolov(p["talaba_id"])
        if oxirgi and oxirgi.get("sana"):
            ogohlar.append(f"   Oxirgi to'lovi: {e(oxirgi['sana'][:10])} — "
                           f"{pul(oxirgi.get('summa'))}")

    if d.get("ishonch") == "past":
        shubhali = True

    # --- dublikat → TO'XTAYDI ---
    sabab, eski = await N.dublikat_izla(
        trx=d.get("tranzaksiya_id"), fayl_id=p.get("fayl_unique"),
        hash_=p.get("hash"), summa=d.get("summa"), sana=d.get("sana"),
        karta_id=karta["id"] if karta else None)
    if sabab:
        holat_saqla(uid, karta=karta, karta_mos=karta_mos,
                    karta_ism_bilan=karta_ism_bilan, shubhali=True,
                    yosh=yosh, dublikat={"sabab": sabab, "url": eski.get("url")},
                    ogohlar=ogohlar)
        matn = _chek_matni(p, d, karta)
        matn += (f"\n🚫 <b>TAKRORIY CHEK!</b>\n"
                 f"Sabab: {e(sabab)}\n"
                 f"<a href='{eski.get('url')}'>Eski yozuvni ochish</a>\n")
        if ogohlar:
            matn += "\n" + "\n".join(ogohlar)
        qatorlar = [
            [InlineKeyboardButton("⚠️ Baribir saqlash", callback_data="fsave")],
            [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
        ]
        await javob(update, matn, kb(qatorlar))
        return

    # === Hammasi aniq → AVTOMAT SAQLASH ===
    holat_saqla(uid, karta=karta, karta_mos=karta_mos,
                karta_ism_bilan=karta_ism_bilan, shubhali=shubhali,
                yosh=yosh, dublikat=None, ogohlar=ogohlar)
    await _saqla(update, uid)


def _chek_matni(p, d, karta):
    """Chek xulosasini HTML matn qilib qaytaradi."""
    sana_matn = "—"
    if d.get("sana"):
        sana_matn = d["sana"][:10].replace("-", ".")
        if d.get("vaqt"):
            sana_matn += f" {d['vaqt']}"
    q = [f"🧾 <b>{e(d.get('tolov_tizimi'))}</b>",
         f"💰 <b>{pul(d.get('summa'))}</b> so'm"]
    if d.get("komissiya"):
        q.append(f"   <i>(yuborilgan {pul(d.get('yuborilgan_summa'))}, "
                 f"komissiya {pul(d['komissiya'])})</i>")
    q += [f"📅 {e(sana_matn)}", f"👤 {e(d.get('tolovchi_fio') or '—')}"]
    if d.get("yuboruvchi_karta"):
        q.append(f"💳 Yuboruvchi: •••• {e(d['yuboruvchi_karta'])}")
    q.append(f"🏦 Qabul: •••• {e(d.get('qabul_karta') or '?')}"
             + (f" → <b>{e(karta['nomi'])}</b> ✅" if karta else " ❌"))
    if d.get("tranzaksiya_id"):
        q.append(f"🔖 <code>{e(d['tranzaksiya_id'])}</code>")
    return (f"👤 <b>{e(p['talaba_nomi'])}</b>\n"
            f"━━━━━━━━━━━━━━━\n" + "\n".join(q) + "\n━━━━━━━━━━━━━━━\n")


async def _karta_sorash(update: Update, uid: int, d: dict):
    kartalar = await N.kartalar()
    holat_saqla(uid, stage="karta_tanlash", karta_royxat=kartalar)
    qatorlar = [[InlineKeyboardButton(f"{k['nomi']} · {k['l4'] or '—'}",
                                      callback_data=f"k:{i}")]
                for i, k in enumerate(kartalar)]
    qatorlar.append([InlineKeyboardButton("➖ Karta yo'q / noma'lum",
                                          callback_data="k:none")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
    fio = d.get("qabul_fio") or "—"
    raqam = d.get("qabul_karta") or "—"
    await javob(update,
                f"❓ <b>Qabul qiluvchi karta topilmadi</b>\n"
                f"Chekda: <b>{e(fio)}</b> · •••• {e(raqam)}\n\n"
                f"Qaysi karta?", kb(qatorlar))


async def _sana_sorash(update: Update, uid: int, d, karta, karta_ism_bilan):
    holat_saqla(uid, stage="sana_kutish", karta=karta,
                karta_ism_bilan=karta_ism_bilan, karta_hal=True)
    qatorlar = [
        [InlineKeyboardButton("📅 Bugun", callback_data="d:t"),
         InlineKeyboardButton("📅 Kecha", callback_data="d:y")],
        [InlineKeyboardButton("✏️ Sanani yozish", callback_data="d:m")],
        [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
    ]
    km = f"→ {e(karta['nomi'])}" if karta else ""
    await javob(update,
                f"🧾 {pul(d.get('summa'))} so'm · {e(d.get('tolov_tizimi'))} {km}\n\n"
                f"⚠️ Chekda sana yo'q.\n<b>To'lov qachon bo'lgan?</b>",
                kb(qatorlar))


async def _saqla(update: Update, uid: int):
    p = holat_p(uid)
    if not p or not p.get("chek"):
        await javob(update, "⌛️ Ma'lumot yo'qolgan. Qaytadan boshlang.")
        return
    d = p["chek"]
    karta = p.get("karta")

    await javob(update, "💾 Notion'ga yozilmoqda…")

    # Telegram ID ni yozib qo'yish (bo'sh bo'lsa)
    yozilgan_id = None
    if p.get("forward_tgid"):
        try:
            if await N.tgid_yoz(p["talaba_id"], p["forward_tgid"]):
                yozilgan_id = p["forward_tgid"]
        except Exception:
            pass

    # Notion'ga fayl yuklash
    upload_id = None
    if p.get("baytlar"):
        upload_id = await N.fayl_yukla(p.get("fayl_nomi") or "chek",
                                       p.get("mime") or "image/jpeg", p["baytlar"])

    sana_iso = d.get("sana")
    if sana_iso and d.get("vaqt"):
        sana_iso = f"{sana_iso[:10]}T{d['vaqt']}:00+05:00"

    izohlar = []
    if not d.get("tranzaksiya_id"):
        izohlar.append("Tranzaksiya ID yo'q")
    if p.get("karta_ism_bilan") and p.get("karta_mos"):
        izohlar.append("Karta ism bo'yicha topildi")
    if not p.get("karta_mos"):
        izohlar.append(f"Karta ro'yxatda yo'q: {d.get('qabul_karta')}")
    if d.get("izoh"):
        izohlar.append(d["izoh"])
    if d.get("komissiya"):
        izohlar.append(f"Komissiya {d['komissiya']}")
    if d.get("qabul_fio"):
        izohlar.append(f"Qabul: {d['qabul_fio']}")
    if p.get("izlangan_guruh"):
        izohlar.append(f"Guruh (izlashda): {p['izlangan_guruh']}")

    sana_qisqa = (sana_iso or "")[:10].replace("-", ".") or "sanasiz"
    nomi = f"{p['talaba_nomi']} — {pul(d.get('summa'))} — {sana_qisqa}"

    try:
        sahifa = await N.tolov_yozuv({
            "nomi": nomi,
            "talaba_id": p["talaba_id"],
            "summa": d.get("summa"),
            "sana": sana_iso,
            "fio": d.get("tolovchi_fio"),
            "karta_id": karta["id"] if karta else None,
            "karta_mos": p.get("karta_mos"),
            "tizim": d.get("tolov_tizimi"),
            "trx": d.get("tranzaksiya_id"),
            "chek_url": p.get("chek_url"),
            "tekshir_url": d.get("tekshirish_havolasi"),
            "yosh": p.get("yosh"),
            "shubhali": p.get("shubhali"),
            "fayl_id": p.get("fayl_unique"),
            "hash": p.get("hash"),
            "izoh": " | ".join(izohlar) or None,
            "yub_karta": d.get("yuboruvchi_karta"),
            "file_upload_id": upload_id,
            "fayl_nomi": p.get("fayl_nomi"),
        })
    except Exception as ex:
        log.exception("Notion'ga yozilmadi")
        await javob(update, f"❌ Saqlanmadi: <code>{e(ex)}</code>\n\n"
                            f"Chekni qayta yuboring.")
        return

    PENDING.pop(uid, None)

    fayl_holati = ("📎 Chek Notion'ga yuklandi" if upload_id else
                   ("🔗 Chek arxiv kanalda" if p.get("chek_url") else
                    "⚠️ Chek saqlanmadi"))
    xabar = (f"✅ <b>To'lov qabul qilindi</b>\n\n"
             f"👤 {e(p['talaba_nomi'])}\n"
             f"💰 {pul(d.get('summa'))} so'm\n"
             f"{fayl_holati}\n")
    if yozilgan_id:
        xabar += f"📝 Telegram ID saqlandi: <code>{yozilgan_id}</code>\n"
    if p.get("shubhali"):
        xabar += "🏷 Shubhali deb belgilandi\n"
    xabar += f"\n<a href='{sahifa.get('url')}'>Notion'da ochish</a>"
    await javob(update, xabar)


# ============================================================ matn handler
def _sana_ajrat(matn: str):
    import re
    matn = matn.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", matn)
    if m:
        y, mo, dd = m.groups()
    else:
        m = re.match(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", matn)
        if not m:
            return None
        dd, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
    try:
        d = datetime(int(y), int(mo), int(dd))
    except ValueError:
        return None
    if d.date() > datetime.now(TZ).date():
        return None
    return d.date().isoformat()


async def matn_qabul(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    matn = (update.effective_message.text or "").strip()
    p = holat_p(uid)
    stage = (p or {}).get("stage")

    # Ism kutilmoqda (forward ID yo'q edi yoki yangi ID)
    if stage in ("ism_kutish", "talaba_tanlash"):
        await _ism_qabul(update, uid, matn)
        return

    # Sana qo'lda
    if stage == "sana_kutish":
        iso = _sana_ajrat(matn)
        if not iso:
            await javob(update, "❌ Sanani <code>25.07.2026</code> shaklida yozing.")
            return
        p["chek"]["sana"] = iso
        p["chek"]["izoh"] = ((p["chek"].get("izoh") or "") +
                             " | sana qo'lda kiritildi").strip(" |")
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_yoki_saqla(update, uid)
        return

    # Summa qo'lda (chek o'qilmagan)
    if stage == "qolda_summa":
        import re
        raqam = "".join(ch for ch in matn if ch.isdigit())
        if not raqam:
            await javob(update, "❌ Faqat raqam yozing, masalan <code>270000</code>.")
            return
        holat_saqla(uid, stage="tasdiq", chek={
            "tolov_tizimi": "Boshqa", "tranzaksiya_id": None,
            "sana": datetime.now(TZ).date().isoformat(), "vaqt": None,
            "summa": int(raqam), "yuborilgan_summa": None, "komissiya": None,
            "yuboruvchi_karta": None, "tolovchi_fio": None,
            "qabul_karta": None, "qabul_fio": None,
            "tekshirish_havolasi": None, "muvaffaqiyatli": True,
            "ishonch": "past", "izoh": "Chek o'qilmadi, summa qo'lda kiritildi",
        })
        await _tasdiq_yoki_saqla(update, uid)
        return

    # Hech qanday holat yo'q — talaba ismi deb qabul qilamiz
    holat_saqla(uid, stage="ism_kutish")
    await _ism_qabul(update, uid, matn)


# ============================================================ callback
async def callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    data = q.data or ""
    p = holat_p(uid)

    if data == "cancel":
        PENDING.pop(uid, None)
        await javob(update, "❌ Bekor qilindi.")
        return

    if not p:
        await javob(update, "⌛️ Muddat tugadi. Qaytadan boshlang.")
        return

    # Talaba tanlash
    if data.startswith("t:"):
        i = int(data[2:])
        taxmin = p.get("talaba_taxmin") or []
        if i >= len(taxmin):
            await javob(update, "❌ Ro'yxat eskirgan. Qaytadan yuboring.")
            return
        t = taxmin[i]
        # Telegram ID ni yozish uchun forward_tgid saqlanadi (agar bor bo'lsa)
        await _talaba_tanlandi(update, uid, {"id": t["id"], "ism": t["ism"]})
        return

    if data == "tlist":
        await _talaba_royxat(update, uid, p.get("talaba_taxmin") or [])
        return

    if data == "tm":
        holat_saqla(uid, stage="ism_kutish")
        await javob(update, "🔍 Talaba ismini (va guruhini) yozing:")
        return

    # Karta tanlash
    if data.startswith("k:"):
        tanlov = data[2:]
        if tanlov == "none":
            holat_saqla(uid, karta=None, karta_mos=False,
                        karta_ism_bilan=False, karta_hal=True, stage="tasdiq")
        else:
            i = int(tanlov)
            royxat = p.get("karta_royxat") or []
            if i >= len(royxat):
                await javob(update, "❌ Ro'yxat eskirgan.")
                return
            holat_saqla(uid, karta=royxat[i], karta_mos=True,
                        karta_ism_bilan=True, karta_hal=True, stage="tasdiq")
        await _tasdiq_yoki_saqla(update, uid)
        return

    # Sana
    if data.startswith("d:"):
        turi = data[2:]
        if turi == "m":
            holat_saqla(uid, stage="sana_kutish")
            await javob(update, "📅 Sanani yozing: <code>25.07.2026</code>")
            return
        kun = datetime.now(TZ).date() - (timedelta(days=1) if turi == "y"
                                         else timedelta())
        p["chek"]["sana"] = kun.isoformat()
        p["chek"]["izoh"] = ((p["chek"].get("izoh") or "") +
                             " | sana qo'lda kiritildi").strip(" |")
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_yoki_saqla(update, uid)
        return

    # Saqlash (dublikat holati)
    if data == "fsave":
        holat_saqla(uid, shubhali=True)
        await _saqla(update, uid)
        return


# ============================================================ xato
async def xato_handler(update, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Kutilmagan xato", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Kutilmagan xato. /bekor bosib qaytadan urinib ko'ring.")
    except Exception:
        pass


# ============================================================ ishga tushirish
def main():
    app = Application.builder().token(C.TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bekor", bekor))
    app.add_handler(CommandHandler("kesh", kesh_yangila))

    # Forward qilingan xabar (matn/rasm/hujjat) — talabani topish
    app.add_handler(MessageHandler(
        filters.FORWARDED & ~(filters.PHOTO | filters.Document.ALL),
        _forward_qabul))
    # Chek (rasm yoki PDF)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, chek_qabul))
    # Oddiy matn
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, matn_qabul))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_error_handler(xato_handler)

    log.info("Chek boti (yangi CRM) ishga tushdi. Adminlar: %s",
             C.ADMIN_IDS or "hammaga ochiq")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
