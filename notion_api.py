"""Notion API bilan ishlash qatlami."""
import time
import logging
import httpx

import config as C
from eslatma_parser import oxshashlik, ustoza_toza

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
        if len(natija) > 3000:      # xavfsizlik chegarasi
            break
    return natija


def _title(sahifa: dict, prop: str) -> str:
    p = sahifa.get("properties", {}).get(prop) or {}
    parts = p.get("title") or []
    return "".join(x.get("plain_text", "") for x in parts).strip()


def _text(sahifa: dict, prop: str) -> str:
    p = sahifa.get("properties", {}).get(prop) or {}
    parts = p.get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in parts).strip()


def _number(sahifa: dict, prop: str):
    p = sahifa.get("properties", {}).get(prop) or {}
    return p.get("number")


def _relation_ids(sahifa: dict, prop: str) -> list:
    p = sahifa.get("properties", {}).get(prop) or {}
    return [x["id"] for x in (p.get("relation") or [])]


def _status(sahifa: dict, prop: str) -> str:
    p = sahifa.get("properties", {}).get(prop) or {}
    st = p.get("status") or p.get("select")
    return (st or {}).get("name", "")


# ---------------------------------------------------------------- keshlar
async def _keshlangan(kalit: str, olish):
    yozuv = _kesh.get(kalit)
    if yozuv and time.time() - yozuv[0] < C.CACHE_TTL:
        return yozuv[1]
    qiymat = await olish()
    _kesh[kalit] = (time.time(), qiymat)
    return qiymat


async def ustozlar() -> dict:
    """{page_id: ustoz_ismi}"""
    async def olish():
        sahifalar = await _query_all(C.USTOZLAR_DB)
        return {s["id"]: _title(s, C.P_USTOZ_NAME) for s in sahifalar}
    return await _keshlangan("ustozlar", olish)


async def guruhlar() -> list:
    """[{id, nomi, narx, ustoza_id, ustoza_nomi, status}]"""
    async def olish():
        u = await ustozlar()
        sahifalar = await _query_all(C.GURUHLAR_DB)
        out = []
        for s in sahifalar:
            uid = (_relation_ids(s, C.P_GURUH_USTOZA) or [None])[0]
            out.append({
                "id": s["id"],
                "nomi": _title(s, C.P_GURUH_NOMI),
                "narx": _number(s, C.P_GURUH_NARX),
                "ustoza_id": uid,
                "ustoza_nomi": u.get(uid, ""),
                "status": _status(s, "Status"),
            })
        return out
    return await _keshlangan("guruhlar", olish)


async def kartalar() -> list:
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
                "status": _status(s, C.P_KARTA_STATUS),
            })
        return out
    return await _keshlangan("kartalar", olish)


def keshni_tozala():
    _kesh.clear()


# ---------------------------------------------------------------- qidirish
async def guruh_top(nomi: str, ustoza: str = "", n=5):
    """
    Guruhni nomi (va ustoza ismi) bo'yicha qidiradi.
    Qaytadi: (aniq_moslik | None, taxminlar_royxati)
    """
    barcha = await guruhlar()
    u_toza = ustoza_toza(ustoza)

    ballar = []
    for g in barcha:
        b = oxshashlik(nomi, g["nomi"])
        # Ustoza mos kelsa ball ko'tariladi
        if u_toza and g["ustoza_nomi"]:
            ub = oxshashlik(u_toza, ustoza_toza(g["ustoza_nomi"]))
            b = b * 0.75 + ub * 0.25
            if ub > 0.85:
                b += 0.08
        # Yopilgan guruhlar pastga
        if "yopilgan" in (g["status"] or "").lower():
            b -= 0.15
        ballar.append((b, g))

    ballar.sort(key=lambda x: -x[0])
    eng_yaxshi = ballar[0] if ballar else None

    aniq = None
    if eng_yaxshi:
        # Nomi aynan bir xil bo'lsa — darrov tanlanadi
        toliq = [g for b, g in ballar if b >= 0.995]
        if len(toliq) == 1:
            aniq = toliq[0]
        elif eng_yaxshi[0] >= 0.90 and (
                len(ballar) < 2 or eng_yaxshi[0] - ballar[1][0] >= 0.08):
            aniq = eng_yaxshi[1]

    return aniq, [g for _, g in ballar[:n]]


