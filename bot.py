import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, List

from telegram import Update, ChatPermissions, BotCommand, BotCommandScopeAllGroupChats
from telegram.ext import Application, CommandHandler, MessageHandler, ChatMemberHandler, ContextTypes, filters

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "freshbot.db")
WARN_THRESHOLD = int(os.getenv("WARN_THRESHOLD", "3"))


class FreshBot:
    def __init__(self) -> None:
        self.token = os.getenv("BOT_TOKEN")
        if not self.token:
            raise ValueError("BOT_TOKEN is required")
        self.app = Application.builder().token(self.token).build()

        self._init_db()
        self._add_handlers()

    # ------------------------- DB -------------------------
    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True) if os.path.dirname(DB_PATH) else None
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS warnings (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count   INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT,
                    updated_at  INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS activity (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    ts      INTEGER NOT NULL
                )
                """
            )
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL,
                    ts          INTEGER NOT NULL,
                    PRIMARY KEY (referrer_id, referred_id)
                )
                """
            )
            conn.commit()

    # ---------------------- Handlers ----------------------
    def _add_handlers(self) -> None:
        # Commands
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("warn", self.cmd_warn))
        self.app.add_handler(CommandHandler("ban", self.cmd_ban))
        self.app.add_handler(CommandHandler("kick", self.cmd_kick))
        self.app.add_handler(CommandHandler(["activity", "activemembers"], self.cmd_activity))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))

        # Group join/leave messages
        self.app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.on_member_join))
        self.app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, self.on_member_left))

        # Track activity on any message in groups
        self.app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, self._track_activity))

        # At startup, set commands visible in groups
        self.app.post_init = self._post_init

    async def _post_init(self, app: Application) -> None:  # type: ignore[override]
        try:
            commands = [
                BotCommand("start", "Show welcome and your referral link"),
                BotCommand("help", "Show help and command list"),
                BotCommand("warn", "Warn a user: /warn @user [reason]"),
                BotCommand("ban", "Ban a user: /ban @user [reason]"),
                BotCommand("kick", "Remove user from group: /kick @user"),
                BotCommand("activity", "Active members: /activity [day|week|month|all]"),
                BotCommand("stats", "Your stats & referrals"),
            ]
            await app.bot.set_my_commands(commands, scope=BotCommandScopeAllGroupChats())
        except Exception as e:
            logger.warning(f"Failed to set group commands: {e}")

    # --------------------- Utilities ----------------------
    @staticmethod
    def _utcnow_ts() -> int:
        return int(datetime.now(tz=timezone.utc).timestamp())

    def _add_warning(self, chat_id: int, user_id: int, reason: str) -> int:
        now = self._utcnow_ts()
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
            row = c.fetchone()
            if row:
                count = row[0] + 1
                c.execute(
                    "UPDATE warnings SET count=?, last_reason=?, updated_at=? WHERE chat_id=? AND user_id=?",
                    (count, reason, now, chat_id, user_id),
                )
            else:
                count = 1
                c.execute(
                    "INSERT INTO warnings (chat_id, user_id, count, last_reason, updated_at) VALUES (?,?,?,?,?)",
                    (chat_id, user_id, count, reason, now),
                )
            conn.commit()
            return count

    def _clear_warnings(self, chat_id: int, user_id: int) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM warnings WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            conn.commit()

    def _record_activity(self, chat_id: int, user_id: int) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO activity (chat_id, user_id, ts) VALUES (?,?,?)",
                (chat_id, user_id, self._utcnow_ts()),
            )
            conn.commit()

    def _record_referral(self, referrer_id: int, referred_id: int) -> None:
        if referrer_id == referred_id:
            return
        with sqlite3.connect(DB_PATH) as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO referrals (referrer_id, referred_id, ts) VALUES (?,?,?)",
                    (referrer_id, referred_id, self._utcnow_ts()),
                )
                conn.commit()
            except sqlite3.Error as e:
                logger.warning(f"Referral insert failed: {e}")

    @staticmethod
    def _parse_target(update: Update) -> Optional[int]:
        """Resolve target user_id from reply or command mention."""
        msg = update.effective_message
        if msg is None:
            return None
        if msg.reply_to_message:
            return msg.reply_to_message.from_user.id if msg.reply_to_message.from_user else None
        # Try entities (mentions), otherwise numbers
        if msg.entities:
            for ent in msg.parse_entities() .values():
                # Not perfect; for simplicity only reply is required for precise id.
                pass
        # Fallback: try last arg as numeric id
        try:
            parts = msg.text.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
        except Exception:
            pass
        return None

    # --------------------- Commands -----------------------
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        user = update.effective_user
        args = context.args or []

        # Referral: /start <referrer_id> in private
        if args and chat and chat.type == chat.PRIVATE:
            try:
                referrer_id = int(args[0])
                self._record_referral(referrer_id, user.id)
            except ValueError:
                pass

        # Compose message depending on chat type
        cmds = (
            "Available commands:\n"
            "/warn (reply) [reason] — add a warning\n"
            "/ban (reply) [reason] — ban user\n"
            "/kick (reply) — remove user\n"
            "/activity [day|week|month|all] — active members\n"
            "/stats — your stats & referrals\n"
            "/help — show details"
        )
        if chat and chat.type in (chat.GROUP, chat.SUPERGROUP):
            await update.effective_message.reply_text(
                f"Hello! I'm alive.\n{cmds}\n\nTip: Your referral link (DM): t.me/{(await context.bot.get_me()).username}?start={user.id}")
        else:
            await update.effective_message.reply_text(
                f"Welcome!\nShare your referral link: t.me/{(await context.bot.get_me()).username}?start={user.id}\n\n{cmds}")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (
            "Commands:\n"
            "• /start — welcome, shows referral link (works in groups and DM)\n"
            "• /help — this help\n"
            "• /warn — reply to a user: /warn [reason]\n"
            "• /ban — reply to a user: /ban [reason]\n"
            "• /kick — reply to a user to remove\n"
            "• /activity [day|week|month|all] — list active members by period\n"
            "• /stats — show your stats and referral count\n"
        )
        await update.effective_message.reply_text(text)

    async def cmd_warn(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        user = update.effective_user
        target_id = self._parse_target(update)
        reason = " ".join((update.effective_message.text.split()[2:])) if len(update.effective_message.text.split()) > 2 else "No reason provided"

        if not chat or chat.type not in (chat.GROUP, chat.SUPERGROUP):
            return await update.effective_message.reply_text("Use in a group by replying to a user's message.")
        if not target_id:
            return await update.effective_message.reply_text("Reply to the user's message or specify numeric user id: /warn <id> [reason]")

        count = self._add_warning(chat.id, target_id, reason)
        await update.effective_message.reply_text(f"Warned user {target_id}. Total warnings: {count}.")

        if count >= WARN_THRESHOLD:
            try:
                await context.bot.ban_chat_member(chat.id, target_id)
                await update.effective_message.reply_text(f"User {target_id} banned (threshold {WARN_THRESHOLD}).")
                self._clear_warnings(chat.id, target_id)
            except Exception as e:
                await update.effective_message.reply_text(f"Failed to ban user: {e}")

    async def cmd_ban(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        target_id = self._parse_target(update)
        if not chat or chat.type not in (chat.GROUP, chat.SUPERGROUP):
            return await update.effective_message.reply_text("Use in a group by replying to a user's message.")
        if not target_id:
            return await update.effective_message.reply_text("Reply to the target user or specify numeric id.")
        try:
            await context.bot.ban_chat_member(chat.id, target_id)
            await update.effective_message.reply_text(f"User {target_id} banned.")
            self._clear_warnings(chat.id, target_id)
        except Exception as e:
            await update.effective_message.reply_text(f"Failed to ban: {e}")

    async def cmd_kick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        target_id = self._parse_target(update)
        if not chat or chat.type not in (chat.GROUP, chat.SUPERGROUP):
            return await update.effective_message.reply_text("Use in a group by replying to a user's message.")
        if not target_id:
            return await update.effective_message.reply_text("Reply to the target user or specify numeric id.")
        try:
            await context.bot.ban_chat_member(chat.id, target_id)
            await context.bot.unban_chat_member(chat.id, target_id)  # Kick (allow rejoin)
            await update.effective_message.reply_text(f"User {target_id} removed.")
            self._clear_warnings(chat.id, target_id)
        except Exception as e:
            await update.effective_message.reply_text(f"Failed to remove: {e}")

    async def cmd_activity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat or chat.type not in (chat.GROUP, chat.SUPERGROUP):
            return await update.effective_message.reply_text("Use in a group.")

        period = (context.args[0].lower() if context.args else "all")
        now = self._utcnow_ts()
        spans = {
            "day": now - 24 * 3600,
            "week": now - 7 * 24 * 3600,
            "month": now - 30 * 24 * 3600,
            "all": 0,
        }
        since = spans.get(period, 0)

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            if since > 0:
                c.execute(
                    """
                    SELECT user_id, COUNT(*) as msgs
                    FROM activity
                    WHERE chat_id=? AND ts>=?
                    GROUP BY user_id
                    ORDER BY msgs DESC
                    LIMIT 25
                    """,
                    (chat.id, since),
                )
            else:
                c.execute(
                    """
                    SELECT user_id, COUNT(*) as msgs
                    FROM activity
                    WHERE chat_id=?
                    GROUP BY user_id
                    ORDER BY msgs DESC
                    LIMIT 25
                    """,
                    (chat.id,),
                )
            rows = c.fetchall()

        title = f"Active members ({period})"
        if not rows:
            return await update.effective_message.reply_text(f"{title}: none yet")

        lines = [title]
        for idx, (uid, msgs) in enumerate(rows, start=1):
            lines.append(f"{idx}. {uid} — {msgs} msgs")
        await update.effective_message.reply_text("\n".join(lines))

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        # Count referrals
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user.id,))
            ref_count = c.fetchone()[0]
        link = f"t.me/{(await context.bot.get_me()).username}?start={user.id}"
        await update.effective_message.reply_text(
            f"Your stats:\nReferrals: {ref_count}\nLink: {link}"
        )

    # ----------------- Group membership -------------------
    async def on_member_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        for u in msg.new_chat_members:
            await msg.reply_text(f"Welcome, {u.first_name}! 🎉")

    async def on_member_left(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        u = msg.left_chat_member
        if u:
            await msg.reply_text(f"{u.first_name} has left the chat.")

    async def _track_activity(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        user = update.effective_user
        if chat and user:
            self._record_activity(chat.id, user.id)

    # ------------------- Webhook/Polling ------------------
    def set_webhook(self, url: str) -> bool:
        try:
            self.app.bot.set_webhook(url=url)
            return True
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            return False

    def run(self, webhook_url: Optional[str] = None) -> None:
        if webhook_url:
            logger.info("Running in webhook mode")
            self.app.run_webhook(
                listen="0.0.0.0",
                port=int(os.getenv("PORT", "10000")),
                webhook_url=webhook_url,
            )
        else:
            logger.info("Running in polling mode")
            self.app.run_polling()

if __name__ == "__main__":
    try:
        bot = FreshBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your BOT_TOKEN and optional BOT_USERNAME")
        print("2. Installed the required dependencies: pip install -r requirements.txt")
