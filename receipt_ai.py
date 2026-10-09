"""Chekdan ma'lumot o'qish — Claude API (vision)."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import logging
import re

import httpx

import config as C

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"

PROMPT = """Sen to'lov cheklarini o'qiydigan yordamchisan. Rasmda yoki PDFda \
O'zbekistondagi to'lov cheki (Payme, Click, Uzum, Paynet, Beepul, bank ilovasi \
va h.k.) berilgan.

Avval chekni diqqat bilan, erkin o'qi — qaysi yozuv nimani bildirishini \
YONIDAGI YORLIQQA qarab tushun. Keyin faqat JSON qaytar. Hech qanday izoh, \
matn yoki markdown belgisi qo'shma.

JSON tuzilishi:
{
  "tolov_tizimi": "Payme|Click|Uzum|Paynet|Beepul|Bank ilovasi|Boshqa",
  "tranzaksiya_id": "chek/tranzaksiya raqami yoki null",
  "sana": "YYYY-MM-DD yoki null",
  "vaqt": "HH:MM yoki null",
  "summa": <qabul qiluvchiga o'tgan summa, butun son>,
  "yuborilgan_summa": <yuboruvchi to'lagan umumiy summa yoki null>,
  "komissiya": <butun son yoki null>,
  "yuboruvchi_karta": "oxirgi 4 raqam yoki null",
  "tolovchi_fio": "yuboruvchining F.I.SH yoki null",
  "qabul_karta": "oxirgi 4 raqam yoki null",
  "qabul_fio": "qabul qiluvchining F.I.SH yoki null",
  "tekshirish_havolasi": "URL yoki null",
  "yil_korsatilgan": true|false,
  "muvaffaqiyatli": true|false,
  "ishonch": "yuqori|orta|past",
  "izoh": "chekda o'qilmagan yoki chalkash narsa bo'lsa qisqa izoh, aks holda null"
}

SUMMALAR:
Chekda bir nechta summa bo'lishi mumkin. Ularni YORLIG'IGA qarab ajrat:
- Qabul qiluvchiga o'tgan summa → "summa".
  Yorliqlari: "Summa", "Asosiy summa", "Qabul qiluvchiga o'tkaziladigan summa",
  "Сумма", "Сумма перевода".
- Yuboruvchi to'lagan umumiy summa → "yuborilgan_summa".
  Yorliqlari: "Umumiy miqdor", "Jami", "Komissiya bilan summa", "Итого",
  "Общая сумма", "К оплате".
Agar ikkita HAR XIL summa bo'lsa: kichigi "summa", kattasi "yuborilgan_summa",
farqi "komissiya". Sarlavhadagi eng yirik raqam odatda komissiya bilan birga —
uni "summa" deb olma. Agar chekda faqat bitta summa bo'lsa, ikkalasi teng bo'ladi.

KARTALAR:
Kartaning roli chekdagi TARTIBIGA bog'liq emas — ba'zi cheklarda qabul qiluvchi
yuqorida turadi. Faqat yonidagi yorliqqa qara:
- "Qabul qiluvchining kartasi", "Qabul qiluvchi", "Karta raqami" (qabul FISH
  yonida), "Получатель" → "qabul_karta"
- "Yuboruvchi kartasi", "Yuboruvchining kartasi", "Отправитель" → "yuboruvchi_karta"
Ikkalasi ham DOIM 4 xonali matn, boshidagi nol saqlanadi ("0419").
Yulduzchalarni olib tashla ("561468*****1881" → "1881").

QOLGAN QOIDALAR:
1. "muvaffaqiyatli" — "Operatsiya bajarildi", "Muvaffaqiyatli", "Готово",
   "Успешно", "To'landi" bo'lsa true. "Kutilmoqda", "Rad etildi", "В обработке",
   "Отклонено" bo'lsa false.
2. Ekran tepasidagi telefon soati (status paneldagi "14:40") to'lov vaqti EMAS.
   Faqat chekning o'zidagi sana/vaqtni ol. Chekda sana bo'lmasa — null.
