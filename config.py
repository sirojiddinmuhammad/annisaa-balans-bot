"""Annisaa Markazi — Chek boti (yangi CRM) konfiguratsiyasi."""
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

# ---------- Yangi CRM baza ID lari ----------
TOLOVLAR_DB = "cf832f9b-0b30-430f-bc6a-7ca6c3f3bc02"
TALABALAR_DB = "d9ce3228-ad86-49ed-b2fb-61319165eb82"
GURUHLAR_DB = "1fa6f318-9629-4970-a2fd-4e133c02a204"
YOZILISHLAR_DB = "83a9b84e-2ef0-4d9f-906e-a7584b702d4e"
KARTALAR_DB = "b7134ca6-fe1e-4b2a-8351-3213eee41301"

# ---------- Maydon nomlari (Notion'da AYNAN shunday yozilgan) ----------
# To'lovlar
P_NOMI = "Nomi"
P_TALABA = "Talaba"                 # → Talabalar
P_SUMMA = "Summa"
P_SANA = "To'lov sanasi"
P_KARTA = "Karta"                   # → Kartalar
P_KARTA_MOS = "Karta mos keldi"
P_TIZIM = "To'lov tizimi"
P_TRX = "Tranzaksiya ID"
P_FIO = "To'lovchi FIO"
P_YUB_KARTA = "Yuboruvchi karta"
P_CHEK = "Chek"
P_CHEK_URL = "Chek havolasi"
P_TEKSHIR_URL = "Tekshirish havolasi"
P_YOSH = "Chek yoshi (kun)"
P_SHUBHALI = "Shubhali"
P_FAYL_ID = "Fayl ID"
P_HASH = "Rasm hash"
P_IZOH = "Izoh"

# Talabalar
P_TALABA_ISM = "Ism"                # title
P_TALABA_TGID = "Telegram ID"       # text
P_TALABA_TELEFON = "Telefon"
P_TALABA_YOZILISH = "Yozilishlar"   # → Yozilishlar

# Yozilishlar
P_YOZ_TALABA = "Talaba"             # → Talabalar
P_YOZ_GURUH = "Guruh"               # → Guruhlar
P_YOZ_HOLAT = "Holat"               # O'qiyabdi / Ta'tilda / Tugatdi

# Guruhlar
P_GURUH_NOMI = "Guruh nomi"         # title

# Kartalar
P_KARTA_NOMI = "Karta nomi"         # title
P_KARTA_L4 = "Oxirgi 4 raqam"
P_KARTA_STATUS = "Status"           # Faol / Biriktirilmagan

# ---------- Sozlamalar ----------
PENDING_TTL = 30 * 60        # kutish muddati (soniya)
ESKI_CHEK_KUN = 30           # shundan eski chek → shubhali
NOTION_MAX_FAYL = 5 * 1024 * 1024   # Notion Free reja chegarasi
CACHE_TTL = 600              # talaba/karta/guruh ro'yxati kesh muddati
