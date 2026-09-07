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
import time
from datetime import date, datetime, timedelta, timezone

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup,
                      KeyboardButton, ReplyKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config as C
import notion_api as N
import receipt_ai as AI
import eslatma as ES
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


# Ekran pastida doim turadigan klaviatura
TUGMA_QARZDOR = "📋 Qarzdorlar"
TUGMA_BUGUN = "📊 Bugun"
TUGMA_NOL = "⚪ Balansi 0"
TUGMA_ESLATMA = "⏳ 1 dars qoldi"
ASOSIY_KB = ReplyKeyboardMarkup(
    [[KeyboardButton(TUGMA_QARZDOR), KeyboardButton(TUGMA_BUGUN)],
     [KeyboardButton(TUGMA_NOL), KeyboardButton(TUGMA_ESLATMA)]],
    resize_keyboard=True, is_persistent=True,
    input_field_placeholder="Chek yuboring yoki tugmani bosing")


async def javob(update: Update, matn, keyboard=None):
    if update.callback_query:
        await update.callback_query.edit_message_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)
    else:
        await update.effective_message.reply_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)


async def javob_yangi(update: Update, matn, keyboard=None):
    """Har doim YANGI xabar yuboradi (mavjudini tahrirlamaydi).
    Ro'yxat ochiq turishi kerak bo'lgan joylarda ishlatiladi."""
    await update.effective_message.reply_text(
        matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
        disable_web_page_preview=True)


def _forward_id(update: Update):
    """Forward qilingan xabardan asl yuboruvchining Telegram ID sini oladi."""
    m = update.effective_message
    fo = getattr(m, "forward_origin", None)
    if fo is not None:
        # Yangi Telegram API — forward_origin
        turi = type(fo).__name__
        user = getattr(fo, "sender_user", None)
        if user is not None:
            log.info("Forward origin=%s, user_id=%s", turi, user.id)
            return user.id, getattr(user, "full_name", None)
        # HiddenUser — foydalanuvchi maxfiylikni yopgan
        nomi = getattr(fo, "sender_user_name", None)
        log.info("Forward origin=%s (ID yashirin), nomi=%s", turi, nomi)
        return None, nomi
    # Eski API — forward_from
    ff = getattr(m, "forward_from", None)
    if ff is not None:
        log.info("Forward_from user_id=%s", ff.id)
        return ff.id, getattr(ff, "full_name", None)
    log.info("Forward ma'lumoti yo'q (forward_origin va forward_from bo'sh)")
    return None, None


async def _kod_dan_talaba(matn: str):
    """Matn ichidagi yashirin kod (#Txxxxxx) orqali talabani topadi.
    Avval xotiradan (tez), topilmasa Notiondan qidiradi (bot qayta ishga
    tushgan bo'lsa ham ishlaydi)."""
    if not matn:
        return None
    malumot = ES.kod_dan_malumot(matn)
    if not malumot:
        m = ES.KOD_RE.search(matn)
        if m:
            try:
                malumot = await N.yozilish_kod_orqali(m.group(0))
            except Exception:
                log.exception("Kod orqali Notiondan qidirishda xato")
                malumot = None
    if not malumot:
        return None
    return {"id": malumot["talaba_id"], "ism": malumot["talaba_ism"]}


# ============================================================ /start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        "👋 <b>Chek boti</b> (yangi CRM)\n\n"
        "1️⃣ Chekni yuboring (rasm yoki PDF)\n"
        "2️⃣ Bot talabani o'zi izlaydi — tanlaysiz\n"
        "3️⃣ Necha talabaga bo'linishini belgilaysiz\n"
        "4️⃣ To'lov bazaga yoziladi\n\n"
        "📋 <b>Qarzdorlar</b> · ⚪ <b>Balansi 0</b> · ⏳ <b>1 dars qoldi</b> — "
        "ro'yxatlar, raqamni bosib eslatma matnini olasiz.\n"
        "📊 <b>Bugun</b> — kunlik hisobot.\n\n"
        "<code>/bekor</code> — bekor qilish\n"
        "<code>/kesh</code> — ro'yxatlarni yangilash\n\n"
        f"🔔 Har kuni {C.HISOBOT_SOATI:02d}:00 da kunlik hisobot keladi.\n\n"
        f"Sizning ID: <code>{uid}</code>",
        parse_mode=ParseMode.HTML, reply_markup=ASOSIY_KB)


async def bekor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    PENDING.pop(update.effective_user.id, None)
    await javob(update, "🧹 Bekor qilindi.")


async def balans_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """SINOV: /balans <ism> — balansni 4 xil usul bilan o'qib ko'rsatadi.
    Qaysi usul Notiondagi haqiqiy qiymatga mos kelishini aniqlash uchun."""
    if not admin_mi(update):
        return
    matn = (update.effective_message.text or "").split(maxsplit=1)
    if len(matn) < 2:
        await javob(update, "Ishlatish: <code>/balans Ra'no Fozilxon</code>")
        return
    ism = matn[1].strip()

    kutish = await update.effective_message.reply_text("🔍 Tekshirilmoqda…")
    try:
        aniq, taxminlar = await N.talaba_izla(ism)
        talaba = aniq or (taxminlar[0] if taxminlar else None)
        if not talaba:
            await kutish.edit_text("❌ Talaba topilmadi.")
            return
        natija = await N.balans_tekshir(talaba["id"], talaba["ism"])
    except Exception as ex:
        log.exception("Balans tekshiruvi")
        await kutish.edit_text(f"❌ Xato: <code>{e(ex)}</code>",
                               parse_mode=ParseMode.HTML)
        return

    qatorlar = [f"<b>{e(talaba['ism'])}</b>", ""]
    for kalit, qiymat in natija.items():
        korinish = pul(qiymat) if isinstance(qiymat, (int, float)) else str(qiymat)
        qatorlar.append(f"{e(kalit)}: <b>{e(korinish)}</b>")
    qatorlar.append("")
    qatorlar.append("<i>Notiondagi haqiqiy qiymat bilan solishtiring.</i>")
    await kutish.edit_text("\n".join(qatorlar), parse_mode=ParseMode.HTML)


