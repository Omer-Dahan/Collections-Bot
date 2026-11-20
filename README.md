<div align="center">

<br>

### Collections Telegram Bot
**Store, organize, browse, search and share collections of media and text, straight from Telegram.**<br>
Everything you send to the bot lands in the active collection as a Telegram `file_id`, so nothing is re-uploaded or kept on your server's disk. Collections can be shared with an expiring code, scanned for duplicates and broken files, and exported as a backup file.

<br>

<a href="#-quick-start"><img src="https://img.shields.io/badge/🚀_Quick_Start-06B6D4?style=for-the-badge&logoColor=white" alt="Quick Start"></a>
<a href="#-features"><img src="https://img.shields.io/badge/✨_Features-D98324?style=for-the-badge&logoColor=white" alt="Features"></a>
<a href="#-architecture"><img src="https://img.shields.io/badge/🧠_Architecture-0D1117?style=for-the-badge&logoColor=white" alt="Architecture"></a>
<a href="#️-running--operations"><img src="https://img.shields.io/badge/⚙️_Operations-22C55E?style=for-the-badge&logoColor=white" alt="Operations"></a>

<br><br>

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)
![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-21.6-0088CC?style=flat-square&logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-WAL-003B57?style=flat-square&logo=sqlite&logoColor=white)
![UI](https://img.shields.io/badge/UI-Hebrew_RTL-blue?style=flat-square)
![License](https://img.shields.io/badge/License-GPL--3.0-blue?style=flat-square)

</div>

---

## 📑 Table of Contents

<table>
<tr>
<td valign="top" width="33%">

**Getting Started**
- [✨ Features](#-features)
- [🧠 Architecture](#-architecture)
- [🚀 Quick Start](#-quick-start)
- [🎮 Interactive Workflow](#-interactive-workflow)

</td>
<td valign="top" width="33%">

**Operations**
- [⚙️ Running & Operations](#️-running--operations)
- [🧱 Project Structure](#-project-structure)
- [🔧 Configuration Matrix](#-configuration-matrix)

</td>
<td valign="top" width="33%">

**Reference**
- [💡 Design Decisions](#-design-decisions--reliability)
- [⚠️ Known Limitations](#️-known-limitations)
- [📜 License & Credits](#-license--credits)

</td>
</tr>
</table>

---

## ✨ Features

<table>
<tr>
<td width="33%" valign="top">

### 📦 Collections
Create named collections (up to 64 characters, unique per user). Whatever you send while a collection is active is saved into it automatically.

</td>
<td width="33%" valign="top">

### ☁️ Media Storage
Videos, photos, documents, audio and text. Only the Telegram `file_id` and metadata go into SQLite, and captions are saved with the item.

</td>
<td width="33%" valign="top">

### 👁️ Browse & Scroll
A paged browse menu, a one-item scroll view and a 🎲 button that jumps to a random item. Sends can go out by group or as a whole page.

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 🔍 Search
Search across your collections by file name or caption text, then open any hit. Admins search across all users.

</td>
<td width="33%" valign="top">

### ⭐ Favorites
Star items from the scroll view. Favorites are per user, listed 100 per page and sent in groups of 10, with their own scroll and random view.

</td>
<td width="33%" valign="top">

### 🔗 Expiring Share Codes
Share a collection with a code. Pick 1h, 12h, 24h, 47h, a custom time from 1 minute to 47 hours, or no expiry. Regenerate, revoke and see who accessed it.

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 🔎 Duplicate Scanner
Groups items that share a Telegram `file_unique_id` using a SQLite window-function query. Review 10 groups per page, then confirm the deletion.

</td>
<td width="33%" valign="top">

### 🧹 Broken File Scanner
A background scan that checks stored `file_id`s against Telegram, with pause, resume and cancel. Invalid items are deleted in batches after you confirm.

</td>
<td width="33%" valign="top">

### 📤 Export & Import
Export a collection to a plain-text backup file, and import it back by sending the file to the bot (up to 5 MB, processed in batches of 500).

</td>
</tr>
<tr>
<td width="33%" valign="top">

### 📊 Collection Info
Item counts and sizes per type (video, photo, file and so on), the total size and item dates, from the collection management menu.

</td>
<td width="33%" valign="top">

### 🛡️ Admin Panel
Users, collections, shares and statistics, plus blocking users, cloning a collection to the admin, disabling a share code and a global file scan.

</td>
<td width="33%" valign="top">

### 🗂️ Activity Archive
Optional: mirror activity and errors to a log channel, and archive media to channels. A bounded queue drops entries instead of blocking handlers.

</td>
</tr>
</table>

---

## 🧠 Architecture

The bot is a single long-polling process built on `python-telegram-bot`. `bot.py` wires everything together: logging, the SQLite schema, the handler registry and the periodic jobs. `handlers/` holds the user flows (commands, callbacks, browsing, favorites, duplicates, broken-file scan, free-form messages), `db.py` is the only module that talks to SQLite, `utils.py` has keyboards, pagination and media-group sending, and `admin_panel.py` and `archive_logger.py` cover the admin side.

```
Update from Telegram
  └─ block_guard (group -3)            drops updates from blocked non-admin users
      └─ activity trackers (-2, -1)    refresh the session timestamp for the user
          └─ CommandHandler / CallbackQueryHandler   routed by command or callback prefix
              └─ ensure the user is the owner (management) or a viewer (read-only)
                  └─ db.py (SQLite, WAL)             read or write collections, items, shares
                      └─ reply in HTML parse mode, or start a per-user background job
```

Free-text and media messages go to `handlers/messages.py`, which first checks the user's current mode (adding items, confirming a delete, entering a search, entering a share code) and otherwise saves the item to the active collection.

---

## 🚀 Quick Start

```bash
git clone https://github.com/Omer-Dahan/Collections-bot
cd Collections-bot

# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

Create a `.env` file next to `bot.py` (it is git-ignored):

```env
BOT_TOKEN=123456:your-bot-token-from-BotFather
ADMIN_IDS=123456789,987654321

# Optional
ENABLE_ARCHIVING=false
LOG_CHANNEL_ID=0
ARCHIVE_CHANNELS=
```

Then start the bot:

```bash
python bot.py
```

The SQLite file `bot_data.db` and its tables are created on the first run.

---

## 🎮 Interactive Workflow

The interface is in Hebrew. The bot registers four commands in Telegram's command menu, and the rest are available by typing them.

| Command | Description |
|:---|:---|
| `/start` | Main menu and current status |
| `/newcollection` | Create a collection, optionally with a name: `/newcollection MyTops` |
| `/collections` | List your collections and pick the active one |
| `/browse` | Browse the items in your collections |
| `/manage` | Collection management: info, export, share, scans, delete |
| `/remove` | Single-item deletion mode |
| `/id_file` | Show the file ID of a file you send (developer tool) |
| `/access` | Open a shared collection with a code: `/access CODE123` |
| `/admin`, `/adminpanel` | Admin panel (admins only) |
| `/scanfiles` | Admin scan for invalid stored `file_id`s, all collections or one |

Command-menu entries (shown by Telegram when you type `/`): `/start`, `/access`, `/browse`, `/newcollection`.

```mermaid
graph TD
    A[/start] --> B{Main menu}
    B -->|New collection| C[Create collection]
    B -->|Select existing| D[Set active collection]
    B -->|Browse| E[Browse, scroll, search, favorites]
    B -->|Manage| F[Info, export, share, scans, delete]

    C --> G[Collecting mode]
    D --> G
    G -->|User sends a file or text| H[Saved to SQLite]
    H --> G

    F -->|Share| I[Generate code and expiry]
    I -->|Send code to a friend| J[Friend runs /access CODE]
    J --> E
```

A shared collection is read-only for the viewer. Management actions such as delete, share settings, scans and export are limited to the owner (and to admins where the menu says so).

---

## ⚙️ Running & Operations

- **Single instance.** On start, `bot.py` reads `.bot.pid` and kills a previous instance of itself so two pollers never share the database. The PID file is removed on a clean exit.
- **Zombie cleanup.** `python kill_bot.py` finds and stops stray processes running this directory's `bot.py`. It needs `psutil`, which is in `requirements.txt`.
- **Windows.** A local `run_bot.bat` can launch `venv\Scripts\python.exe bot.py`. Batch files are git-ignored in this repository, so create your own.
- **Logs.** `bot.log` is next to the source, rotated at 5 MB with 3 backups. Only user actions and errors go to the file, and everything goes to the console.
- **Backups.** The whole state lives in `bot_data.db`. Copy it while the bot is stopped, or use the per-collection export for a portable backup.
- **Updating.** Pull the new code, run `pip install -r requirements.txt` and restart. Schema migrations run automatically at startup.

---

## 🧱 Project Structure

```
Collections-bot/
├── bot.py                    # Entry point: logging, handler registry, jobs, PID lock
├── config.py                 # Reads .env: token, admins, archive and log channels
├── constants.py              # In-memory session state (active collections and shares)
├── db.py                     # SQLite schema, migrations, all queries
├── utils.py                  # Keyboards, pagination, media-group sending, background jobs
├── admin_panel.py            # Admin menus, user blocking, global file scan
├── archive_logger.py         # Optional activity log and media archiving queue
├── message_tracker.py        # Tracks bot messages for later cleanup
├── kill_bot.py               # Stops leftover bot processes of this directory
├── requirements.txt          # Dependencies
├── LICENSE.txt               # GPL-3.0
└── handlers/
    ├── commands.py           # /start, /newcollection, /collections, /browse, /manage ...
    ├── callbacks.py          # Management, sharing, export, collection info buttons
    ├── browse_handlers.py    # Paged browse, scroll view, random item, search entry
    ├── favorites.py          # Per-user favorites: list, scroll, random, toggle
    ├── duplicate_handlers.py # Duplicate scan and confirmed deletion
    ├── invalid_items_scan.py # Background broken-file scan with pause and resume
    └── messages.py           # Mode-aware text and media intake, import, share codes
```

---

## 🔧 Configuration Matrix

All values are read from the environment by `config.py` (a `.env` file is loaded automatically).

| Variable | Group | What it does | Default |
|---|---|---|---|
| `BOT_TOKEN` | Core | Telegram bot token. Without it the bot cannot start. | none |
| `ADMIN_IDS` | Core | Comma-separated Telegram user IDs with admin rights. Non-numeric entries are ignored. | empty (no admins) |
| `ENABLE_ARCHIVING` | Archiving | Set to `true` to turn on the activity log and media archiving. | `False` |
| `LOG_CHANNEL_ID` | Archiving | Channel for activity events and errors. Archiving stays off while this is `0`. | `0` |
| `ARCHIVE_CHANNELS` | Archiving | Comma-separated channel IDs (negative IDs allowed) used to archive media. | empty |

---

## 💡 Design Decisions / Reliability

- **File IDs, not files.** Items store Telegram `file_id` and `file_unique_id`, so storage stays small and sends are instant. The cost is that an item can go stale, which is what the broken-file scanner is for.
- **One background job per user.** Bulk sends run through `start_user_job`, which rejects a second job from the same user, and admin file scans run as background tasks. This keeps the sequential update loop responsive.
- **Owner checks on every management action.** Viewing uses a looser access check (owner, admin or active share viewer), while delete, share, scan, export and set-active require ownership.
- **Flood control.** The application uses `AIORateLimiter` with 3 retries on 429 responses, and the incoming update queue is bounded to 100 so a burst waits at Telegram instead of in memory.
- **Safe messages.** Replies use HTML parse mode and escape user-provided values such as collection names and usernames.
- **Expiry housekeeping.** A job runs every minute to expire shares and remove the messages they delivered. In-memory sessions and `user_data` expire after one hour of inactivity.
- **Bounded memory on import.** Imports are streamed from a temporary file in batches of 500 rather than loaded whole.
- **Schema safety.** Tables are created with `IF NOT EXISTS`, migrations add missing columns, and favorites and archive rows are removed by foreign-key cascade when an item is deleted.

---

## ⚠️ Known Limitations

- **Telegram owns the files.** The database holds references, not content. If Telegram invalidates a `file_id` the item stops sending until it is removed (use the broken-file scan).
- **Deleting a chat message does not delete the item.** Use the bot's own delete tools to remove it from the database.
- **Share expiry tops out at 47 hours,** or you can pick no expiry.
- **Import size is capped at 5 MB** per file.
- **Hebrew-only interface.** The UI strings are not localized.
- **Single process, long polling.** There is no webhook mode and no horizontal scaling, because the state lives in one SQLite file.
- **Back up `bot_data.db`.** Deleting it removes every collection.

---

## 📜 License & Credits

Licensed under the **GNU General Public License v3.0**, see [LICENSE.txt](LICENSE.txt). Any redistribution or modification must comply with that license.

The bot is intended for lawful use only. Responsibility for stored content and for compliance with local laws and platform policies lies with the user.

<div align="center">

**Made with ❤️ by Omer**

</div>
