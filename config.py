"""Annisaa Markazi — Chek boti konfiguratsiyasi."""
import os

# ---------- Muhit o'zgaruvchilari (Railway → Variables) ----------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Kim botdan foydalanishi mumkin (vergul bilan): "123456789,987654321"
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Cheklar arxiv kanali
ARXIV_CHANNEL_ID = int(os.environ.get("ARXIV_CHANNEL_ID", "-1003938684575"))

# Chek o'qish modeli
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ---------- Notion baza ID lari ----------
BALANS_DB = "528cde55-c8d4-4cf3-a23d-17f9b55b8b69"
TOLIBALAR_DB = "4cf46df6-4639-4da3-bfd2-d7147ffde767"
GURUHLAR_DB = "10f5fce8-a0b1-4513-84a3-d67c7bb99b9d"
USTOZLAR_DB = "11b88936-578b-468d-9919-15cd5b527120"
KARTALAR_DB = "361dd606-4e43-806a-8a5c-f09143dff90f"
TOLOVLAR_DB = "64408559-326f-421e-830d-066a24024233"

# ---------- Maydon nomlari (Notion'da AYNAN shunday yozilgan) ----------
# Balans
P_NOMI = "Nomi"
P_TOLIBA = "Toliba"
P_GURUH = "Guruh"
P_USTOZA = "Ustoza"
P_SUMMA = "Summa"
P_SANA = "Tolov sanasi"
P_FIO = "Tolovchi FIO"
P_KARTA = "Karta"
P_KARTA_MOS = "Karta mos keldi"
P_TIZIM = "Tolov tizimi"
P_TRX = "Tranzaksiya ID"
P_CHEK = "Chek"
P_CHEK_URL = "Chek havolasi"
P_KUTILGAN = "Kutilgan summa"
P_HOLAT = "Holat"
P_YOSH = "Chek yoshi (kun)"
P_SHUBHALI = "Shubhali"
P_TEKSHIR_URL = "Tekshirish havolasi"
P_FAYL_ID = "Fayl ID"
P_HASH = "Rasm hash"
P_IZOH = "Izoh"
P_YUB_KARTA = "Yuboruvchi karta"

# Boshqa bazalar
P_GURUH_NOMI = "Guruh nomi"          # Guruhlar (title)
P_GURUH_USTOZA = "Ustozalar"          # Guruhlar → Ustozlar
P_GURUH_NARX = "Oylik narxi"          # Guruhlar
P_TOLIBA_NAME = "Name"                # Tolibalar (title)
P_USTOZ_NAME = "Ustoz ismi"           # Ustozlar (title)
P_KARTA_NOMI = "Karta nomi "          # Kartalar (title) — OXIRIDA PROBEL BOR
P_KARTA_L4 = "Oxirgi 4 raqam"         # Kartalar
P_KARTA_STATUS = "Status"             # Kartalar
P_TOLOV_TOLIBA = "Toliba ismi"        # Tolovlar → Tolibalar
P_TOLOV_GURUH = "Guruhlar"            # Tolovlar → Guruhlar
P_TOLOV_ISM_ROLLUP = "Rollup_ismi"    # Tolovlar rollup (toliba ismi)

# ---------- Sozlamalar ----------
PENDING_TTL = 30 * 60        # eslatma kutish muddati (soniya)
SUMMA_TOLERANS = 1000        # shu farqgacha "To'liq" hisoblanadi
ESKI_CHEK_KUN = 30           # shundan eski chek → ogohlantirish
NOTION_MAX_FAYL = 5 * 1024 * 1024   # Notion Free reja chegarasi
CACHE_TTL = 600              # guruh/karta ro'yxati kesh muddati
