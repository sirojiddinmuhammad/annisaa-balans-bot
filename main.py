"""
Annisaa Markazi — Chek boti
===========================
Ish tartibi:
  1) Admin to'lov eslatmasini botga repost qiladi
  2) Bot tolibani / guruhni / ustozani aniqlaydi
  3) Admin chekni yuboradi (rasm yoki PDF)
  4) Claude chekdan ma'lumot o'qiydi
  5) Admin tasdiqlaydi → Tolibalar Balansi bazasiga yozuv qo'shiladi
"""
import asyncio
import html
import logging
import time
from datetime import datetime, timedelta, timezone

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config as C
import notion_api as N
import receipt_ai as AI
from eslatma_parser import eslatmami, parse_eslatma, ustoza_toza

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("chekbot")

# Toshkent vaqti
TZ = timezone(timedelta(hours=5))

# Kutish holati: {user_id: {...}}
PENDING: dict = {}


# ============================================================ yordamchilar
def e(s) -> str:
    """HTML uchun xavfsiz matn."""
    return html.escape(str(s)) if s is not None else ""


def pul(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", " ")


def admin_mi(update: Update) -> bool:
    if not C.ADMIN_IDS:
        return True                      # ro'yxat bo'sh bo'lsa — hammaga ruxsat
    u = update.effective_user
    return bool(u and u.id in C.ADMIN_IDS)


def holat_p(user_id: int):
    p = PENDING.get(user_id)
    if not p:
        return None
    if time.time() - p.get("vaqt", 0) > C.PENDING_TTL:
        PENDING.pop(user_id, None)
        return None
    return p


def holat_saqla(user_id: int, **kw):
    p = PENDING.setdefault(user_id, {})
    p.update(kw)
    p["vaqt"] = time.time()
    return p


def kb(qatorlar):
    return InlineKeyboardMarkup(qatorlar)


async def javob(update: Update, matn: str, keyboard=None):
    """Xabar yoki callback'ga javob."""
    if update.callback_query:
        await update.callback_query.edit_message_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)
    else:
        await update.effective_message.reply_text(
            matn, parse_mode=ParseMode.HTML, reply_markup=keyboard,
            disable_web_page_preview=True)


# ============================================================ /start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    await javob(update,
                "👋 <b>Chek boti</b>\n\n"
                "1️⃣ To'lov eslatmasini menga repost qiling\n"
                "2️⃣ Keyin chekni yuboring (rasm yoki PDF)\n"
                "3️⃣ Tasdiqlang — Balans bazasiga yoziladi\n\n"
                f"<code>/bekor</code> — kutishni bekor qilish\n"
                f"<code>/kesh</code> — guruh/karta ro'yxatini yangilash\n\n"
                f"Sizning ID: <code>{uid}</code>")


async def bekor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    PENDING.pop(update.effective_user.id, None)
    await javob(update, "🧹 Kutish bekor qilindi.")