async def kesh_yangila(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    N.keshni_tozala()
    g = await N.guruhlar_map()
    k = await N.kartalar()
    await javob(update, f"♻️ Yangilandi: {len(g)} guruh, {len(k)} karta.")


# ============================================================ QARZDORLAR
# {uid: {"royxat": [...], "sarlavha": str, "offset": int}}
QARZDOR_HOLAT: dict = {}


def _kun_farqi(sana_iso):
    """Berilgan sanadan bugungacha necha kun (None → None)."""
    if not sana_iso:
        return None
    try:
        return (datetime.now(TZ).date() -
                date.fromisoformat(sana_iso[:10])).days
    except Exception:
        return None


def _qarz_rang(kun):
    if kun is None:
        return "\u26aa"
    for chegara, belgi in C.QARZDOR_RANG:
        if kun <= chegara:
            return belgi
    return "\U0001f534"


def _eslatma_matn(kun):
    if kun is None:
        return "eslatilmagan"
    if kun == 0:
        return "bugun eslatilgan"
    if kun == 1:
        return "kecha eslatilgan"
    return f"{kun} kun oldin eslatilgan"


async def _qarzdorlar_yigish(tur="qarzdor"):
    """Ro'yxatni yig'adi. tur: 'qarzdor' (balans<0) | 'nol' (balans=0)
    | 'eslatma' ('Eslatma kerak' ✓).
    Har talaba BIR marta, guruhlari ro'yxat bilan. Yozilishlar bitta
    so'rovda olinadi (har talaba uchun alohida so'rov emas)."""
    if tur == "nol":
        talabalar = await N.talabalar_nol_balans()
    elif tur == "eslatma":
        talabalar = await N.talabalar_eslatma_toliq()
    else:
        talabalar = await N.talabalar_qarzdor()

    barcha_yoz = await N.yozilishlar_hammasi()
    natija = []
    for t in talabalar:
        yozilishlar = barcha_yoz.get(t["id"]) or []
        if not yozilishlar:
            continue
        natija.append({
            "talaba_id": t["id"],
            "talaba_ism": t["ism"],
            "balans": t.get("balans"),
            "qarz_kun": _kun_farqi(t.get("qarz_sana")),
            "eslatma_kun": _kun_farqi(t.get("eslatma_sana")),
            "yozilishlar": yozilishlar,
        })
    # Hech eslatilmaganlar birinchi, keyin eng uzoq eslatilmaganlar
    natija.sort(key=lambda x: (x["eslatma_kun"] is not None,
                               -(x["eslatma_kun"] or 0)))
    return natija


def _qarzdor_qator(nom, item, guruh_filtr=None, tur="qarzdor"):
    """Ro'yxatdagi bitta talaba qatorini matn qilib yasaydi."""
    yoz = item["yozilishlar"]
    if guruh_filtr:
        guruh_matn = ""
        # Filtrlangan ro'yxatda balans umumiy ekanini belgilab qo'yamiz
        qoshimcha = f" (umumiy, {len(yoz)} guruh)" if len(yoz) > 1 else ""
    else:
        guruh_matn = ", ".join(y["guruh_nomi"] for y in yoz if y["guruh_nomi"])
        qoshimcha = ""

    satr = f"<b>{nom}. {e(item['talaba_ism'])}</b>\n"
    if guruh_matn:
        satr += f"   {e(guruh_matn)} · {pul(item['balans'])} so'm{qoshimcha}\n"
    else:
        satr += f"   {pul(item['balans'])} so'm{qoshimcha}\n"

    # Ikkinchi qator turga qarab
    if tur == "qarzdor":
        qarz_kun = item["qarz_kun"]
        holat = ("yangi qarzdor" if qarz_kun is None
                 else ("bugun qarzga tushdi" if qarz_kun == 0
                       else f"{qarz_kun} kun qarzda"))
        belgi = _qarz_rang(qarz_kun)
    elif tur == "nol":
        holat = "to'lovi tugagan"
        belgi = "⚪"
    else:
        holat = "1 darsga puli qolgan"
        belgi = "⏳"

    satr += f"   {belgi} {holat} · {_eslatma_matn(item['eslatma_kun'])}"
    return satr


async def _qarzdorlar_korsat(update: Update, uid: int, offset: int = 0):
    h = QARZDOR_HOLAT.get(uid) or {}
    royxat = h.get("royxat") or []
    sarlavha = h.get("sarlavha") or "Barcha qarzdorlar"
    guruh_filtr = h.get("guruh_filtr")
    tur = h.get("tur") or "qarzdor"
    jami = len(royxat)
    if not jami:
        await javob(update, "\u2705 Hech kim topilmadi.")
        return

    h["offset"] = offset
    QARZDOR_HOLAT[uid] = h
    n = C.QARZDOR_SAHIFA
    sahifa = royxat[offset:offset + n]

    satrlar = [_qarzdor_qator(offset + i + 1, item, guruh_filtr, tur)
               for i, item in enumerate(sahifa)]
    matn = (f"\U0001f4cb <b>{e(sarlavha)}</b> ({jami} ta) · "
            f"{offset + 1}-{offset + len(sahifa)}\n\n" + "\n\n".join(satrlar))

    # Raqamli tugmalar — 5 tadan ikki qator
    qatorlar = []
    raqamlar = [InlineKeyboardButton(str(offset + i + 1), callback_data=f"qz:{offset + i}")
                for i in range(len(sahifa))]
    for i in range(0, len(raqamlar), 5):
        qatorlar.append(raqamlar[i:i + 5])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("\u2b05\ufe0f Oldingi",
                                        callback_data=f"qzp:{max(offset - n, 0)}"))
    if offset + n < jami:
        nav.append(InlineKeyboardButton(f"\u27a1\ufe0f Keyingi {min(n, jami - offset - n)} ta",
                                        callback_data=f"qzp:{offset + n}"))
    if nav:
        qatorlar.append(nav)
    if tur == "qarzdor":
        qatorlar.append([InlineKeyboardButton("\U0001f504 Yangilash / filtr",
                                              callback_data="qzf")])
    else:
        qatorlar.append([InlineKeyboardButton("\U0001f504 Yangilash",
                                              callback_data=f"qzr:{tur}")])
    await javob(update, matn, kb(qatorlar))


async def qarzdorlar_filtr_sorash(update: Update, uid: int):
    """Qarzdorlar tugmasi bosilganda — filtr tanlash.
    Har safar keshni tozalaymiz: aks holda to'lov qilgan talaba eski
    ro'yxatda qarzdor bo'lib qolaverardi."""
    QARZDOR_HOLAT.pop(uid, None)
    qatorlar = [
        [InlineKeyboardButton("\U0001f4ca Hammasi", callback_data="qzall")],
        [InlineKeyboardButton("\U0001f9d1\u200d\U0001f3eb Ustoz bo'yicha", callback_data="qzu")],
        [InlineKeyboardButton("\U0001f6aa Guruh bo'yicha", callback_data="qzg")],
    ]
    await javob(update, "\U0001f4cb <b>Qarzdorlar</b>\n\nQanday ko'rsatay?", kb(qatorlar))


async def _qarzdorlar_yukla(update: Update, uid: int, tur="qarzdor"):
    """Ro'yxatni Notiondan oladi va keshlaydi."""
    kutish = await update.effective_message.reply_text("\U0001f50d Izlanmoqda\u2026")
    try:
        royxat = await _qarzdorlar_yigish(tur)
    except Exception as ex:
        log.exception("Ro'yxat olinmadi (tur=%s)", tur)
        await kutish.edit_text(f"\u274c Notion xatosi: <code>{e(ex)}</code>",
                               parse_mode=ParseMode.HTML)
        return None
    await kutish.delete()
    QARZDOR_HOLAT[uid] = {"hammasi": royxat, "tur": tur, "vaqt": time.time()}
    return royxat


def _kesh_yaroqli(h):
    """Ro'yxat keshi hali yangimi? (C.QARZDOR_KESH_TTL soniya)"""
    vaqt = (h or {}).get("vaqt")
    return bool(vaqt) and (time.time() - vaqt) < C.QARZDOR_KESH_TTL


async def qarzdorlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    await qarzdorlar_filtr_sorash(update, uid)


async def _oddiy_royxat(update: Update, uid: int, tur: str):
    """Filtrsiz ro'yxat (balansi 0 / 1 dars qoldi) — har safar yangilanadi."""
    QARZDOR_HOLAT.pop(uid, None)
    royxat = await _qarzdorlar_yukla(update, uid, tur)
    if royxat is None:
        return
    sarlavha = ("Balansi 0 bo'lganlar" if tur == "nol"
                else "1 darsga puli qolganlar")
    h = QARZDOR_HOLAT[uid]
    h.update({"royxat": royxat, "sarlavha": sarlavha,
              "guruh_filtr": None, "tur": tur})
    QARZDOR_HOLAT[uid] = h
    await _qarzdorlar_korsat(update, uid, 0)


