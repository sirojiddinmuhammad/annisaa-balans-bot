# 🧾 Annisaa Markazi — Chek boti (yangi CRM)

Admin talaba xabarini forward qiladi → bot talabani topadi → chek yuboriladi →
**To'lovlar** bazasiga yozuv qo'shiladi.

---

## 1. Railway sozlamalari (Variables)

| Nomi | Qiymati |
|---|---|
| `TELEGRAM_TOKEN` | @BotFather bergan yangi bot tokeni |
| `NOTION_TOKEN` | Notion integratsiya tokeni (`ntn_…`) |
| `ANTHROPIC_API_KEY` | console.anthropic.com dan olingan kalit |
| `ARXIV_CHANNEL_ID` | `-1003938684575` (eski kanal) |
| `ADMIN_IDS` | Telegram ID laringiz, vergul bilan |
| `CLAUDE_MODEL` | *(ixtiyoriy)* standart: `claude-haiku-4-5-20251001` |

`ADMIN_IDS` ni bilmasangiz — botni ishga tushiring, `/start` bosing, bot ID ni yozadi.

⚠️ `ADMIN_IDS` bo'sh qolsa **hammaga ochiq** bo'ladi.

---

## 2. Notion integratsiyasi

Integratsiya quyidagi bazalarga ulanishi shart
(har bir bazada `···` → **Connections** → integratsiyani tanlang):

- **To'lovlar** — yozish uchun
- **Talabalar** — talabani topish + Telegram ID yozish uchun
- **Guruhlar** — guruh nomi bo'yicha saralash uchun
- **Yozilishlar** — talaba↔guruh bog'lanishi uchun
- **💳 Kartalar** — qabul qiluvchi kartani topish uchun

---

## 3. Ishlatish

```
1. Talaba xabarini (chek/eslatma) botga FORWARD qiling
   → forward'da Telegram ID bo'lsa: talaba avtomat topiladi
   → bo'lmasa: bot ism so'raydi

2. (Agar so'ralsa) ism + guruh yozing:
   "Kamila Obidova, 83-Fonetika"
   → bot topganini tugma bilan ko'rsatadi, tanlaysiz
   → Telegram ID bo'sh bo'lsa avtomat saqlanadi

3. Chekni yuboring (rasm yoki PDF)
   → bot o'qiydi, kartani tekshiradi
   → hammasi aniq bo'lsa AVTOMAT saqlaydi
   → topilmasa so'raydi (karta, sana)
```

### Buyruqlar

| Buyruq | Vazifa |
|---|---|
| `/start` | Yo'riqnoma + Telegram ID |
| `/bekor` | Bekor qilish |
| `/kesh` | Guruh/karta ro'yxatini yangilash |

> Notion'da yangi karta yoki guruh qo'shsangiz — `/kesh` bosing.

---

## 4. Talabani qanday topadi

**1-usul — Telegram ID (aniq):**
Forward'da talabaning Telegram ID si keladi → `Telegram ID` maydonidan qidiriladi.

**2-usul — ism + guruh:**
ID kelmasa (talaba maxfiylikni yopgan) → admin ism yozadi → bot Talabalar bazasidan
qidiradi. Bir nechta bir xil ismli talaba bo'lsa, **guruh nomi** orqali to'g'risini
ajratadi (Yozilishlar bazasi bo'yicha).

**Telegram ID avtomat to'lib boradi:** forward'dan ID kelsa va talabaning
`Telegram ID` maydoni bo'sh bo'lsa, bot uni yozib qo'yadi. Keyingi safar
o'sha talaba forward qilinsa — darrov topiladi.

---

## 5. Chek va tekshiruvlar

Bot faqat quyida **so'raydi**, aks holda avtomat saqlaydi:

| Holat | Reaksiya |
|---|---|
| Karta topilmadi (raqam + ism) | Tugma bilan tanlash |
| Chekda sana yo'q | `[Bugun] [Kecha] [Yozish]` |
| Dublikat (takroriy chek) | `[Baribir saqlash] [Bekor]` |

Quyidagilar **to'xtatmaydi**, faqat `Shubhali ☑` qiladi:
tranzaksiya ID yo'q · karta ism bo'yicha topildi · chek noaniq ·
chek 30 kundan eski · karta faol emas.

**Summa qoidasi:** doim *qabul qiluvchiga o'tgan* summa
(Paynet komissiyasi ayirib tashlanadi).

**Karta topish:** avval oxirgi 4 raqam → topilmasa ism bo'yicha
(`Gulzoda X` → Gulzoda Xursandova).

---

## 6. To'lovlar bazasiga nima yoziladi

`Talaba` (avtomat bog'lanadi), `Summa`, `To'lov sanasi`, `Karta`,
`To'lov tizimi`, `Tranzaksiya ID`, `To'lovchi FIO`, `Yuboruvchi karta`,
`Karta mos keldi`, `Chek` (fayl), `Chek havolasi`, `Tekshirish havolasi`,
`Shubhali`, `Chek yoshi`, `Fayl ID`, `Rasm hash`, `Izoh`.

> To'lov **umumiy balansga** tushadi. Balans/chegirma/holat —
> Talabalar va Yozilishlar bazasidagi formulalar orqali avtomat hisoblanadi.

---

## 7. Fayllar

| Fayl | Vazifa |
|---|---|
| `main.py` | Bot oqimi: forward, talaba topish, chek, saqlash |
| `notion_api.py` | Notion: talaba/karta qidirish, dublikat, yozish |
| `receipt_ai.py` | Chekni Claude orqali o'qish |
| `eslatma_parser.py` | Matndan ism/guruh ajratish, ism solishtirish |
| `config.py` | Baza ID lari va maydon nomlari |

⚠️ Notion'da maydon nomini o'zgartirsangiz — `config.py` da ham o'zgartiring.

---

## 8. Mahalliy sinov

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...
export NOTION_TOKEN=...
export ANTHROPIC_API_KEY=...
export ARXIV_CHANNEL_ID=-1003938684575
export ADMIN_IDS=123456789
python main.py
```
