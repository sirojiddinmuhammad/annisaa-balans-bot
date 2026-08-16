"""
Eslatma / Qarzdor modul
========================
- Ikki matn shabloni: eslatma (1 darsga puli qolganlar) va qarzdor (balans 0/minus)
- Har bir yuborilgan xabar oxiriga yashirin kod (#Txxxxx) qo'shiladi
- KOD_MAP — kod orqali talaba/yozilishni topish uchun xotiradagi jadval
  (bot forward/reply orqali chekni to'g'ri talabaga bog'lay olishi uchun)
"""
import calendar
import datetime as _dt
import hashlib
import html
import re

HAFTA_QISQA = None  # (hozircha ishlatilmaydi, kelajakda kerak bo'lishi mumkin)

# {kod: {"yozilish_id":..., "talaba_id":..., "talaba_ism":..., "guruh_nomi":...}}
KOD_MAP: dict = {}

_KOD_RE = re.compile(r"#T[0-9a-f]{6}")


def _e(s):
    return html.escape(str(s)) if s is not None else ""


def _pul(n):
    return "—" if n is None else f"{int(n):,}".replace(",", " ")


def _sana_fmt(iso, fmt="%d/%m"):
    if not iso:
        return "—"
    try:
        return _dt.date.fromisoformat(iso[:10]).strftime(fmt)
    except Exception:
        return iso


# ---------------------------------------------------------------- kod
def kod_yasash(yozilish_id: str, talaba_id: str, talaba_ism: str, guruh_nomi: str) -> str:
    """Yozilish ID dan qisqa kod yasaydi (hash orqali — ID prefikslari
    bu workspace'da bir-biriga o'xshab qolgani uchun oddiy kesish ishlamaydi)
    va KOD_MAP ga yozadi."""
    hash_ = hashlib.sha1(yozilish_id.encode()).hexdigest()
    kod = "#T" + hash_[:6]
    KOD_MAP[kod] = {
        "yozilish_id": yozilish_id,
        "talaba_id": talaba_id,
        "talaba_ism": talaba_ism,
        "guruh_nomi": guruh_nomi,
    }
    return kod


def kod_dan_malumot(matn: str):
    """Matn ichidan #Txxxxx kodni topib, KOD_MAP dan ma'lumotini qaytaradi (yoki None)."""
    if not matn:
        return None
    m = _KOD_RE.search(matn)
    if not m:
        return None
    return KOD_MAP.get(m.group(0))


# ---------------------------------------------------------------- oy oralig'i (oylik guruh)
def _oy_qosh(sana: _dt.date, oy: int) -> _dt.date:
    y, m, d = sana.year, sana.month, sana.day
    m2 = m + oy
    y2 = y + (m2 - 1) // 12
    m2 = (m2 - 1) % 12 + 1
    oxirgi_kun = calendar.monthrange(y2, m2)[1]
    return _dt.date(y2, m2, min(d, oxirgi_kun))


def _oy_oralig(boshlagan_iso):
    """(oldingi_matn, yangi_matn) — 'oldingi oy tugadi' va 'yangi oy' oraliqlari."""
    bugun = _dt.date.today()
    boshlash = _dt.date.fromisoformat(boshlagan_iso[:10]) if boshlagan_iso else bugun
    n = 0
    while _oy_qosh(boshlash, n + 1) <= bugun:
        n += 1
    joriy = _oy_qosh(boshlash, n)
    oldingi = _oy_qosh(boshlash, n - 1) if n > 0 else boshlash
    keyingi = _oy_qosh(boshlash, n + 1)
    fmt = lambda s: s.strftime("%d/%m/%y")
    return f"{fmt(oldingi)} — {fmt(joriy)} tugadi", f"{fmt(joriy)} — {fmt(keyingi)}"


# ---------------------------------------------------------------- matnlar
_KARTA_OGOHLANTIRISH = (
    "📌 E'tibor bering! To'lov qilishdan oldin biz bilan bog'laning — "
    "sizga yangilangan karta raqamini yuboramiz.\n"
    "‼️ Eski kartaga pul o'tkazmang! Kartamiz o'zgargan bo'lishi mumkin."
)


def eslatma_matn_darsbay(talaba_ism, guruh_nomi, tolov, darslar, kod) -> str:
    """1 darsga puli qolgan, darsbay (chastotali) guruh uchun to'liq matn."""
    belgi = {"keldi": "✅", "kelmadi": "❌", "bolajak": "⏳"}
    satrlar = [f"{i}. {_sana_fmt(d['sana'])} {belgi.get(d['holat'], '✅')}"
               for i, d in enumerate(darslar, 1)]
    darslar_matn = "\n".join(satrlar) if satrlar else "—"
    return (
        f"📨 Assalomu alaykum. <code>{_e(talaba_ism)}</code>\n\n"
        f"⛔️ <b>\"{_e(guruh_nomi)}\"</b> guruhida ⏳ 1 darsdan so'ng to'lovingiz tugaydi.\n\n"
        f"♻️ Kursni davom ettirasizmi? Agar ha, iltimos, keyingi oylik to'lovni "
        f"amalga oshiring.\n\n"
        f"💰 To'lov: <b>{_pul(tolov)} so'm</b>\n\n"
        f"📊 Darslar:\n{darslar_matn}\n\n"
        f"{_KARTA_OGOHLANTIRISH}\n\n"
        f"{kod}"
    )


def eslatma_matn_oylik(talaba_ism, guruh_nomi, tolov, boshlagan_sana, kod) -> str:
    """1 darsga puli qolgan, oylik (davomatsiz) guruh uchun matn — sana oralig'i bilan."""
    oldingi, yangi = _oy_oralig(boshlagan_sana)
    return (
        f"📨 Assalomu alaykum. <code>{_e(talaba_ism)}</code>\n\n"
        f"⛔️ <b>\"{_e(guruh_nomi)}\"</b> guruhida ⏳ 1 darsdan so'ng to'lovingiz tugaydi.\n\n"
        f"♻️ Kursni davom ettirasizmi? Agar ha, iltimos, keyingi oylik to'lovni "
        f"amalga oshiring.\n\n"
        f"💰 To'lov: <b>{_pul(tolov)} so'm</b>\n\n"
        f"📆 Oldingi oy: {oldingi}\n"
        f"🔄 Yangi oy: {yangi}\n\n"
        f"{_KARTA_OGOHLANTIRISH}\n\n"
        f"{kod}"
    )


def qarzdor_matn(talaba_ism, guruh_nomi, tolov, kod) -> str:
    """Balansi 0/minus bo'lgan talaba uchun qisqa matn."""
    return (
        f"📨 Assalomu alaykum. <code>{_e(talaba_ism)}</code>\n\n"
        f"⚠️ <b>\"{_e(guruh_nomi)}\"</b> guruhida qarzga o'qiyapsiz.\n\n"
        f"❓ To'lovni qachon amalga oshirasiz?\n\n"
        f"💰 To'lov: <b>{_pul(tolov)} so'm</b>\n\n"
        f"{_KARTA_OGOHLANTIRISH}\n\n"
        f"{kod}"
    )
