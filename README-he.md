<div align="center">

# 📁 בוט אוספים לטלגרם

![Python](https://img.shields.io/badge/python-3.10+-green?style=for-the-badge&logo=python)
![Telegram](https://img.shields.io/badge/Telegram-Bot-blue?style=for-the-badge&logo=telegram)
![SQLite](https://img.shields.io/badge/SQLite-Database-blue?style=for-the-badge&logo=sqlite)

**בוט טלגרם מתקדם המאפשר לך לשמור, לארגן, לדפדף, ולשתף אוספים של מדיה וטקסט.**

[🇮🇱 עברית](README-he.md) • [🇺🇸 English](README.md)

</div>

---

## ✨ תכונות עיקריות

| פיצ'ר | תיאור |
|---------|-------------|
| 📦 **ניהול אוספים** | יצירה וניהול של מספר בלתי מוגבל של אוספים |
| ☁️ **שמירת מדיה** | תמיכה בתמונות, סרטונים, מסמכים, אודיו וטקסט |
| 🔗 **שיתוף מאובטח** | שיתוף אוספים באמצעות קודים ייחודיים ומעקב |
| 📊 **סטטוס בזמן אמת** | עדכון חי על התקדמות העלאת קבצים מרובה |
| 🛡️ **ממשק ניהול** | דשבורד מלא לניהול משתמשים וסטטיסטיקות |
| 📝 **כיתוב אוטומטי** | שמירת כיתוב (Caption) יחד עם הקבצים |
| 👁️ **דפדוף נוח** | ממשק דפדוף בתוך הבוט לצפייה בתוכן |
| ⚡ **ביצועים** | תכנות אסינכרוני עם הגנה מהצפות (Anti-Flood) |

---

## 🚀 התקנה

### 1️⃣ שכפול הפרויקט
```bash
git clone https://github.com/Omer-Dahan/Collections-bot
cd Collections-bot
```

### 2️⃣ יצירת סביבה וירטואלית
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

### 3️⃣ התקנת תלויות
```bash
pip install -r requirements.txt
```

### 4️⃣ הגדרות
יש ליצור קובץ `config.py` לפי הדוגמה הבאה:
```python
BOT_TOKEN = "your_bot_token_here"
ADMIN_IDS = [123456789]  # רשימת ה-ID של המנהלים

# הגדרות אופציונליות
MAX_CAPTION_LENGTH = 800
```

### 5️⃣ הרצת הבוט
```bash
python bot.py
```

---

## 🧱 מבנה הפרויקט

<div align="center">

| קובץ | תיאור |
|------|-------------|
| `bot.py` | 🧠 הקובץ הראשי ולוגיקת הבוט |
| `admin_panel.py` | 🔧 ממשק ניהול וסטטיסטיקות לאדמינים |
| `db.py` | 🗄️ ניהול מסד הנתונים (SQLite) |
| `utils.py` | 🛠️ פונקציות עזר ובניית ממשק משתמש |
| `config.py` | ⚙️ קובץ הגדרות (לא נכלל בגיט) |
| `handlers/` | 📂 טיפול בהודעות ו-Callbacks |

</div>

---

## 🗄️ מסד הנתונים

הבוט משתמש ב-**SQLite** לשמירה יעילה ומהירה של הנתונים.
הטבלאות נוצרות אוטומטית בהפעלה הראשונה:

- **Collections**: טבלת האוספים של המשתמשים
- **Items**: פריטי מדיה המקושרים לאוספים
- **Users**: פרופילי משתמשים ומידע כללי
- **Shared_Collections**: קודי שיתוף פעילים והרשאות
- **Access_Logs**: לוג גישות לאוספים משותפים

---

## 🛡️ אבטחה ופרטיות

- 🔒 **פרטי כברירת מחדל**: כל האוספים פרטיים אלא אם שותפו ידנית.
- 🔑 **קודים ייחודיים**: השיתוף מתבצע ע"י קודים אקראיים וייחודיים.
- 🚫 **בקרת גישה**: בעל האוסף יכול לבטל קודי שיתוף בכל רגע.
- 📝 **לוגים**: תיעוד מלא של פעולות ושגיאות לניטור מהיר.

---

## 📜 רישיון
פרויקט זה משוחרר תחת **MIT License**.

---

<div align="center">

**Made with ❤️ by Omer**

</div>
