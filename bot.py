import asyncio
import contextlib
import html
import io
import logging
import os
import random
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set
from urllib.parse import urljoin

from telegram import (
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
    User,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8682681706:AAErMVOtyLVOJl69Bs4jpL_WS8o6QHzDCj0")

BAN_IMAGE_URL = os.environ.get(
    "BAN_IMAGE_URL",
    "https://kommodo.ai/i/ByyY31VH3SrpeiByrA3Z",
)
UNBAN_IMAGE_URL = os.environ.get(
    "UNBAN_IMAGE_URL",
    "https://kommodo.ai/i/SZI9YH0ZFmvP16N7VzV6",
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("cruxiferw")


ADMIN_IDS_URL = "https://github.com/leviashford/Anonova/blob/main/adminids.txt"
DEFAULT_ADMIN_IDS: Set[int] = {7480132504, 6646862550, 6938342889}


def fetch_admin_ids() -> Set[int]:
    ids: Set[int] = set()
    try:
        req = urllib.request.Request(
            ADMIN_IDS_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AnonovaBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
        for line in content.splitlines():
            line = line.strip()
            if line.isdigit():
                ids.add(int(line))
        if ids:
            logger.info("Fetched %d admin IDs from GitHub.", len(ids))
    except Exception as e:
        logger.warning("Failed to fetch admin IDs from GitHub: %s", e)

    if not ids:
        _admin_env = os.environ.get("ADMIN_IDS", "")
        for _id in _admin_env.replace(" ", "").split(","):
            if _id.isdigit():
                ids.add(int(_id))

    if not ids:
        ids = set(DEFAULT_ADMIN_IDS)

    return ids


ADMIN_IDS: Set[int] = fetch_admin_ids()

_admin_chat_env = os.environ.get("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS: Set[int] = set()
for _id in _admin_chat_env.replace(" ", "").split(","):
    if _id.lstrip("-").isdigit():
        ADMIN_CHAT_IDS.add(int(_id))
if not ADMIN_CHAT_IDS:
    ADMIN_CHAT_IDS = set(ADMIN_IDS)

DB_PATH = "bot_data.db"
MAINTENANCE = False

FORCE_SUB_CHANNEL: Optional[int] = None
FORCE_SUB_LINK: Optional[str] = None
FORCE_SUB_ENABLED: bool = False
FORCE_SUB_TITLE: Optional[str] = None
FORCE_SUB_TYPE: str = "channel"
SEARCH_TIMEOUT_SECONDS = 60

_BAN_FILE_ID: Optional[str] = None
_UNBAN_FILE_ID: Optional[str] = None


REPORT_FLOW: Dict[int, dict] = {}

HEADER = (
    "୨୧ 𝐀𝐍𝐎𝐍𝐎𝐕𝐀 ୨୧\n"
    "𝐖ʜᴇʀᴇ sᴛʀᴀɴɢᴇʀs ᴍᴇᴇᴛ"
)


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                is_banned INTEGER DEFAULT 0,
                banned_until TEXT,
                report_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS blocks (
                blocker_id INTEGER NOT NULL,
                blocked_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (blocker_id, blocked_id)
            );
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER NOT NULL,
                reported_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                reason TEXT,
                proof_file_id TEXT,
                proof_type TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stats (
                key TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS welcome_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL UNIQUE,
                caption TEXT,
                added_by INTEGER,
                added_at TEXT NOT NULL
            );
            """
        )

        cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "banned_until" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN banned_until TEXT")

        rcols = {r["name"] for r in conn.execute("PRAGMA table_info(reports)").fetchall()}
        if "reason" not in rcols:
            conn.execute("ALTER TABLE reports ADD COLUMN reason TEXT")
        if "proof_file_id" not in rcols:
            conn.execute("ALTER TABLE reports ADD COLUMN proof_file_id TEXT")
        if "proof_type" not in rcols:
            conn.execute("ALTER TABLE reports ADD COLUMN proof_type TEXT")

        welcome_default = (
            HEADER
            + "\n\n"
            + "      ⋆｡°✩ ─────────────── ✩°｡⋆\n\n"
            + "          ୨୧ 𝐖ᴇʟᴄᴏᴍᴇ ᴛᴏ ୨୧\n"
            + "             𝐀 𝐍 𝐎 𝐍 𝐎 𝐕 𝐀\n\n"
            + "       ☁ 𝐖ʜᴇʀᴇ sᴛʀᴀɴɢᴇʀs ᴍᴇᴇᴛ\n"
            + "       ✧ 𝐖ʜᴇʀᴇ ᴄᴏɴɴᴇᴄᴛɪᴏɴs ʙᴇɢɪɴ ✧\n\n"
            + "      ⋆｡°✩ ─────────────── ✩°｡⋆\n\n"
            + "       ୨୧ 𝐘ᴏᴜʀ ɴᴇxᴛ ᴄᴏɴᴠᴇʀsᴀᴛɪᴏɴ\n"
            + "              ɪs ᴡᴀɪᴛɪɴɢ ୨୧\n\n"
            + "       ☁ 𝐏ʀᴇss /find ᴛᴏ ᴍᴇᴇᴛ\n"
            + "              sᴏᴍᴇᴏɴᴇ ɴᴇᴡ\n\n"
            + "      ⋆｡°✩ ─────────────── ✩°｡⋆\n\n"
            + "           𝐃ᴇᴠ  ⌁➤  @CRUXAETH"
        )

        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            ("welcome_message", welcome_default),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            ("rules", "1. Be respectful.\n2. No harassment, threats, or scams.\n3. No sharing private information.\n4. No NSFW content.\n5. Minimum age: 18+."),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            ("force_sub_enabled", "0"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            ("force_sub_type", "channel"),
        )
        conn.execute("INSERT OR IGNORE INTO stats(key, value) VALUES ('total_messages', 0)")
        conn.commit()


def register_user(user: User) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO users(user_id, first_seen, last_seen) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen",
            (user.id, now, now),
        )
        conn.commit()


def is_banned(user_id: int) -> bool:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT is_banned, banned_until FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return False
        if row["is_banned"]:
            return True
        bu = row["banned_until"]
        if bu:
            try:
                until = datetime.fromisoformat(bu)
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < until:
                    return True
                conn.execute(
                    "UPDATE users SET banned_until=NULL, is_banned=0 WHERE user_id=?",
                    (user_id,),
                )
                conn.commit()
                return False
            except (ValueError, TypeError):
                return False
        return False


def get_ban_info(user_id: int) -> Optional[dict]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT is_banned, banned_until FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if not row:
            return None
        if row["is_banned"]:
            return {"permanent": True, "until": None, "days_left": None}
        bu = row["banned_until"]
        if not bu:
            return None
        try:
            until = datetime.fromisoformat(bu)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None
        now = datetime.now(timezone.utc)
        if until <= now:
            return None
        delta = until - now
        days_left = max(1, delta.days + (1 if delta.seconds or delta.microseconds else 0))
        return {"permanent": False, "until": until, "days_left": days_left}


def set_ban(user_id: int, banned: bool) -> None:
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET is_banned=?, banned_until=NULL WHERE user_id=?",
            (1 if banned else 0, user_id),
        )
        conn.commit()


def set_temp_ban(user_id: int, days: int) -> datetime:
    until = datetime.now(timezone.utc) + timedelta(days=days)
    with db_conn() as conn:
        conn.execute(
            "UPDATE users SET is_banned=0, banned_until=? WHERE user_id=?",
            (until.isoformat(), user_id),
        )
        row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO users(user_id, first_seen, last_seen, is_banned, banned_until) "
                "VALUES (?, ?, ?, 0, ?)",
                (user_id, now, now, until.isoformat()),
            )
        conn.commit()
    return until


def get_blocks(user_id: int) -> Set[int]:
    with db_conn() as conn:
        rows = conn.execute("SELECT blocked_id FROM blocks WHERE blocker_id=?", (user_id,)).fetchall()
        return {r["blocked_id"] for r in rows}


def add_block(blocker: int, blocked: int) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO blocks(blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
            (blocker, blocked, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def remove_block(blocker: int, blocked: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?", (blocker, blocked))
        conn.commit()


def add_report(
    reporter: int,
    reported: int,
    reason: Optional[str] = None,
    proof_file_id: Optional[str] = None,
    proof_type: Optional[str] = None,
) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO reports(reporter_id, reported_id, created_at, reason, proof_file_id, proof_type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                reporter,
                reported,
                datetime.now(timezone.utc).isoformat(),
                reason,
                proof_file_id,
                proof_type,
            ),
        )
        conn.execute("UPDATE users SET report_count = report_count + 1 WHERE user_id=?", (reported,))
        conn.commit()


def increment_stat(key: str) -> None:
    with db_conn() as conn:
        conn.execute("UPDATE stats SET value = value + 1 WHERE key=?", (key,))
        conn.commit()


def get_setting(key: str, default: str = "") -> str:
    with db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_counts() -> Dict[str, int]:
    with db_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned=1").fetchone()["c"]
        reports = conn.execute("SELECT COUNT(*) as c FROM reports").fetchone()["c"]
        blocked = conn.execute("SELECT COUNT(*) as c FROM blocks").fetchone()["c"]
        total_messages = conn.execute("SELECT value FROM stats WHERE key='total_messages'").fetchone()
        total_videos = conn.execute("SELECT COUNT(*) as c FROM welcome_videos").fetchone()["c"]
        return {
            "total_users": total_users,
            "banned": banned,
            "reports": reports,
            "blocked": blocked,
            "total_messages": total_messages["value"] if total_messages else 0,
            "total_videos": total_videos,
        }


def add_welcome_video(file_id: str, admin_id: int) -> bool:
    try:
        with db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO welcome_videos(file_id, caption, added_by, added_at) "
                "VALUES (?, NULL, ?, ?)",
                (file_id, admin_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        return True
    except sqlite3.Error as e:
        logger.warning("Failed to add welcome video: %s", e)
        return False


def get_welcome_videos() -> list:
    with db_conn() as conn:
        rows = conn.execute("SELECT id, file_id, caption FROM welcome_videos").fetchall()
        return [dict(r) for r in rows]


def remove_welcome_video(video_id: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM welcome_videos WHERE id=?", (video_id,))
        conn.commit()


def get_random_welcome_video() -> Optional[dict]:
    videos = get_welcome_videos()
    return random.choice(videos) if videos else None


DEFAULT_VIDEO_CAPTION = (
    "      ⋆｡°✩ ───────────── ✩°｡⋆\n"
    "           ୨୧ 𝐀𝐍𝐎𝐍𝐎𝐕𝐀 ୨୧\n"
    "        𝐖ʜᴇʀᴇ sᴛʀᴀɴɢᴇʀs ᴍᴇᴇᴛ\n"
    "      ⋆｡°✩ ───────────── ✩°｡⋆\n"
    "\n"
    "       ୨୧ <b>𝐖ᴇʟᴄᴏᴍᴇ, {mention}!</b> ୨୧\n"
    "\n"
    "       ✧ ── 𝐇ᴏᴡ ᴛᴏ ʙᴇɢɪɴ ── ✧\n"
    "\n"
    "       ୨୧ 𝐏ʀᴇss /find ᴛᴏ ᴍᴇᴇᴛ sᴏᴍᴇᴏɴᴇ\n"
    "       ୨୨ 𝐔sᴇ /skip ᴛᴏ sᴋɪᴘ ᴀ ᴘᴀʀᴛɴᴇʀ\n"
    "       ୨୨ 𝐔sᴇ /stop ᴛᴏ ᴇɴᴅ ᴛʜᴇ ᴄʜᴀᴛ\n"
    "\n"
    "      ⋆｡°✩ ───────────── ✩°｡⋆\n"
    "          𝐃ᴇᴠ ↳ @CRUXAETH\n"
)


async def _fetch_remote_image(url: str, _depth: int = 0) -> Optional[io.BytesIO]:
    if _depth > 2:
        return None
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; AnonovaBot/1.0)",
                "Accept": "image/jpeg,image/jpg,image/png,image/webp,image/*,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            data = resp.read()
        if not data:
            return None

        head = data[:512].lstrip().lower()
        is_html = (
            "text/html" in content_type
            or head.startswith(b"<!doctype html")
            or head.startswith(b"<html")
        )

        if is_html:
            text = data.decode("utf-8", errors="ignore")
            patterns = [
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
                r'<img[^>]+src=["\']([^"\']+)["\']',
            ]
            for pat in patterns:
                m = re.search(pat, text, re.I)
                if m:
                    img_url = html.unescape(m.group(1))
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    elif img_url.startswith("/"):
                        img_url = urljoin(url, img_url)
                    logger.info("HTML page detected; following image: %s", img_url)
                    return await _fetch_remote_image(img_url, _depth + 1)
            logger.warning("HTML page at %s has no extractable image.", url)
            return None

        buf = io.BytesIO(data)
        if "png" in content_type:
            buf.name = "image.png"
        elif "webp" in content_type:
            buf.name = "image.webp"
        elif "gif" in content_type:
            buf.name = "image.gif"
        else:
            buf.name = "image.jpg"
        return buf
    except Exception as e:
        logger.warning("Remote image fetch failed for %s: %s", url, e)
        return None


async def _resolve_image_file_id(
    context: ContextTypes.DEFAULT_TYPE, url: str
) -> Optional[str]:
    target = next(iter(ADMIN_IDS)) if ADMIN_IDS else None
    if target is None:
        return None

    try:
        m = await context.bot.send_photo(chat_id=target, photo=url)
        if m.photo:
            logger.info("Resolved image via URL: %s", url)
            return m.photo[-1].file_id
    except (BadRequest, Forbidden, TelegramError) as e:
        logger.warning("send_photo(url) failed for %s: %s", url, e)

    buf = await _fetch_remote_image(url)
    if buf is not None:
        try:
            m = await context.bot.send_photo(chat_id=target, photo=buf)
            if m.photo:
                logger.info("Resolved image via download: %s", url)
                return m.photo[-1].file_id
        except (BadRequest, Forbidden, TelegramError) as e:
            logger.warning("send_photo(download) failed for %s: %s", url, e)

    return None


async def _send_png(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    url: str,
    cached_file_id: Optional[str],
    caption: str,
) -> bool:
    if len(caption) > 1024:
        caption = caption[:1021] + "..."

    if cached_file_id:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=cached_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return True
        except (Forbidden, BadRequest, TelegramError) as e:
            logger.warning("Cached file_id failed for %s: %s", chat_id, e)

    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=url,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return True
    except (Forbidden, BadRequest, TelegramError) as e:
        logger.warning("URL send_photo failed for %s: %s", chat_id, e)

    buf = await _fetch_remote_image(url)
    if buf is not None:
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=buf,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return True
        except (Forbidden, BadRequest, TelegramError) as e:
            logger.warning("Downloaded send_photo failed for %s: %s", chat_id, e)

    with contextlib.suppress(Forbidden, BadRequest, TelegramError):
        await context.bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML)
        return True
    return False


async def send_ban_notice(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    global _BAN_FILE_ID
    info = get_ban_info(user_id)
    if info is None:
        return
    if info["permanent"]:
        caption = (
            "🚫 <b>𝐘ᴏᴜ 𝐀ʀᴇ 𝐁ᴀɴɴᴇᴅ</b>\n\n"
            "Yᴏᴜʀ ᴀᴄᴄᴇss ᴛᴏ ᴛʜɪs ʙᴏᴛ ʜᴀs ʙᴇᴇɴ "
            "ᴘᴇʀᴍᴀɴᴇɴᴛʟʏ ʀᴇsᴛʀɪᴄᴛᴇᴅ.\n\n"
            "Iꜰ ʏᴏᴜ ʙᴇʟɪᴇᴠᴇ ᴛʜɪs ᴀᴄᴛɪᴏɴ ᴡᴀs ᴛᴀᴋᴇɴ "
            "ɪɴ ᴇʀʀᴏʀ, ᴘʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ.\n\n"
            "✉️ <b>𝐒ᴜᴘᴘᴏʀᴛ:</b> @CRUXAETH"
        )
    else:
        days_left = info["days_left"]
        until_str = info["until"].strftime("%Y-%m-%d %H:%M UTC")
        caption = (
            "🚫 <b>𝐘ᴏᴜ 𝐀ʀᴇ 𝐁ᴀɴɴᴇᴅ</b>\n\n"
            f"Yᴏᴜʀ ᴀᴄᴄᴇss ʜᴀs ʙᴇᴇɴ ʀᴇsᴛʀɪᴄᴛᴇᴅ ꜰᴏʀ "
            f"<b>{days_left} ᴅᴀʏ(s)</b>.\n\n"
            f"⌛ <b>𝐄xᴘɪʀᴇs:</b> {until_str}\n\n"
            "Iꜰ ʏᴏᴜ ʙᴇʟɪᴇᴠᴇ ᴛʜɪs ᴀᴄᴛɪᴏɴ ɪs ɪɴᴄᴏʀʀᴇᴄᴛ, "
            "ᴘʟᴇᴀsᴇ ᴄᴏɴᴛᴀᴄᴛ sᴜᴘᴘᴏʀᴛ.\n\n"
            "✉️ <b>𝐒ᴜᴘᴘᴏʀᴛ:</b> @CRUXAETH"
        )

    delivered = False
    if _BAN_FILE_ID:
        delivered = await _send_png(context, user_id, BAN_IMAGE_URL, _BAN_FILE_ID, caption)
        if not delivered:
            _BAN_FILE_ID = None

    if not delivered:
        fid = await _resolve_image_file_id(context, BAN_IMAGE_URL)
        if fid:
            _BAN_FILE_ID = fid
        delivered = await _send_png(context, user_id, BAN_IMAGE_URL, _BAN_FILE_ID, caption)

    if not delivered:
        with contextlib.suppress(Forbidden, BadRequest, TelegramError):
            await context.bot.send_message(user_id, caption, parse_mode=ParseMode.HTML)
            logger.info("Sent ban text fallback to %s", user_id)


async def send_unban_notice(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    global _UNBAN_FILE_ID
    caption = (
        "✅ <b>𝐘ᴏᴜ 𝐀ʀᴇ 𝐔ɴʙᴀɴɴᴇᴅ</b>\n\n"
        "Yᴏᴜ ᴄᴀɴ ɴᴏᴡ ᴀᴄᴄᴇss ᴛʜᴇ ʙᴏᴛ ᴀɢᴀɪɴ.\n\n"
        "◈ <b>𝐒ᴛᴀʀᴛ ᴄʜᴀᴛᴛɪɴɢ:</b> /find"
    )

    if _UNBAN_FILE_ID:
        ok = await _send_png(context, user_id, UNBAN_IMAGE_URL, _UNBAN_FILE_ID, caption)
        if ok:
            return
        _UNBAN_FILE_ID = None

    fid = await _resolve_image_file_id(context, UNBAN_IMAGE_URL)
    if fid:
        _UNBAN_FILE_ID = fid
    await _send_png(context, user_id, UNBAN_IMAGE_URL, _UNBAN_FILE_ID, caption)


async def send_welcome(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    if user is None:
        return

    mention = user.mention_html()
    username = html.escape(user.first_name or "friend")

    welcome = get_setting("welcome_message", HEADER)
    try:
        welcome = welcome.format(mention=mention, username=username)
    except (KeyError, IndexError, ValueError):
        pass

    video = get_random_welcome_video()
    if video:
        try:
            base_caption = DEFAULT_VIDEO_CAPTION.format(mention=mention, username=username)
        except (KeyError, IndexError, ValueError):
            base_caption = DEFAULT_VIDEO_CAPTION

        caption = base_caption
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        try:
            await context.bot.send_video(
                chat_id=user.id,
                video=video["file_id"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_kb(),
                supports_streaming=True,
            )
            logger.info("Welcome video #%s sent to %s", video["id"], user.id)
            return
        except (BadRequest, Forbidden, TelegramError) as e:
            logger.warning("Welcome video failed for %s (video #%s): %s", user.id, video["id"], e)

    logger.info("Falling back to text welcome for %s", user.id)
    with contextlib.suppress(Forbidden, BadRequest):
        await context.bot.send_message(
            user.id,
            welcome,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )


def load_force_sub_settings() -> None:
    global FORCE_SUB_CHANNEL, FORCE_SUB_LINK, FORCE_SUB_ENABLED, FORCE_SUB_TITLE, FORCE_SUB_TYPE
    FORCE_SUB_ENABLED = get_setting("force_sub_enabled", "0") == "1"
    FORCE_SUB_TYPE = get_setting("force_sub_type", "channel")
    ch = get_setting("force_sub_channel", "")
    if ch:
        with contextlib.suppress(ValueError):
            FORCE_SUB_CHANNEL = int(ch)
    lk = get_setting("force_sub_link", "")
    if lk:
        FORCE_SUB_LINK = lk
    ti = get_setting("force_sub_title", "")
    if ti:
        FORCE_SUB_TITLE = ti


async def check_channel_membership(bot, user_id: int) -> bool:
    if not FORCE_SUB_ENABLED or FORCE_SUB_CHANNEL is None:
        return True
    try:
        member = await bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
        return member.status in (
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
            ChatMember.RESTRICTED,
        )
    except (Forbidden, BadRequest):
        return False
    except TelegramError:
        return False


def force_sub_kb() -> InlineKeyboardMarkup:
    buttons = []
    label = "୨୧ 𝐉ᴏɪɴ 𝐆ʀᴏᴜᴘ ☁" if FORCE_SUB_TYPE == "group" else "୨୧ 𝐉ᴏɪɴ 𝐂ʜᴀɴɴᴇʟ ☁"
    if FORCE_SUB_LINK:
        buttons.append([InlineKeyboardButton(label, url=FORCE_SUB_LINK)])
    else:
        buttons.append([InlineKeyboardButton(label, callback_data="noop")])
    buttons.append([InlineKeyboardButton("✧ 𝐕ᴇʀɪꜰʏ 𝐉ᴏɪɴ ✧", callback_data="verify_sub")])
    return InlineKeyboardMarkup(buttons)


def _force_sub_text() -> str:
    target_word = "ɢʀᴏᴜᴘ" if FORCE_SUB_TYPE == "group" else "ᴄʜᴀɴɴᴇʟ"
    return (
        f"{HEADER}\n\n"
        "       ୨୧ 𝐉ᴏɪɴ 𝐑ᴇǫᴜɪʀᴇᴅ ୨୧\n\n"
        f"       ᴘʟᴇᴀsᴇ ᴊᴏɪɴ ᴏᴜʀ ᴏꜰꜰɪᴄɪᴀʟ\n"
        f"       {target_word} ꜰɪʀsᴛ. ☾\n\n"
    )


async def send_force_sub_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = _force_sub_text()
    kb = force_sub_kb()
    user = update.effective_user

    video = get_random_welcome_video()
    if video and user is not None:
        caption = text
        if len(caption) > 1024:
            caption = caption[:1021] + "..."
        try:
            await context.bot.send_video(
                chat_id=user.id,
                video=video["file_id"],
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                supports_streaming=True,
            )
            logger.info("Force-sub video #%s sent to %s", video["id"], user.id)
            return
        except (BadRequest, Forbidden, TelegramError) as e:
            logger.warning("Force-sub video failed for %s (video #%s): %s", user.id, video["id"], e)

    if update.callback_query:
        with contextlib.suppress(BadRequest):
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


_queue: asyncio.Queue = asyncio.Queue()
_queued: Set[int] = set()
_active: Dict[int, int] = {}
_partner_to_user: Dict[int, int] = {}
_searching_tasks: Dict[int, asyncio.Task] = {}
_timeout_tasks: Dict[int, asyncio.Task] = {}
_lock = asyncio.Lock()


def _can_match(a: int, b: int) -> bool:
    if a == b:
        return False
    if is_banned(a) or is_banned(b):
        return False
    if a in get_blocks(b) or b in get_blocks(a):
        return False
    return True


async def _try_match(user_id: int) -> Optional[int]:
    skipped: list = []
    partner: Optional[int] = None
    while not _queue.empty():
        candidate = _queue.get_nowait()
        if candidate == user_id or not _can_match(user_id, candidate):
            skipped.append(candidate)
            continue
        partner = candidate
        _queued.discard(candidate)
        break
    for sid in skipped:
        await _queue.put(sid)
    return partner


async def find_partner(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    async with _lock:
        if is_banned(user_id):
            return False
        if user_id in _active:
            return True
        if user_id in _queued:
            return False
        partner_id = await _try_match(user_id)
        if partner_id is not None:
            if is_banned(partner_id):
                _queued.discard(partner_id)
                _active.pop(partner_id, None)
                return await find_partner(user_id, context)
            _active[user_id] = partner_id
            _active[partner_id] = user_id
            _partner_to_user[user_id] = partner_id
            _partner_to_user[partner_id] = user_id
            for uid in (user_id, partner_id):
                task = _searching_tasks.pop(uid, None)
                if task and not task.done():
                    task.cancel()
                ttask = _timeout_tasks.pop(uid, None)
                if ttask and not ttask.done():
                    ttask.cancel()
            return True
        _queued.add(user_id)
        await _queue.put(user_id)
        return False


def disconnect(user_id: int) -> Optional[int]:
    partner = _active.pop(user_id, None)
    if partner is not None:
        _active.pop(partner, None)
        _partner_to_user.pop(user_id, None)
        _partner_to_user.pop(partner, None)
    if user_id in _queued:
        _queued.discard(user_id)
    task = _searching_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()
    ttask = _timeout_tasks.pop(user_id, None)
    if ttask and not ttask.done():
        ttask.cancel()
    return partner


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("୨୧ 𝐅ɪɴᴅ 𝐏ᴀʀᴛɴᴇʀ ☁", callback_data="find")],
            [
                InlineKeyboardButton("⏹ 𝐒ᴛᴏᴘ", callback_data="stop"),
                InlineKeyboardButton("☾ 𝐒ᴛᴀᴛᴜs", callback_data="status"),
            ],
        ]
    )


def active_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("୨୧ 𝐒ᴋɪᴘ ✧", callback_data="next"),
                InlineKeyboardButton("☾ 𝐒ᴛᴏᴘ", callback_data="stop"),
            ],
            [
                InlineKeyboardButton("⚑ 𝐑ᴇᴘᴏʀᴛ", callback_data="report"),
                InlineKeyboardButton("🔒 𝐁ʟᴏᴄᴋ", callback_data="block"),
            ],
        ]
    )


def searching_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("୨୧ 𝐂ᴀɴᴄᴇʟ 𝐒ᴇᴀʀᴄʜ ☁", callback_data="cancel")]]
    )


def not_found_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Try Again", callback_data="find"),
                InlineKeyboardButton("Back", callback_data="back_menu"),
            ]
        ]
    )


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("୨୧ 𝐒ᴛᴀᴛs ✧", callback_data="admin_stats")],
            [InlineKeyboardButton("☁ 𝐁ʀᴏᴀᴅᴄᴀsᴛ", callback_data="admin_broadcast")],
            [InlineKeyboardButton("⚑ 𝐁ᴀɴ / 𝐔ɴʙᴀɴ", callback_data="admin_ban_menu")],
            [InlineKeyboardButton("✧ 𝐌ᴀɪɴᴛᴇɴᴀɴᴄᴇ", callback_data="admin_maintenance")],
            [InlineKeyboardButton("୨୧ 𝐄ᴅɪᴛ 𝐖ᴇʟᴄᴏᴍᴇ", callback_data="admin_edit_welcome")],
            [InlineKeyboardButton("☾ 𝐄ᴅɪᴛ 𝐑ᴜʟᴇs", callback_data="admin_edit_rules")],
            [InlineKeyboardButton("☁ 𝐅ᴏʀᴄᴇ 𝐒ᴜʙ", callback_data="admin_force_sub")],
            [
                InlineKeyboardButton("🎬 𝐀ᴅᴅ 𝐕ɪᴅᴇᴏ", callback_data="admin_add_video"),
                InlineKeyboardButton("📋 𝐕ɪᴅᴇᴏs", callback_data="admin_list_videos"),
            ],
        ]
    )


SPINNER_FRAMES = ["◐", "◓", "◑", "◒"]

SEARCH_STAGES = [
    "୨୧ 𝐅ɪɴᴅɪɴɢ ʏᴏᴜʀ 𝐏ᴀʀᴛɴᴇʀ",
    "⌁ 𝐒ᴇᴀʀᴄʜɪɴɢ ғᴏʀ 𝐀ᴠᴀɪʟᴀʙʟᴇ 𝐔sᴇʀs",
    "⋆｡°✩ 𝐄sᴛᴀʙʟɪsʜɪɴɢ 𝐂ᴏɴɴᴇᴄᴛɪᴏɴ ✩°｡⋆",
    "☷ 𝐒ᴄᴀɴɴɪɴɢ 𝐀ᴄᴛɪᴠᴇ 𝐔sᴇʀs",
    "⌁ 𝐋ᴏᴄᴀᴛɪɴɢ 𝐀 𝐌ᴀᴛᴄʜ",
    "↳ 𝐏ʀᴇᴘᴀʀɪɴɢ 𝐘ᴏᴜʀ 𝐂ʜᴀᴛ",
]

SEARCH_TIPS = [
    "⌁ 𝐏ʟᴇᴀsᴇ 𝐖ᴀɪᴛ 𝐖ʜɪʟᴇ 𝐖ᴇ 𝐅ɪɴᴅ 𝐀 𝐌ᴀᴛᴄʜ...",
    "୨୧ 𝐖ᴇ'ʀᴇ 𝐋ᴏᴏᴋɪɴɢ ғᴏʀ 𝐀 𝐔sᴇʀ 𝐅ᴏʀ 𝐘ᴏᴜ",
    "⋆｡°✩ 𝐘ᴏᴜʀ 𝐂ᴏɴɴᴇᴄᴛɪᴏɴ 𝐈s 𝐁ᴇɪɴɢ 𝐏ʀᴏᴄᴇssᴇᴅ ✩°｡⋆",
    "☷ 𝐒ᴇᴀʀᴄʜ 𝐈ɴ 𝐏ʀᴏɢʀᴇss — 𝐏ʟᴇᴀsᴇ 𝐖ᴀɪᴛ",
    "୨୧ 𝐍ᴏ 𝐌ᴀᴛᴄʜ 𝐘ᴇᴛ. 𝐂ᴏɴᴛɪɴᴜɪɴɢ 𝐒ᴇᴀʀᴄʜ...",
    "↳ 𝐒ᴛᴀʏ 𝐎ɴʟɪɴᴇ 𝐅ᴏʀ 𝐅ᴀsᴛᴇʀ 𝐌ᴀᴛᴄʜɪɴɢ",
]

PROGRESS_BARS = [
    "▱▱▱▱▱▱▱▱",
    "▰▱▱▱▱▱▱▱",
    "▰▰▱▱▱▱▱▱",
    "▰▰▰▱▱▱▱▱",
    "▰▰▰▰▱▱▱▱",
    "▰▰▰▰▰▱▱▱",
    "▰▰▰▰▰▰▱▱",
    "▰▰▰▰▰▰▰▱",
    "▰▰▰▰▰▰▰▰",
]


def _build_search_text(frame_idx: int, stage_idx: int, tip_idx: int, bar_idx: int) -> str:
    spinner = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    stage = SEARCH_STAGES[stage_idx % len(SEARCH_STAGES)]
    tip = SEARCH_TIPS[tip_idx % len(SEARCH_TIPS)]
    bar = PROGRESS_BARS[bar_idx % len(PROGRESS_BARS)]
    return (
        f"{HEADER}\n\n"
        f"       {spinner} <b>{stage}</b>\n"
        f"       <code>{bar}</code>\n\n"
        f"       <i>{tip}</i>"
    )


async def safe_edit_message(
    source,
    text: str,
    parse_mode: str = ParseMode.HTML,
    reply_markup=None,
    edit_only: bool = False,
) -> None:
    query = None
    message = None

    if hasattr(source, "callback_query") and hasattr(source, "effective_user"):
        query = getattr(source, "callback_query", None)
        if query is not None:
            message = query.message
        else:
            message = getattr(source, "effective_message", None)
    elif hasattr(source, "edit_message_text") and hasattr(source, "message"):
        query = source
        message = source.message
    elif isinstance(source, Message):
        message = source
    else:
        message = source

    async def _try_edit() -> bool:
        if query is not None and message is not None:
            try:
                await query.edit_message_text(
                    text, parse_mode=parse_mode, reply_markup=reply_markup
                )
                return True
            except BadRequest as e:
                err = str(e).lower()
                if "no text" in err or "there is no text" in err:
                    try:
                        await query.edit_message_caption(
                            caption=text,
                            parse_mode=parse_mode,
                            reply_markup=reply_markup,
                        )
                        return True
                    except BadRequest as e2:
                        logger.debug("edit_message_caption fallback failed: %s", e2)
                elif "message is not modified" in err:
                    return True
                else:
                    logger.debug("edit_message_text failed: %s", e)
            except TelegramError as e:
                logger.debug("edit_message_text TelegramError: %s", e)
            return False

        if isinstance(message, Message):
            try:
                await message.edit_text(
                    text, parse_mode=parse_mode, reply_markup=reply_markup
                )
                return True
            except BadRequest as e:
                err = str(e).lower()
                if "no text" in err or "there is no text" in err:
                    try:
                        await message.edit_caption(
                            caption=text,
                            parse_mode=parse_mode,
                            reply_markup=reply_markup,
                        )
                        return True
                    except BadRequest as e2:
                        logger.debug("message.edit_caption fallback failed: %s", e2)
                elif "message is not modified" in err:
                    return True
                else:
                    logger.debug("message.edit_text failed: %s", e)
            except TelegramError as e:
                logger.debug("message.edit_text TelegramError: %s", e)
            return False

        return False

    if await _try_edit():
        return

    if edit_only:
        return

    try:
        target_chat = getattr(message, "chat", None)
        if target_chat is not None:
            await target_chat.send_message(
                text, parse_mode=parse_mode, reply_markup=reply_markup
            )
    except TelegramError as e:
        logger.debug("Fallback send_message failed: %s", e)


async def animate_search(message: Message, user_id: int) -> None:
    frame_idx = 0
    stage_idx = 0
    tip_idx = 0
    bar_idx = 0
    tick = 0
    try:
        while True:
            if user_id not in _queued:
                break
            text = _build_search_text(frame_idx, stage_idx, tip_idx, bar_idx)
            try:
                await safe_edit_message(
                    message,
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=searching_kb(),
                    edit_only=True,
                )
            except BadRequest:
                pass
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramError:
                pass

            frame_idx += 1
            tick += 1
            bar_idx = min(bar_idx + 1, len(PROGRESS_BARS) - 1)
            if tick % 4 == 0:
                tip_idx += 1
            if tick % 8 == 0:
                stage_idx += 1
            if bar_idx >= len(PROGRESS_BARS) - 1 and tick % 8 == 4:
                bar_idx = 0
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug("Search animation stopped: %s", e)


async def search_timeout(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.sleep(SEARCH_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return
    if user_id not in _queued:
        return
    if user_id in _queued:
        _queued.discard(user_id)
    task = _searching_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()
    _timeout_tasks.pop(user_id, None)

    text = (
        "      ⋆｡°✩ ───────────── ✩°｡⋆\n\n"
        "          ⌁ <b>𝐏ᴀʀᴛɴᴇʀ 𝐍ᴏᴛ 𝐅ᴏᴜɴᴅ</b>\n\n"
        "       ୨୧ 𝐍ᴏ 𝐌ᴀᴛᴄʜᴇs 𝐀ᴠᴀɪʟᴀʙʟᴇ\n"
        "             𝐀ᴛ 𝐓ʜᴇ 𝐌ᴏᴍᴇɴᴛ\n\n"
        "       ↳ 𝐏ʟᴇᴀsᴇ 𝐓ʀʏ 𝐀ɢᴀɪɴ 𝐒ʜᴏʀᴛʟʏ\n\n"
        "       <code>/find</code>  ↳ 𝐒ᴇᴀʀᴄʜ 𝐀ɢᴀɪɴ \n\n"
        "      ⋆｡°✩ ───────────── ✩°｡⋆"
    )
    try:
        await context.bot.send_message(
            user_id, text, parse_mode=ParseMode.HTML, reply_markup=not_found_kb()
        )
    except (Forbidden, BadRequest, TelegramError) as e:
        logger.debug("Failed to send not-found notice to %s: %s", user_id, e)


async def notify_match(
    user_id: int,
    partner_id: int,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = (
        f"{HEADER}\n\n"
        "୨୧ <b>𝐏ᴀʀᴛɴᴇʀ 𝐅ᴏᴜɴᴅ</b> ୨୧\n\n"
        "✓ 𝐘ᴏᴜ ᴀʀᴇ ɴᴏᴡ ᴄᴏɴɴᴇᴄᴛᴇᴅ\n"
        "   𝐀ɴᴏɴʏᴍᴏᴜsʟʏ & sᴇᴄᴜʀᴇʟʏ\n\n"
        "⌁ <b>𝐒ᴛᴀʀᴛ 𝐂ʜᴀᴛᴛɪɴɢ</b>\n"
        "↳ Sᴇɴᴅ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʙᴇɢɪɴ\n"
        "↳ 𝐘ᴏᴜʀ ɪᴅᴇɴᴛɪᴛʏ ʀᴇᴍᴀɪɴs ᴘʀɪᴠᴀᴛᴇ\n\n"
        "──────────────\n\n"
        "<i>↳ /skip  — 𝐍ᴇxᴛ 𝐏ᴀʀᴛɴᴇʀ\n"
        "↳ /stop  — 𝐄ɴᴅ 𝐂ʜᴀᴛ</i>"
    )

    try:
        await context.bot.send_message(user_id, text, parse_mode=ParseMode.HTML, reply_markup=active_kb())
    except (Forbidden, BadRequest) as e:
        logger.warning("Failed to notify match for %s: %s", user_id, e)
    if user_id != partner_id:
        try:
            await context.bot.send_message(partner_id, text, parse_mode=ParseMode.HTML, reply_markup=active_kb())
        except (Forbidden, BadRequest) as e:
            logger.warning("Failed to notify match for %s: %s", partner_id, e)


def get_partner(user_id: int) -> Optional[int]:
    return _active.get(user_id)


async def ban_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if user.id in ADMIN_IDS:
        return
    if not is_banned(user.id):
        return

    partner = disconnect(user.id)
    if partner:
        with contextlib.suppress(Forbidden, BadRequest, TelegramError):
            await context.bot.send_message(
                partner,
                "⚠️ <b>𝐏ᴀʀᴛɴᴇʀ 𝐃ɪsᴄᴏɴɴ𝐞𝐜𝐭ᴇᴅ</b>\n\n"
                "Yᴏᴜʀ ᴘᴀʀᴛɴᴇʀ ɪs ɴᴏ ʟᴏɴɢᴇʀ ᴀᴠᴀɪʟᴀʙʟᴇ.\n\n"
                "↳ 𝐔sᴇ /find ᴛᴏ ᴍᴇᴇᴛ sᴏᴍᴇᴏɴᴇ ɴᴇᴡ.",
            )

    await send_ban_notice(context, user.id)

    if update.callback_query:
        with contextlib.suppress(BadRequest, TelegramError):
            await update.callback_query.answer("🚫 You are banned.")

    raise ApplicationHandlerStop


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    register_user(user)

    if FORCE_SUB_ENABLED and user.id not in ADMIN_IDS:
        if not await check_channel_membership(context.bot, user.id):
            await send_force_sub_prompt(update, context)
            return
    await send_welcome(update, context)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    text = (
        f"{HEADER}\n\n"
        "      ୨୧ <b>𝐂ᴏᴍᴍᴀɴᴅ 𝐆ᴜɪᴅᴇ</b> ୨୧\n"
        "      ──────────────────\n\n"
        "   <b>⌁ 𝐂ʜᴀᴛ 𝐂ᴏᴍᴍᴀɴᴅs</b>\n"
        "   ↳ /find — 𝐅ɪɴᴅ ᴀ ʀᴀɴᴅᴏᴍ ᴘᴀʀᴛɴᴇʀ\n"
        "   ↳ /skip — 𝐌ᴀᴛᴄʜ ᴡɪᴛʜ ᴀɴᴏᴛʜᴇʀ ᴜsᴇʀ\n"
        "   ↳ /stop — 𝐄ɴᴅ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ ᴄʜᴀᴛ\n"
        "   ↳ /cancel — 𝐂ᴀɴᴄᴇʟ ᴀɴ ᴀᴄᴛɪᴠᴇ sᴇᴀʀᴄʜ\n\n"
        "   <b>⌁ 𝐀ᴄᴄᴏᴜɴᴛ & 𝐏ʀɪᴠᴀᴄʏ</b>\n"
        "   ↳ /status — 𝐕ɪᴇᴡ ʏᴏᴜʀ ᴄʜᴀᴛ sᴛᴀᴛᴜs\n"
        "   ↳ /settings — 𝐌ᴀɴᴀɢᴇ ᴍᴀᴛᴄʜɪɴɢ sᴇᴛᴛɪɴɢs\n"
        "   ↳ /privacy — 𝐑ᴇᴀᴅ ᴅᴀᴛᴀ ᴘʀɪᴠᴀᴄʏ ɪɴꜰᴏ\n"
        "   ↳ /rules — 𝐕ɪᴇᴡ ᴄᴏᴍᴍᴜɴɪᴛʏ ʀᴜʟᴇs\n\n"
        "   <b>⌁ 𝐀ᴅᴅɪᴛɪᴏɴᴀʟ 𝐂ᴏᴍᴍᴀɴᴅs</b>\n"
        "   ↳ /report — 𝐑ᴇᴘᴏʀᴛ ᴀ ᴜsᴇʀ\n"
        "   ↳ /block — 𝐁ʟᴏᴄᴋ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ᴘᴀʀᴛɴᴇʀ\n"
        "   ↳ /unblock — 𝐔ɴʙʟᴏᴄᴋ ᴀ ᴜsᴇʀ\n"
        "   ↳ /stats — 𝐕ɪᴇᴡ ʏᴏᴜʀ ᴘᴇʀsᴏɴᴀʟ sᴛᴀᴛs\n"
        "   ↳ /ping — 𝐂ʜᴇᴄᴋ ʙᴏᴛ ʀᴇsᴘᴏɴsᴇ ᴛɪᴍᴇ\n"
        "   ↳ /id — 𝐕ɪᴇᴡ ʏᴏᴜʀ 𝐓ᴇʟᴇɢʀᴀᴍ 𝐈𝐃\n"
        "   ↳ /about — 𝐀ʙᴏᴜᴛ 𝐀ɴᴏɴᴏᴠᴀ\n"
        "   ↳ /feedback — 𝐒ᴇɴᴅ ғᴇᴇᴅʙᴀᴄᴋ\n"
        "   ↳ /donate — 𝐕ɪᴇᴡ ᴅᴏɴᴀᴛɪᴏɴ ɪɴꜰᴏ\n\n"
        "      ──────────────────\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    if MAINTENANCE and user.id not in ADMIN_IDS:
        await update.message.reply_text("🔧 <b>Maintenance mode.</b> Please try again later.", parse_mode=ParseMode.HTML)
        return
    if FORCE_SUB_ENABLED and user.id not in ADMIN_IDS:
        if not await check_channel_membership(context.bot, user.id):
            await send_force_sub_prompt(update, context)
            return
    register_user(user)

    if user.id in _active:
        await update.message.reply_text("You are already in a chat. Use /skip or /stop first.")
        return

    matched = await find_partner(user.id, context)
    if matched:
        partner = get_partner(user.id)
        if partner:
            await notify_match(user.id, partner, context)
    else:
        msg = await update.message.reply_text(
            _build_search_text(0, 0, 0, 0),
            parse_mode=ParseMode.HTML,
            reply_markup=searching_kb(),
        )
        task = asyncio.create_task(animate_search(msg, user.id))
        _searching_tasks[user.id] = task
        ttask = asyncio.create_task(search_timeout(user.id, context))
        _timeout_tasks[user.id] = ttask


async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    partner = disconnect(user.id)
    if partner:
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(
                partner,
                (
                    "୨୧ 𝐘ᴏᴜʀ ᴘᴀʀᴛɴᴇʀ ʜᴀs "
                    "ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ. ☾\n\n"
                    "☁ 𝐔sᴇ /find ᴛᴏ sᴇᴀʀᴄʜ "
                    "ꜰᴏʀ ᴀɴᴏᴛʜᴇʀ ᴘᴀʀᴛɴᴇʀ. ୨୧"
                ),
            )
    await cmd_find(update, context)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    was_active = user.id in _active or user.id in _queued
    partner = disconnect(user.id)
    if partner:
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(
                chat_id=partner,
                text=(
                    "୨୧ 𝐘ᴏᴜʀ ᴘᴀʀᴛɴᴇʀ ʜᴀs "
                    "ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ. ☾\n\n"
                    "☁ 𝐔sᴇ /find ᴛᴏ sᴇᴀʀᴄʜ "
                    "ꜰᴏʀ ᴀɴᴏᴛʜᴇʀ ᴘᴀʀᴛɴᴇʀ. ୨୧"
                ),
            )

    if was_active:
        await update.message.reply_text(
            f"{HEADER}\n\n"
            "୨୧ <b>𝐂ᴏɴᴠᴇʀsᴀᴛɪᴏɴ 𝐄ɴᴅᴇᴅ.</b> ☾\n\n"
            "☁ 𝐔sᴇ /find ᴛᴏ sᴛᴀʀᴛ ᴀ ɴᴇᴡ sᴇᴀʀᴄʜ. ୨୧",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
    else:
        await update.message.reply_text(
            "୨୧ 𝐘ᴏᴜ ᴀʀᴇ ɴᴏᴛ ɪɴ ᴀ ᴄᴏɴᴠᴇʀsᴀᴛɪᴏɴ "
            "ᴏʀ sᴇᴀʀᴄʜ ǫᴜᴇᴜᴇ. ☁"
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    if user.id not in _queued:
        await update.message.reply_text("You are not currently searching.")
        return
    disconnect(user.id)
    await update.message.reply_text(
        f"{HEADER}\n\n❌ <b>Search cancelled.</b>\n\nUse /find to try again.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    partner = get_partner(user.id)
    if partner:
        state = "🟢 In a conversation (anonymous)"
    elif user.id in _queued:
        state = "🟡 Searching for a partner..."
    else:
        state = "⚪ Idle"
    text = f"{HEADER}\n\n<b>ꜱᴛᴀᴛᴜꜱ</b>\n\n{state}\n\nID: <code>{user.id}</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    partner = get_partner(user.id)
    if not partner:
        await update.message.reply_text("You are not in a conversation.")
        return

    partner_name = f"User {partner}"
    try:
        chat = await context.bot.get_chat(partner)
        partner_name = chat.first_name or chat.username or partner_name
        if chat.username:
            partner_name = f"{partner_name} (@{chat.username})"
    except (BadRequest, Forbidden, TelegramError):
        pass

    reporter_name = user.first_name or "User"
    if user.username:
        reporter_name = f"{reporter_name} (@{user.username})"

    REPORT_FLOW[user.id] = {
        "reported_id": partner,
        "reported_name": partner_name,
        "reporter_name": reporter_name,
        "reason": None,
        "proof_file_id": None,
        "proof_type": None,
    }

    await update.message.reply_text(
        f"{HEADER}\n\n"
        "⚑ <b>𝐑ᴇᴘᴏʀᴛ 𝐔sᴇʀ</b>\n\n"
        "Pʟᴇᴀsᴇ ᴅᴇsᴄʀɪʙᴇ ᴛʜᴇ ʀᴇᴀsᴏɴ ꜰᴏʀ "
        "ʀᴇᴘᴏʀᴛɪɴɢ ᴛʜɪs ᴜsᴇʀ.\n\n"
        "↳ Sᴜʙᴍɪᴛ ʏᴏᴜʀ ʀᴇᴀsᴏɴ ꜰɪʀsᴛ.\n"
        "↳ Tʜᴇɴ sᴇɴᴅ ᴀ sᴄʀᴇᴇɴsʜᴏᴛ ᴀs ᴇᴠɪᴅᴇɴᴄᴇ.\n\n"
        "⌁ <b>𝐍ᴇᴇᴅ 𝐇ᴇʟᴘ?</b>\n"
        "Yᴏᴜ ᴄᴀɴ ᴄᴏɴᴛᴀᴄᴛ @CRUXAETH ᴅɪʀᴇᴄᴛʟʏ "
        "ᴛᴏ ʀᴇᴘᴏʀᴛ ᴀ ᴘʀᴏʙʟᴇᴍ ᴛᴏ ᴛʜᴇ ᴅᴇᴠᴇʟᴏᴘᴇʀ.\n\n"
        "──────────────\n"
        "<i>↳ /cancelreport  — 𝐂ᴀɴᴄᴇʟ 𝐑ᴇᴘᴏʀᴛ</i>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_cancelreport(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    if user.id in REPORT_FLOW:
        REPORT_FLOW.pop(user.id, None)
        await update.message.reply_text("❌ Report cancelled.")
    else:
        await update.message.reply_text("You are not currently reporting anyone.")


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    partner = get_partner(user.id)
    if not partner:
        await update.message.reply_text(
            "🔒 You are not in a conversation.\n\n"
            "Use <code>/unblock &lt;user_id&gt;</code> to unblock someone.",
            parse_mode=ParseMode.HTML,
        )
        return
    add_block(user.id, partner)
    disconnect(user.id)
    with contextlib.suppress(Forbidden, BadRequest, TelegramError):
        await context.bot.send_message(
            partner,
            "୨୧ 𝐘ᴏᴜʀ ᴘᴀʀᴛɴᴇʀ ʜᴀs ʙʟᴏᴄᴋᴇᴅ ʏᴏᴜ. ☾\n\n"
            "☁ 𝐔sᴇ /find ᴛᴏ sᴇᴀʀᴄʜ ꜰᴏʀ ᴀɴᴏᴛʜᴇʀ ᴘᴀʀᴛɴᴇʀ. ୨୧",
        )
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "🔒 <b>𝐔sᴇʀ 𝐁ʟᴏᴄᴋᴇᴅ</b>\n\n"
        "Yᴏᴜ ᴡɪʟʟ ɴᴏ ʟᴏɴɢᴇʀ ʙᴇ ᴍᴀᴛᴄʜᴇᴅ "
        "ᴡɪᴛʜ ᴛʜɪs ᴜsᴇʀ.\n\n"
        "↳ 𝐔sᴇ /find ᴛᴏ ᴅɪsᴄᴏᴠᴇʀ ᴀ ɴᴇᴡ ᴘᴀʀᴛɴᴇʀ.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
    )
    logger.info("Block: %s -> %s", user.id, partner)


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Usage: <code>/unblock &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    target = int(args[0])
    remove_block(user.id, target)
    await update.message.reply_text(
        f"✅ <b>User</b> <code>{target}</code> <b>unblocked.</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    rules = get_setting("rules")
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "📜 <b>𝐂ᴏᴍᴍᴜɴɪᴛʏ 𝐑ᴜʟᴇs</b>\n\n"
        f"{rules}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    text = (
        f"{HEADER}\n\n"
        "🔐 <b>𝐏ʀɪᴠᴀᴄʏ 𝐏ᴏʟɪᴄʏ</b>\n\n"
        "↳ Cʜᴀᴛs ᴀʀᴇ ᴀɴᴏɴʏᴍᴏᴜs ᴛᴏ ᴏᴛʜᴇʀ ᴜsᴇʀs, "
        "ʙᴜᴛ ɴᴏᴛ ᴛᴏ ᴛʜᴇ ʙᴏᴛ ᴏᴘᴇʀᴀᴛᴏʀ ᴏʀ Tᴇʟᴇɢʀᴀᴍ.\n\n"
        "↳ Mᴇssᴀɢᴇ ᴄᴏɴᴛᴇɴᴛ ɪs <b>ɴᴏᴛ</b> sᴛᴏʀᴇᴅ "
        "ɪɴ ᴛʜᴇ ᴅᴀᴛᴀʙᴀsᴇ.\n\n"
        "↳ Wᴇ sᴛᴏʀᴇ ʏᴏᴜʀ ᴜsᴇʀ ID, ғɪʀsᴛ/ʟᴀsᴛ sᴇᴇɴ "
        "ᴛɪᴍᴇsᴛᴀᴍᴘs, ʀᴇᴘᴏʀᴛs, ᴀɴᴅ sᴇᴛᴛɪɴɢs.\n\n"
        "↳ Usᴇ /stop ᴏʀ /skip ɪᴍᴍᴇᴅɪᴀᴛᴇʟʏ "
        "ɪғ ʏᴏᴜ ғᴇᴇʟ ᴜɴsᴀғᴇ."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    with db_conn() as conn:
        row = conn.execute("SELECT report_count FROM users WHERE user_id=?", (user.id,)).fetchone()
        reports = row["report_count"] if row else 0
    text = (
        f"{HEADER}\n\n📊 <b>ʏᴏᴜʀ ꜱᴛᴀᴛꜱ</b>\n\n"
        f"Reports against you: {reports}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    start = time.monotonic()
    msg = await update.message.reply_text("🏓 Pong...")
    elapsed = (time.monotonic() - start) * 1000
    await msg.edit_text(f"🏓 <b>Pong!</b> <code>{elapsed:.1f} ms</code>", parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and update.message:
        await update.message.reply_text(f"🆔 <b>Your Telegram ID:</b> <code>{user.id}</code>", parse_mode=ParseMode.HTML)


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    text = (
        f"{HEADER}\n\n"
        "୨୧ <b>𝐀ɴᴏɴʏᴍᴏᴜs 𝐂ʜᴀᴛ</b> ୨୧\n\n"
        "Cᴏɴɴᴇᴄᴛ ᴡɪᴛʜ sᴛʀᴀɴɢᴇʀs ᴀɴᴅ "
        "ᴄʜᴀᴛ ғʀᴇᴇʟʏ.\n\n"
        "🔒 Yᴏᴜʀ ɪᴅᴇɴᴛɪᴛʏ ʀᴇᴍᴀɪɴs ᴘʀɪᴠᴀᴛᴇ.\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /feedback &lt;your message&gt;", parse_mode=ParseMode.HTML)
        return
    feedback = " ".join(args)
    for admin_chat in ADMIN_CHAT_IDS:
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(
                admin_chat,
                f"📬 <b>Feedback</b> from <code>{user.id}</code>:\n\n{html.escape(feedback)}",
                parse_mode=ParseMode.HTML,
            )
    await update.message.reply_text("✅ <b>Feedback sent.</b> Thank you!", parse_mode=ParseMode.HTML)


async def cmd_donate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    caption = (
        f"{HEADER}\n\n"
        "୨୧ 💝 <b>𝐒ᴜᴘᴘᴏʀᴛ 𝐓ʜᴇ 𝐁ᴏᴛ</b> ୨୧\n\n"
        "Dᴏɴᴀᴛᴇ ᴀᴄᴄᴏʀᴅɪɴɢ ᴛᴏ ʏᴏᴜʀ ᴘᴏᴄᴋᴇᴛ.\n\n"
        "Tʜɪs ʙᴏᴛ ɪs ғʀᴇᴇ ᴛᴏ ᴜsᴇ.\n"
        "Eᴠᴇʀʏ ᴅᴏɴᴀᴛɪᴏɴ ɪs ᴏᴘᴛɪᴏɴᴀʟ ᴀɴᴅ "
        "ɢʀᴇᴀᴛʟʏ ᴀᴘᴘʀᴇᴄɪᴀᴛᴇᴅ. ♡"
    )
    await context.bot.send_photo(
        chat_id=update.message.chat_id,
        photo="https://kommodo.ai/i/uX66uDJgKdVcoftxi40c",
        caption=caption,
        parse_mode=ParseMode.HTML,
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "⚙️ <b>𝐒ᴇᴛᴛɪɴɢs</b>\n\n"
        "Yᴏᴜʀ ᴘʀᴇғᴇʀᴇɴᴄᴇs ᴀʀᴇ ᴀᴘᴘʟɪᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ.\n"
        "↳ Rᴇᴘᴏʀᴛs ᴀʀᴇ ʀᴇᴠɪᴇᴡᴇᴅ ʙʏ ᴀᴅᴍɪɴs.\n\n"
        "──────────────\n\n"
        "↳ /block — 𝐁ʟᴏᴄᴋ 𝐏ᴀʀᴛɴᴇʀ\n"
        "↳ /unblock <code>&lt;id&gt;</code> — 𝐔ɴʙʟᴏᴄᴋ 𝐔sᴇʀ",
        parse_mode=ParseMode.HTML,
    )


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if user is None or user.id not in ADMIN_IDS:
            if update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ <b>Unauthorized.</b>", parse_mode=ParseMode.HTML)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    counts = get_counts()
    target_label = FORCE_SUB_TYPE.capitalize() if FORCE_SUB_TYPE else "None"
    text = (
        f"{HEADER}\n\n🛡 <b>ᴀᴅᴍɪɴ ᴅᴀꜱʜʙᴏᴀʀᴅ</b>\n\n"
        f"👥 Users: {counts['total_users']}\n"
        f"🔍 Searching: {len(_queued)}\n"
        f"💬 Active chats: {len(_active) // 2}\n"
        f"🚫 Banned: {counts['banned']}\n"
        f"📬 Reports: {counts['reports']}\n"
        f"✉️ Messages relayed: {counts['total_messages']}\n"
        f"🎬 Welcome videos: {counts['total_videos']}\n"
        f"🔧 Maintenance: {'ON' if MAINTENANCE else 'OFF'}\n"
        f"📢 Force Sub: {'ON' if FORCE_SUB_ENABLED else 'OFF'} ({target_label})"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /broadcast &lt;message&gt;", parse_mode=ParseMode.HTML)
        return
    message = " ".join(args)
    with db_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE is_banned=0").fetchall()
    sent = 0
    failed = 0
    for row in rows:
        uid = row["user_id"]
        try:
            await context.bot.send_message(
                uid,
                f"{HEADER}\n\n"
                f"📢 <b>𝐀ɴɴᴏᴜɴᴄᴇᴍᴇɴᴛ</b>\n\n"
                f"{html.escape(message)}",
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except (Forbidden, BadRequest):
            failed += 1
        except TelegramError:
            failed += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        f"✅ <b>Broadcast complete.</b> Sent: {sent}, Failed: {failed}", parse_mode=ParseMode.HTML
    )


@admin_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Usage:\n"
            "• <code>/ban &lt;user_id&gt;</code> — permanent ban\n"
            "• <code>/ban &lt;user_id&gt; &lt;days&gt;</code> — temporary ban\n\n"
            "Examples:\n"
            "<code>/ban 123456789</code>\n"
            "<code>/ban 123456789 7</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target = int(args[0])

    if len(args) == 1:
        set_ban(target, True)
        disconnect(target)
        await send_ban_notice(context, target)
        await update.message.reply_text(
            f"🚫 <b>User</b> <code>{target}</code> <b>banned permanently.</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not args[1].isdigit() or int(args[1]) <= 0:
        await update.message.reply_text(
            "❌ Days must be a positive integer (e.g. <code>/ban 123456789 3</code>).",
            parse_mode=ParseMode.HTML,
        )
        return

    days = int(args[1])
    until = set_temp_ban(target, days)
    disconnect(target)
    await send_ban_notice(context, target)

    await update.message.reply_text(
        f"🚫 <b>User</b> <code>{target}</code> <b>banned for {days} day(s).</b>\n\n"
        f"<b>Expires:</b> {until.strftime('%Y-%m-%d %H:%M UTC')}",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /unban &lt;user_id&gt;", parse_mode=ParseMode.HTML)
        return
    target = int(args[0])
    set_ban(target, False)
    await update.message.reply_text(
        f"✅ <b>User</b> <code>{target}</code> <b>unbanned.</b>",
        parse_mode=ParseMode.HTML,
    )
    await send_unban_notice(context, target)


@admin_only
async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global MAINTENANCE
    if update.message is None:
        return
    MAINTENANCE = not MAINTENANCE
    await update.message.reply_text(
        f"🔧 <b>Maintenance mode:</b> {'ON' if MAINTENANCE else 'OFF'}", parse_mode=ParseMode.HTML
    )


@admin_only
async def cmd_exportstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    counts = get_counts()
    text = (
        f"📊 <b>Aggregate Statistics</b>\n\n"
        f"Total users: {counts['total_users']}\n"
        f"Active chats: {len(_active) // 2}\n"
        f"Queue length: {len(_queued)}\n"
        f"Total reports: {counts['reports']}\n"
        f"Total messages relayed: {counts['total_messages']}\n"
        f"Welcome videos: {counts['total_videos']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
async def cmd_setwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setwelcome &lt;new welcome text&gt;", parse_mode=ParseMode.HTML)
        return
    set_setting("welcome_message", " ".join(args))
    await update.message.reply_text("✅ <b>Welcome message updated.</b>", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_setrules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /setrules &lt;new rules text&gt;", parse_mode=ParseMode.HTML)
        return
    set_setting("rules", " ".join(args))
    await update.message.reply_text("✅ <b>Rules updated.</b>", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    counts = get_counts()
    await update.message.reply_text(f"👥 <b>Total registered users:</b> {counts['total_users']}", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(f"💬 <b>Active conversations:</b> {len(_active) // 2}", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(f"🔍 <b>Users in queue:</b> {len(_queued)}", parse_mode=ParseMode.HTML)


def _resolve_invite_link(channel_id: int, chat_type: str) -> str:
    if chat_type == "group":
        return f"https://t.me/c/{str(channel_id).replace('-100', '')}"
    return f"https://t.me/c/{str(channel_id).replace('-100', '')}"


@admin_only
async def cmd_forcesub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global FORCE_SUB_CHANNEL, FORCE_SUB_LINK, FORCE_SUB_ENABLED, FORCE_SUB_TYPE, FORCE_SUB_TITLE
    if update.message is None:
        return

    if not context.args:
        await update.message.reply_text(
            f"{HEADER}\n\n"
            "⚙️ <b>𝐅ᴏʀᴄᴇ 𝐒ᴜʙsᴄʀɪʙᴇ</b>\n\n"
            "↳ Fᴏʀᴡᴀʀᴅ ᴀɴʏ ᴍᴇssᴀɢᴇ ғʀᴏᴍ ʏᴏᴜʀ ᴄʜᴀɴɴᴇʟ "
            "ᴏʀ ɢʀᴏᴜᴘ ᴛᴏ ᴄᴏɴғɪɢᴜʀᴇ ɪᴛ ᴇᴀsɪʟʏ.\n\n"
            "⌁ <b>𝐌ᴀɴᴜᴀʟ 𝐒ᴇᴛᴜᴘ</b>\n"
            "<code>/forcesub &lt;chat_id&gt; &lt;invite_link&gt; [channel|group]</code>\n\n"
            "⌁ <b>𝐄xᴀᴍᴘʟᴇs</b>\n"
            "<code>/forcesub -1001234567890 https://t.me/+abc123 channel</code>\n"
            "<code>/forcesub -1001234567890 https://t.me/+abc123 group</code>\n\n"
            "──────────────\n\n"
            f"◈ <b>𝐒ᴛᴀᴛᴜs:</b> {'🟢 Enabled' if FORCE_SUB_ENABLED else '🔴 Disabled'}\n"
            f"◈ <b>𝐓ʏᴘᴇ:</b> {html.escape(FORCE_SUB_TYPE.capitalize())}\n"
            f"◈ <b>𝐓ɪᴛʟᴇ:</b> {html.escape(str(FORCE_SUB_TITLE or 'Not set'))}\n"
            f"◈ <b>𝐂ʜᴀᴛ 𝐈ᴅ:</b> <code>{html.escape(str(FORCE_SUB_CHANNEL or 'Not set'))}</code>\n"
            f"◈ <b>𝐋ɪɴᴋ:</b> {html.escape(str(FORCE_SUB_LINK or 'Not set'))}\n\n"
            "<i>Configure and manage your subscription settings below.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🟢 ᴛᴜʀɴ ᴏɴ", callback_data="fs_enable")],
                    [InlineKeyboardButton("🔴 ᴛᴜʀɴ ᴏꜰꜰ", callback_data="fs_disable")],
                    [InlineKeyboardButton("🔍 ᴄʜᴇᴄᴋ ᴀᴅᴍɪɴ", callback_data="fs_check_admin")],
                    [InlineKeyboardButton("🗑 ʀᴇᴍᴏᴠᴇ", callback_data="fs_remove")],
                ]
            ),
        )
        return

    args = context.args
    if len(args) >= 2:
        try:
            channel_id = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ Invalid chat ID.")
            return
        link = args[1]
        chat_type = args[2].lower() if len(args) >= 3 else "channel"
        if chat_type not in ("channel", "group"):
            await update.message.reply_text("❌ Type must be 'channel' or 'group'.")
            return
        set_setting("force_sub_channel", str(channel_id))
        set_setting("force_sub_link", link)
        set_setting("force_sub_enabled", "1")
        set_setting("force_sub_type", chat_type)
        FORCE_SUB_CHANNEL = channel_id
        FORCE_SUB_LINK = link
        FORCE_SUB_ENABLED = True
        FORCE_SUB_TYPE = chat_type
        await update.message.reply_text(
            f"✅ <b>Force Subscribe enabled.</b>\n"
            f"Type: <b>{chat_type.capitalize()}</b>\n"
            f"ID: <code>{channel_id}</code>\n"
            f"Link: {link}",
            parse_mode=ParseMode.HTML,
        )


@admin_only
async def cmd_removeforcesub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global FORCE_SUB_CHANNEL, FORCE_SUB_LINK, FORCE_SUB_ENABLED, FORCE_SUB_TYPE, FORCE_SUB_TITLE
    if update.message is None:
        return
    set_setting("force_sub_channel", "")
    set_setting("force_sub_link", "")
    set_setting("force_sub_enabled", "0")
    set_setting("force_sub_type", "channel")
    set_setting("force_sub_title", "")
    FORCE_SUB_CHANNEL = None
    FORCE_SUB_LINK = None
    FORCE_SUB_ENABLED = False
    FORCE_SUB_TYPE = "channel"
    FORCE_SUB_TITLE = None
    await update.message.reply_text(
        "🗑 <b>Force Sub removed.</b>\n\n"
        "The channel/group has been cleared and Force Subscribe is now <b>OFF</b>.",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def capture_forwarded_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global FORCE_SUB_CHANNEL, FORCE_SUB_LINK, FORCE_SUB_ENABLED, FORCE_SUB_TYPE, FORCE_SUB_TITLE
    if not update.message:
        return
    if not update.message.forward_origin:
        await update.message.reply_text("⚠️ Please forward a message from the channel or group you want to add.")
        return

    origin = update.message.forward_origin
    if origin.type not in ("channel", "chat"):
        await update.message.reply_text(
            "⚠️ Please forward a message from a <b>channel</b> or <b>group</b> (not a user).",
            parse_mode=ParseMode.HTML,
        )
        return

    chat = origin.chat
    channel_id = chat.id
    title = chat.title or "Untitled"
    chat_type = "channel" if origin.type == "channel" else "group"

    link = None
    try:
        link = await context.bot.export_chat_invite_link(channel_id)
    except (BadRequest, Forbidden):
        try:
            chat_info = await context.bot.get_chat(channel_id)
            if chat_info.username:
                link = f"https://t.me/{chat_info.username}"
            else:
                link = _resolve_invite_link(channel_id, chat_type)
        except (BadRequest, Forbidden):
            link = _resolve_invite_link(channel_id, chat_type)

    set_setting("force_sub_channel", str(channel_id))
    set_setting("force_sub_link", link)
    set_setting("force_sub_enabled", "1")
    set_setting("force_sub_type", chat_type)
    set_setting("force_sub_title", title)

    FORCE_SUB_CHANNEL = channel_id
    FORCE_SUB_LINK = link
    FORCE_SUB_ENABLED = True
    FORCE_SUB_TYPE = chat_type
    FORCE_SUB_TITLE = title

    is_admin = False
    try:
        bot_member = await context.bot.get_chat_member(channel_id, context.bot.id)
        is_admin = bot_member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except (Forbidden, BadRequest):
        is_admin = False

    admin_status = (
        "✅ <b>Bot is admin</b>"
        if is_admin
        else f"⚠️ <b>I am not admin in that {chat_type}.</b> Please promote me to admin so I can verify members."
    )

    type_label = "📢 <b>Channel</b>" if chat_type == "channel" else "👥 <b>Group</b>"

    await update.message.reply_text(
        f"{HEADER}\n\n"
        f"{type_label} <b>Added as Force Sub</b>\n\n"
        f"Title: <b>{html.escape(title)}</b>\n"
        f"Type: <b>{chat_type.capitalize()}</b>\n"
        f"ID: <code>{channel_id}</code>\n"
        f"Link: {link}\n\n"
        f"Force Subscribe: <b>ON</b>\n"
        f"{admin_status}\n\n"
        f"<i>Only admins can use admin commands. Regular users will be asked to join.</i>",
        parse_mode=ParseMode.HTML,
    )


@admin_only
async def cmd_addvideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    msg = update.message

    if msg.video:
        ok = add_welcome_video(msg.video.file_id, update.effective_user.id)
        if ok:
            await msg.reply_text(
                "✅ Video sent successfully!\n\n"
                f"Total videos: {len(get_welcome_videos())}\n"
                "Bot will randomly pick one for new users with the built-in aesthetic caption.",
            )
        else:
            await msg.reply_text("⚠️ This video was already added.")
        return

    if msg.reply_to_message and msg.reply_to_message.video:
        ok = add_welcome_video(
            msg.reply_to_message.video.file_id, update.effective_user.id
        )
        if ok:
            await msg.reply_text(
                "✅ Video sent successfully!\n\n"
                f"Total videos: {len(get_welcome_videos())}",
            )
        else:
            await msg.reply_text("⚠️ This video was already added.")
        return

    await msg.reply_text(
        "⚠️ How to add a welcome video:\n\n"
        "1. Send or forward a video to me\n"
        "2. Reply to it with /addvideo\n"
        "3. Or send the video directly with /addvideo\n\n"
        "The bot automatically attaches the built-in aesthetic caption.",
    )


@admin_only
async def cmd_videos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    videos = get_welcome_videos()
    if not videos:
        await update.message.reply_text(
            "📭 No welcome videos set.\n\nUse /addvideo to add one.",
        )
        return

    lines = [f"Welcome Videos ({len(videos)})\n"]
    for v in videos:
        lines.append(f"🎥 #{v['id']}")
    lines.append("\nUse /delvideo <id> to remove.")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_delvideo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /delvideo <id>")
        return
    remove_welcome_video(int(args[0]))
    await update.message.reply_text("🗑 Video removed.")


@admin_only
async def cmd_testwelcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text("🎬 Sending a random welcome video...")
    await send_welcome(update, context)


async def cb_find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    if MAINTENANCE and user.id not in ADMIN_IDS:
        await safe_edit_message(query, "🔧 <b>Maintenance mode. Try later.</b>", parse_mode=ParseMode.HTML, edit_only=True)
        return
    if FORCE_SUB_ENABLED and user.id not in ADMIN_IDS:
        if not await check_channel_membership(context.bot, user.id):
            await send_force_sub_prompt(update, context)
            return
    if user.id in _active:
        await safe_edit_message(query, "You are already in a chat.", parse_mode=ParseMode.HTML, edit_only=True)
        return
    matched = await find_partner(user.id, context)
    if matched:
        partner = get_partner(user.id)
        if partner:
            await notify_match(user.id, partner, context)
        await safe_edit_message(
            query,
            "✓ <b>Partner found!</b> Check the notification above.",
            parse_mode=ParseMode.HTML,
            edit_only=True,
        )
    else:
        await safe_edit_message(
            query,
            _build_search_text(0, 0, 0, 0),
            parse_mode=ParseMode.HTML,
            reply_markup=searching_kb(),
            edit_only=True,
        )
        task = asyncio.create_task(animate_search(query.message, user.id))
        _searching_tasks[user.id] = task
        ttask = asyncio.create_task(search_timeout(user.id, context))
        _timeout_tasks[user.id] = ttask


async def cb_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    partner = disconnect(user.id)
    if partner:
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(partner, "⚠️ Partner disconnected. Use /find.")
    await safe_edit_message(
        query,
        f"{HEADER}\n\n⏹ <b>Ended.</b>\n\nUse /find to search again.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        edit_only=True,
    )


async def cb_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    partner = disconnect(user.id)
    if partner:
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(partner, "⚠️ Partner skipped. Use /find.")
    matched = await find_partner(user.id, context)
    if matched:
        p = get_partner(user.id)
        if p:
            await notify_match(user.id, p, context)
        await safe_edit_message(
            query,
            "✓ <b>New partner found!</b> Check the notification above.",
            parse_mode=ParseMode.HTML,
            edit_only=True,
        )
    else:
        await safe_edit_message(
            query,
            _build_search_text(0, 0, 0, 0),
            parse_mode=ParseMode.HTML,
            reply_markup=searching_kb(),
            edit_only=True,
        )
        task = asyncio.create_task(animate_search(query.message, user.id))
        _searching_tasks[user.id] = task
        ttask = asyncio.create_task(search_timeout(user.id, context))
        _timeout_tasks[user.id] = ttask


async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    partner = get_partner(user.id)
    if partner:
        state = "🟢 In a conversation"
    elif user.id in _queued:
        state = "🟡 Searching"
    else:
        state = "⚪ Idle"
    await safe_edit_message(
        query,
        f"{HEADER}\n\n<b>Status:</b> {state}\n\nID: <code>{user.id}</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        edit_only=True,
    )


async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    if user.id in _queued:
        disconnect(user.id)
        await safe_edit_message(
            query,
            f"{HEADER}\n\n❌ <b>Search cancelled.</b>\n\nUse /find to try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
            edit_only=True,
        )
    else:
        await safe_edit_message(query, "Not searching.", parse_mode=ParseMode.HTML, edit_only=True)


async def cb_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    partner = get_partner(user.id)
    if not partner:
        await safe_edit_message(query, "You are not in a conversation.", parse_mode=ParseMode.HTML, edit_only=True)
        return

    partner_name = f"User {partner}"
    try:
        chat = await context.bot.get_chat(partner)
        partner_name = chat.first_name or chat.username or partner_name
        if chat.username:
            partner_name = f"{partner_name} (@{chat.username})"
    except (BadRequest, Forbidden, TelegramError):
        pass

    reporter_name = user.first_name or "User"
    if user.username:
        reporter_name = f"{reporter_name} (@{user.username})"

    REPORT_FLOW[user.id] = {
        "reported_id": partner,
        "reported_name": partner_name,
        "reporter_name": reporter_name,
        "reason": None,
        "proof_file_id": None,
        "proof_type": None,
    }

    await safe_edit_message(
        query,
        f"{HEADER}\n\n"
        "⚑ <b>𝐑ᴇᴘᴏʀᴛ 𝐅ʟᴏᴡ</b>\n\n"
        "Please describe <b>why</b> you are reporting this user.\n\n"
        "After sending the reason, send a <b>screenshot as proof</b>.\n\n"
        "If you want to report directly to the developer, you can also DM @CRUXAETH and explain your problem.\n\n"
        "Type /cancelreport to cancel this report.",
        parse_mode=ParseMode.HTML,
        edit_only=True,
    )


async def cb_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    partner = get_partner(user.id)
    if not partner:
        await safe_edit_message(
            query,
            "🔒 <b>Not in a conversation.</b>\n\nUse /unblock &lt;id&gt; to unblock someone.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
            edit_only=True,
        )
        return
    add_block(user.id, partner)
    disconnect(user.id)
    with contextlib.suppress(Forbidden, BadRequest, TelegramError):
        await context.bot.send_message(
            partner,
            "୨୧ 𝐘ᴏᴜʀ ᴘᴀʀᴛɴᴇʀ ʜᴀs ʙʟᴏᴄᴋᴇᴅ ʏᴏᴜ. ☾\n\n"
            "☁ 𝐔sᴇ /find ᴛᴏ sᴇᴀʀᴄʜ ꜰᴏʀ ᴀɴᴏᴛʜᴇʀ ᴘᴀʀᴛɴᴇʀ. ୨୧",
        )
    await safe_edit_message(
        query,
        f"{HEADER}\n\n"
        "🔒 <b>User blocked.</b>\n\n"
        "You will no longer be matched with this user.\n"
        "Use /find to search for a new partner.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        edit_only=True,
    )
    logger.info("Block: %s -> %s", user.id, partner)


async def cb_verify_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    if user is None:
        return
    if not FORCE_SUB_ENABLED:
        await safe_edit_message(query, "✅ Force sub is disabled. You can use /find.", parse_mode=ParseMode.HTML, edit_only=True)
        return
    if await check_channel_membership(context.bot, user.id):
        register_user(user)
        with contextlib.suppress(BadRequest):
            await safe_edit_message(
                query,
                f"{HEADER}\n\n✅ <b>Verified!</b> Sending your welcome...",
                parse_mode=ParseMode.HTML,
                edit_only=True,
            )
        await send_welcome(update, context)
    else:
        target_word = "group" if FORCE_SUB_TYPE == "group" else "channel"
        await query.answer(f"❌ You haven't joined the {target_word} yet.", show_alert=True)


async def cb_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Invite link not configured.")


async def cb_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await safe_edit_message(
        query,
        f"{HEADER}\n\nUse /find to search for a partner.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_kb(),
        edit_only=True,
    )


async def cb_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return
    await query.answer()
    counts = get_counts()
    target_label = FORCE_SUB_TYPE.capitalize() if FORCE_SUB_TYPE else "None"
    text = (
        f"{HEADER}\n\n🛡 <b>ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b>\n\n"
        f"👥 Users: {counts['total_users']}\n"
        f"💬 Active: {len(_active) // 2}\n"
        f"🔍 Queue: {len(_queued)}\n"
        f"📬 Reports: {counts['reports']}\n"
        f"🎬 Videos: {counts['total_videos']}\n"
        f"🔧 Maintenance: {'ON' if MAINTENANCE else 'OFF'}\n"
        f"📢 Force Sub: {'ON' if FORCE_SUB_ENABLED else 'OFF'} ({target_label})"
    )
    await safe_edit_message(query, text, parse_mode=ParseMode.HTML, reply_markup=admin_kb(), edit_only=True)


async def cb_admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global FORCE_SUB_CHANNEL, FORCE_SUB_LINK, FORCE_SUB_ENABLED, FORCE_SUB_TYPE, FORCE_SUB_TITLE, MAINTENANCE
    query = update.callback_query
    if update.effective_user is None or update.effective_user.id not in ADMIN_IDS:
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return
    await query.answer()
    data = query.data
    if data == "admin_stats":
        counts = get_counts()
        text = (
            f"📊 <b>Stats</b>\n\n"
            f"Users: {counts['total_users']}\n"
            f"Banned: {counts['banned']}\n"
            f"Reports: {counts['reports']}\n"
            f"Messages relayed: {counts['total_messages']}\n"
            f"Active chats: {len(_active) // 2}\n"
            f"Queue: {len(_queued)}\n"
            f"Welcome videos: {counts['total_videos']}"
        )
        await safe_edit_message(query, text, parse_mode=ParseMode.HTML, reply_markup=admin_kb(), edit_only=True)
    elif data == "admin_maintenance":
        MAINTENANCE = not MAINTENANCE
        await safe_edit_message(
            query,
            f"🔧 <b>Maintenance:</b> {'ON' if MAINTENANCE else 'OFF'}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    elif data == "admin_ban_menu":
        await safe_edit_message(
            query,
            "⚑ <b>Ban / Unban</b>\n\n"
            "<b>Permanent ban:</b>\n"
            "<code>/ban &lt;user_id&gt;</code>\n\n"
            "<b>Temporary ban:</b>\n"
            "<code>/ban &lt;user_id&gt; &lt;days&gt;</code>\n\n"
            "<b>Unban:</b>\n"
            "<code>/unban &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    elif data == "admin_edit_welcome":
        await safe_edit_message(query, "Use /setwelcome &lt;text&gt;", parse_mode=ParseMode.HTML, reply_markup=admin_kb(), edit_only=True)
    elif data == "admin_edit_rules":
        await safe_edit_message(query, "Use /setrules &lt;text&gt;", parse_mode=ParseMode.HTML, reply_markup=admin_kb(), edit_only=True)
    elif data == "admin_broadcast":
        await safe_edit_message(query, "Use /broadcast &lt;message&gt;", parse_mode=ParseMode.HTML, reply_markup=admin_kb(), edit_only=True)
    elif data == "admin_add_video":
        await safe_edit_message(
            query,
            "🎬 <b>Add a welcome video</b>\n\n"
            "Send a video with <code>/addvideo</code>\n"
            "or reply to a video with <code>/addvideo</code>.\n\n"
            "<i>The bot automatically attaches the built-in aesthetic caption.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    elif data == "admin_list_videos":
        videos = get_welcome_videos()
        if not videos:
            await safe_edit_message(
                query,
                "📭 <b>No welcome videos set.</b>\n\nUse /addvideo to add one.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
                edit_only=True,
            )
        else:
            lines = [f"🎬 <b>Welcome Videos ({len(videos)})</b>\n"]
            for v in videos:
                lines.append(f"🎥 <code>#{v['id']}</code>")
            lines.append("\nUse /delvideo &lt;id&gt; to remove.")
            await safe_edit_message(
                query,
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
                edit_only=True,
            )
    elif data == "admin_force_sub":
        await safe_edit_message(
            query,
            f"{HEADER}\n\n"
            "📢 <b>Force Subscribe</b>\n\n"
            "✅ <b>Easiest way:</b> Forward any message from your channel/group to the bot.\n\n"
            "Or use:\n"
            "/forcesub &lt;chat_id&gt; &lt;invite_link&gt; [channel|group]\n\n"
            "🗑 To remove: /removeforcesub\n\n"
            f"Status: <b>{'ON' if FORCE_SUB_ENABLED else 'OFF'}</b>\n"
            f"Type: <b>{FORCE_SUB_TYPE.capitalize()}</b>\n"
            f"Title: {FORCE_SUB_TITLE or 'None'}\n"
            f"ID: <code>{FORCE_SUB_CHANNEL or 'None'}</code>\n"
            f"Link: {FORCE_SUB_LINK or 'None'}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🟢 ᴛᴜʀɴ ᴏɴ", callback_data="fs_enable")],
                    [InlineKeyboardButton("🔴 ᴛᴜʀɴ ᴏꜰꜰ", callback_data="fs_disable")],
                    [InlineKeyboardButton("🔍 ᴄʜᴇᴄᴋ ᴀᴅᴍɪɴ", callback_data="fs_check_admin")],
                    [InlineKeyboardButton("🗑 ʀᴇᴍᴏᴠᴇ", callback_data="fs_remove")],
                    [InlineKeyboardButton("⬅️ ʙᴀᴄᴋ", callback_data="admin_menu")],
                ]
            ),
            edit_only=True,
        )
    elif data == "fs_enable":
        if FORCE_SUB_CHANNEL is None:
            await safe_edit_message(query, "⚠️ No channel/group set. Forward a message first.", parse_mode=ParseMode.HTML, edit_only=True)
            return
        set_setting("force_sub_enabled", "1")
        FORCE_SUB_ENABLED = True
        await safe_edit_message(
            query,
            "✅ <b>Force Sub enabled.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    elif data == "fs_disable":
        set_setting("force_sub_enabled", "0")
        FORCE_SUB_ENABLED = False
        await safe_edit_message(
            query,
            "🔴 <b>Force Sub disabled.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    elif data == "fs_check_admin":
        if FORCE_SUB_CHANNEL is None:
            await safe_edit_message(query, "⚠️ No channel/group set.", parse_mode=ParseMode.HTML, edit_only=True)
            return
        try:
            bot_member = await context.bot.get_chat_member(FORCE_SUB_CHANNEL, context.bot.id)
            if bot_member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER):
                await safe_edit_message(
                    query,
                    "✅ <b>I am admin in that chat.</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_kb(),
                    edit_only=True,
                )
            else:
                await safe_edit_message(
                    query,
                    "⚠️ <b>I am not admin in that chat.</b> Please give me admin access.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_kb(),
                    edit_only=True,
                )
        except (Forbidden, BadRequest):
            await safe_edit_message(
                query,
                "❌ <b>I am not admin in that chat.</b> Please give me admin access.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
                edit_only=True,
            )
    elif data == "fs_remove":
        set_setting("force_sub_channel", "")
        set_setting("force_sub_link", "")
        set_setting("force_sub_enabled", "0")
        set_setting("force_sub_type", "channel")
        set_setting("force_sub_title", "")
        FORCE_SUB_CHANNEL = None
        FORCE_SUB_LINK = None
        FORCE_SUB_ENABLED = False
        FORCE_SUB_TYPE = "channel"
        FORCE_SUB_TITLE = None
        await safe_edit_message(
            query,
            "🗑 <b>Force Sub removed.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
            edit_only=True,
        )
    else:
        await safe_edit_message(query, "Unknown action.", reply_markup=admin_kb(), edit_only=True)


async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if user is None or msg is None:
        return

    if user.id in REPORT_FLOW:
        flow = REPORT_FLOW[user.id]

        if flow.get("reason") is None:
            if msg.text:
                flow["reason"] = msg.text
                await msg.reply_text(
                    "✅ Reason saved.\n\n"
                    "Now please send a <b>screenshot</b> (photo/image) as proof.\n\n"
                    "Type /cancelreport to cancel.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await msg.reply_text(
                    "⚠️ Please send your reason as a <b>text message</b> first.",
                    parse_mode=ParseMode.HTML,
                )
            return

        photo_file_id = None
        if msg.photo:
            photo_file_id = msg.photo[-1].file_id
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
            photo_file_id = msg.document.file_id

        if not photo_file_id:
            await msg.reply_text(
                "⚠️ Please send a <b>screenshot (photo/image)</b> as proof.\n\n"
                "Type /cancelreport to cancel.",
                parse_mode=ParseMode.HTML,
            )
            return

        reported_id = flow["reported_id"]
        reported_name = flow.get("reported_name", f"User {reported_id}")
        reporter_name = flow.get("reporter_name", f"User {user.id}")
        reason = flow.get("reason") or "No reason provided"

        add_report(
            user.id,
            reported_id,
            reason=reason,
            proof_file_id=photo_file_id,
            proof_type="photo",
        )

        caption = (
            f"⚑ <b>New Report with Proof</b>\n\n"
            f"<b>Reported user:</b> {html.escape(reported_name)} — <code>{reported_id}</code>\n"
            f"<b>Reporting user:</b> {html.escape(reporter_name)} — <code>{user.id}</code>\n\n"
            f"<b>Reason:</b> {html.escape(reason)}\n\n"
            f"<i>Time: {datetime.now(timezone.utc).isoformat()}</i>"
        )
        if len(caption) > 1024:
            caption = caption[:1021] + "..."

        for admin_chat in ADMIN_CHAT_IDS:
            try:
                await context.bot.send_photo(
                    chat_id=admin_chat,
                    photo=photo_file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            except (Forbidden, BadRequest, TelegramError) as e:
                logger.warning("Failed to send report proof to admin chat %s: %s", admin_chat, e)

        review_alert = (
            f"{HEADER}\n\n"
            "🔔 <b>𝐑ᴇᴠɪᴇᴡ 𝐑ᴇǫᴜᴇsᴛ</b>\n\n"
            f"<b>Reported user:</b> {html.escape(reported_name)} — <code>{reported_id}</code>\n"
            f"<b>Reporting user:</b> {html.escape(reporter_name)} — <code>{user.id}</code>\n"
            f"<b>Reason:</b> {html.escape(reason)}\n\n"
            "Please review the proof above and take action if the user is found guilty.\n\n"
            f"<b>Options:</b>\n"
            f"• <code>/ban {reported_id}</code> — permanent ban\n"
            f"• <code>/ban {reported_id} &lt;days&gt;</code> — temporary ban\n"
            f"• <code>/unban {reported_id}</code> — if not guilty"
        )
        if len(review_alert) > 4096:
            review_alert = review_alert[:4093] + "..."

        for admin_chat in ADMIN_CHAT_IDS:
            try:
                await context.bot.send_message(
                    chat_id=admin_chat,
                    text=review_alert,
                    parse_mode=ParseMode.HTML,
                )
            except (Forbidden, BadRequest, TelegramError) as e:
                logger.warning("Failed to send review alert to admin chat %s: %s", admin_chat, e)

        logger.info(
            "Report with proof: %s (%s) -> %s (%s) | reason=%s",
            user.id, reporter_name, reported_id, reported_name, reason,
        )

        REPORT_FLOW.pop(user.id, None)

        await msg.reply_text(
            f"{HEADER}\n\n"
            "✅ <b>𝐑ᴇᴘᴏʀᴛ 𝐒ᴜʙᴍɪᴛᴛᴇᴅ</b>\n\n"
            "Your report has been sent to the admin team.\n\n"
            "🕵️ <b>We will review this report carefully.</b>\n"
            "If the user is found guilty, we will take action against them.\n\n"
            "If this is urgent, you can also DM @CRUXAETH and explain your problem.",
            parse_mode=ParseMode.HTML,
        )
        return

    partner_id = get_partner(user.id)
    if partner_id is None:
        return

    try:
        await context.bot.copy_message(
            chat_id=partner_id,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )
        increment_stat("total_messages")
        return
    except (BadRequest, TelegramError) as e:
        logger.debug("copy_message failed (%s), trying fallback: %s", type(e).__name__, e)

    try:
        if msg.poll:
            await context.bot.send_poll(
                chat_id=partner_id,
                question=msg.poll.question,
                options=[o.text for o in msg.poll.options],
                is_anonymous=msg.poll.is_anonymous,
                type=msg.poll.type,
                allows_multiple_answers=msg.poll.allows_multiple_answers,
                correct_option_id=msg.poll.correct_option_id,
                explanation=msg.poll.explanation,
            )
            increment_stat("total_messages")
            return
        elif msg.location:
            await context.bot.send_location(
                chat_id=partner_id,
                latitude=msg.location.latitude,
                longitude=msg.location.longitude,
                horizontal_accuracy=msg.location.horizontal_accuracy,
            )
            increment_stat("total_messages")
            return
        elif msg.contact:
            await context.bot.send_contact(
                chat_id=partner_id,
                phone_number=msg.contact.phone_number,
                first_name=msg.contact.first_name,
                last_name=msg.contact.last_name,
                vcard=msg.contact.vcard,
            )
            increment_stat("total_messages")
            return
        elif msg.text:
            await context.bot.send_message(
                chat_id=partner_id,
                text=msg.text,
                entities=msg.entities,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            increment_stat("total_messages")
            return
        else:
            await context.bot.send_message(
                partner_id, "⚠️ This message type cannot be relayed."
            )
    except (Forbidden, BadRequest, TelegramError) as e:
        logger.debug("Relay failed for %s -> %s: %s", user.id, partner_id, e)
        with contextlib.suppress(Forbidden, BadRequest):
            await context.bot.send_message(
                user.id, "⚠️ Failed to deliver your message. Your partner may have blocked the bot."
            )


async def post_init(app: Application) -> None:
    global _queue, _queued, _active, _partner_to_user, ADMIN_IDS, ADMIN_CHAT_IDS
    _queue = asyncio.Queue()
    _queued.clear()
    _active.clear()
    _partner_to_user.clear()
    _timeout_tasks.clear()
    _searching_tasks.clear()
    load_force_sub_settings()

    fresh_ids = fetch_admin_ids()
    if fresh_ids:
        ADMIN_IDS = fresh_ids
        if not _admin_chat_env.strip():
            ADMIN_CHAT_IDS = set(ADMIN_IDS)

    logger.info("Matchmaking state cleared.")
    logger.info("BAN_IMAGE_URL: %s", BAN_IMAGE_URL)
    logger.info("UNBAN_IMAGE_URL: %s", UNBAN_IMAGE_URL)
    logger.info("ADMIN_IDS (from GitHub): %s", sorted(ADMIN_IDS))
    logger.info("ADMIN_CHAT_IDS: %s", sorted(ADMIN_CHAT_IDS))
    logger.info("Force Sub: %s | Type: %s | Chat: %s", FORCE_SUB_ENABLED, FORCE_SUB_TYPE, FORCE_SUB_CHANNEL)
    videos = get_welcome_videos()
    logger.info("Welcome videos loaded: %d", len(videos))
    for v in videos:
        logger.info("  Video #%s: %s...", v["id"], v["file_id"][:30])


async def post_shutdown(app: Application) -> None:
    logger.info("Shutting down cleanly.")


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        print("❌ BOT_TOKEN is not set. Export it or edit the fallback in the script.")
        sys.exit(1)

    init_db()

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )


    application.add_handler(
        MessageHandler(filters.ALL, ban_gate),
        group=-1,
    )
    application.add_handler(
        CallbackQueryHandler(ban_gate),
        group=-1,
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("find", cmd_find))
    application.add_handler(CommandHandler("next", cmd_next))
    application.add_handler(CommandHandler("skip", cmd_next))
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("leave", cmd_stop))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("cancelreport", cmd_cancelreport))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("report", cmd_report))
    application.add_handler(CommandHandler("block", cmd_block))
    application.add_handler(CommandHandler("unblock", cmd_unblock))
    application.add_handler(CommandHandler("rules", cmd_rules))
    application.add_handler(CommandHandler("privacy", cmd_privacy))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("ping", cmd_ping))
    application.add_handler(CommandHandler("id", cmd_id))
    application.add_handler(CommandHandler("about", cmd_about))
    application.add_handler(CommandHandler("feedback", cmd_feedback))
    application.add_handler(CommandHandler("donate", cmd_donate))
    application.add_handler(CommandHandler("settings", cmd_settings))

    application.add_handler(CommandHandler("admin", cmd_admin))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast))
    application.add_handler(CommandHandler("ban", cmd_ban))
    application.add_handler(CommandHandler("unban", cmd_unban))
    application.add_handler(CommandHandler("maintenance", cmd_maintenance))
    application.add_handler(CommandHandler("exportstats", cmd_exportstats))
    application.add_handler(CommandHandler("setwelcome", cmd_setwelcome))
    application.add_handler(CommandHandler("setrules", cmd_setrules))
    application.add_handler(CommandHandler("users", cmd_users))
    application.add_handler(CommandHandler("active", cmd_active))
    application.add_handler(CommandHandler("queue", cmd_queue))
    application.add_handler(CommandHandler("forcesub", cmd_forcesub))
    application.add_handler(CommandHandler("removeforcesub", cmd_removeforcesub))

    application.add_handler(CommandHandler("addvideo", cmd_addvideo))
    application.add_handler(CommandHandler("videos", cmd_videos))
    application.add_handler(CommandHandler("delvideo", cmd_delvideo))
    application.add_handler(CommandHandler("testwelcome", cmd_testwelcome))

    application.add_handler(CallbackQueryHandler(cb_find, pattern="^find$"))
    application.add_handler(CallbackQueryHandler(cb_stop, pattern="^stop$"))
    application.add_handler(CallbackQueryHandler(cb_next, pattern="^next$"))
    application.add_handler(CallbackQueryHandler(cb_status, pattern="^status$"))
    application.add_handler(CallbackQueryHandler(cb_cancel, pattern="^cancel$"))
    application.add_handler(CallbackQueryHandler(cb_report, pattern="^report$"))
    application.add_handler(CallbackQueryHandler(cb_block, pattern="^block$"))
    application.add_handler(CallbackQueryHandler(cb_verify_sub, pattern="^verify_sub$"))
    application.add_handler(CallbackQueryHandler(cb_noop, pattern="^noop$"))
    application.add_handler(CallbackQueryHandler(cb_back_menu, pattern="^back_menu$"))
    application.add_handler(CallbackQueryHandler(cb_admin_menu, pattern="^admin_menu$"))
    application.add_handler(CallbackQueryHandler(cb_admin_actions, pattern="^admin_"))

    application.add_handler(
        MessageHandler(
            filters.FORWARDED & ~filters.COMMAND,
            capture_forwarded_channel,
        ),
        group=0,
    )

    application.add_handler(
        MessageHandler(
            ~filters.COMMAND & ~filters.StatusUpdate.ALL & ~filters.FORWARDED,
            relay_message,
        ),
        group=1,
    )

    logger.info("🚀 Starting....")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Goodbye.")