3. Biror maydon topilmasa — null. HECH QACHON taxmin qilma, o'ylab topma.
4. Rasm noaniq, kesilgan yoki qisman ko'rinmasa — "ishonch": "past".
5. SANA VA YIL. Bugungi sana yuqorida berilgan — yil kerak bo'lsa o'shandan ol,
   o'zingdan taxmin qilma.
   - Chekda yil YOZILGAN bo'lsa: "yil_korsatilgan": true, va aynan o'sha yilni qo'y.
   - Chekda faqat kun/oy bo'lsa (masalan "08.10" yoki "8 oktabr"):
     "yil_korsatilgan": false, va sana KELAJAKKA o'tib ketmaydigan eng yaqin
     yilni qo'y (odatda joriy yil, agar u kun hali kelmagan bo'lsa — o'tgan yil).
6. Summani so'm birligida butun son qilib ber, tiyinlarni tashla."""


def _bugun():
    """Toshkent vaqti bo'yicha bugungi sana."""
    return datetime.now(timezone(timedelta(hours=5))).date()


def _prompt_bugun():
    """Modelda soat yo'q — bugungi sanani o'zimiz aytishimiz kerak, aks holda
    'joriy yilni qo'y' degan qoidani bajara olmaydi va yilni taxmin qiladi."""
    b = _bugun()
    return (f"BUGUNGI SANA: {b.isoformat()} ({b.strftime('%d.%m.%Y')}), "
            f"Toshkent vaqti.\n\n")


def yilni_tugrila(sana_iso, yil_korsatilgan):
    """Chekda yil yozilmagan bo'lsa — yilni o'zimiz qo'yamiz: shu kun/oy
    bo'yicha KELAJAKKA o'tmaydigan eng yaqin sana.
    Chekda yil yozilgan bo'lsa tegmaymiz (eski chekni qo'lda kiritish uchun).
    Qaytadi: (sana_iso, tuzatildimi)"""
    if not sana_iso or yil_korsatilgan:
        return sana_iso, False
    try:
        kun = datetime.strptime(str(sana_iso)[:10], "%Y-%m-%d").date()
    except Exception:
        return sana_iso, False

    bugun = _bugun()
    nomzod = kun.replace(year=bugun.year)
    if nomzod > bugun:                       # bu yil hali kelmagan → o'tgan yil
        nomzod = kun.replace(year=bugun.year - 1)
    if nomzod == kun:
        return sana_iso, False
    return nomzod.isoformat(), True


def rasm_hash(baytlar: bytes, mime: str) -> str:
    """
    Fayl barmoq izi.
    Rasm bo'lsa — perceptual hash (kesilgan/siqilgan nusxani ham tutadi).
    Aks holda — sha256.
    """
    if mime.startswith("image/"):
        try:
            from PIL import Image
            import imagehash
            im = Image.open(io.BytesIO(baytlar))
            return "p:" + str(imagehash.phash(im))
        except Exception as e:
            log.warning("phash hisoblanmadi (%s), sha256 ishlatiladi", e)
    return "s:" + hashlib.sha256(baytlar).hexdigest()[:32]


