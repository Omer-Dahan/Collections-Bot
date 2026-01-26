<div align="center">

# 📁 Collections Telegram Bot

![Python](https://img.shields.io/badge/python-3.10+-green?style=for-the-badge&logo=python)
![Telegram](https://img.shields.io/badge/Telegram-Bot-blue?style=for-the-badge&logo=telegram)
![SQLite](https://img.shields.io/badge/SQLite-Database-blue?style=for-the-badge&logo=sqlite)

**A powerful Telegram bot that helps you store, organize, browse, share, and manage collections of media and text.**

[🇮🇱 עברית](README-he.md) • [🇺🇸 English](README.md)

</div>

---

## ✨ Key Features

| Feature | Description |
|---------|-------------|
| 📦 **Collections** | Create and manage multiple media collections |
| ☁️ **Media Storage** | Store photos, videos, documents, audio, and text |
| 🔗 **Secure Sharing** | Share collections via unique codes with access tracking |
| 📊 **Batch Status** | Real-time updates during mass uploads |
| 🛡️ **Admin Panel** | Full user management and analytics dashboard |
| 📝 **Auto-Captions** | Automatically saves captions with media |
| 👁️ **Browsing** | Native Telegram UI for browsing stored content |
| ⚡ **Performance** | Asynchronous design with flood protection |

---

## 🚀 Installation

### 1️⃣ Clone the project
```bash
git clone https://github.com/Omer-Dahan/Collections-bot
cd Collections-bot
```

### 2️⃣ Create virtual environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

### 3️⃣ Install dependencies
```bash
pip install -r requirements.txt
```

### 4️⃣ Configuration
Create a `config.py` file based on the example below:
```python
BOT_TOKEN = "your_bot_token_here"
ADMIN_IDS = [123456789]  # List of admin Telegram IDs

# Optional custom settings
MAX_CAPTION_LENGTH = 800
```

### 5️⃣ Run the bot
```bash
python bot.py
```

---

## 🧱 Project Structure


```
Collections-bot/
├── 📄 bot.py               # 🧠 Main bot entry point
├── 📄 admin_panel.py       # 🛡️ Admin interface & stats
├── 📄 db.py                # 🗄️ Database operations
├── 📄 utils.py             # 🛠️ Helper functions
├── 📄 config.py            # ⚙️ Configuration (Sensitive)
├── 📂 handlers/            # 🎮 Logic handlers
│   ├── 📄 commands.py      # /start, /help, etc.
│   ├── 📄 callbacks.py     # Button interactions
│   └── 📄 messages.py      # Media & text handling
├── 📄 constants.py         # 📝 Constant values
├── 📄 message_tracker.py   # 📨 Message tracking
├── 📄 requirements.txt     # 📦 Dependencies
├── 📄 run_bot.bat          # 🚀 Windows runner
├── 📄 LICENSE.txt          # 📜 GPL-3.0
└── 📄 README.md            # You are here! 👋
```


---

## 🗄️ Database Architecture

The bot uses **SQLite** for efficient local storage.
Tables are automatically created on first run:

- **Collections**: Stores collection metadata
- **Items**: Links media files to collections
- **Users**: User profiles and stats
- **Shared_Collections**: Active share codes and permissions
- **Access_Logs**: Tracks who accessed shared content

---

## 🛡️ Security & Privacy

- 🔒 **Private by Default**: Collections are private unless explicitly shared.
- 🔑 **Unique Codes**: Sharing uses randomly generated unique access codes.
- 🚫 **Access Control**: Owners can revoke share codes at any time.
- 📝 **Logging**: Comprehensive logging of user actions and errors.

---

## 📜 License
This project is licensed under **GNU General Public License v3.0**.

Any redistribution or modification must comply with the terms of this license.

---

<div align="center">

**Made with ❤️ by Omer**

</div>