async def kesh_yangila(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    N.keshni_tozala()
    g = await N.guruhlar()
    k = await N.kartalar()
    await javob(update, f"♻️ Yangilandi: {len(g)} guruh, {len(k)} karta.")


# ============================================================ eslatma
async def eslatma_qabul(update: Update, ctx: ContextTypes.DEFAULT_TYPE, matn: str):
    uid = update.effective_user.id
    p = parse_eslatma(matn)

    if not p["toliba"] and not p["guruh"]:
        await javob(update, "❓ Bu eslatmaga o'xshamadi. Eslatma matnini repost qiling.")
        return

    eski = holat_p(uid)
    ogoh = ""
    if eski and eski.get("toliba_nomi"):
        ogoh = (f"⚠️ Oldingi eslatma bekor qilindi "
                f"(<i>{e(eski['toliba_nomi'])}</i>)\n\n")

    PENDING.pop(uid, None)
    holat_saqla(uid,
                stage="izlash",
                xom_toliba=p["toliba"],
                xom_guruh=p["guruh"],
                xom_ustoza=p["ustoza"],
                kutilgan=p["summa"],
                eslatma=matn[:1500])

    kutish = await update.effective_message.reply_text("🔍 Notion'dan izlanmoqda…")

    try:
        guruh, taxminlar = await N.guruh_top(p["guruh"] or "", p["ustoza"] or "")
    except Exception as ex:
        log.exception("Guruh izlashda xato")
        await kutish.edit_text(f"❌ Notion xatosi: <code>{e(ex)}</code>",
                               parse_mode=ParseMode.HTML)
        return

    await kutish.delete()

    if guruh:
        await _guruh_tanlandi(update, uid, guruh)
    else:
        holat_saqla(uid, stage="guruh_tanlash", guruh_taxmin=taxminlar)
        qatorlar = [[InlineKeyboardButton(
            f"{g['nomi']} — {ustoza_toza(g['ustoza_nomi']) or '?'}",
            callback_data=f"g:{i}")] for i, g in enumerate(taxminlar)]
        qatorlar.append([InlineKeyboardButton("🔍 Nomini yozib qidirish",
                                              callback_data="gm")])
        qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
        await javob(update,
                    f"{ogoh}"
                    f"❌ <b>Guruh topilmadi:</b> <code>{e(p['guruh'])}</code>\n\n"
                    f"Quyidagilardan tanlang:", kb(qatorlar))


async def _guruh_tanlandi(update: Update, uid: int, guruh: dict):
    """Guruh aniqlangach — toliba izlanadi."""
    holat_saqla(uid, guruh=guruh)
    p = holat_p(uid)

    # Kutilgan summa eslatmada bo'lmasa — guruh narxidan olamiz
    if not p.get("kutilgan") and guruh.get("narx"):
        holat_saqla(uid, kutilgan=int(guruh["narx"]))

    try:
        toliba, taxminlar = await N.toliba_top(p.get("xom_toliba") or "", guruh["id"])
    except Exception as ex:
        log.exception("Toliba izlashda xato")
        await javob(update, f"❌ Notion xatosi: <code>{e(ex)}</code>")
        return

    if toliba:
        await _toliba_tanlandi(update, uid, toliba)
        return

    holat_saqla(uid, stage="toliba_tanlash", toliba_taxmin=taxminlar)
    qatorlar = [[InlineKeyboardButton(t["nomi"], callback_data=f"t:{i}")]
                for i, t in enumerate(taxminlar)]
    qatorlar.append([InlineKeyboardButton("🔍 Ismni yozib qidirish", callback_data="tm")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])

    izoh = (f"❌ <b>Toliba topilmadi:</b> <code>{e(p.get('xom_toliba'))}</code>\n"
            f"📚 Guruh: <b>{e(guruh['nomi'])}</b>\n\n")
    if not taxminlar:
        izoh += "Bu guruhda toliba yo'q. Guruhni qayta tanlang yoki ismni yozing.\n\n"
    await javob(update, izoh + "Tanlang:", kb(qatorlar))


async def _toliba_tanlandi(update: Update, uid: int, toliba: dict):
    p = holat_saqla(uid, toliba_id=toliba["id"], toliba_nomi=toliba["nomi"])
    g = p.get("guruh") or {}
    ustoza = ustoza_toza(g.get("ustoza_nomi") or "") or "—"

    # Chek allaqachon o'qilgan bo'lsa (eslatmasiz oqim) → to'g'ridan tasdiqqa
    if p.get("chek"):
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_korsat(update, uid)
        return

    holat_saqla(uid, stage="chek_kutish")
    await javob(update,
                f"✅ <b>{e(toliba['nomi'])}</b>\n"
                f"📚 {e(g.get('nomi'))}\n"
                f"👩‍🏫 {e(ustoza)}\n"
                f"💰 Kutilgan: <b>{pul(p.get('kutilgan'))}</b> so'm\n\n"
                f"📎 <b>Endi chekni yuboring</b> (rasm yoki PDF)")


# ============================================================ chek
def _mime_aniqla(update: Update):
    """(fayl_obj, mime, nomi) yoki (None, None, None)."""
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

    # --- Agar chek bilan birga caption'da eslatma kelgan bo'lsa ---
    caption = (update.effective_message.caption or "").strip()
    if caption and eslatmami(caption):
        await eslatma_qabul(update, ctx, caption)

    # --- Eslatma hali izlanayotgan bo'lsa — chek biroz kutadi ---
    # (eslatma + chek birdaniga, 2 alohida xabar kelganda)
    for _ in range(12):                       # ~6 soniya
        p = holat_p(uid)
        stage = (p or {}).get("stage")
        if stage == "izlash":                 # eslatma hali qidirilmoqda
            await asyncio.sleep(0.5)
            continue
        break

    p = holat_p(uid)

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
        izoh = (f"{p['toliba_nomi']} | {(p.get('guruh') or {}).get('nomi', '')} | "
                f"{datetime.now(TZ):%d.%m.%Y %H:%M}")
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
        holat_saqla(uid, stage="qolda_summa", baytlar_len=len(baytlar),
                    chek_url=chek_url, hash=hash_, fayl_unique=fayl_unique,
                    fayl_nomi=nomi, mime=mime, baytlar=baytlar)
        await kutish.edit_text(
            f"❌ Chekdan ma'lumot o'qilmadi.\n<code>{e(ex)}</code>\n\n"
            f"Summani qo'lda yozing (masalan <code>400000</code>) "
            f"yoki /bekor bosing.",
            parse_mode=ParseMode.HTML)
        return

    holat_saqla(uid, chek=d, chek_url=chek_url, hash=hash_,
                fayl_unique=fayl_unique, fayl_nomi=nomi, mime=mime, baytlar=baytlar)

    await kutish.delete()

    # Eslatma bo'lgan (toliba aniqlangan) → to'g'ridan tasdiqqa
    if p and p.get("toliba_id"):
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_korsat(update, uid)
    else:
        # Eslatmasiz chek → qo'lda guruh/toliba tanlash
        await _eslatmasiz_boshla(update, uid, d)


async def _eslatmasiz_boshla(update: Update, uid: int, d: dict):
    """Eslatmasiz kelgan chek: avval qisqa xulosa, keyin guruh so'raladi."""
    karta = None
    if d.get("qabul_karta"):
        karta = await N.karta_top(d["qabul_karta"])
    if not karta and d.get("qabul_fio"):
        karta, _ = await N.karta_top_ism(d["qabul_fio"])

    karta_matn = (f"→ <b>{e(karta['nomi'])}</b> ✅" if karta
                  else "❌ (topilmadi)")
    qatorlar = [
        [InlineKeyboardButton("🔍 Guruhni tanlash", callback_data="gm")],
        [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
    ]
    await javob(update,
                f"🧾 <b>Chek o'qildi</b> (eslatmasiz)\n"
                f"💰 {pul(d.get('summa'))} so'm · {e(d.get('tolov_tizimi'))}\n"
                f"🏦 •••• {e(d.get('qabul_karta') or '?')} {karta_matn}\n\n"
                f"❓ Bu qaysi toliba? Avval guruhni tanlang:",
                kb(qatorlar))


# ============================================================ tasdiq ekrani
def _holat_hisobla(summa, kutilgan):
    if summa is None:
        return "Tekshirilsin", None
    if kutilgan is None:
        return "Toliq", None
    farq = summa - kutilgan
    if abs(farq) <= C.SUMMA_TOLERANS:
        return "Toliq", farq
    return ("Ortiqcha" if farq > 0 else "Qisman"), farq


def _yosh(sana_iso):
    if not sana_iso:
        return None
    try:
        d = datetime.fromisoformat(sana_iso[:10]).date()
        return (datetime.now(TZ).date() - d).days
    except Exception:
        return None


async def _tasdiq_korsat(update: Update, uid: int):
    p = holat_p(uid)
    if not p:
        await javob(update, "⌛️ Kutish muddati tugadi. Eslatmani qaytadan yuboring.")
        return
    d = p["chek"]

    ogohlar = []
    shubhali = False

    # --- karta tekshiruvi: avval raqam, keyin ism ---
    karta = p.get("karta")                       # qo'lda tanlangan bo'lishi mumkin
    karta_ism_bilan = p.get("karta_ism_bilan", False)
    karta_hal = p.get("karta_hal", False)        # qo'lda hal qilinganmi
    if karta is None and not karta_hal:
        karta = await N.karta_top(d.get("qabul_karta"))
        if karta is None and d.get("qabul_fio"):
            # Raqam topilmadi — ism bo'yicha qidiramiz
            karta, koplik = await N.karta_top_ism(d["qabul_fio"])
            if karta:
                karta_ism_bilan = True
            elif koplik:
                # Bir nechta karta mos keldi — tanlashni so'raymiz
                await _karta_sorash(update, uid, d)
                return
        if karta is None:
            # Karta umuman topilmadi → so'raymiz
            await _karta_sorash(update, uid, d)
            return

    karta_mos = bool(karta)
    if not karta_mos:
        # Qo'lda "karta yo'q" tanlandi → shubhali, davom etadi
        shubhali = True
        ogohlar.append(
            f"🚫 <b>Karta ro'yxatda yo'q</b> — noma'lum deb belgilandi.")

    if karta_ism_bilan:
        shubhali = True
        ogohlar.append(f"ℹ️ Karta ism bo'yicha topildi: <b>{e(karta['nomi'])}</b> "
                       f"(raqam chekda yo'q edi).")
    if "biriktirilmagan" in (karta.get("status") or "").lower():
        shubhali = True
        ogohlar.append(f"⚠️ Karta <b>{e(karta['nomi'])}</b> hozir biriktirilmagan.")

    # --- muvaffaqiyat ---
    if not d.get("muvaffaqiyatli"):
        ogohlar.append("🚫 <b>Chekda tasdiq yo'q</b> — to'lov o'tmagan bo'lishi mumkin!")
        shubhali = True

    # --- tranzaksiya ID (yo'q bo'lsa — to'xtatmaydi, faqat shubhali) ---
    if not d.get("tranzaksiya_id"):
        shubhali = True

    # --- sana ---
    yosh = _yosh(d.get("sana"))
    if not d.get("sana"):
        # Sana yo'q → so'raymiz (avval kartani hal qildik)
        await _sana_sorash(update, uid, d, karta, karta_ism_bilan)
        return
    elif yosh is not None and yosh > C.ESKI_CHEK_KUN:
        shubhali = True
        ogohlar.append(f"🚨 <b>Chek {yosh} kunlik!</b> Eski oyning to'lovi bo'lishi mumkin.")
        if p.get("toliba_id"):
            oxirgi = await N.oxirgi_tolov(p["toliba_id"])
            if oxirgi and oxirgi.get("sana"):
                ogohlar.append(f"     Oxirgi to'lovi: {e(oxirgi['sana'][:10])} — "
                               f"{pul(oxirgi.get('summa'))}")

    # --- ishonch ---
    if d.get("ishonch") == "past":
        shubhali = True

    # --- dublikat ---
    sabab, eski = await N.dublikat_izla(
        trx=d.get("tranzaksiya_id"), fayl_id=p.get("fayl_unique"),
        hash_=p.get("hash"), summa=d.get("summa"), sana=d.get("sana"),
        karta_id=karta["id"] if karta else None)
    dublikat = None
    if sabab:
        dublikat = {"sabab": sabab, "url": eski.get("url")}
        shubhali = True

    # --- holat ---
    holat, farq = _holat_hisobla(d.get("summa"), p.get("kutilgan"))
    holat_saqla(uid, karta=karta, karta_mos=karta_mos,
                karta_ism_bilan=karta_ism_bilan, shubhali=shubhali,
                holat=holat, yosh=yosh, dublikat=dublikat, ogohlar=ogohlar)

    # === Hamma narsa aniq → AVTOMAT SAQLASH ===
    # Faqat dublikat bo'lsa to'xtaymiz (jiddiy).
    if not dublikat:
        await _saqla(update, uid, avtomatik=True)
        return

    # --- matn ---
    sana_matn = "—"
    if d.get("sana"):
        sana_matn = d["sana"][:10].replace("-", ".")
        if d.get("vaqt"):
            sana_matn += f" {d['vaqt']}"

    q = [f"🧾 <b>{e(d.get('tolov_tizimi'))}</b>",
         f"💰 Summa: <b>{pul(d.get('summa'))}</b> so'm"]
    if d.get("komissiya"):
        q.append(f"   <i>(yuborilgan {pul(d.get('yuborilgan_summa'))}, "
                 f"komissiya {pul(d['komissiya'])})</i>")
    q += [f"📅 {e(sana_matn)}",
          f"👤 {e(d.get('tolovchi_fio') or '—')}"]
    if d.get("yuboruvchi_karta"):
        q.append(f"💳 Yuboruvchi: •••• {e(d['yuboruvchi_karta'])}")
    q.append(f"🏦 Qabul: •••• {e(d.get('qabul_karta') or '?')}"
             + (f" → <b>{e(karta['nomi'])}</b> ✅" if karta else " ❌"))
    if d.get("tranzaksiya_id"):
        q.append(f"🔖 <code>{e(d['tranzaksiya_id'])}</code>")

    matn = (f"👤 <b>{e(p['toliba_nomi'])}</b> · "
            f"{e((p.get('guruh') or {}).get('nomi'))}\n"
            f"━━━━━━━━━━━━━━━\n" + "\n".join(q) + "\n━━━━━━━━━━━━━━━\n")

    belgi = {"Toliq": "✅", "Qisman": "🟡", "Ortiqcha": "🔵", "Tekshirilsin": "🔴"}[holat]
    matn += f"{belgi} <b>{holat}</b> — kutilgan {pul(p.get('kutilgan'))}"
    if farq:
        matn += f" ({'+' if farq > 0 else ''}{pul(farq)})"
    matn += "\n"

    # Bu yerga faqat DUBLIKAT holatida yetib kelinadi
    matn += (f"\n🚫 <b>TAKRORIY CHEK!</b>\n"
             f"Sabab: {e(dublikat['sabab'])}\n"
             f"<a href='{dublikat['url']}'>Eski yozuvni ochish</a>\n")

    if ogohlar:
        matn += "\n" + "\n".join(ogohlar) + "\n"

    qatorlar = [
        [InlineKeyboardButton("⚠️ Baribir saqlash", callback_data="fsave")],
        [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
    ]
    await javob(update, matn, kb(qatorlar))


# ============================================================ so'rov ekranlari
async def _karta_sorash(update: Update, uid: int, d: dict):
    """Karta topilmadi yoki bir nechta mos keldi → tugma bilan tanlash."""
    holat_saqla(uid, stage="karta_tanlash")
    kartalar = await N.kartalar()
    qatorlar = [[InlineKeyboardButton(
        f"{k['nomi']} · {k['l4'] or '—'}", callback_data=f"k:{i}")]
        for i, k in enumerate(kartalar)]
    qatorlar.append([InlineKeyboardButton("➖ Karta yo'q / noma'lum",
                                          callback_data="k:none")])
    qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
    holat_saqla(uid, karta_royxat=kartalar)

    fio = d.get("qabul_fio") or "—"
    raqam = d.get("qabul_karta") or "—"
    await javob(update,
                f"❓ <b>Qabul qiluvchi karta topilmadi</b>\n"
                f"Chekda: <b>{e(fio)}</b> · •••• {e(raqam)}\n\n"
                f"Qaysi karta?", kb(qatorlar))


async def _sana_sorash(update: Update, uid: int, d: dict, karta, karta_ism_bilan):
    """Chekda sana yo'q → so'raymiz."""
    holat_saqla(uid, stage="sana_kutish", karta=karta,
                karta_ism_bilan=karta_ism_bilan)
    qatorlar = [
        [InlineKeyboardButton("📅 Bugun", callback_data="d:t"),
         InlineKeyboardButton("📅 Kecha", callback_data="d:y")],
        [InlineKeyboardButton("✏️ Sanani yozish", callback_data="d:m")],
        [InlineKeyboardButton("❌ Bekor", callback_data="cancel")],
    ]
    karta_matn = f"→ {e(karta['nomi'])}" if karta else ""
    await javob(update,
                f"🧾 {pul(d.get('summa'))} so'm · {e(d.get('tolov_tizimi'))} {karta_matn}\n\n"
                f"⚠️ Chekda sana yo'q.\n<b>To'lov qachon bo'lgan?</b>",
                kb(qatorlar))


# ============================================================ saqlash
async def _saqla(update: Update, uid: int, avtomatik: bool = False):
    p = holat_p(uid)
    if not p or not p.get("chek"):
        await javob(update, "⌛️ Ma'lumot yo'qolgan. Eslatmani qaytadan yuboring.")
        return

    d = p["chek"]
    g = p.get("guruh") or {}
    karta = p.get("karta")

    await javob(update, "💾 Notion'ga yozilmoqda…")

    # Notion'ga fayl yuklash (ixtiyoriy — ishlamasa havola qoladi)
    upload_id = None
    if p.get("baytlar"):
        upload_id = await N.fayl_yukla(p.get("fayl_nomi") or "chek",
                                       p.get("mime") or "image/jpeg",
                                       p["baytlar"])

    sana_iso = d.get("sana")
    if sana_iso and d.get("vaqt"):
        sana_iso = f"{sana_iso[:10]}T{d['vaqt']}:00+05:00"

    izohlar = []
    if not d.get("tranzaksiya_id"):
        izohlar.append("Tranzaksiya ID yo'q")
    if p.get("dublikat"):
        izohlar.append(f"Dublikat ogohlantirishi: {p['dublikat']['sabab']}")
    if p.get("karta_ism_bilan") and p.get("karta_mos"):
        izohlar.append("Karta ism bo'yicha topildi (raqam chekda yo'q)")
    if not p.get("karta_mos"):
        izohlar.append(f"Karta ro'yxatda yo'q: {d.get('qabul_karta')}")
    if d.get("izoh"):
        izohlar.append(d["izoh"])
    if d.get("komissiya"):
        izohlar.append(f"Komissiya {d['komissiya']}")
    if d.get("qabul_fio"):
        izohlar.append(f"Qabul qiluvchi: {d['qabul_fio']}")

    sana_qisqa = (sana_iso or "")[:10].replace("-", ".") or "sanasiz"
    nomi = f"{p['toliba_nomi']} — {pul(d.get('summa'))} — {sana_qisqa}"

    try:
        sahifa = await N.balans_yozuv({
            "nomi": nomi,
            "toliba_id": p["toliba_id"],
            "guruh_id": g.get("id"),
            "ustoza_id": g.get("ustoza_id"),
            "summa": d.get("summa"),
            "kutilgan": p.get("kutilgan"),
            "sana": sana_iso,
            "fio": d.get("tolovchi_fio"),
            "karta_id": karta["id"] if karta else None,
            "karta_mos": p.get("karta_mos"),
            "tizim": d.get("tolov_tizimi"),
            "trx": d.get("tranzaksiya_id"),
            "chek_url": p.get("chek_url"),
            "tekshir_url": d.get("tekshirish_havolasi"),
            "holat": p.get("holat"),
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
                            f"Qayta urinish uchun chekni yana yuboring.")
        return

    PENDING.pop(uid, None)

    fayl_holati = "📎 Chek Notion'ga yuklandi" if upload_id else \
                  ("🔗 Chek arxiv kanalda" if p.get("chek_url") else "⚠️ Chek saqlanmadi")

    await javob(update,
                f"✅ <b>Balansga qo'shildi</b>\n\n"
                f"👤 {e(p['toliba_nomi'])}\n"
                f"💰 {pul(d.get('summa'))} so'm — {e(p.get('holat'))}\n"
                f"{fayl_holati}\n"
                + ("🏷 Shubhali deb belgilandi\n" if p.get("shubhali") else "")
                + f"\n<a href='{sahifa.get('url')}'>Notion'da ochish</a>")


# ============================================================ matn handler
async def matn_qabul(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not admin_mi(update):
        return
    uid = update.effective_user.id
    matn = (update.effective_message.text or "").strip()
    p = holat_p(uid)
    stage = (p or {}).get("stage")

    # --- guruh nomini qo'lda qidirish ---
    if stage == "guruh_izlash":
        _, taxminlar = await N.guruh_top(matn, (p or {}).get("xom_ustoza") or "", n=8)
        if not taxminlar:
            await javob(update, "❌ Hech narsa topilmadi. Boshqa nom yozing.")
            return
        holat_saqla(uid, stage="guruh_tanlash", guruh_taxmin=taxminlar)
        qatorlar = [[InlineKeyboardButton(
            f"{g['nomi']} — {ustoza_toza(g['ustoza_nomi']) or '?'}",
            callback_data=f"g:{i}")] for i, g in enumerate(taxminlar)]
        qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
        await javob(update, "Topilganlar:", kb(qatorlar))
        return

    # --- toliba ismini qo'lda qidirish ---
    if stage == "toliba_izlash":
        guruh_id = ((p or {}).get("guruh") or {}).get("id")
        _, taxminlar = await N.toliba_top(matn, guruh_id, n=8)
        if not taxminlar:
            # Guruh ichida topilmasa — butun bazadan
            _, taxminlar = await N.toliba_top(matn, None, n=8)
        if not taxminlar:
            await javob(update, "❌ Hech narsa topilmadi. Boshqa ism yozing.")
            return
        holat_saqla(uid, stage="toliba_tanlash", toliba_taxmin=taxminlar)
        qatorlar = [[InlineKeyboardButton(t["nomi"], callback_data=f"t:{i}")]
                    for i, t in enumerate(taxminlar)]
        qatorlar.append([InlineKeyboardButton("❌ Bekor", callback_data="cancel")])
        await javob(update, "Topilganlar:", kb(qatorlar))
        return

    # --- sanani qo'lda kiritish ---
    if stage == "sana_kutish":
        iso = _sana_ajrat(matn)
        if not iso:
            await javob(update, "❌ Sanani <code>25.07.2026</code> shaklida yozing.")
            return
        p["chek"]["sana"] = iso
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_korsat(update, uid)
        return

    # --- summani qo'lda kiritish (chek o'qilmagan holat) ---
    if stage == "qolda_summa":
        raqam = "".join(ch for ch in matn if ch.isdigit())
        if not raqam:
            await javob(update, "❌ Faqat raqam yozing, masalan <code>400000</code>.")
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
        await _tasdiq_korsat(update, uid)
        return

    # --- yangi eslatma ---
    if eslatmami(matn):
        await eslatma_qabul(update, ctx, matn)
        return

    await javob(update,
                "❓ Tushunmadim.\n\n"
                "To'lov eslatmasini repost qiling, keyin chekni yuboring.\n"
                "/start — yo'riqnoma")


def _sana_ajrat(matn: str):
    """'25.07.2026' / '25/07/26' / '2026-07-25' → ISO sana"""
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
        await javob(update, "⌛️ Kutish muddati tugadi. Eslatmani qaytadan yuboring.")
        return

    if data.startswith("g:"):
        i = int(data[2:])
        taxmin = p.get("guruh_taxmin") or []
        if i >= len(taxmin):
            await javob(update, "❌ Ro'yxat eskirgan. Eslatmani qaytadan yuboring.")
            return
        await _guruh_tanlandi(update, uid, taxmin[i])
        return

    if data.startswith("t:"):
        i = int(data[2:])
        taxmin = p.get("toliba_taxmin") or []
        if i >= len(taxmin):
            await javob(update, "❌ Ro'yxat eskirgan. Eslatmani qaytadan yuboring.")
            return
        await _toliba_tanlandi(update, uid, taxmin[i])
        return

    if data.startswith("k:"):
        tanlov = data[2:]
        if tanlov == "none":
            # Karta noma'lum → shubhali, davom etadi
            holat_saqla(uid, karta=None, karta_mos=False, karta_ism_bilan=False,
                        karta_hal=True, stage="tasdiq")
        else:
            i = int(tanlov)
            royxat = p.get("karta_royxat") or []
            if i >= len(royxat):
                await javob(update, "❌ Ro'yxat eskirgan. Chekni qaytadan yuboring.")
                return
            holat_saqla(uid, karta=royxat[i], karta_mos=True,
                        karta_ism_bilan=True, karta_hal=True, stage="tasdiq")
        await _tasdiq_korsat(update, uid)
        return

    if data == "gm":
        holat_saqla(uid, stage="guruh_izlash")
        await javob(update, "🔍 Guruh nomini yozing:")
        return

    if data == "tm":
        holat_saqla(uid, stage="toliba_izlash")
        await javob(update, "🔍 Toliba ismini yozing:")
        return

    if data.startswith("d:"):
        turi = data[2:]
        if turi == "m":
            holat_saqla(uid, stage="sana_kutish")
            await javob(update, "📅 Sanani yozing: <code>25.07.2026</code>")
            return
        kun = datetime.now(TZ).date() - (timedelta(days=1) if turi == "y" else timedelta())
        p["chek"]["sana"] = kun.isoformat()
        p["chek"]["izoh"] = ((p["chek"].get("izoh") or "") +
                             " | sana qo'lda kiritildi").strip(" |")
        holat_saqla(uid, stage="tasdiq")
        await _tasdiq_korsat(update, uid)
        return

    if data in ("save", "fsave"):
        if data == "fsave":
            holat_saqla(uid, shubhali=True)
        await _saqla(update, uid)
        return


# ============================================================ xato
async def xato_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    log.exception("Kutilmagan xato", exc_info=ctx.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Kutilmagan xato yuz berdi. /bekor bosib qaytadan urinib ko'ring.")
    except Exception:
        pass


# ============================================================ ishga tushirish
def main():
    app = Application.builder().token(C.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bekor", bekor))
    app.add_handler(CommandHandler("kesh", kesh_yangila))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, chek_qabul))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, matn_qabul))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_error_handler(xato_handler)

    log.info("Chek boti ishga tushdi. Adminlar: %s", C.ADMIN_IDS or "hammaga ochiq")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
