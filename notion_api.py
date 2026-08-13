"""Notion API bilan ishlash qatlami (yangi CRM)."""
import time
import logging
import httpx

import config as C
from eslatma_parser import oxshashlik

log = logging.getLogger(__name__)

BASE = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {C.NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

_kesh: dict = {}


# ---------------------------------------------------------------- yordamchi
async def _req(method: str, path: str, json=None, timeout=30):
    async with httpx.AsyncClient(timeout=timeout) as cli:
        r = await cli.request(method, f"{BASE}{path}", headers=HEADERS, json=json)
    if r.status_code >= 400:
        log.error("Notion %s %s → %s %s", method, path, r.status_code, r.text[:500])
        r.raise_for_status()
    return r.json()


async def _query_all(db_id: str, filter_=None, page_size=100):
    """Butun bazani (yoki filtrlangan qismini) sahifalab o'qish."""
    natija, cursor = [], None
    while True:
        body = {"page_size": page_size}
        if filter_:
            body["filter"] = filter_
        if cursor:
            body["start_cursor"] = cursor
        data = await _req("POST", f"/databases/{db_id}/query", body)
        natija.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if len(natija) > 5000:
            break
    return natija


def _title(sahifa, prop):
    p = sahifa.get("properties", {}).get(prop) or {}
    return "".join(x.get("plain_text", "") for x in (p.get("title") or [])).strip()


def _text(sahifa, prop):
    p = sahifa.get("properties", {}).get(prop) or {}
    return "".join(x.get("plain_text", "") for x in (p.get("rich_text") or [])).strip()


def _number(sahifa, prop):
    p = sahifa.get("properties", {}).get(prop) or {}
    return p.get("number")


def _relation_ids(sahifa, prop):
    p = sahifa.get("properties", {}).get(prop) or {}
    return [x["id"] for x in (p.get("relation") or [])]


def _select(sahifa, prop):
    p = sahifa.get("properties", {}).get(prop) or {}
    st = p.get("select") or p.get("status")
    return (st or {}).get("name", "")


# ---------------------------------------------------------------- keshlar
async def _keshlangan(kalit, olish):
    yozuv = _kesh.get(kalit)
    if yozuv and time.time() - yozuv[0] < C.CACHE_TTL:
        return yozuv[1]
    qiymat = await olish()
    _kesh[kalit] = (time.time(), qiymat)
    return qiymat


async def guruhlar_map():
    """{guruh_id: guruh_nomi}"""
    async def olish():
        sahifalar = await _query_all(C.GURUHLAR_DB)
        return {s["id"]: _title(s, C.P_GURUH_NOMI) for s in sahifalar}
    return await _keshlangan("guruhlar", olish)


async def kartalar():
    """[{id, nomi, l4, status}]"""
    async def olish():
        sahifalar = await _query_all(C.KARTALAR_DB)
        out = []
        for s in sahifalar:
            l4 = _text(s, C.P_KARTA_L4)
            out.append({
                "id": s["id"],
                "nomi": _title(s, C.P_KARTA_NOMI),
                "l4": "".join(ch for ch in l4 if ch.isdigit()).zfill(4) if l4 else "",
                "status": _select(s, C.P_KARTA_STATUS),
            })
        return out
    return await _keshlangan("kartalar", olish)


def keshni_tozala():
    _kesh.clear()


# ---------------------------------------------------------------- talaba topish
async def talaba_tgid(tg_id):
    """Telegram ID bo'yicha talabani topadi (aniq usul)."""
    if not tg_id:
        return None
    filter_ = {"property": C.P_TALABA_TGID, "rich_text": {"equals": str(tg_id)}}
    res = await _query_all(C.TALABALAR_DB, filter_, page_size=5)
    if not res:
        return None
    s = res[0]
    return {"id": s["id"], "ism": _title(s, C.P_TALABA_ISM),
            "tgid": _text(s, C.P_TALABA_TGID)}


async def _talaba_guruhlari(talaba_id):
    """Talabaning yozilishlaridagi guruh nomlari va holati.
    Qaytadi: [{guruh_nomi, holat}]"""
    filter_ = {"property": C.P_YOZ_TALABA, "relation": {"contains": talaba_id}}
    yozuvlar = await _query_all(C.YOZILISHLAR_DB, filter_)
    gmap = await guruhlar_map()
    out = []
    for y in yozuvlar:
        gids = _relation_ids(y, C.P_YOZ_GURUH)
        holat = _select(y, C.P_YOZ_HOLAT)
        for gid in gids:
            out.append({"guruh_nomi": gmap.get(gid, ""), "holat": holat})
    return out


async def talaba_izla(ism, guruh_matn="", n=6):
    """
    Ism (va ixtiyoriy guruh nomi) bo'yicha talabani qidiradi.
    Guruh nomi berilsa — o'sha guruhda o'qiyotgan talaba yuqoriga chiqadi.
    Qaytadi: (aniq_moslik | None, taxminlar_royxati)
      taxmin: {id, ism, guruhlar: [nomi], mos_guruh: bool}
    """
    if not ism or len(ism.strip()) < 2:
        return None, []

    # Ism bo'yicha nomzodlarni olamiz (birinchi so'z bilan filtr)
    birinchi = ism.strip().split()[0]
    filter_ = {"property": C.P_TALABA_ISM, "title": {"contains": birinchi}}
    sahifalar = await _query_all(C.TALABALAR_DB, filter_, page_size=50)
    if not sahifalar:
        # Kengroq — butun bazadan
        sahifalar = await _query_all(C.TALABALAR_DB)

    nomzodlar = []
    for s in sahifalar:
        nomzodlar.append({
            "id": s["id"],
            "ism": _title(s, C.P_TALABA_ISM),
            "tgid": _text(s, C.P_TALABA_TGID),
        })

    # Ism o'xshashligi bo'yicha ball
    ballar = []
    for t in nomzodlar:
        b = oxshashlik(ism, t["ism"])
        if b >= 0.55:
            ballar.append([b, t])

    if not ballar:
        return None, []

    # Guruh nomi berilgan bo'lsa — yuqori balli nomzodlarning guruhini tekshiramiz
    guruh_matn = (guruh_matn or "").strip()
    if guruh_matn:
        for juft in ballar[:8]:      # faqat yuqori nomzodlar uchun (tejamkor)
            b, t = juft
            guruhlar = await _talaba_guruhlari(t["id"])
            t["guruhlar"] = [g["guruh_nomi"] for g in guruhlar if g["guruh_nomi"]]
            t["mos_guruh"] = False
            for g in guruhlar:
                if not g["guruh_nomi"]:
                    continue
                gb = oxshashlik(guruh_matn, g["guruh_nomi"])
                # qism-nom ham hisobga olinadi (matnda qisqartma bo'lishi mumkin)
                if gb >= 0.6 or _qism_mos(guruh_matn, g["guruh_nomi"]):
                    t["mos_guruh"] = True
                    juft[0] += 0.5           # guruh mos kelsa ballni ko'taramiz
                    if "o'qiy" in (g["holat"] or "").lower():
                        juft[0] += 0.1       # faol yozilish ustun
                    break
    else:
        for juft in ballar[:8]:
            juft[1]["guruhlar"] = []
            juft[1]["mos_guruh"] = False

    ballar.sort(key=lambda x: -x[0])
    eng = ballar[0]

    # Aniq moslik sharti
    aniq = None
    # Guruh mos kelgan yagona nomzod bo'lsa — aniq
    guruh_moslar = [t for b, t in ballar if t.get("mos_guruh")]
    if len(guruh_moslar) == 1 and eng[1].get("mos_guruh"):
        aniq = eng[1]
    elif eng[0] >= 0.9 and (len(ballar) < 2 or eng[0] - ballar[1][0] >= 0.15):
        aniq = eng[1]

    return aniq, [t for _, t in ballar[:n]]


def _qism_mos(matn, guruh_nomi):
    """Matnda guruh nomining bir qismi bormi (masalan '83-Fonetika' ↔ 'Fonetika')."""
    from eslatma_parser import _normalize
    m = _normalize(matn)
    g = _normalize(guruh_nomi)
    if not m or not g:
        return False
    # guruh nomining har bir 'so'zi' matnda bormi
    g_soz = [x for x in g.split() if len(x) >= 3]
    if not g_soz:
        return False
    topildi = sum(1 for x in g_soz if x in m)
    return topildi >= 1


# ---------------------------------------------------------------- Telegram ID yozish
async def tgid_yoz(talaba_id, tg_id):
    """Talabaning Telegram ID maydoni bo'sh bo'lsa — yozib qo'yadi."""
    if not tg_id:
        return False
    try:
        s = await _req("GET", f"/pages/{talaba_id}")
        mavjud = _text(s, C.P_TALABA_TGID)
        if mavjud:
            return False        # allaqachon bor, tegmaymiz
        await _req("PATCH", f"/pages/{talaba_id}", {
            "properties": {
                C.P_TALABA_TGID: {"rich_text": [{"text": {"content": str(tg_id)}}]}
            }
        })
        return True
    except Exception as e:
        log.warning("Telegram ID yozilmadi: %s", e)
        return False


# ---------------------------------------------------------------- karta
async def karta_top(l4):
    """Oxirgi 4 raqam bo'yicha kartani topadi."""
    if not l4:
        return None
    l4 = "".join(ch for ch in str(l4) if ch.isdigit()).zfill(4)
    for k in await kartalar():
        if k["l4"] and k["l4"] == l4:
            return k
    return None


def _karta_ism_balli(qidiruv, karta_nomi):
    """Kartani ism bo'yicha solishtirish (bosh harf ham hisobga olinadi)."""
    from eslatma_parser import _normalize
    q = _normalize(qidiruv)
    n = _normalize(karta_nomi)
    if not q or not n:
        return 0.0
    q_soz, n_soz = q.split(), n.split()
    if not q_soz or not n_soz:
        return 0.0
    mos, ishlatilgan = 0, set()
    for qs in q_soz:
        eng, eng_idx = 0.0, -1
        for i, ns in enumerate(n_soz):
            if i in ishlatilgan:
                continue
            if qs == ns:
                ball = 1.0
            elif len(qs) == 1 and ns.startswith(qs):
                ball = 0.9
            elif len(ns) == 1 and qs.startswith(ns):
                ball = 0.9
            elif ns.startswith(qs) or qs.startswith(ns):
                ball = 0.85
            else:
                ball = oxshashlik(qs, ns)
            if ball > eng:
                eng, eng_idx = ball, i
        if eng >= 0.8:
            mos += 1
            ishlatilgan.add(eng_idx)
    return mos / len(q_soz)


async def karta_top_ism(qabul_fio):
    """Kartani qabul qiluvchi ismi bo'yicha topadi.
    Qaytadi: (karta | None, koplik_bormi)"""
    if not qabul_fio or len(qabul_fio.strip()) < 2:
        return None, False
    ballar = []
    for k in await kartalar():
        if not k["nomi"]:
            continue
        b = _karta_ism_balli(qabul_fio, k["nomi"])
        if b >= 0.75:
            ballar.append((b, k))
    if not ballar:
        return None, False
    ballar.sort(key=lambda x: -x[0])
    yuqori = [k for b, k in ballar if b >= 0.75]
    if len(yuqori) == 1:
        return yuqori[0], False
    if len(ballar) >= 2 and ballar[0][0] - ballar[1][0] >= 0.25:
        return ballar[0][1], False
    return None, True


# ---------------------------------------------------------------- dublikat
async def dublikat_izla(trx=None, fayl_id=None, hash_=None,
                        summa=None, sana=None, karta_id=None):
    """Takroriy chekni qidiradi. Topilsa (sabab, yozuv)."""
    tekshiruvlar = []
    if trx:
        tekshiruvlar.append(("Tranzaksiya ID",
                             {"property": C.P_TRX, "rich_text": {"equals": str(trx)}}))
    if fayl_id:
        tekshiruvlar.append(("Aynan shu fayl",
                             {"property": C.P_FAYL_ID, "rich_text": {"equals": fayl_id}}))
    if hash_:
        tekshiruvlar.append(("Aynan shu rasm",
                             {"property": C.P_HASH, "rich_text": {"equals": hash_}}))
    for sabab, f in tekshiruvlar:
        res = await _query_all(C.TOLOVLAR_DB, f, page_size=5)
        if res:
            return sabab, res[0]
    if summa and sana and karta_id:
        f = {"and": [
            {"property": C.P_SUMMA, "number": {"equals": summa}},
            {"property": C.P_SANA, "date": {"equals": sana[:10]}},
            {"property": C.P_KARTA, "relation": {"contains": karta_id}},
        ]}
        res = await _query_all(C.TOLOVLAR_DB, f, page_size=5)
        if res:
            return "Summa + sana + karta bir xil", res[0]
    return None, None


async def oxirgi_tolov(talaba_id):
    """Talabaning oxirgi to'lovi (sana, summa)."""
    f = {"property": C.P_TALABA, "relation": {"contains": talaba_id}}
    body = {"filter": f,
            "sorts": [{"property": C.P_SANA, "direction": "descending"}],
            "page_size": 1}
    data = await _req("POST", f"/databases/{C.TOLOVLAR_DB}/query", body)
    res = data.get("results") or []
    if not res:
        return None
    s = res[0]
    d = (s["properties"].get(C.P_SANA) or {}).get("date") or {}
    return {"sana": d.get("start"), "summa": _number(s, C.P_SUMMA)}


# ---------------------------------------------------------------- fayl yuklash
async def fayl_yukla(nomi, content_type, baytlar):
    """Notion'ga fayl yuklaydi, file_upload id qaytaradi. Xato → None."""
    if len(baytlar) > C.NOTION_MAX_FAYL:
        log.warning("Fayl juda katta (%s bayt)", len(baytlar))
        return None
    try:
        boshlash = await _req("POST", "/file_uploads",
                              {"filename": nomi, "content_type": content_type})
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(
                boshlash["upload_url"],
                headers={"Authorization": f"Bearer {C.NOTION_TOKEN}",
                         "Notion-Version": "2022-06-28"},
                files={"file": (nomi, baytlar, content_type)})
        if r.status_code >= 400:
            log.error("Fayl yuklash xatosi: %s %s", r.status_code, r.text[:300])
            return None
        return boshlash["id"]
    except Exception as e:
        log.exception("Notion fayl yuklash muvaffaqiyatsiz: %s", e)
        return None


# ---------------------------------------------------------------- to'lov yozuvi
async def tolov_yozuv(d):
    """
    To'lovlar bazasiga yangi yozuv qo'shadi.
    d: nomi, talaba_id, summa, sana, fio, karta_id, karta_mos, tizim, trx,
       chek_url, tekshir_url, yosh, shubhali, fayl_id, hash, izoh,
       yub_karta, file_upload_id, fayl_nomi
    """
    props = {C.P_NOMI: {"title": [{"text": {"content": d["nomi"][:200]}}]}}

    def rel(prop, page_id):
        if page_id:
            props[prop] = {"relation": [{"id": page_id}]}

    def txt(prop, qiymat):
        if qiymat:
            props[prop] = {"rich_text": [{"text": {"content": str(qiymat)[:1900]}}]}

    def num(prop, qiymat):
        if qiymat is not None:
            props[prop] = {"number": qiymat}

    def url(prop, qiymat):
        if qiymat:
            props[prop] = {"url": qiymat}

    rel(C.P_TALABA, d.get("talaba_id"))
    rel(C.P_KARTA, d.get("karta_id"))
    num(C.P_SUMMA, d.get("summa"))
    num(C.P_YOSH, d.get("yosh"))
    txt(C.P_FIO, d.get("fio"))
    txt(C.P_TRX, d.get("trx"))
    txt(C.P_FAYL_ID, d.get("fayl_id"))
    txt(C.P_HASH, d.get("hash"))
    txt(C.P_IZOH, d.get("izoh"))
    txt(C.P_YUB_KARTA, d.get("yub_karta"))
    url(C.P_CHEK_URL, d.get("chek_url"))
    url(C.P_TEKSHIR_URL, d.get("tekshir_url"))

    if d.get("sana"):
        props[C.P_SANA] = {"date": {"start": d["sana"]}}
    if d.get("tizim"):
        props[C.P_TIZIM] = {"select": {"name": d["tizim"]}}

    props[C.P_KARTA_MOS] = {"checkbox": bool(d.get("karta_mos"))}
    props[C.P_SHUBHALI] = {"checkbox": bool(d.get("shubhali"))}

    if d.get("file_upload_id"):
        props[C.P_CHEK] = {"files": [{
            "type": "file_upload",
            "file_upload": {"id": d["file_upload_id"]},
            "name": d.get("fayl_nomi", "chek"),
        }]}

    return await _req("POST", "/pages", {
        "parent": {"database_id": C.TOLOVLAR_DB},
        "properties": props,
    })