async def _qarzdor_filtr_royxati(update: Update, uid: int, tur: str):
    """Ustoz yoki guruh ro'yxatini chiqaradi (faqat qarzdori borlari)."""
    h = QARZDOR_HOLAT.get(uid) or {}
    hammasi = h.get("hammasi") if _kesh_yaroqli(h) else None
    if hammasi is None:
        hammasi = await _qarzdorlar_yukla(update, uid)
        if hammasi is None:
            return
        h = QARZDOR_HOLAT[uid]

    sanoq = {}
    for item in hammasi:
        for y in item["yozilishlar"]:
            kalit = (y.get("ustoz") or "").strip() if tur == "ustoz" else \
                    (y.get("guruh_nomi") or "").strip()
            if not kalit:
                continue
            sanoq.setdefault(kalit, set()).add(item["talaba_id"])

    if not sanoq:
        await javob(update, "\u2705 Qarzdor topilmadi.")
        return

    tartib = sorted(sanoq.items(), key=lambda kv: -len(kv[1]))
    h["filtr_kalitlar"] = [k for k, _ in tartib]
    QARZDOR_HOLAT[uid] = h

    qatorlar = [[InlineKeyboardButton(f"{k} ({len(v)})"[:64],
                                      callback_data=f"qzs:{tur[0]}:{i}")]
                for i, (k, v) in enumerate(tartib)]
    qatorlar.append([InlineKeyboardButton("\u2b05\ufe0f Orqaga", callback_data="qzf")])
    nom = "Ustoz" if tur == "ustoz" else "Guruh"
    await javob(update, f"\U0001f4cb <b>{nom} tanlang</b>", kb(qatorlar))


async def _qarzdor_filtrla(update: Update, uid: int, tur: str, idx: int):
    """Tanlangan ustoz/guruh bo'yicha ro'yxatni tayyorlaydi."""
    h = QARZDOR_HOLAT.get(uid) or {}
    kalitlar = h.get("filtr_kalitlar") or []
    if idx >= len(kalitlar):
        await javob(update, "\u23f3 Ro'yxat eskirgan. Qaytadan boshlang.")
        return
    kalit = kalitlar[idx]
    hammasi = h.get("hammasi") or []

    filtrlangan = []
    for item in hammasi:
        mos = [y for y in item["yozilishlar"]
               if ((y.get("ustoz") or "").strip() == kalit if tur == "ustoz"
                   else (y.get("guruh_nomi") or "").strip() == kalit)]
        if mos:
            yangi = dict(item)
            yangi["mos_yozilishlar"] = mos
            filtrlangan.append(yangi)

    h.update({"royxat": filtrlangan, "sarlavha": f"{kalit} qarzdorlari",
              "guruh_filtr": True, "tur": "qarzdor"})
    QARZDOR_HOLAT[uid] = h
    await _qarzdorlar_korsat(update, uid, 0)


async def _qarzdor_hammasi(update: Update, uid: int):
    h = QARZDOR_HOLAT.get(uid) or {}
    hammasi = h.get("hammasi") if _kesh_yaroqli(h) else None
    if hammasi is None:
        hammasi = await _qarzdorlar_yukla(update, uid)
        if hammasi is None:
            return
        h = QARZDOR_HOLAT[uid]
    h.update({"royxat": hammasi, "sarlavha": "Barcha qarzdorlar",
              "guruh_filtr": None, "tur": "qarzdor"})
    QARZDOR_HOLAT[uid] = h
    await _qarzdorlar_korsat(update, uid, 0)


async def _qarzdor_tanlandi(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                            uid: int, idx: int):
    """Raqam bosilgan — turga mos matn(lar)ni chiqaradi va sanani yozadi.
    Talaba ham tanlanadi: shundan keyin chek yuborilsa, o'shanga yoziladi."""
    h = QARZDOR_HOLAT.get(uid) or {}
    royxat = h.get("royxat") or []
    tur = h.get("tur") or "qarzdor"
    if idx >= len(royxat):
        await javob(update, "\u23f3 Ro'yxat eskirgan. Qaytadan boshlang.")
        return
    item = royxat[idx]
    yozilishlar = item.get("mos_yozilishlar") or item["yozilishlar"]

    for y in yozilishlar:
        kod = ES.kod_yasash(y["yozilish_id"], item["talaba_id"],
                            item["talaba_ism"], y["guruh_nomi"])
        matn = await _royxat_matni(tur, item, y, kod)
        await ctx.bot.send_message(update.effective_chat.id, matn,
                                   parse_mode=ParseMode.HTML)

    # Oxirgi eslatma sanasini yozamiz
    await N.eslatma_sana_yoz(item["talaba_id"])
    # Xotiradagi ro'yxatni ham yangilaymiz (qayta yuklamasdan)
    item["eslatma_kun"] = 0

    # Talabani tanlangan qilib qo'yamiz — chek shu talabaga yoziladi.
    # Suhbatga xabar yozmaymiz: forward qilishga xalaqit beradi.
    holat_saqla(uid, talaba_id=item["talaba_id"],
                talaba_nomi=item["talaba_ism"], stage="chek_kutish")


async def _royxat_matni(tur, item, y, kod):
    """Ro'yxat turiga mos xabar matnini yasaydi."""
    if tur == "qarzdor":
        return ES.qarzdor_matn(item["talaba_ism"], y["guruh_nomi"],
                               y["tolov"], kod)

    if tur == "nol":
        # Yangi oyning 1-darsi: oylik guruhda — boshlagan sanadan,
        # darsbay guruhda — Davomat + dars kunlaridan
        if y.get("oylikmi"):
            keyingi = ES.oylik_keyingi_dars(y.get("boshlagan_sana"))
        else:
            try:
                keyingi = await N.keyingi_dars_sanasi(y["yozilish_id"],
                                                      y.get("dars_kunlari"))
            except Exception:
                log.exception("Keyingi dars sanasi topilmadi")
                keyingi = None
        return ES.nol_balans_matn(item["talaba_ism"], y["guruh_nomi"],
                                  y["tolov"], keyingi, kod)

    # tur == "eslatma" — 1 darsga puli qolganlar
    if y.get("oylikmi"):
        return ES.eslatma_matn_oylik(item["talaba_ism"], y["guruh_nomi"],
                                     y["tolov"], y.get("boshlagan_sana"), kod)
    try:
        darslar = await N.yozilish_darslari(y["yozilish_id"], y.get("chastota"),
                                            y.get("dars_kunlari"))
    except Exception:
        log.exception("Darslar ro'yxati olinmadi")
        darslar = []
    return ES.eslatma_matn_darsbay(item["talaba_ism"], y["guruh_nomi"],
                                   y["tolov"], darslar, kod)


async def _uzun_yubor(bot, chat_id, matn, chegara=3800):
    """Uzun xabarni bo'lib yuboradi (Telegram chegarasi 4096 belgi).
    Qatorlar bo'yicha bo'ladi — HTML teglari buzilmasligi uchun."""
    qatorlar = matn.split("\n")
    bolak, uzunlik = [], 0
    for q in qatorlar:
        if uzunlik + len(q) + 1 > chegara and bolak:
            await bot.send_message(chat_id, "\n".join(bolak),
                                   parse_mode=ParseMode.HTML)
            bolak, uzunlik = [], 0
        bolak.append(q)
        uzunlik += len(q) + 1
    if bolak:
        await bot.send_message(chat_id, "\n".join(bolak),
                               parse_mode=ParseMode.HTML)