async def guruh_tolibalari(guruh_id: str) -> list:
    """
    Guruhdagi tolibalar. Tolibalar bazasi Guruhlar bilan to'g'ridan-to'g'ri
    bog'lanmagan — aloqa To'lovlar bazasi orqali boradi.
    Qaytadi: [{id, nomi}]
    """
    filter_ = {"property": C.P_TOLOV_GURUH, "relation": {"contains": guruh_id}}
    yozuvlar = await _query_all(C.TOLOVLAR_DB, filter_)

    topilgan: dict = {}
    for y in yozuvlar:
        ids = _relation_ids(y, C.P_TOLOV_TOLIBA)
        if not ids:
            continue
        tid = ids[0]
        if tid in topilgan:
            continue
        # Rollup orqali ismni olishga harakat qilamiz (qo'shimcha so'rovsiz)
        ism = ""
        rp = y.get("properties", {}).get(C.P_TOLOV_ISM_ROLLUP) or {}
        arr = (rp.get("rollup") or {}).get("array") or []
        for el in arr:
            if el.get("type") == "title":
                ism = "".join(x.get("plain_text", "") for x in el["title"]).strip()
                break
        topilgan[tid] = ism

    # Rollup ishlamagan holatlar uchun sahifadan o'qiymiz
    for tid, ism in list(topilgan.items()):
        if not ism:
            try:
                s = await _req("GET", f"/pages/{tid}")
                topilgan[tid] = _title(s, C.P_TOLIBA_NAME)
            except Exception:
                topilgan[tid] = "(nomsiz)"

    return [{"id": k, "nomi": v} for k, v in topilgan.items() if v]


async def toliba_top(ism: str, guruh_id: str = None, n=5):
    """
    Tolibani qidiradi. Guruh berilgan bo'lsa — faqat shu guruh ichidan.
    Qaytadi: (aniq_moslik | None, taxminlar)
    """
    if guruh_id:
        nomzodlar = await guruh_tolibalari(guruh_id)
    else:
        filter_ = {"property": C.P_TOLIBA_NAME, "title": {"contains": ism.split()[0]}}
        sahifalar = await _query_all(C.TOLIBALAR_DB, filter_)
        nomzodlar = [{"id": s["id"], "nomi": _title(s, C.P_TOLIBA_NAME)}
                     for s in sahifalar]

    if not nomzodlar:
        return None, []

    ballar = sorted(((oxshashlik(ism, t["nomi"]), t) for t in nomzodlar),
                    key=lambda x: -x[0])
    eng = ballar[0]
    aniq = None
    toliq = [t for b, t in ballar if b >= 0.995]
    if len(toliq) == 1:
        aniq = toliq[0]
    elif eng[0] >= 0.88 and (len(ballar) < 2 or eng[0] - ballar[1][0] >= 0.10):
        aniq = eng[1]
    return aniq, [t for _, t in ballar[:n]]


async def karta_top(l4: str):
    """Oxirgi 4 raqam bo'yicha kartani topadi."""
    if not l4:
        return None
    l4 = "".join(ch for ch in str(l4) if ch.isdigit()).zfill(4)
    for k in await kartalar():
        if k["l4"] and k["l4"] == l4:
            return k
    return None