def _json_ajrat(matn: str):
    """Claude javobidan JSON ni ajratib olish."""
    matn = matn.strip()
    matn = re.sub(r"^```(?:json)?|```$", "", matn, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", matn, re.DOTALL)
    if not m:
        raise ValueError("JSON topilmadi")
    return json.loads(m.group(0))


def _l4(qiymat):
    """Kartani 4 xonali matnga keltirish."""
    if qiymat is None:
        return None
    raqamlar = "".join(ch for ch in str(qiymat) if ch.isdigit())
    if not raqamlar:
        return None
    return raqamlar[-4:].zfill(4)


def _butun(qiymat):
    if qiymat is None:
        return None
    if isinstance(qiymat, (int, float)):
        return int(qiymat)
    raqamlar = "".join(ch for ch in str(qiymat) if ch.isdigit())
    return int(raqamlar) if raqamlar else None


async def chekni_oqi(baytlar: bytes, mime: str) -> dict:
    """Chekdan ma'lumot o'qib, tozalangan dict qaytaradi."""
    b64 = base64.standard_b64encode(baytlar).decode()

    if mime == "application/pdf":
        blok = {"type": "document",
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": b64}}
    else:
        blok = {"type": "image",
                "source": {"type": "base64", "media_type": mime, "data": b64}}

    body = {
        "model": C.CLAUDE_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": [
            blok, {"type": "text", "text": _prompt_bugun() + PROMPT}]}],
    }
    headers = {
        "x-api-key": C.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(API_URL, headers=headers, json=body)

    if r.status_code >= 400:
        log.error("Claude API xatosi: %s %s", r.status_code, r.text[:500])
        raise RuntimeError(f"Claude API {r.status_code}")

    data = r.json()
    matn = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    xom = _json_ajrat(matn)

    # ---- Tozalash va tekshirish ----
    d = {
        "tolov_tizimi": (xom.get("tolov_tizimi") or "Boshqa").strip(),
        "tranzaksiya_id": (str(xom["tranzaksiya_id"]).strip()
                           if xom.get("tranzaksiya_id") else None),
        "sana": xom.get("sana") or None,
        "vaqt": xom.get("vaqt") or None,
        "summa": _butun(xom.get("summa")),
        "yuborilgan_summa": _butun(xom.get("yuborilgan_summa")),
        "komissiya": _butun(xom.get("komissiya")),
        "yuboruvchi_karta": _l4(xom.get("yuboruvchi_karta")),
        "tolovchi_fio": (xom.get("tolovchi_fio") or None),
        "qabul_karta": _l4(xom.get("qabul_karta")),
        "qabul_fio": (xom.get("qabul_fio") or None),
        "tekshirish_havolasi": (xom.get("tekshirish_havolasi") or None),
        "yil_korsatilgan": bool(xom.get("yil_korsatilgan", True)),
        "muvaffaqiyatli": bool(xom.get("muvaffaqiyatli", True)),
        "ishonch": (xom.get("ishonch") or "orta").lower(),
        "izoh": xom.get("izoh") or None,
    }

    # Tizim nomini ruxsat etilgan ro'yxatga keltirish
    ruxsat = {"payme": "Payme", "click": "Click", "uzum": "Uzum",
              "paynet": "Paynet", "beepul": "Beepul", "bank ilovasi": "Bank ilovasi"}
    d["tolov_tizimi"] = ruxsat.get(d["tolov_tizimi"].lower(), "Boshqa")

    # ---- Yilni tuzatish ----
    # Chekda yil yozilmagan bo'lsa, model uni taxmin qilgan bo'lishi mumkin
    # (masalan 2026 o'rniga 2024). O'zimiz to'g'rilaymiz.
    yangi_sana, tuzatildi = yilni_tugrila(d["sana"], d["yil_korsatilgan"])
    if tuzatildi:
        log.info("Yil tuzatildi: %s → %s", d["sana"], yangi_sana)
        d["sana"] = yangi_sana
        d["izoh"] = ((d["izoh"] or "") + " | yil tuzatildi").strip(" |")

    # ---- Summalarni o'z-o'zini tuzatish ----
    # Model ba'zan sarlavhadagi (komissiya bilan) summani "summa" deb oladi.
    # Qoida sodda: qabul qiluvchiga o'tgan summa HECH QACHON yuborilganidan
    # katta bo'lmaydi.
    s, ys, k = d["summa"], d["yuborilgan_summa"], d["komissiya"]

    if s and ys and s > ys:
        d["summa"], d["yuborilgan_summa"] = ys, s
        d["izoh"] = ((d["izoh"] or "") + " | summa/yuborilgan almashtirildi").strip(" |")
        s, ys = ys, s

    # Ikkalasi teng, lekin komissiya bor → sarlavhadagi raqam olingan
    if s and ys and s == ys and k and ys > k:
        d["summa"] = ys - k
        d["izoh"] = ((d["izoh"] or "") + " | komissiya ayirildi").strip(" |")

    # Komissiya ko'rsatilmagan, lekin ikki summa farq qiladi → o'zimiz hisoblaymiz
    if s and ys and ys > s and not k:
        d["komissiya"] = ys - s

    return d