# ============================================================ KUNLIK HISOBOT
async def _hisobot_yasash():
    """Kunlik hisobot matnini yasaydi.
    Avval sanalarni tekshirib/tozalab chiqadi, keyin sonlar va 'bugun
    holatga tushganlar' ro'yxatini yig'adi."""
    bugun = datetime.now(TZ).date()
    bugun_iso = bugun.isoformat()

    # 1) Sanalarni tekshirib chiqamiz (yangilariga yozadi, chiqqanlarnikini o'chiradi)
    try:
        await N.sanalarni_tekshir(bugun_iso)
    except Exception:
        log.exception("Sanalarni tekshirishda xato")

    # 2) Sonlar
    sonlar = {}
    for kalit, olish in (("qarzdor", N.talabalar_qarzdor),
                          ("nol", N.talabalar_nol_balans),
                          ("eslatma", N.talabalar_eslatma_toliq)):
        try:
            sonlar[kalit] = len(await olish())
        except Exception:
            log.exception("Son olinmadi: %s", kalit)
            sonlar[kalit] = None

    def son(k):
        return f"{sonlar[k]} ta" if sonlar.get(k) is not None else "—"

    qatorlar = [
        f"\U0001f4ca <b>{bugun.strftime('%d/%m')} — Kunlik hisobot</b>",
        "",
        f"\U0001f534 Qarzdorlar: <b>{son('qarzdor')}</b>",
        f"\u26aa Balansi tugaganlar: <b>{son('nol')}</b>",
        f"\u23f3 1 dars qolganlar: <b>{son('eslatma')}</b>",
    ]

    # 3) Bugun holatga tushganlar
    gmap = await N.guruhlar_map()
    barcha_yoz = await N.yozilishlar_hammasi()

    bolimlar = (
        ("\U0001f195 Bugun qarzga kirdi", C.P_TALABA_QARZ_SANA),
        ("\U0001f195 Balansi bugun tugadi", C.P_TALABA_NOL_SANA),
        ("\U0001f195 Bugun 1 dars qoldi", C.P_TALABA_1DARS_SANA),
    )
    for sarlavha, maydon in bolimlar:
        try:
            talabalar = await N.talabalar_sana_boyicha(maydon, bugun_iso)
        except Exception:
            log.exception("Bugungi ro'yxat olinmadi: %s", maydon)
            continue
        if not talabalar:
            continue
        satrlar = []
        for t in talabalar:
            yozilishlar = barcha_yoz.get(t["id"]) or []
            if not yozilishlar:
                satrlar.append(f"- {e(t['ism'])}")
                continue
            # Har guruh alohida qator
            for y in yozilishlar:
                ustoz = (y.get("ustoz") or "").strip() or "—"
                satrlar.append(
                    f"- {e(t['ism'])} · {e(y['guruh_nomi'])} · {e(ustoz)}")
        qatorlar.append("\u2501" * 15)
        qatorlar.append(f"{sarlavha} ({len(talabalar)})")
        qatorlar.extend(satrlar)

    return "\n".join(qatorlar)


async def hisobot_yubor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tugma bosilganda — bugungi hisobot."""
    if not admin_mi(update):
        return
    kutish = await update.effective_message.reply_text("\U0001f4ca Hisobot tayyorlanmoqda\u2026")
    try:
        matn = await _hisobot_yasash()
    except Exception as ex:
        log.exception("Hisobot yasalmadi")
        await kutish.edit_text(f"\u274c Xato: <code>{e(ex)}</code>",
                               parse_mode=ParseMode.HTML)
        return
    try:
        await kutish.delete()
    except Exception:
        pass
    await _uzun_yubor(ctx.bot, update.effective_chat.id, matn)


async def kunlik_hisobot(ctx: ContextTypes.DEFAULT_TYPE):
    """Har kuni C.HISOBOT_SOATI da (Toshkent) adminlarga yuboriladi.
    O'zgarish bo'lmasa ham sonlar keladi."""
    if not C.ADMIN_IDS:
        log.warning("ADMIN_IDS bo'sh — kunlik hisobot yuborilmadi.")
        return
    try:
        matn = await _hisobot_yasash()
    except Exception:
        log.exception("Kunlik hisobot yasalmadi")
        return
    for admin_id in C.ADMIN_IDS:
        try:
            await _uzun_yubor(ctx.bot, admin_id, matn)
        except Exception:
            log.exception("Kunlik hisobot yuborilmadi: admin=%s", admin_id)


# ============================================================ talaba topish
async def _talaba_tanlandi(update: Update, uid: int, talaba: dict,
                          yozilgan_id=None):
    """Talaba aniqlangach — chekni kutish holatiga o'tadi."""
    p = holat_saqla(uid, talaba_id=talaba["id"], talaba_nomi=talaba["ism"])

    # Telegram ID ni DARROV yozamiz (forward'dan kelgan va bazada bo'sh bo'lsa).
    # Bu yerda yozsak, keyin holat almashsa ham ID yo'qolmaydi.
    if p.get("forward_tgid") and not p.get("tgid_yozildi"):
        try:
            if await N.tgid_yoz(talaba["id"], p["forward_tgid"]):
                yozilgan_id = p["forward_tgid"]
                holat_saqla(uid, tgid_yozildi=True, tgid_natija=p["forward_tgid"])
                log.info("Telegram ID DARROV yozildi: %s → %s",
                         talaba["ism"], p["forward_tgid"])
            else:
                log.info("Telegram ID yozilmadi (bor yoki xato): %s", talaba["ism"])
        except Exception as ex:
            log.warning("tgid_yoz (darrov) xatosi: %s", ex)

    ekstra = ""
    if yozilgan_id:
        ekstra = f"\n📝 Telegram ID saqlandi: <code>{yozilgan_id}</code>"

    # Chek allaqachon kelgan bo'lsa → "necha talaba" so'raymiz
    # (bir marta; so'ralgandan keyin to'g'ridan-to'g'ri tasdiqqa)
    if p.get("chek"):
        if not p.get("necha_sorandi"):
            await _necha_talaba_sorash(update, uid)
        else:
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
    m = update.effective_message

    # Avval — yashirin kod orqali tekshiramiz (eslatma/qarzdor xabarini
    # qayta forward qilgan bo'lsangiz, shu yerda tanib olamiz).
    matn_kod = m.text or m.caption or ""
    talaba_kod = await _kod_dan_talaba(matn_kod)
    if talaba_kod:
        p = holat_p(uid)
        bolishda = p and p.get("bolish") and \
            (p.get("stage") in ("bolish_talaba", "bolish_talaba_tanlash"))
        log.info("Forward: talaba KOD orqali topildi — %s", talaba_kod["ism"])
        if bolishda:
            await _bolish_talaba_tanlandi(update, uid, talaba_kod)
        else:
            await _talaba_tanlandi(update, uid, talaba_kod)
        return

    tg_id, ism = _forward_id(update)
    p = holat_p(uid)
    bolishda = p and p.get("bolish") and \
        (p.get("stage") in ("bolish_talaba", "bolish_talaba_tanlash"))

    if not tg_id:
        # Forward ID yashiringan — admin ism yuborishi kerak
        if bolishda:
            await javob(update,
                        "🔒 Forward'da ID yo'q. Talaba ismini yozing:")
            return
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

    # Bo'lish rejimida — topilgan talabani bo'lishga qo'shamiz
    if bolishda:
        if talaba:
            await _bolish_talaba_tanlandi(update, uid, talaba)
        else:
            await javob(update,
                        "🆕 Bu ID bazada yo'q. Talaba ismini yozing:")
        return

    if talaba:
        holat_saqla(uid, forward_tgid=tg_id)
        log.info("Forward: talaba TOPILDI (ID li) — %s, forward_tgid=%s saqlandi",
                 talaba["ism"], tg_id)
        await _talaba_tanlandi(update, uid, talaba)
    else:
        # ID yangi — talaba bazada bor, lekin ID yozilmagan. Ism bilan qidiramiz.
        holat_saqla(uid, stage="ism_kutish", forward_tgid=tg_id, forward_ism=ism)
        log.info("Forward: talaba TOPILMADI (ID yangi=%s), ism so'raladi", tg_id)
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

    # Chek REPLY qilingan bo'lsa (eslatma/qarzdor xabariga) yoki caption'ida
    # kod bo'lsa — yashirin kod orqali talabani aniq topamiz. Bu forward'dan
    # ham ustun, chunki bot o'zi yozgan xabar bo'lgani uchun 100% aniq.
    if not (holat_p(uid) or {}).get("talaba_id"):
        reply = update.effective_message.reply_to_message
        reply_matn = (reply.text or reply.caption or "") if reply else ""
        oz_matn = update.effective_message.caption or ""
        talaba_kod = await _kod_dan_talaba(reply_matn) or await _kod_dan_talaba(oz_matn)
        if talaba_kod:
            holat_saqla(uid, talaba_id=talaba_kod["id"], talaba_nomi=talaba_kod["ism"])
            log.info("Chek: talaba KOD orqali topildi — %s", talaba_kod["ism"])

    # Chek forward qilingan bo'lsa — Telegram ID va profil ismini saqlaymiz
    # (keyin avtomat izlashda ishlatiladi)
    if update.effective_message.forward_origin or \
       getattr(update.effective_message, "forward_from", None):
        tg_id, f_ism = _forward_id(update)
        if tg_id:
            holat_saqla(uid, forward_tgid=tg_id)
        if f_ism:
            holat_saqla(uid, forward_ism=f_ism)

    p = holat_p(uid)
    # Chek talabasiz ham qabul qilinadi — "necha talaba" keyin so'raladi.
    # (oddiy holatda talaba oldin forward qilingan bo'ladi, bo'lishda esa yo'q)

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
                fayl_unique=fayl_unique, fayl_nomi=nomi, mime=mime, baytlar=baytlar)
    await kutish.delete()
    await _talaba_avto_izla(update, uid)


