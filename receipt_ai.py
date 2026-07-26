"""Chekdan ma'lumot o'qish — Claude API (vision)."""
import base64
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
O'zbekistondagi to'lov cheki (Payme, Click, Uzum, Paynet, bank ilovasi va h.k.) berilgan.

Faqat JSON qaytar. Hech qanday izoh, matn yoki markdown belgisi qo'shma.

JSON tuzilishi:
{
  "tolov_tizimi": "Payme|Click|Uzum|Paynet|Bank ilovasi|Boshqa",
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
  "muvaffaqiyatli": true|false,
  "ishonch": "yuqori|orta|past",
  "izoh": "chekda o'qilmagan yoki chalkash narsa bo'lsa qisqa izoh, aks holda null"
}

QAT'IY QOIDALAR:
1. "summa" — QABUL QILUVCHIGA O'TGAN summa. Agar chekda "Qabul qiluvchiga \
o'tkaziladigan summa" yoki shunga o'xshash maydon bo'lsa, AYNAN shuni ol. \
Sarlavhadagi eng katta raqamni olma — u komissiya bilan birga bo'lishi mumkin. \
Agar faqat bitta summa bo'lsa, "summa" va "yuborilgan_summa" teng bo'ladi.
2. "qabul_karta" va "yuboruvchi_karta" — DOIM 4 xonali matn, boshidagi nol \
saqlanadi (masalan "0419"). Yulduzchalarni ("**** **** **** 4690") olib tashla, \
faqat oxirgi 4 raqamni qoldir.
3. "muvaffaqiyatli" — chekda "Operatsiya bajarildi", "Muvaffaqiyatli", "Готово", \
"Успешно", "To'landi" kabi tasdiq bo'lsa true. "Kutilmoqda", "Rad etildi", \
"В обработке", "Отклонено" bo'lsa false.
4. Ekranning yuqorisidagi telefon soati (masalan status paneldagi "14:40") \
to'lov vaqti EMAS. Faqat chekning o'zida ko'rsatilgan sana/vaqtni ol. \
Agar chekda sana yo'q bo'lsa — null qaytar.
5. Biror maydon topilmasa — null. HECH QACHON taxmin qilma, o'ylab topma.
6. Rasm noaniq, kesilgan yoki qisman ko'rinmasa — "ishonch": "past".
7. Sana faqat kun/oy bo'lsa (yil ko'rsatilmagan) — joriy yilni qo'y.
8. Summani so'm birligida butun son qilib ber, tiyinlarni tashla."""


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
        "messages": [{"role": "user", "content": [blok, {"type": "text", "text": PROMPT}]}],
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
        "muvaffaqiyatli": bool(xom.get("muvaffaqiyatli", True)),
        "ishonch": (xom.get("ishonch") or "orta").lower(),
        "izoh": xom.get("izoh") or None,
    }

    # Tizim nomini ruxsat etilgan ro'yxatga keltirish
    ruxsat = {"payme": "Payme", "click": "Click", "uzum": "Uzum",
              "paynet": "Paynet", "bank ilovasi": "Bank ilovasi"}
    d["tolov_tizimi"] = ruxsat.get(d["tolov_tizimi"].lower(), "Boshqa")

    # Agar summa sarlavhadan olinib, komissiya ham bor bo'lsa — tuzatish
    if (d["summa"] and d["komissiya"] and d["yuborilgan_summa"]
            and d["summa"] == d["yuborilgan_summa"]
            and d["yuborilgan_summa"] > d["komissiya"]):
        d["summa"] = d["yuborilgan_summa"] - d["komissiya"]
        d["izoh"] = ((d["izoh"] or "") + " | summa komissiya ayirib tuzatildi").strip(" |")

    return d
