# 🧾 Annisaa Markazi — Chek boti

To'lov eslatmasi + chekni birlashtirib **Tolibalar Balansi** bazasiga yozadi.

---

## 1. Railway sozlamalari

**Variables** bo'limiga quyidagilarni qo'shing:

| Nomi | Qiymati |
|---|---|
| `TELEGRAM_TOKEN` | @BotFather bergan token |
| `NOTION_TOKEN` | Notion integratsiya tokeni (`ntn_…`) |
| `ANTHROPIC_API_KEY` | console.anthropic.com dan olingan kalit |
| `ARXIV_CHANNEL_ID` | `-1003938684575` |
| `ADMIN_IDS` | Telegram ID laringiz, vergul bilan |
| `CLAUDE_MODEL` | *(ixtiyoriy)* standart: `claude-haiku-4-5-20251001` |

`ADMIN_IDS` ni bilmasangiz — botni ishga tushiring, `/start` bosing, bot sizga ID ingizni yozadi. Keyin Variables ga qo'ying va qayta deploy qiling.

⚠️ `ADMIN_IDS` bo'sh qolsa **hammaga ochiq** bo'ladi. Albatta to'ldiring.

---

## 2. Notion integratsiyasi

Integratsiya quyidagi bazalarga ulangan bo'lishi shart
(har bir bazada `···` → **Connections** → integratsiyani tanlang):

- 💰 Tolibalar Balansi
- 🎓 Tolibalar
- 🚪 Guruhlar
- Ustozlar
- Kartalar
- 💎 To'lovlar *(faqat o'qish uchun — guruhdagi tolibalarni topishga kerak)*

---

## 3. Telegram kanali

Arxiv kanalda bot **admin** bo'lishi va **Post Messages** ruxsati bo'lishi kerak.

---

## 4. Ishlatish

```
1. To'lov eslatmasini botga repost qiling
2. Bot tolibani/guruhni/ustozani topadi va tasdiqlaydi
3. Chekni yuboring (rasm yoki PDF)
4. Bot chekni o'qiydi va tekshiruvlarni ko'rsatadi
5. "Saqlash" → Balans bazasiga yoziladi
```

### Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `/start` | Yo'riqnoma va Telegram ID |
| `/bekor` | Kutishni bekor qilish |
| `/kesh` | Guruh/karta ro'yxatini Notion'dan qayta o'qish |

> Notion'da yangi guruh yoki karta qo'shsangiz — `/kesh` bosing.
> Aks holda bot 10 daqiqagacha eski ro'yxatni ishlatadi.

---

## 5. Tekshiruvlar

Bot quyidagi hollarda ogohlantiradi va yozuvni **Shubhali ☑** deb belgilaydi:

| Holat | Reaksiya |
|---|---|
| Karta ro'yxatda yo'q | 🚫 Saqlashdan oldin tasdiq so'raydi |
| Tranzaksiya ID takrorlangan | 🚫 "Takroriy chek" — eski yozuvga havola |
| Aynan shu rasm/fayl avval kelgan | 🚫 Takroriy |
| Summa + sana + karta bir xil | 🚫 Takroriy |
| Chek 30 kundan eski | 🚨 Tolibaning oxirgi to'lovini ko'rsatadi |
| Chekda sana yo'q | 📅 Sanani so'raydi |
| Chekda tranzaksiya ID yo'q | ⚠️ Shubhali |
| Rasm noaniq | ⚠️ Shubhali |
| To'lov tasdiqlanmagan | 🚫 Ogohlantiradi |

**Summa qoidasi:** doim *qabul qiluvchiga o'tgan* summa olinadi.
Paynet kabi tizimlarda sarlavhada komissiya bilan birga ko'rsatiladi —
bot uni ayirib tashlaydi.

---

## 6. Xarajat

Chek o'qish uchun Claude API ishlatiladi.
Bitta chek ≈ 2000 token. Haiku 4.5 narxida **1000 ta chek ≈ $2–3**.

---

## 7. Mahalliy sinov

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...
export NOTION_TOKEN=...
export ANTHROPIC_API_KEY=...
export ARXIV_CHANNEL_ID=-1003938684575
export ADMIN_IDS=123456789
python main.py
```

---

## 8. Fayllar

| Fayl | Vazifasi |
|---|---|
| `main.py` | Bot mantiqi, suhbat oqimi |
| `notion_api.py` | Notion bilan aloqa, qidiruv, dublikat |
| `receipt_ai.py` | Chekni Claude orqali o'qish |
| `parser.py` | Eslatma matnini tahlil, ism solishtirish |
| `config.py` | Sozlamalar va maydon nomlari |

⚠️ Notion'da maydon nomlarini o'zgartirsangiz — `config.py` da ham
o'zgartiring, aks holda bot topa olmaydi.
