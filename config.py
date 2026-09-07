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

# ---------- Yangi CRM baza ID lari (DATABASE ID — /databases/{id}/query uchun) ----------
TOLOVLAR_DB = "a8b11a13-b698-4dc3-9398-56bbefaa3c78"
TALABALAR_DB = "b079bfbc-48c1-4b7a-a1f3-df7389647260"
GURUHLAR_DB = "d15acd14-3436-4ac0-98bf-76d4ed714d0d"
YOZILISHLAR_DB = "aa2dfb43-91a2-48fa-9f37-48792761f699"
KARTALAR_DB = "937b5f13-3b9c-45df-89f9-0604a11e6886"

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
P_GURUH_NARX = "Oylik to'lov"        # number
P_GURUH_HOLAT = "Guruh holati"       # Faol / Boshlanmagan / Tugagan

# Kartalar
P_KARTA_NOMI = "Karta nomi"         # title
P_KARTA_L4 = "Oxirgi 4 raqam"
P_KARTA_STATUS = "Status"           # Faol / Biriktirilmagan

# ---------- Eslatma / qarzdor funksiyasi uchun ----------
# Davomat bazasi (Notion sahifa URL'idagi ID, tire bilan)
DAVOMAT_DB = "e52488ca-0ae2-4b29-9132-039aefa80e4b"

# Talabalar (formula/rollup, faqat o'qish uchun)
P_TALABA_BALANS = "Balans"                    # formula (number)
P_TALABA_BALANS_HOLATI = "Balans holati"      # formula (matn, masalan "🔴 Qarzdor")
P_TALABA_ESLATMA_KERAK = "Eslatma kerak"      # formula (checkbox)
P_TALABA_1AYLANMA = "1 aylanma"               # rollup (number)
P_TALABA_QARZ_SANA = "Qarzga tushgan sana"    # date — bot yuritadi
P_TALABA_ESLATMA_SANA = "Oxirgi eslatma sanasi"  # date — bot to'ldiradi
P_TALABA_NOL_SANA = "Balansi tugagan sana"    # date — bot yuritadi
P_TALABA_1DARS_SANA = "1 dars qolgan sana"    # date — bot yuritadi

# Yozilishlar (qo'shimcha)
P_YOZ_NOMI = "Nomi"                 # title
P_YOZ_FAOL_TOLOV = "Faol to'lov"     # formula (number) — 1 aylanma narxi
P_YOZ_DAVOMAT = "Davomat"           # relation → Davomat
P_YOZ_BOSHLAGAN = "Boshlagan sana"  # date

# Guruhlar (qo'shimcha)
P_GURUH_CHASTOTA = "Chastota"                 # number
P_GURUH_DARS_KUNLARI = "Dars kunlari"         # multi_select
P_GURUH_DAVOMAT_KERAK_EMAS = "Davomat kerak emas"  # checkbox
P_GURUH_OYLIK = "Oylik to'lov"                # number (allaqachon P_GURUH_NARX bilan bir xil)
P_GURUH_USTOZ_ISM = "Ustoz ismi (matn)"       # formula (matn)

# Qarzdorlar ro'yxati
QARZDOR_SAHIFA = 10           # bir sahifada nechta talaba
QARZDOR_KESH_TTL = 60         # ro'yxat keshi (soniya)
HISOBOT_SOATI = 8             # kunlik hisobot va sana tekshiruvi (Toshkent)
QARZDOR_RANG = ((7, "🟢"), (20, "🟡"))   # shundan katta → 🔴
NOMZOD_MAX = 3                # chek uchun avtomat izlashda nomzodlar soni

# Davomat
P_DAVOMAT_SANA = "Sana"             # date
P_DAVOMAT_HOLAT = "Holat"           # select

HAFTA_KUNLARI = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba",
                  "Juma", "Shanba", "Yakshanba"]

# ---------- Sozlamalar ----------
PENDING_TTL = 30 * 60        # kutish muddati (soniya)
ESKI_CHEK_KUN = 30           # shundan eski chek → shubhali
NOTION_MAX_FAYL = 5 * 1024 * 1024   # Notion Free reja chegarasi
CACHE_TTL = 600              # talaba/karta/guruh ro'yxati kesh muddati
