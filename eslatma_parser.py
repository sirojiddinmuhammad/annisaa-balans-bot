"""Eslatma matnini tahlil qilish va ismlarni solishtirish."""
import re
from difflib import SequenceMatcher

# Turli xil apostrof/qo'shtirnoq belgilari
APOSTROFLAR = "'\u2018\u2019\u02bb\u02bc\u0060\u00b4"
QOSHTIRNOQ = '"\u201c\u201d\u00ab\u00bb'


def _normalize(s: str) -> str:
    """Ismni solishtirishga tayyorlash: kichik harf, apostrofsiz, bir xil harflar."""
    if not s:
        return ""
    s = s.lower().strip()
    for ch in APOSTROFLAR:
        s = s.replace(ch, "")
    # Lotin/kirill va yozilish farqlarini tekislash
    almashtirish = {
        "ё": "yo", "ю": "yu", "я": "ya", "ц": "ts", "щ": "sh", "ш": "sh",
        "ч": "ch", "ж": "j", "х": "x", "ҳ": "h", "қ": "q", "ғ": "g",
        "ў": "o", "й": "y", "ъ": "", "ь": "",
        "sh": "sh", "ch": "ch",
        "ye": "e", "iy": "i", "yi": "i",
        "gʻ": "g", "oʻ": "o",
    }
    for a, b in almashtirish.items():
        s = s.replace(a, b)
    s = re.sub(r"[^a-zа-я0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def oxshashlik(a: str, b: str) -> float:
    """0.0 – 1.0 oralig'ida o'xshashlik. So'zlar tartibi muhim emas."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    tola = sorted(na.split())
    tolb = sorted(nb.split())
    tartibsiz = SequenceMatcher(None, " ".join(tola), " ".join(tolb)).ratio()
    tugridan = SequenceMatcher(None, na, nb).ratio()

    # So'zlarning nechtasi mos keldi (familiya tartibi buzilgan holatlar uchun)
    set_a, setb = set(tola), set(tolb)
    kesishma = len(set_a & setb) / max(len(set_a), len(setb))

    return max(tartibsiz, tugridan, kesishma)


def _raqam(matn: str):
    """Matndan butun sonni ajratish: '400 000 so'm' → 400000"""
    if not matn:
        return None
    faqat = re.sub(r"[^\d]", "", matn)
    return int(faqat) if faqat else None


def parse_eslatma(matn: str) -> dict:
    """
    Eslatma matnidan ma'lumot ajratadi.

    Namuna:
        📨 Assalomu alaykum. @`Kamila Obidova`
        ⛔️ "83-Fonetika | KIDS | 03.Ummu Maryam" kursida oylik to'lov muddati tugadi.
        To'lov: 400000 so'm.

    Qaytadi: {toliba, guruh, ustoza, summa, xom_guruh}
    """
    natija = {"toliba": None, "guruh": None, "ustoza": None,
              "summa": None, "xom_guruh": None}

    # --- 1. Toliba ismi: @`Ism Familiya` ---
    m = re.search(r"@\s*[`'\u2018\u2019]([^`'\u2018\u2019\n]{2,60})[`'\u2018\u2019]", matn)
    if m:
        natija["toliba"] = m.group(1).strip()
    else:
        # Zaxira: @ dan keyin qator oxirigacha
        m = re.search(r"@\s*([^\n@]{2,60})", matn)
        if m:
            natija["toliba"] = m.group(1).strip(" .,`'\u2018\u2019")

    # --- 2. Guruh bloki: "..." ichidagi matn ---
    qosh = re.escape(QOSHTIRNOQ)
    m = re.search(rf"[{qosh}]([^{qosh}\n]{{3,120}})[{qosh}]", matn)
    if m:
        blok = m.group(1).strip()
        natija["xom_guruh"] = blok
        qismlar = [q.strip() for q in blok.split("|") if q.strip()]
        if qismlar:
            natija["guruh"] = qismlar[0]
            if len(qismlar) > 1:
                # Oxirgi qism = ustoza. "03.Ummu Maryam" → raqamli prefiks saqlanadi,
                # chunki Notion'da ham shunday yozilgan. Zaxira uchun tozasi ham kerak.
                natija["ustoza"] = qismlar[-1]

    # --- 3. Summa: "To'lov: 400000 so'm" ---
    m = re.search(
        rf"To[{APOSTROFLAR}]?lov\s*[:\-]?\s*([\d\s\u00a0.,]{{3,20}})",
        matn, re.IGNORECASE)
    if m:
        natija["summa"] = _raqam(m.group(1))
    if natija["summa"] is None:
        # Zaxira: matndagi eng katta 5-7 xonali son
        sonlar = [_raqam(x) for x in re.findall(r"\d[\d\s\u00a0.,]{4,}", matn)]
        sonlar = [s for s in sonlar if s and 10_000 <= s <= 50_000_000]
        if sonlar:
            natija["summa"] = max(sonlar)

    return natija


def ustoza_toza(ism: str) -> str:
    """'03.Ummu Maryam' → 'Ummu Maryam'"""
    if not ism:
        return ""
    return re.sub(r"^\s*\d+\s*[.\-)]\s*", "", ism).strip()


def eslatmami(matn: str) -> bool:
    """Bu matn to'lov eslatmasiga o'xshaydimi?"""
    if not matn or len(matn) < 30:
        return False
    p = parse_eslatma(matn)
    return bool(p["toliba"] or p["guruh"])