# ---------------------------------------------------------------- dublikat
async def dublikat_izla(trx=None, fayl_id=None, hash_=None,
                        summa=None, sana=None, karta_id=None):
    """
    Takroriy chekni qidiradi. Topilsa (sabab, yozuv) qaytadi.
    """
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
        res = await _query_all(C.BALANS_DB, f, page_size=5)
        if res:
            return sabab, res[0]

    # Zaxira: summa + sana + karta birikmasi
    if summa and sana and karta_id:
        f = {"and": [
            {"property": C.P_SUMMA, "number": {"equals": summa}},
            {"property": C.P_SANA, "date": {"equals": sana[:10]}},
            {"property": C.P_KARTA, "relation": {"contains": karta_id}},
        ]}
        res = await _query_all(C.BALANS_DB, f, page_size=5)
        if res:
            return "Summa + sana + karta bir xil", res[0]

    return None, None


async def oxirgi_tolov(toliba_id: str):
    """Tolibaning Balansdagi oxirgi to'lovi (sana, summa)."""
    f = {"property": C.P_TOLIBA, "relation": {"contains": toliba_id}}
    body = {
        "filter": f,
        "sorts": [{"property": C.P_SANA, "direction": "descending"}],
        "page_size": 1,
    }
    data = await _req("POST", f"/databases/{C.BALANS_DB}/query", body)
    res = data.get("results") or []
    if not res:
        return None
    s = res[0]
    d = (s["properties"].get(C.P_SANA) or {}).get("date") or {}
    return {"sana": d.get("start"), "summa": _number(s, C.P_SUMMA)}


# ---------------------------------------------------------------- fayl yuklash
async def fayl_yukla(nomi: str, content_type: str, baytlar: bytes):
    """
    Notion'ga fayl yuklaydi (3 qadam) va file_upload id qaytaradi.
    Xato bo'lsa None qaytadi — yozuv baribir saqlanadi.
    """
    if len(baytlar) > C.NOTION_MAX_FAYL:
        log.warning("Fayl juda katta (%s bayt), Notion'ga yuklanmaydi", len(baytlar))
        return None
    try:
        boshlash = await _req("POST", "/file_uploads", {
            "filename": nomi,
            "content_type": content_type,
        })
        upload_url = boshlash["upload_url"]
        async with httpx.AsyncClient(timeout=90) as cli:
            r = await cli.post(
                upload_url,
                headers={"Authorization": f"Bearer {C.NOTION_TOKEN}",
                         "Notion-Version": "2022-06-28"},
                files={"file": (nomi, baytlar, content_type)},
            )
        if r.status_code >= 400:
            log.error("Fayl yuklash xatosi: %s %s", r.status_code, r.text[:300])
            return None
        return boshlash["id"]
    except Exception as e:
        log.exception("Notion fayl yuklash muvaffaqiyatsiz: %s", e)
        return None


# ---------------------------------------------------------------- yozuv yaratish
async def balans_yozuv(d: dict):
    """
    Balans bazasiga yangi yozuv qo'shadi.
    d ichida: nomi, toliba_id, guruh_id, ustoza_id, summa, sana(ISO), vaqt,
              fio, karta_id, karta_mos, tizim, trx, chek_url, kutilgan,
              holat, yosh, shubhali, tekshir_url, fayl_id, hash, izoh,
              yub_karta, file_upload_id
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

    rel(C.P_TOLIBA, d.get("toliba_id"))
    rel(C.P_GURUH, d.get("guruh_id"))
    rel(C.P_USTOZA, d.get("ustoza_id"))
    rel(C.P_KARTA, d.get("karta_id"))

    num(C.P_SUMMA, d.get("summa"))
    num(C.P_KUTILGAN, d.get("kutilgan"))
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
    if d.get("holat"):
        props[C.P_HOLAT] = {"select": {"name": d["holat"]}}

    props[C.P_KARTA_MOS] = {"checkbox": bool(d.get("karta_mos"))}
    props[C.P_SHUBHALI] = {"checkbox": bool(d.get("shubhali"))}

    if d.get("file_upload_id"):
        props[C.P_CHEK] = {"files": [{
            "type": "file_upload",
            "file_upload": {"id": d["file_upload_id"]},
            "name": d.get("fayl_nomi", "chek"),
        }]}

    return await _req("POST", "/pages", {
        "parent": {"database_id": C.BALANS_DB},
        "properties": props,
    })