# ============================================================ avtomat talaba izlash
async def _talaba_avto_izla(update: Update, uid: int):
    """Chek o'qilgach — talabani AVVAL o'zi izlaydi, keyin 'necha talaba' so'raydi.
    Tartib: kod (allaqachon topilgan) → Telegram ID → profil ismi → chek FIO si."""
    p = holat_p(uid)

    # Talaba allaqachon aniq (kod orqali, qarzdorlar ro'yxatidan yoki forward)
    if p.get("talaba_id"):
        await _necha_talaba_sorash(update, uid)
        return

    d = p.get("chek") or {}
    kutish = await update.effective_message.reply_text("🔍 Talaba izlanmoqda…")

    nomzodlar = []
    korilgan = set()

    def qosh(t):
        if t and t.get("id") and t["id"] not in korilgan:
            korilgan.add(t["id"])
            nomzodlar.append({"id": t["id"], "ism": t["ism"],
                              "guruhlar": t.get("guruhlar") or []})

    # 1) Telegram ID — eng ishonchli
    if p.get("forward_tgid"):
        try:
            qosh(await N.talaba_tgid(p["forward_tgid"]))
        except Exception:
            log.exception("Telegram ID bo'yicha izlashda xato")

    # 2) Forward qiluvchining profil ismi
    # 3) Chekdagi to'lovchi FIO si
    for manba in (p.get("forward_ism"), d.get("tolovchi_fio")):
        if not manba or len(nomzodlar) >= C.NOMZOD_MAX:
            continue
        try:
            _, taxminlar = await N.talaba_izla(manba)
        except Exception:
            log.exception("Ism bo'yicha izlashda xato: %s", manba)
            continue
        for t in taxminlar:
            if len(nomzodlar) >= C.NOMZOD_MAX:
                break
            qosh(t)

    await kutish.delete()

    if not nomzodlar:
        holat_saqla(uid, stage="ism_kutish")
        await javob(update,
                    f"🧾 Chek o'qildi: <b>{pul(d.get('summa'))}</b> so'm\n\n"
                    f"❓ Talaba topilmadi.\n"
                    f"👤 Talabani forward qiling yoki ismini yozing:\n"
                    f"<i>masalan: Kamila Obidova, 83-Fonetika</i>")
        return

    holat_saqla(uid, stage="talaba_tanlash", talaba_taxmin=nomzodlar)
    qatorlar = []
    for i, t in enumerate(nomzodlar):
        yorliq = t["ism"]
        if t.get("guruhlar"):
            yorliq += f" · {t['guruhlar'][0]}"
        qatorlar.append([InlineKeyboardButton(yorliq[:60], callback_data=f"t:{i}")])
    qatorlar.append([InlineKeyboardButton("🔍 Boshqa ism yozish", callback_data="tm")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
    await javob(update,
                f"🧾 Chek o'qildi: <b>{pul(d.get('summa'))}</b> so'm\n\n"
                f"👤 Talabani tanlang:", kb(qatorlar))


# ============================================================ necha talaba (bo'lish)
async def _necha_talaba_sorash(update: Update, uid: int):
    """Chek o'qilgach: bir necha talabaga bo'linadimi?"""
    p = holat_p(uid)
    d = p["chek"]
    holat_saqla(uid, stage="necha_talaba", necha_sorandi=True)
    qatorlar = [
        [InlineKeyboardButton("👤 1 talaba", callback_data="nt:1")],
        [InlineKeyboardButton("👥 2 talaba", callback_data="nt:2"),
         InlineKeyboardButton("👥 3 talaba", callback_data="nt:3")],
        [InlineKeyboardButton("✏️ Boshqa son", callback_data="nt:m")],
        [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
    ]
    await javob(update,
                f"🧾 Chek o'qildi: <b>{pul(d.get('summa'))}</b> so'm\n\n"
                f"Bu to'lov <b>necha talaba</b> uchun?", kb(qatorlar))


async def _bolish_boshla(update: Update, uid: int, soni: int):
    """Bo'lish rejimini boshlaydi."""
    p = holat_p(uid)
    d = p["chek"]
    if soni <= 1:
        # Oddiy — bitta talaba
        holat_saqla(uid, bolish=False)
        if p.get("talaba_id"):
            # Talaba oldindan aniqlangan (forward qilingan) → tasdiqqa
            holat_saqla(uid, stage="tasdiq")
            await _tasdiq_yoki_saqla(update, uid)
        else:
            # Talaba yo'q — forward/ism so'raymiz
            holat_saqla(uid, stage="ism_kutish")
            await javob(update,
                        "👤 Talabani forward qiling yoki ismini yozing:\n"
                        "<i>masalan: Kamila Obidova, 83-Fonetika</i>")
        return

    holat_saqla(uid, bolish=True, talaba_soni=soni, qismlar=[],
                qoldiq=d.get("summa") or 0, joriy_talaba=None,
                stage="bolish_talaba")

    # 1-talaba allaqachon aniqlangan bo'lsa (avtomat izlashda tanlangan) —
    # uni qayta so'ramaymiz, to'g'ridan-to'g'ri summasini so'raymiz.
    if p.get("talaba_id"):
        await _bolish_talaba_tanlandi(
            update, uid, {"id": p["talaba_id"], "ism": p["talaba_nomi"]})
        return

    await _bolish_keyingi(update, uid)


async def _bolish_keyingi(update: Update, uid: int):
    """Bo'lishda keyingi talabani so'rash yoki tugatish."""
    p = holat_p(uid)
    qismlar = p.get("qismlar", [])
    soni = p.get("talaba_soni", 1)
    n = len(qismlar)

    if n >= soni:
        # Hammasi kiritildi → saqlash
        await _bolish_saqla(update, uid)
        return

    qoldiq = p.get("qoldiq", 0)
    holat_saqla(uid, stage="bolish_talaba", joriy_talaba=None)
    kiritilgan = "\n".join(
        f"  {i+1}. {e(q['ism'])} — {pul(q['summa'])}" for i, q in enumerate(qismlar))
    matn = (f"👥 <b>{n+1}-talaba</b> ({soni} tadan)\n")
    if kiritilgan:
        matn += f"\nKiritilgan:\n{kiritilgan}\n"
    matn += (f"\n💰 Qolgan: <b>{pul(qoldiq)}</b> so'm\n\n"
             f"Talabani forward qiling yoki ismini yozing:")
    await javob(update, matn)


async def _bolish_talaba_tanlandi(update: Update, uid: int, talaba: dict):
    """Bo'lishda talaba aniqlandi → summa so'raladi (guruh narxlari bilan)."""
    p = holat_saqla(uid, joriy_talaba={"id": talaba["id"], "ism": talaba["ism"]})
    qoldiq = p.get("qoldiq", 0)
    n = len(p.get("qismlar", []))
    soni = p.get("talaba_soni", 1)

    # Faol guruhlar narxi
    try:
        guruhlar = await N.talaba_faol_guruhlari(talaba["id"])
    except Exception:
        guruhlar = []

    qatorlar = []
    korilgan_narx = set()
    for g in guruhlar:
        narx = g.get("narx")
        if narx and narx not in korilgan_narx:
            korilgan_narx.add(narx)
            yorliq = f"{pul(narx)} · {g['guruh_nomi']}"[:60]
            qatorlar.append([InlineKeyboardButton(yorliq,
                                                 callback_data=f"bs:{int(narx)}")])
    # Oxirgi talaba bo'lsa — "qolganini berish" varianti
    if n == soni - 1 and qoldiq > 0:
        qatorlar.append([InlineKeyboardButton(f"💯 Qolganini: {pul(qoldiq)}",
                                              callback_data=f"bs:{int(qoldiq)}")])
    qatorlar.append([InlineKeyboardButton("✏️ Qo'lda yozish", callback_data="bs:m")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])

    holat_saqla(uid, stage="bolish_summa")
    await javob(update,
                f"👤 <b>{e(talaba['ism'])}</b>\n"
                f"💰 Qolgan: {pul(qoldiq)}\n\n"
                f"Bu talaba uchun qancha?", kb(qatorlar))


async def _bolish_summa_qabul(update: Update, uid: int, summa: int):
    """Bo'lishda bitta talaba summasi kiritildi."""
    p = holat_p(uid)
    jt = p.get("joriy_talaba")
    if not jt:
        await javob(update, "⌛️ Ma'lumot yo'qolgan. /bekor bosing.")
        return
    qismlar = p.get("qismlar", [])
    qismlar.append({"id": jt["id"], "ism": jt["ism"], "summa": summa})
    qoldiq = (p.get("qoldiq", 0)) - summa
    holat_saqla(uid, qismlar=qismlar, qoldiq=qoldiq)
    await _bolish_keyingi(update, uid)


async def _bolish_saqla(update: Update, uid: int):
    """Bo'lingan to'lovlarni har talabaga alohida yozadi."""
    p = holat_p(uid)
    d = p["chek"]
    qismlar = p.get("qismlar", [])
    qoldiq = p.get("qoldiq", 0)

    await javob(update, "💾 Notion'ga yozilmoqda…")

    # Barcha talabalar balansini yozishdan OLDIN o'qib qo'yamiz (Notion
    # formulani darrov qayta hisoblamaydi). Bir talaba bir necha qismda
    # bo'lsa ham to'g'ri chiqishi uchun lug'atda yuritamiz.
    eski_balanslar = {}
    for q in qismlar:
        tid = q.get("talaba_id")
        if tid and tid not in eski_balanslar:
            eski_balanslar[tid] = await N.talaba_balans(tid, q.get("ism"))

    # Chekni bir marta yuklaymiz, hamma yozuvga o'sha faylni beramiz
    upload_id = None
    if p.get("baytlar"):
        upload_id = await N.fayl_yukla(p.get("fayl_nomi") or "chek",
                                       p.get("mime") or "image/jpeg", p["baytlar"])

    # Karta (hammasiga bir xil)
    karta = await N.karta_top(d.get("qabul_karta"))
    if not karta and d.get("qabul_fio"):
        karta, _ = await N.karta_top_ism(d["qabul_fio"])

    sana_iso = d.get("sana") or datetime.now(TZ).date().isoformat()
    if d.get("sana") and d.get("vaqt"):
        sana_iso = f"{d['sana'][:10]}T{d['vaqt']}:00+05:00"
    sana_qisqa = sana_iso[:10].replace("-", ".")

    natijalar = []
    sahifa_idlar = []          # [(sahifa_id, ism)] — tuzatish tugmalari uchun
    jami = len(qismlar)
    for idx, q in enumerate(qismlar, 1):
        izohlar = [f"Bo'lingan to'lov ({idx}/{jami}) — umumiy {pul(d.get('summa'))}"]
        if d.get("tranzaksiya_id"):
            izohlar.append(f"Trx: {d['tranzaksiya_id']}")
        if d.get("qabul_fio"):
            izohlar.append(f"Qabul: {d['qabul_fio']}")
        if d.get("izoh"):
            izohlar.append(d["izoh"])

        nomi = f"{q['ism']} — {pul(q['summa'])} — {sana_qisqa}"
        try:
            sahifa = await N.tolov_yozuv({
                "nomi": nomi,
                "talaba_id": q["id"],
                "summa": q["summa"],
                "sana": sana_iso,
                "fio": d.get("tolovchi_fio"),
                "karta_id": karta["id"] if karta else None,
                "karta_mos": bool(karta),
                "tizim": d.get("tolov_tizimi"),
                # Tranzaksiya ID ni faqat BIRINCHI yozuvga beramiz —
                # aks holda dublikat nazorati ikkinchisini bloklaydi
                "trx": d.get("tranzaksiya_id") if idx == 1 else None,
                "chek_url": p.get("chek_url"),
                "tekshir_url": d.get("tekshirish_havolasi"),
                "shubhali": True,        # bo'lingan to'lov doim tekshiruvga
                "fayl_id": p.get("fayl_unique") if idx == 1 else None,
                "hash": p.get("hash") if idx == 1 else None,
                "izoh": " | ".join(izohlar),
                "yub_karta": d.get("yuboruvchi_karta"),
                "file_upload_id": upload_id,
                "fayl_nomi": p.get("fayl_nomi"),
            })
            tid = q.get("talaba_id")
            eski = eski_balanslar.get(tid)
            qator = f"✅ {e(q['ism'])} — {pul(q['summa'])}"
            if eski is not None:
                yangi = eski + (q["summa"] or 0)
                belgi = "🟢" if yangi > 0 else ("⚪" if yangi == 0 else "🔴")
                qator += f"\n   💳 {pul(eski)} → <b>{pul(yangi)}</b> {belgi}"
                eski_balanslar[tid] = yangi   # bir talaba 2 qismda bo'lsa
            natijalar.append(qator)
            sahifa_idlar.append(((sahifa.get("id") or "").replace("-", ""), q["ism"]))
        except Exception as ex:
            log.exception("Bo'lingan yozuv xatosi")
            natijalar.append(f"❌ {e(q['ism'])} — xato: {e(ex)}")

    # Telegram ID yozish (forward bo'lgan talabaga)
    if p.get("forward_tgid") and qismlar:
        try:
            await N.tgid_yoz(qismlar[0]["id"], p["forward_tgid"])
        except Exception:
            pass

    PENDING.pop(uid, None)

    xabar = (f"✅ <b>Bo'lingan to'lov saqlandi</b>\n"
             f"Umumiy: {pul(d.get('summa'))} so'm\n\n"
             + "\n".join(natijalar))
    if qoldiq > 0:
        xabar += f"\n\n⚠️ <b>Taqsimlanmagan qoldiq: {pul(qoldiq)}</b>"
    elif qoldiq < 0:
        xabar += f"\n\n⚠️ <b>Ortiqcha taqsimlandi: {pul(-qoldiq)}</b>"
    xabar += "\n\n🏷 Hammasi Shubhali deb belgilandi (tekshirib qo'ying)."

    # Har talabaning holat sanalarini tozalaymiz
    for tid, ism_q in {q.get("talaba_id"): q.get("ism") for q in qismlar}.items():
        if tid:
            try:
                await N.tolovdan_keyin_sanalar(tid, ism_q)
            except Exception:
                log.exception("Sanalar tozalanmadi: %s", ism_q)

    # Har qismga alohida "tuzatish" tugmasi. Qismlar bir-biriga bog'liq
    # bo'lgani uchun (yig'indi chek summasiga teng bo'lishi kerak) — guruhni
    # eslab qolamiz, keyin tuzatishda yig'indi to'g'riligini tekshiramiz.
    qatorlar = []
    barcha_id = [sid for sid, _ in sahifa_idlar if sid]
    for sid, ism in sahifa_idlar:
        if not sid:
            continue
        BOLISH_GURUH[sid] = {"jami": d.get("summa"), "sheriklar": barcha_id}
        qatorlar.append([InlineKeyboardButton(f"✏️ {ism}"[:60],
                                              callback_data=f"fx:{sid}")])
    await javob(update, xabar, kb(qatorlar) if qatorlar else None)


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


def _balans_qatori(eski, summa):
    """'💳 Balans: -40 000 → 20 000 so'm' qatorini yasaydi.
    Balans o'qilmagan bo'lsa (None) — bo'sh qator qaytaradi, chunki
    noto'g'ri raqam ko'rsatgandan ko'ra umuman ko'rsatmagan yaxshi."""
    if eski is None:
        return ""
    yangi = eski + (summa or 0)
    belgi = "🟢" if yangi > 0 else ("⚪" if yangi == 0 else "🔴")
    return f"💳 Balans: {pul(eski)} → <b>{pul(yangi)}</b> so'm {belgi}\n"


async def _saqla(update: Update, uid: int):
    p = holat_p(uid)
    if not p or not p.get("chek"):
        await javob(update, "⌛️ Ma'lumot yo'qolgan. Qaytadan boshlang.")
        return
    d = p["chek"]
    karta = p.get("karta")

    await javob(update, "💾 Notion'ga yozilmoqda…")

    # Balansni to'lovdan OLDIN o'qib qo'yamiz. Notion formulani darrov qayta
    # hisoblamagani uchun, yangi balansni o'zimiz hisoblaymiz (eski + summa).
    eski_balans = await N.talaba_balans(p["talaba_id"], p.get("talaba_nomi"))

    # Telegram ID: agar talaba tanlanganda darrov yozilgan bo'lsa — o'shani olamiz.
    # Bo'lmasa (masalan forward+chek birga kelib darrov yozilmagan bo'lsa) — shu yerda.
    yozilgan_id = p.get("tgid_natija")
    if not yozilgan_id and p.get("forward_tgid") and not p.get("tgid_yozildi"):
        try:
            if await N.tgid_yoz(p["talaba_id"], p["forward_tgid"]):
                yozilgan_id = p["forward_tgid"]
                log.info("Telegram ID yozildi (saqlashda): talaba=%s id=%s",
                         p.get("talaba_nomi"), p["forward_tgid"])
        except Exception as ex:
            log.warning("tgid_yoz xatosi: %s", ex)

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
             f"{_balans_qatori(eski_balans, d.get('summa'))}"
             f"{fayl_holati}\n")
    if yozilgan_id:
        xabar += f"📝 Telegram ID saqlandi: <code>{yozilgan_id}</code>\n"
    if p.get("shubhali"):
        xabar += "🏷 Shubhali deb belgilandi\n"
    xabar += f"\n<a href='{sahifa.get('url')}'>Notion'da ochish</a>"

    # To'lov kelgach holat sanalarini tozalaymiz (qarzga tushgan sana faqat
    # talaba qarzdorlar ro'yxatidan chiqqan bo'lsa o'chadi)
    try:
        await N.tolovdan_keyin_sanalar(p["talaba_id"], p.get("talaba_nomi"))
    except Exception:
        log.exception("Sanalar tozalanmadi")

    sid = (sahifa.get("id") or "").replace("-", "")
    tuzat = kb([[InlineKeyboardButton("✏️ Summani tuzatish",
                                      callback_data=f"fx:{sid}")]]) if sid else None
    await javob(update, xabar, tuzat)


# ============================================================ SUMMANI TUZATISH
# Bo'lingan to'lovda qismlar bir-biriga bog'liq — yig'indi chek summasiga
# teng bo'lishi kerak. Bot qayta ishga tushsa bu yo'qoladi, lekin u holda
# ham tuzatish ishlayveradi (faqat yig'indi ogohlantirishi bo'lmaydi).
BOLISH_GURUH: dict = {}    # {sahifa_id: {"jami": int, "sheriklar": [sahifa_id]}}


async def _summa_tuzatish_sorash(update: Update, uid: int, sahifa_id: str):
    """Tuzatish tugmasi bosilgan — yangi summani so'raymiz."""
    try:
        eski = await N.tolov_oqi(sahifa_id)
    except Exception as ex:
        log.exception("To'lov o'qilmadi: %s", sahifa_id)
        await javob_yangi(update, f"❌ Yozuv topilmadi: <code>{e(ex)}</code>")
        return
    holat_saqla(uid, stage="summa_tuzatish", fix_sahifa=sahifa_id,
                fix_ism=eski.get("nomi"))
    await javob_yangi(update,
                      f"✏️ <b>Summani tuzatish</b>\n"
                      f"{e(eski.get('nomi') or '')}\n\n"
                      f"Hozirgi: <b>{pul(eski.get('summa'))}</b> so'm\n\n"
                      f"Yangi summani yozing (masalan <code>270000</code>) "
                      f"yoki /bekor.")


async def _summa_tuzatish_qabul(update: Update, uid: int, matn: str):
    """Yangi summa kiritildi — Notionda yangilaymiz."""
    p = holat_p(uid) or {}
    sahifa_id = p.get("fix_sahifa")
    if not sahifa_id:
        await javob(update, "⌛️ Ma'lumot yo'qolgan. Tugmani qaytadan bosing.")
        return

    raqam = "".join(ch for ch in matn if ch.isdigit())
    if not raqam:
        await javob(update, "❌ Summa tushunarsiz. Faqat raqam yozing "
                            "(masalan <code>270000</code>).")
        return
    yangi = int(raqam)

    try:
        natija = await N.tolov_summa_yangila(sahifa_id, yangi)
    except Exception as ex:
        log.exception("Summa yangilanmadi")
        await javob(update, f"❌ Yangilanmadi: <code>{e(ex)}</code>")
        return

    PENDING.pop(uid, None)
    xabar = (f"✅ <b>Summa tuzatildi</b>\n\n"
             f"{pul(natija['eski_summa'])} → <b>{pul(yangi)}</b> so'm")

    # Bo'lingan to'lov bo'lsa — qismlar yig'indisini tekshiramiz
    guruh = BOLISH_GURUH.get(sahifa_id)
    if guruh and guruh.get("jami"):
        yigindi = 0
        toliq = True
        for sid in guruh["sheriklar"]:
            try:
                yigindi += (await N.tolov_oqi(sid)).get("summa") or 0
            except Exception:
                toliq = False
        if toliq and yigindi != guruh["jami"]:
            farq = guruh["jami"] - yigindi
            xabar += (f"\n\n⚠️ Qismlar yig'indisi: <b>{pul(yigindi)}</b>, "
                      f"chekda: <b>{pul(guruh['jami'])}</b>\n"
                      f"Farq: {pul(abs(farq))} so'm "
                      f"{'yetmayapti' if farq > 0 else 'ortiqcha'}")
    await javob(update, xabar)


# ============================================================ matn handler
async def _bolish_ism_qabul(update: Update, uid: int, matn: str):
    """Bo'lish rejimida talaba ismini qidirish."""
    r = ajrat_ism_guruh(matn)
    if not r["ism"]:
        await javob(update, "❌ Ism topilmadi. Qaytadan yozing.")
        return
    kutish = await update.effective_message.reply_text("🔍 Talaba izlanmoqda…")
    try:
        aniq, taxminlar = await N.talaba_izla(r["ism"], r["guruh"])
    except Exception as ex:
        await kutish.edit_text(f"❌ Notion xatosi: <code>{e(ex)}</code>",
                              parse_mode=ParseMode.HTML)
        return
    await kutish.delete()
    if not taxminlar:
        await javob(update, f"❌ <b>{e(r['ism'])}</b> topilmadi. Qaytadan yozing.")
        return
    holat_saqla(uid, stage="bolish_talaba_tanlash", talaba_taxmin=taxminlar)
    if aniq:
        await _bolish_talaba_tanlandi(update, uid,
                                     {"id": aniq["id"], "ism": aniq["ism"]})
        return
    qatorlar = []
    for i, t in enumerate(taxminlar):
        yorliq = t["ism"]
        if t.get("guruhlar"):
            yorliq += f" · {t['guruhlar'][0]}"
        qatorlar.append([InlineKeyboardButton(yorliq[:60], callback_data=f"bt:{i}")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
    await javob(update, "Qaysi talaba?", kb(qatorlar))


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

    # Doimiy klaviaturadagi tugmalar (oddiy matn bo'lib keladi)
    if matn == TUGMA_QARZDOR:
        await qarzdorlar_filtr_sorash(update, uid)
        return
    if matn == TUGMA_BUGUN:
        await hisobot_yubor(update, ctx)
        return
    if matn == TUGMA_NOL:
        await _oddiy_royxat(update, uid, "nol")
        return
    if matn == TUGMA_ESLATMA:
        await _oddiy_royxat(update, uid, "eslatma")
        return

    # Summani tuzatish — yangi summa kutilmoqda
    if stage == "summa_tuzatish":
        await _summa_tuzatish_qabul(update, uid, matn)
        return

    # Yashirin kod orqali talaba topish (o'zi yozgan matnda yoki reply
    # qilingan xabarda). Faqat talaba hali aniqlanmagan bosqichlarda.
    if stage in ("ism_kutish", "chek_kutish", None) or not (p or {}).get("talaba_id"):
        reply = update.effective_message.reply_to_message
        reply_matn = (reply.text or reply.caption or "") if reply else ""
        talaba_kod = await _kod_dan_talaba(matn) or await _kod_dan_talaba(reply_matn)
        if talaba_kod:
            log.info("Matn: talaba KOD orqali topildi — %s", talaba_kod["ism"])
            await _talaba_tanlandi(update, uid, talaba_kod)
            return

    # Bo'lish rejimi — talaba ismi kutilmoqda
    if stage in ("bolish_talaba", "bolish_talaba_tanlash"):
        await _bolish_ism_qabul(update, uid, matn)
        return

    # Bo'lish rejimi — summa qo'lda (yoki summa tanlash ekranida raqam yozildi)
    if stage in ("bolish_summa_qolda", "bolish_summa"):
        raqam = "".join(ch for ch in matn if ch.isdigit())
        if not raqam:
            await javob(update, "❌ Faqat raqam yozing, masalan <code>270000</code>.")
            return
        await _bolish_summa_qabul(update, uid, int(raqam))
        return

    # Necha talaba — qo'lda son
    if stage == "necha_talaba_son":
        raqam = "".join(ch for ch in matn if ch.isdigit())
        if not raqam or int(raqam) < 1 or int(raqam) > 10:
            await javob(update, "❌ 1 dan 10 gacha son yozing.")
            return
        await _bolish_boshla(update, uid, int(raqam))
        return

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

    # ---------------- Qarzdorlar ----------------
    if data == "qzf":                       # filtr menyusi
        await qarzdorlar_filtr_sorash(update, uid)
        return

    if data == "qzall":                     # hammasi
        await _qarzdor_hammasi(update, uid)
        return

    if data == "qzu":                       # ustoz bo'yicha
        await _qarzdor_filtr_royxati(update, uid, "ustoz")
        return

    if data == "qzg":                       # guruh bo'yicha
        await _qarzdor_filtr_royxati(update, uid, "guruh")
        return

    if data.startswith("qzs:"):             # ustoz/guruh tanlandi
        _, tur_h, idx = data.split(":", 2)
        await _qarzdor_filtrla(update, uid,
                               "ustoz" if tur_h == "u" else "guruh", int(idx))
        return

    if data.startswith("qzr:"):             # nol/eslatma ro'yxatini yangilash
        await _oddiy_royxat(update, uid, data[4:])
        return

    if data.startswith("qzp:"):             # sahifalash
        await _qarzdorlar_korsat(update, uid, int(data[4:]))
        return

    if data.startswith("qz:"):              # raqam bosildi → matn + sana
        await _qarzdor_tanlandi(update, ctx, uid, int(data[3:]))
        return

    # ---------------- Summani tuzatish ----------------
    if data.startswith("fx:"):
        await _summa_tuzatish_sorash(update, uid, data[3:])
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

    # Necha talaba (bo'lish boshlash)
    if data.startswith("nt:"):
        tanlov = data[3:]
        if tanlov == "m":
            holat_saqla(uid, stage="necha_talaba_son")
            await javob(update, "✏️ Necha talaba? Son yozing (masalan 2):")
            return
        await _bolish_boshla(update, uid, int(tanlov))
        return

    # Bo'lishda talaba tanlash
    if data.startswith("bt:"):
        i = int(data[3:])
        taxmin = p.get("talaba_taxmin") or []
        if i >= len(taxmin):
            await javob(update, "❌ Ro'yxat eskirgan.")
            return
        t = taxmin[i]
        await _bolish_talaba_tanlandi(update, uid, {"id": t["id"], "ism": t["ism"]})
        return

    # Bo'lishda summa
    if data.startswith("bs:"):
        tanlov = data[3:]
        if tanlov == "m":
            holat_saqla(uid, stage="bolish_summa_qolda")
            await javob(update, "✏️ Summani yozing (masalan 270000):")
            return
        await _bolish_summa_qabul(update, uid, int(tanlov))
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
    app.add_handler(CommandHandler("balans", balans_cmd))
    app.add_handler(CommandHandler("qarzdorlar", qarzdorlar_cmd))

    if app.job_queue is not None:
        import datetime as _dt
        app.job_queue.run_daily(
            kunlik_hisobot,
            time=_dt.time(hour=C.HISOBOT_SOATI, minute=0, tzinfo=TZ),
            name="kunlik_hisobot")
    else:
        log.warning(
            "JobQueue mavjud emas — requirements.txt da "
            "'python-telegram-bot[job-queue]' borligini tekshiring.")

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
