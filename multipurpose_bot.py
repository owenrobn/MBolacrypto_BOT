import os
import logging
import sqlite3
import asyncio
import io
import re
from datetime import datetime, timedelta
from typing import Dict, List

import httpx
from PIL import Image
import telegram
import telegram.ext as tg_ext
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault, BotCommandScopeChat
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ChatMemberHandler, filters, ContextTypes
from dotenv import load_dotenv
from database import Database

# Load env
load_dotenv()

logger = logging.getLogger("multipurpose_bot")
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

db = Database()

AFRICA_INFO: Dict[str, Dict[str, str]] = {
    "NG": {"country": "Nigeria", "capital": "Abuja", "timezone": "Africa/Lagos"},
    "GH": {"country": "Ghana", "capital": "Accra", "timezone": "Africa/Accra"},
    "KE": {"country": "Kenya", "capital": "Nairobi", "timezone": "Africa/Nairobi"},
    "ZA": {"country": "South Africa", "capital": "Pretoria (admin)", "timezone": "Africa/Johannesburg"},
    "EG": {"country": "Egypt", "capital": "Cairo", "timezone": "Africa/Cairo"},
    "DZ": {"country": "Algeria", "capital": "Algiers", "timezone": "Africa/Algiers"},
    "MA": {"country": "Morocco", "capital": "Rabat", "timezone": "Africa/Casablanca"},
    "TZ": {"country": "Tanzania", "capital": "Dodoma", "timezone": "Africa/Dar_es_Salaam"},
}


class MultipurposeBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        if not self.bot_token or not self.bot_username:
            raise RuntimeError("BOT_TOKEN and BOT_USERNAME must be set in environment")

        # Seed admins from env (comma-separated) once; DB is source of truth after
        admin_ids_env = os.getenv('ADMIN_IDS', '')
        try:
            self.admin_ids = {int(x.strip()) for x in admin_ids_env.split(',') if x.strip()}
        except Exception:
            self.admin_ids = set()
        if self.admin_ids:
            try:
                db.seed_admins(sorted(list(self.admin_ids)))
            except Exception as e:
                logger.warning(f"Admin seed failed: {e}")

        # Log PTB version
        try:
            import telegram
            ptb_ver = getattr(telegram, '__version__', 'unknown')
        except Exception:
            ptb_ver = 'unknown'
        logger.info(f"python-telegram-bot version: {ptb_ver}")
        logger.info(f"telegram.ext module file: {getattr(tg_ext, '__file__', 'unknown')}")

        # In-memory state for anti-flood tracking: (chat_id, user_id) -> list[timestamps]
        self._flood_window: Dict[tuple, List[float]] = {}
        # In-memory: recent captcha messages to clean
        self._pending_captcha: Dict[tuple, int] = {}

    # ========== Helpers ==========
    def _back_main_markup(self, chat_type: str):
        """Inline back-to-main button for private chats only."""
        try:
            if chat_type == 'private':
                return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]])
        except Exception:
            pass
        return None

    async def _log(self, chat_id: int, text: str):
        try:
            gs = db.get_group_settings(chat_id)
            log_chat_id = gs.get('log_chat_id')
            if log_chat_id:
                await self.app.bot.send_message(int(log_chat_id), text, disable_web_page_preview=True)
        except Exception:
            pass

    async def _is_group_admin(self, bot, chat_id: int, user_id: int) -> bool:
        """Return True if user_id is an admin of chat_id."""
        try:
            admins = await bot.get_chat_administrators(chat_id)
            return any(a.user and a.user.id == user_id for a in admins)
        except Exception:
            return False

    def _parse_target_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Resolve target user from reply or first arg (user_id)."""
        try:
            if update.message and update.message.reply_to_message and update.message.reply_to_message.from_user:
                return update.message.reply_to_message.from_user
            if context.args:
                arg = context.args[0]
                try:
                    uid = int(arg)
                    # Build a lightweight user-like object
                    class _U:
                        def __init__(self, _id):
                            self.id = _id
                            self.first_name = None
                            self.username = None
                    return _U(uid)
                except Exception:
                    return None
        except Exception:
            return None
        return None

    # ========== Core run ==========
    def run(self):
        async def _post_init(app: Application):
            try:
                await app.bot.delete_webhook(drop_pending_updates=True)
                logger.info("Webhook deleted (if existed)")
            except Exception as e:
                logger.warning(f"delete_webhook failed: {e}")
            # Set bot commands for better UX in chats
            try:
                # Group commands (admin only)
                group_cmds = [
                    BotCommand('config', 'Group settings'),
                    BotCommand('antilinks', 'Toggle anti-links'),
                    BotCommand('setwarns', 'Set warning threshold'),
                    BotCommand('setmute', 'Set mute duration'),
                    BotCommand('setautoban', 'Toggle auto-ban'),
                    BotCommand('setresetwarns', 'Toggle reset warnings'),
                    BotCommand('warn', 'Warn a user'),
                    BotCommand('mute', 'Mute a user'),
                    BotCommand('ban', 'Ban a user'),
                    BotCommand('kick', 'Kick a user'),
                    BotCommand('purge', 'Delete messages'),
                    BotCommand('rules', 'Show group rules'),
                    BotCommand('report', 'Report a user/message')
                ]
                await app.bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())

                # Private chat commands
                private_cmds = [
                    BotCommand('start', 'Start the bot'),
                    BotCommand('menu', 'Open main menu'),
                    BotCommand('help', 'Show help'),
                    BotCommand('leaderboard', 'Show leaderboard')
                ]
                await app.bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())

                # Default fallback
                await app.bot.set_my_commands(private_cmds, scope=BotCommandScopeDefault())
            except Exception as e:
                logger.warning(f"set_my_commands failed: {e}")

        application = (
            Application.builder()
            .token(self.bot_token)
            .post_init(_post_init)
            .concurrent_updates(True)
            .build()
        )
        # keep reference for logging helper
        self.app = application

        # Core commands
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.help_command))
        application.add_handler(CommandHandler('menu', self.menu_command))
        application.add_handler(CommandHandler('refreshcommands', self.refreshcommands_command))
        
        # Group management commands
        application.add_handler(CommandHandler('config', self.group_config_command))
        application.add_handler(CommandHandler('antilinks', self.antilinks_command))
        application.add_handler(CommandHandler('setwarns', self.setwarns_command))
        application.add_handler(CommandHandler('setmute', self.setmute_command))
        application.add_handler(CommandHandler('setautoban', self.setautoban_command))
        application.add_handler(CommandHandler('setresetwarns', self.setresetwarns_command))
        
        # Moderation commands
        application.add_handler(CommandHandler('warn', self.warn_command))
        application.add_handler(CommandHandler('mute', self.mute_command))
        application.add_handler(CommandHandler('ban', self.ban_command))
        application.add_handler(CommandHandler('kick', self.kick_command))
        application.add_handler(CommandHandler('purge', self.purge_command))
        
        # Info commands
        application.add_handler(CommandHandler('rules', self.rules_command))
        application.add_handler(CommandHandler('report', self.report_command))
        application.add_handler(CommandHandler('leaderboard', self.group_leaderboard_command))
        
        # Callback handlers
        application.add_handler(CallbackQueryHandler(self.button_handler))
        application.add_handler(CallbackQueryHandler(self.group_config_callback, pattern=r'^gc:'))
        application.add_handler(CallbackQueryHandler(self.approval_callback, pattern=r'^approve:'))
        
        # Message handlers
        application.add_handler(MessageHandler(filters.StatusUpdate.ALL, self._service_message_handler))  # Service messages
        application.add_handler(MessageHandler(
            filters.ChatType.GROUPS & (filters.ALL),
            self._group_message_handler_unified
        ))
        # Member updates for welcome/goodbye
        application.add_handler(ChatMemberHandler(self.chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        application.add_error_handler(self.error_handler)

        logger.info("🚀 Multipurpose bot is starting...")

        port = os.getenv("PORT")
        webhook_url = os.getenv("WEBHOOK_URL")
        # Auto-detect Render external URL if WEBHOOK_URL isn't provided
        if not webhook_url:
            render_url = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("RENDER_EXTERNAL_HOSTNAME")
            if render_url:
                # Ensure it has https scheme
                if not render_url.startswith("http://") and not render_url.startswith("https://"):
                    render_url = f"https://{render_url}"
                # Strip trailing slash to avoid double slashes
                webhook_url = render_url.rstrip('/')
        if port and webhook_url:
            logger.info(f"Starting in WEBHOOK mode on port {port}, url: {webhook_url}")
            application.run_webhook(
                listen="0.0.0.0",
                port=int(port),
                webhook_url=webhook_url,
                drop_pending_updates=True,
            )
        else:
            logger.info("Starting in POLLING mode (PORT or WEBHOOK_URL missing)")
            application.run_polling()

    # ===== Clean service and captcha toggles =====
    async def cleanservice_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if not context.args or context.args[0].lower() not in ['on','off']:
            await update.message.reply_text("Usage: /cleanservice on|off")
            return
        val = 1 if context.args[0].lower() == 'on' else 0
        db.set_group_setting(update.effective_chat.id, 'clean_service', val)
        await update.message.reply_text(f"Clean service set to {'ON' if val else 'OFF'}.")

    async def captcha_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if not context.args or context.args[0].lower() not in ['on','off']:
            await update.message.reply_text("Usage: /captcha on|off")
            return
        val = 1 if context.args[0].lower() == 'on' else 0
        db.set_group_setting(update.effective_chat.id, 'captcha_enabled', val)
        await update.message.reply_text(f"Captcha set to {'ON' if val else 'OFF'}.")

    # ===== Notes =====
    async def save_note_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /save <name> [text or reply with media]")
            return
        name = context.args[0].strip().lstrip('#')
        msg = update.effective_message
        content = ' '.join(context.args[1:]) if len(context.args) > 1 else None
        file_id = None
        ctype = 'text'
        if msg.reply_to_message:
            r = msg.reply_to_message
            for attr, t in [('photo','photo'),('video','video'),('animation','animation'),('document','document'),('sticker','sticker'),('voice','voice'),('audio','audio')]:
                media = getattr(r, attr, None)
                if media:
                    file_id = media[-1].file_id if isinstance(media, list) else getattr(media, 'file_id', None)
                    ctype = t
                    break
            if not file_id and (r.text or r.caption):
                content = r.text or r.caption
        if not file_id and not content:
            await update.message.reply_text("Nothing to save. Provide text or reply to a message.")
            return
        db.save_note(update.effective_chat.id, name, content, file_id, ctype, update.effective_user.id)
        await update.message.reply_text(f"Saved note #{name}.")

    async def get_note_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /get <name>")
            return
        name = context.args[0].strip().lstrip('#')
        note = db.get_note(update.effective_chat.id, name)
        if not note:
            await update.message.reply_text("Note not found.")
            return
        await self._send_note(context, update.effective_chat.id, note)

    async def notes_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        names = db.list_notes(update.effective_chat.id)
        if not names:
            await update.message.reply_text("No notes saved.")
            return
        await update.message.reply_text("Notes:\n" + '\n'.join(f"#%s"%n for n in names))

    async def delnote_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /delnote <name>")
            return
        name = context.args[0].strip().lstrip('#')
        db.delete_note(update.effective_chat.id, name)
        await update.message.reply_text(f"Deleted note #{name}.")

    async def _send_note(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, note: dict):
        try:
            ctype = note.get('content_type') or 'text'
            file_id = note.get('file_id')
            content = note.get('content')
            if ctype == 'text' or not file_id:
                await context.bot.send_message(chat_id, content or '')
            elif ctype == 'photo':
                await context.bot.send_photo(chat_id, file_id, caption=content or None)
            elif ctype == 'video':
                await context.bot.send_video(chat_id, file_id, caption=content or None)
            elif ctype == 'animation':
                await context.bot.send_animation(chat_id, file_id, caption=content or None)
            elif ctype == 'document':
                await context.bot.send_document(chat_id, file_id, caption=content or None)
            elif ctype == 'sticker':
                await context.bot.send_sticker(chat_id, file_id)
            elif ctype == 'voice':
                await context.bot.send_voice(chat_id, file_id, caption=content or None)
            elif ctype == 'audio':
                await context.bot.send_audio(chat_id, file_id, caption=content or None)
        except Exception:
            pass

    # ===== Filters =====
    async def filter_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if len(context.args) < 2:
            await update.message.reply_text("Usage: /filter <trigger> <response or #note>")
            return
        trigger = context.args[0]
        response = ' '.join(context.args[1:])
        note_name = None
        if response.startswith('#'):
            note_name = response.lstrip('#')
            response = None
        db.add_filter(update.effective_chat.id, trigger, response, note_name, update.effective_user.id)
        await update.message.reply_text(f"Filter '{trigger}' saved.")

    async def stopfilter_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use in a group.")
            return
        if not await self._is_group_admin(context.bot, update.effective_chat.id, update.effective_user.id):
            await update.message.reply_text("Admins only.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /stop <trigger>")
            return
        trigger = context.args[0]
        db.remove_filter(update.effective_chat.id, trigger)
        await update.message.reply_text(f"Filter '{trigger}' removed.")

    # ===== Approvals/Captcha =====
    async def approval_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        try:
            data = query.data  # approve:<chat_id>:<user_id>
            _, chat_id_s, user_id_s = data.split(':')
            chat_id = int(chat_id_s)
            user_id = int(user_id_s)
            # Only group admins can approve
            if not await self._is_group_admin(context.bot, chat_id, query.from_user.id):
                await query.answer("Admins only", show_alert=True)
                return
            perms = ChatPermissions(can_send_messages=True, can_send_media_messages=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True)
            try:
                await context.bot.restrict_chat_member(chat_id, user_id, permissions=perms)
            except Exception:
                pass
            await query.answer("Approved")
            await context.bot.send_message(chat_id, f"✅ Approved <a href=\"tg://user?id={user_id}\">user</a>.", parse_mode=ParseMode.HTML)
            # Clean approval message
            try:
                await context.bot.delete_message(query.message.chat_id, query.message.message_id)
            except Exception:
                pass
        except Exception:
            try:
                await update.callback_query.answer()
            except Exception:
                pass

    # ========== Menus ==========
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            args = context.args
            referred_by_id = None
            event_id = None
            redirect_to_group = None

            # Parse referral code or event code
            if args:
                code = args[0]
                # event personal code: REF8_EVENTCODE or EVENTCODE
                if '_' in code:
                    left, right = code.split('_', 1)
                    # left could be referral code
                    ref_user = db.get_user_by_referral_code(left)
                    if ref_user:
                        referred_by_id = ref_user['user_id']
                    code = right
                # Now treat code as event_code or user referral code
                # event_code lookup
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT id, group_link FROM events WHERE event_code = ? AND is_active = 1', (code,))
                    row = cursor.fetchone()
                    if row:
                        event_id = row[0]
                        redirect_to_group = row[1]
                # else: pure user referral code handled by add_user below

            # Register/ensure user exists
            existing_user = db.get_user(user.id)
            if not existing_user:
                referral_by = referred_by_id
                user_referral_code = db.add_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    referred_by=referral_by,
                    event_id=event_id,
                )
                welcome_msg = f"👋 Welcome, {user.first_name}!\n\n"
                if referred_by_id:
                    referrer = db.get_user(referred_by_id)
                    if referrer:
                        welcome_msg += f"✅ You joined via {referrer['first_name']}'s referral link!\n\n"
                if redirect_to_group:
                    welcome_msg += "🎪 You've been invited to join a group. Use the button below to join.\n\n"
                welcome_msg += "Use the menu below to explore features. Referral tools are inside the 🎯 Referral Center."
                
            else:
                welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"

            keyboard: List[List[InlineKeyboardButton]] = []

            # Referral Center (consolidated)
            keyboard.append([InlineKeyboardButton("🎯 Referral Center", callback_data="ref_center")])

            keyboard.append([InlineKeyboardButton("ℹ️ Help", callback_data="help")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            if redirect_to_group:
                # also show a join group button at top
                join_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎪 Join Group", url=redirect_to_group)]])
                await update.message.reply_text(welcome_msg, reply_markup=join_kb)
                await update.message.reply_text("Main Menu:", reply_markup=reply_markup)
            else:
                await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"/start error: {e}")

    # ========== Rules & Reports ==========
    async def rules_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat.type in ['group', 'supergroup']:
            gs = db.get_group_settings(chat.id)
            text = gs.get('rules_text') or "No rules set for this group yet. Admins can set with /setrules in a reply or with text."
            await update.message.reply_text(text)
        else:
            await update.message.reply_text("Use this in a group to view that group's rules.")

    async def setrules_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can set rules.")
            return
        # Allow setting via replied text or arguments
        text = None
        if update.message.reply_to_message and (update.message.reply_to_message.text or update.message.reply_to_message.caption):
            text = update.message.reply_to_message.text or update.message.reply_to_message.caption
        else:
            text = ' '.join(context.args).strip()
        if not text:
            await update.message.reply_text("Usage: reply to a message with /setrules, or use /setrules <text>.")
            return
        db.set_group_setting(chat_id, 'rules_text', text)
        db.add_log(chat_id, issuer, 'set_rules', f'len={len(text)}')
        await update.message.reply_text("✅ Rules updated. Use /rules to view.")

    async def report_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        reporter = update.effective_user
        target_id = None
        message_id = None
        reason = ' '.join(context.args).strip() if context.args else None
        if update.message.reply_to_message:
            message_id = update.message.reply_to_message.message_id
            if update.message.reply_to_message.from_user:
                target_id = update.message.reply_to_message.from_user.id
        db.add_report(chat_id, reporter.id, target_id, message_id, reason)
        # Notify admins silently
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_ids = [a.user.id for a in admins]
            note = f"🚩 Report from {reporter.first_name}"
            if target_id:
                note += f" against user {target_id}"
            if reason:
                note += f"\nReason: {reason}"
            note += f"\nMessage ID: {message_id or 'n/a'}"
            for uid in admin_ids[:10]:
                try:
                    await context.bot.send_message(uid, f"[Report] chat {chat_id}:\n{note}")
                except Exception:
                    pass
        except Exception:
            pass
        await update.message.reply_text("✅ Report submitted. Group admins have been notified.")
        try:
            await self._log(chat_id, f"[REPORT] From {reporter.id} reason={reason or 'n/a'} msg_id={message_id or 'n/a'}")
        except Exception:
            pass

    # ========== Member Updates (Welcome/Goodbye) ==========
    async def chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            chat = update.effective_chat
            if chat.type not in ['group', 'supergroup']:
                return
            cmu = update.chat_member
            if not cmu:
                return
            old = cmu.old_chat_member.status
            new = cmu.new_chat_member.status
            user = cmu.new_chat_member.user
            gs = db.get_group_settings(chat.id)
            # Joined
            if old in ('left', 'kicked') and new in ('member', 'administrator'):
                # Captcha/approval flow
                if gs.get('captcha_enabled', 0):
                    try:
                        # Restrict new user from sending messages until approved
                        perms = ChatPermissions(
                            can_send_messages=False,
                            can_send_media_messages=False,
                            can_send_polls=False,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False,
                        )
                        await context.bot.restrict_chat_member(chat.id, user.id, permissions=perms)
                    except Exception:
                        pass
                    # Send inline approval button for admins
                    try:
                        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{chat.id}:{user.id}")]])
                        msg = await context.bot.send_message(
                            chat.id,
                            f"🚪 New member pending approval: <a href=\"tg://user?id={user.id}\">{user.first_name or user.username or user.id}</a>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb
                        )
                        # Track for optional cleanup
                        self._pending_captcha[(chat.id, user.id)] = msg.message_id
                        await self._log(chat.id, f"[CAPTCHA] Pending approval for user {user.id}")
                    except Exception:
                        pass
                else:
                    # Normal welcome
                    if gs.get('welcome_enabled', 1):
                        text = gs.get('welcome_text') or "Welcome, {name}!"
                        text = text.replace('{name}', user.first_name or (user.username or str(user.id)))
                        try:
                            await context.bot.send_message(chat.id, text)
                        except Exception:
                            pass
                return
            # Left
            if new in ('left', 'kicked'):
                if gs.get('goodbye_enabled', 0):
                    text = gs.get('goodbye_text') or "Goodbye, {name}!"
                    text = text.replace('{name}', user.first_name or (user.username or str(user.id)))
                    try:
                        await context.bot.send_message(chat.id, text)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"chat_member_update error: {e}")

    async def _service_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Deletes service messages if clean service is enabled for the group.
        Handles joins/leaves, title changes, pins, etc. This is complementary to chat_member_update.
        """
        try:
            chat = update.effective_chat
            if not chat or chat.type not in ['group', 'supergroup']:
                return
            gs = db.get_group_settings(chat.id)
            if not gs.get('clean_service', 0):
                return
            # Delete the service message
            try:
                await context.bot.delete_message(chat.id, update.effective_message.message_id)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"service cleaner error: {e}")

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Global error handler for the Telegram application.
        Logs the exception; optionally could notify admins or write to log chat.
        """
        try:
            err = getattr(context, 'error', None)
            logger.error("Unhandled exception in handler", exc_info=err)
            # Attempt to relay minimal info when possible (avoid raising further exceptions)
            if update and isinstance(update, Update):
                chat = update.effective_chat
                if chat and chat.type in ['group', 'supergroup']:
                    try:
                        await self._log(chat.id, f"[ERROR] {type(err).__name__ if err else 'Exception'} occurred.")
                    except Exception:
                        pass
        except Exception as e:
            # As a last resort
            logger.error(f"error_handler internal failure: {e}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "ℹ️ About This Bot\n\n"
            "This bot currently provides:\n"
            "• Referral Events: create/join events, track referrals, view leaderboards\n"
            "• Group Moderation Core: warnings, mute/unmute, thresholds, anti-links, inline group config\n"
            "• Notes and Filters: store and retrieve notes, keyword filters\n"
            "• Logging: log group events, errors\n\n"
            "Use /start to open the menu. Referral tools are inside the 🎯 Referral Center."
        )
        chat_type = update.effective_chat.type if update.effective_chat else 'private'
        markup = self._back_main_markup(chat_type)
        if markup:
            await update.message.reply_text(msg, reply_markup=markup)
        else:
            await update.message.reply_text(msg + "\n\nUse /menu to open the main menu.")

    # ========== Menu Management ==========

    async def menu_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Open the main menu via command. In private, show inline menu; in groups, hint to DM."""
        try:
            chat = update.effective_chat
            if chat.type == 'private':
                keyboard = [
                    [InlineKeyboardButton("🎯 Referral Center", callback_data="ref_center")],
                    [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
                ]
                await update.message.reply_text("Main Menu:", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.message.reply_text("Open a private chat with me and use /start or /menu to access the main menu.")
        except Exception as e:
            logger.error(f"/menu error: {e}")
            await update.message.reply_text("Could not open menu. Please try again.")



    # ========== Group Message Handlers ==========

    async def _group_message_handler_unified(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Unified group message handler: records activity, enforces anti-links and per-media locks
        with warning/mute escalation. Skips enforcement for group admins.
        """
        chat = update.effective_chat
        if not chat or chat.type not in ["group", "supergroup"]:
            return
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return

        # Record activity for tagactives
        try:
            db.record_activity(chat.id, user.id)
        except Exception:
            pass

        gs = db.get_group_settings(chat.id)

        # Helper: check if user is admin
        try:
            is_admin = await self._is_group_admin(context.bot, chat.id, user.id)
        except Exception:
            is_admin = False
        if is_admin:
            return  # Do not enforce against admins

        # Clean service messages (joins/leaves/pins) if enabled
        try:
            gs = db.get_group_settings(chat.id)
            if gs.get('clean_service', 0):
                if getattr(message, 'new_chat_members', None) or getattr(message, 'left_chat_member', None) or getattr(message, 'pinned_message', None):
                    try:
                        await context.bot.delete_message(chat.id, message.message_id)
                    except Exception:
                        pass
                    # continue processing new member via captcha below even if message deleted
        except Exception:
            pass

        # Captcha/approval flow on new members
        try:
            gs = db.get_group_settings(chat.id)
            if gs.get('captcha_enabled', 0) and getattr(message, 'new_chat_members', None):
                for m in message.new_chat_members:
                    # Restrict user to read-only until approval
                    try:
                        perms = ChatPermissions(
                            can_send_messages=False,
                            can_send_media_messages=False,
                            can_send_polls=False,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False,
                        )
                        await context.bot.restrict_chat_member(chat.id, m.id, permissions=perms)
                    except Exception:
                        pass
                    # Send approve button for admins
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve:{chat.id}:{m.id}")]])
                    try:
                        sent = await context.bot.send_message(chat.id, f"New member pending approval: <a href=\"tg://user?id={m.id}\">{m.first_name}</a>", parse_mode=ParseMode.HTML, reply_markup=kb)
                        self._pending_captcha[(chat.id, m.id)] = sent.message_id
                    except Exception:
                        pass
        except Exception:
            pass

        # Filters and notes triggers
        try:
            text_source = message.text or message.caption
            if message and text_source:
                txt = text_source
                # Hash-note retrieval: #name
                if txt.startswith('#') and len(txt) > 1:
                    name = txt[1:].split()[0]
                    note = db.get_note(chat.id, name)
                    if note:
                        await self._send_note(context, chat.id, note)
                        return
                # Keyword filters
                hits = db.find_filters(chat.id, txt)
                for _, resp, note_name in hits[:1]:
                    if note_name:
                        note = db.get_note(chat.id, note_name)
                        if note:
                            await self._send_note(context, chat.id, note)
                            return
                    elif resp:
                        await context.bot.send_message(chat.id, resp)
                        return
        except Exception:
            pass

        # Anti-links enforcement
        try:
            gs = db.get_group_settings(chat.id)  # Get fresh settings
            if gs.get('anti_links', 0):
                has_link = False
                
                # Check for links in text and caption
                text = (message.text or message.caption or "").lower()
                
                # Check Telegram entities first (for URLs and text links)
                for ent in (message.entities or []):
                    if ent.type in ("url", "text_link"):
                        has_link = True
                        break
                
                # Fallback regex for common link patterns if no entities found
                if not has_link and text:
                    # Match http(s)://, www., t.me/, telegram.me/
                    link_patterns = [
                        r'https?://\S+',
                        r'www\.\S+',
                        r't\.me/\S+',
                        r'telegram\.me/\S+',
                        r'[a-z0-9-]+\.(com|org|net|io|me|gg|xyz|info|app|dev|co|us|uk|ca|au|nz|in|de|fr|es|it|nl|pt|ru|jp|cn|kr|br|mx|ar|id|my|sg|th|vn|ph|tr|sa|ae|eg|za|ng|ke|ma|dz|eg|za|ng|ke|ma|dz|com\.\w{2,3})(/\S*)?'
                    ]
                    
                    for pattern in link_patterns:
                        if re.search(pattern, text, re.IGNORECASE):
                            has_link = True
                            break
                
                # If link detected, take action
                if has_link:
                    try:
                        await context.bot.delete_message(chat.id, message.message_id)
                    except Exception:
                        pass
                    # warn and possibly mute/ban
                    try:
                        count = db.increment_warning(chat.id, user.id, reason='links')
                        wt = int(gs.get('warn_threshold', 3) or 3)
                        await context.bot.send_message(chat.id, f"⚠️ Links are not allowed. Warning {count}/{wt}.")
                        if count >= wt:
                            minutes = int(gs.get('mute_minutes_default', 10) or 10)
                            until = datetime.utcnow() + timedelta(minutes=minutes)
                            perms = ChatPermissions(
                                can_send_messages=False,
                                can_send_media_messages=False,
                                can_send_polls=False,
                                can_send_other_messages=False,
                                can_add_web_page_previews=False,
                            )
                            try:
                                await context.bot.restrict_chat_member(chat.id, user.id, permissions=perms, until_date=until)
                                await context.bot.send_message(chat.id, f"🔇 Auto-muted for {minutes} minutes due to warnings.")
                                if gs.get('strikes_reset_on_mute', 1):
                                    db.clear_warnings(chat.id, user.id)
                            except Exception as e:
                                logger.warning(f"auto mute failed: {e}")
                    except Exception:
                        pass
                    return  # already handled as a violation
        except Exception as e:
            logger.warning(f"antilinks enforcement error: {e}")

        # ========== Media locks enforcement ==========
        try:
            media_checks = [
                ('lock_photos', bool(getattr(message, 'photo', None))),
                ('lock_videos', bool(getattr(message, 'video', None))),
                ('lock_gifs', bool(getattr(message, 'animation', None))),
                ('lock_stickers', bool(getattr(message, 'sticker', None))),
                ('lock_documents', bool(getattr(message, 'document', None))),
                ('lock_voice', bool(getattr(message, 'voice', None))),
                ('lock_audio', bool(getattr(message, 'audio', None))),
                ('lock_forwards', bool(getattr(message, 'forward_date', None)) or bool(getattr(message, 'forward_origin', None))),
            ]
            for key, present in media_checks:
                if present and gs.get(key, 0):
                    try:
                        await context.bot.delete_message(chat.id, message.message_id)
                    except Exception:
                        pass
                    try:
                        reason = key.replace('lock_', '')
                        count = db.increment_warning(chat.id, user.id, reason=reason)
                        wt = int(gs.get('warn_threshold', 3) or 3)
                        await context.bot.send_message(chat.id, f"⚠️ {reason.capitalize()} are locked. Warning {count}/{wt}.")
                        if count >= wt:
                            minutes = int(gs.get('mute_minutes_default', 10) or 10)
                            until = datetime.utcnow() + timedelta(minutes=minutes)
                            perms = ChatPermissions(
                                can_send_messages=False,
                                can_send_media_messages=False,
                                can_send_polls=False,
                                can_send_other_messages=False,
                                can_add_web_page_previews=False,
                            )
                            try:
                                await context.bot.restrict_chat_member(chat.id, user.id, permissions=perms, until_date=until)
                                await context.bot.send_message(chat.id, f"🔇 Auto-muted for {minutes} minutes due to warnings.")
                                if gs.get('strikes_reset_on_mute', 1):
                                    db.clear_warnings(chat.id, user.id)
                            except Exception as e:
                                logger.warning(f"auto mute failed: {e}")
                    except Exception:
                        pass
                    break
        except Exception as e:
            logger.warning(f"media locks enforcement error: {e}")

    # ========== Buttons / Flows ==========
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        data = query.data
        try:
            if data == "noop":
                # No operation; keep menu
                await query.answer("Use the menu buttons or /help")
                return
            if data == "ref_center":
                await self.show_referral_center(query, user_id)
                return
            if data == "main_menu":
                await self.show_main_menu(query, user_id)
                return
            if data == "help":
                await self.show_help(query)
                return
            if data == "stats":
                await self.show_stats(query, user_id)
            elif data == "leaderboard":
                await self.show_leaderboard(query)
            elif data == "my_events":
                await self.show_my_events(query, user_id)
            elif data == "create_event":
                await self.start_create_event(query, context)
            elif data == "help":
                await self.show_help(query)
            elif data == "my_event_links":
                await self.show_my_event_links(query, user_id)
            elif data.startswith("event_"):
                event_id = int(data.split('_')[1])
                await self.show_event_stats(query, event_id)
            elif data.startswith("event_link_"):
                event_id = int(data.split('_')[2])
                await self.send_event_link(query, event_id)
            elif data.startswith("join_event_"):
                event_id = int(data.split('_')[2])
                await self.join_event_confirm(query, event_id)
            elif data.startswith("set_group_"):
                event_id = int(data.split('_')[2])
                await self.start_set_group_link(query, context, event_id)
            elif data == "skip_group_link":
                await self.skip_group_link(query, context)
        except Exception as e:
            logger.error(f"button_handler error: {e}")
            try:
                await query.edit_message_text("An error occurred. Please try again or use /start.")
            except Exception:
                pass

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            ud = context.user_data
            if ud.get('creating_event'):
                if ud.get('event_step') == 'title':
                    await self.create_event_title(update, context)
                elif ud.get('event_step') == 'description':
                    await self.create_event_description(update, context)
                elif ud.get('event_step') == 'group_link':
                    await self.create_event_group_link(update, context)
            elif ud.get('setting_group_link'):
                await self.set_group_link(update, context)
        except Exception as e:
            logger.error(f"handle_text_message error: {e}")

    # ========== Help (inline) ==========
    async def show_help(self, query):
        help_msg = (
            "ℹ️ About This Bot\n\n"
            "• Referral Events: create events, set group links, track referrals\n"
            "• Group Moderation: warn/mute/ban/kick, anti-links, notes, filters, logging\n"
            "• Use /groupconfig in groups for moderation settings\n"
        )
        await query.edit_message_text(help_msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]))

    async def refreshcommands_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin-only in groups: refresh the slash command menu for this chat and global scopes."""
        chat = update.effective_chat
        user_id = update.effective_user.id if update.effective_user else None
        try:
            # Group commands (admin only)
            group_cmds = [
                BotCommand('config', 'Group settings'),
                BotCommand('antilinks', 'Toggle anti-links'),
                BotCommand('setwarns', 'Set warning threshold'),
                BotCommand('setmute', 'Set mute duration'),
                BotCommand('setautoban', 'Toggle auto-ban'),
                BotCommand('setresetwarns', 'Toggle reset warnings'),
                BotCommand('warn', 'Warn a user'),
                BotCommand('mute', 'Mute a user'),
                BotCommand('ban', 'Ban a user'),
                BotCommand('kick', 'Kick a user'),
                BotCommand('purge', 'Delete messages'),
                BotCommand('rules', 'Show group rules'),
                BotCommand('report', 'Report a user/message')
            ]
            
            # Private chat commands
            private_cmds = [
                BotCommand('start', 'Start the bot'),
                BotCommand('menu', 'Open main menu'),
                BotCommand('help', 'Show help'),
                BotCommand('leaderboard', 'Show leaderboard')
            ]
            
            # Always refresh global scopes too
            await context.bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
            await context.bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
            
            # Per-chat override when in a group/supergroup
            if chat.type in ['group', 'supergroup']:
                if not await self._is_group_admin(context.bot, chat.id, user_id):
                    await update.message.reply_text("Only admins can refresh commands in groups.")
                    return
                await context.bot.set_my_commands(group_cmds, scope=BotCommandScopeChat(chat_id=chat.id))
                await update.message.reply_text("✅ Commands refreshed for this group. If you still see the old list, wait ~1 minute or type / to refresh.")
            else:
                # Private chat
                await context.bot.set_my_commands(private_cmds, scope=BotCommandScopeChat(chat_id=chat.id))
                await update.message.reply_text("✅ Commands refreshed for this chat.")
        except Exception as e:
            logger.warning(f"refreshcommands failed: {e}")
            try:
                await update.message.reply_text("❌ Failed to refresh commands.")
            except Exception:
                pass

    async def showcommands_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show which commands the bot currently exposes for this chat and default scopes."""
        chat = update.effective_chat
        try:
            cur = await context.bot.get_my_commands(scope=BotCommandScopeChat(chat_id=chat.id))
            default_cmds = await context.bot.get_my_commands()
            group_cmds = await context.bot.get_my_commands(scope=BotCommandScopeAllGroupChats())
            private_cmds = await context.bot.get_my_commands(scope=BotCommandScopeAllPrivateChats())
            def fmt(cmds):
                return '\n'.join(f"/{c.command} - {c.description}" for c in cmds) or '(none)'
            msg = (
                f"Scope: this chat ({chat.id})\n" + fmt(cur) + "\n\n" +
                "Scope: all group chats\n" + fmt(group_cmds) + "\n\n" +
                "Scope: all private chats\n" + fmt(private_cmds) + "\n\n" +
                "Scope: default\n" + fmt(default_cmds)
            )
            await update.message.reply_text(msg)
        except Exception as e:
            logger.warning(f"showcommands failed: {e}")
            try:
                await update.message.reply_text("❌ Failed to get commands.")
            except Exception:
                pass

    async def resetcommands_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin-only in groups: clear all command scopes and re-apply defaults."""
        chat = update.effective_chat
        user_id = update.effective_user.id if update.effective_user else None
        try:
            # Clear
            await context.bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
            await context.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
            await context.bot.delete_my_commands(scope=BotCommandScopeDefault())
            if chat.type in ['group', 'supergroup']:
                if not await self._is_group_admin(context.bot, chat.id, user_id):
                    await update.message.reply_text("Only admins can reset in groups.")
                    return
                await context.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat.id))
            # Re-apply by calling refresh
            await self.refreshcommands_command(update, context)
        except Exception as e:
            logger.warning(f"resetcommands failed: {e}")
            try:
                await update.message.reply_text("❌ Failed to reset commands.")
            except Exception:
                pass

    # ========== Referral: Stats / Leaderboard ==========
    async def show_stats(self, query, user_id: int):
        user = db.get_user(user_id)
        if not user:
            await query.edit_message_text("❌ User not found. Please use /start to register.")
            return
        stats = db.get_referral_stats(user_id)
        msg = (
            "📊 Your Referral Stats\n\n"
            f"🔗 Your referral code: {user['referral_code']}\n"
            f"📱 Your link: https://t.me/{self.bot_username}?start={user['referral_code']}\n\n"
        )
        if stats['total_referrals'] > 0:
            msg += f"✅ Total referrals: {stats['total_referrals']}\n\n"
            for ru in stats['referred_users'][:5]:
                name = ru['first_name'] or ru['username'] or str(ru['user_id'])
                msg += f"• {name} at {ru['joined_at']}\n"
            if len(stats['referred_users']) > 5:
                msg += f"... and {len(stats['referred_users']) - 5} more!\n"
        else:
            msg += "🔄 No referrals yet. Share your link to start!"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]))

    async def show_leaderboard(self, query):
        lb = db.get_leaderboard(10)
        msg = "🏆 Referral Leaderboard\n\n"
        if lb:
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
            for i, u in enumerate(lb):
                if u['referral_count'] > 0:
                    name = u['first_name'] or u['username'] or "Unknown"
                    medal = medals[i] if i < len(medals) else "🏅"
                    msg += f"{medal} {name}: {u['referral_count']} referrals\n"
        else:
            msg += "No participants yet. Be the first to start referring!"
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]))

    # Group admin /leaderboard
    async def group_leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("This command only works in groups where I'm an admin.")
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            admin_ids = {a.user.id for a in admins}
            if user_id not in admin_ids:
                await update.message.reply_text("Only group admins can request the leaderboard.")
                return
            lb = db.get_leaderboard(10)
            msg = "🏆 Referral Leaderboard\n\n"
            if lb:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                for i, u in enumerate(lb):
                    name = u['first_name'] or u['username'] or "Unknown"
                    medal = medals[i] if i < len(medals) else "🏅"
                    msg += f"{medal} {name}: {u['referral_count']} referrals\n"
            else:
                msg += "No participants yet. Be the first to start referring!\n\n"
                msg += f"🚀 Start here: @{self.bot_username}"
            await update.message.reply_text(msg)
        except Exception as e:
            logger.error(f"group_leaderboard error: {e}")
            await update.message.reply_text("Error loading leaderboard.")

    # ===== Phase 2: Group moderation helpers and commands =====

    async def _is_group_admin(self, bot, chat_id: int, user_id: int) -> bool:
        try:
            admins = await bot.get_chat_administrators(chat_id)
            return user_id in {a.user.id for a in admins}
        except Exception:
            return False

    async def _has_immunity(self, bot, chat_id: int, target_id: int, issuer_id: int) -> tuple[bool, str]:
        """
        Return (True, reason) if target should be immune from moderation actions.
        Protect: self, bot, chat owner, and other admins.
        """
        try:
            bot_id = bot.id
        except Exception:
            bot_id = None
        if target_id == issuer_id:
            return True, "You can't act on yourself."
        if bot_id and target_id == bot_id:
            return True, "I can't act on myself."
        try:
            admins = await bot.get_chat_administrators(chat_id)
            admin_ids = {a.user.id for a in admins}
            owner_id = next((a.user.id for a in admins if getattr(a, 'status', '') == 'creator'), None)
        except Exception:
            admins = []
            admin_ids = set()
            owner_id = None
        if owner_id and target_id == owner_id:
            return True, "You can't act on the group owner."
        if target_id in admin_ids:
            return True, "You can't act on a group admin."
        return False, ""

    def _parse_target_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Prefer reply target
        if update.message and update.message.reply_to_message:
            return update.message.reply_to_message.from_user
        # Else try first arg as user_id
        if context.args:
            try:
                uid = int(context.args[0])
                class _Tmp: id = uid; first_name = str(uid); username = None
                return _Tmp()
            except Exception:
                return None
        return None

    async def antilinks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only group admins can change this.")
            return
        arg = context.args[0].lower() if context.args else 'status'
        if arg in ['on', 'enable', 'enabled']:
            db.set_group_setting(chat_id, 'anti_links', 1)
            await update.message.reply_text("🔗 Anti-links is now ON.")
        elif arg in ['off', 'disable', 'disabled']:
            db.set_group_setting(chat_id, 'anti_links', 0)
            await update.message.reply_text("🔗 Anti-links is now OFF.")
        else:
            gs = db.get_group_settings(chat_id)
            await update.message.reply_text(f"🔗 Anti-links status: {'ON' if gs['anti_links'] else 'OFF'}\nWarn threshold: {gs['warn_threshold']}\nDefault mute: {gs['mute_minutes_default']} min")

    async def warn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can warn members.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply to a user's message or provide user_id. Usage: /warn <reason> (reply) or /warn <user_id> <reason>")
            return
        immune, reason_txt = await self._has_immunity(context.bot, chat_id, target.id, issuer)
        if immune:
            await update.message.reply_text(f"❌ {reason_txt}")
            return
        reason = ''
        if update.message.reply_to_message and context.args:
            reason = ' '.join(context.args)
        elif context.args and len(context.args) > 1:
            reason = ' '.join(context.args[1:])
        count = db.increment_warning(chat_id, target.id, reason or None)
        gs = db.get_group_settings(chat_id)
        await update.message.reply_text(f"⚠️ Warned {getattr(target, 'first_name', target.id)}. Warnings: {count}/{gs['warn_threshold']}")
        if count >= gs['warn_threshold']:
            # Threshold reached: increment strikes and enforce
            strikes = db.add_strike(chat_id, target.id)
            auto_ban = bool(gs.get('auto_ban_on_repeat', 1))
            reset_on_mute = bool(gs.get('strikes_reset_on_mute', 1))
            if auto_ban and strikes >= 2:
                # Ban on repeated threshold breach
                try:
                    await context.bot.ban_chat_member(chat_id, target.id)
                    await update.message.reply_text("🚫 User has been banned due to repeated violations.")
                except Exception as e:
                    logger.warning(f"ban on threshold failed: {e}")
                try:
                    db.clear_warnings(chat_id, target.id)
                except Exception:
                    pass
            else:
                # First threshold breach (or repeat when autoban disabled) => mute
                minutes = gs['mute_minutes_default']
                until = datetime.utcnow() + timedelta(minutes=minutes)
                perms = ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_polls=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                )
                try:
                    await context.bot.restrict_chat_member(chat_id, target.id, permissions=perms, until_date=until)
                    if auto_ban:
                        await update.message.reply_text(f"🔇 Auto-muted for {minutes} minutes due to warnings. Next time will result in a ban.")
                    else:
                        await update.message.reply_text(f"🔇 Auto-muted for {minutes} minutes due to warnings.")
                    if reset_on_mute:
                        db.clear_warnings(chat_id, target.id)
                except Exception as e:
                    logger.warning(f"mute on threshold failed: {e}")
        try:
            await self._log(chat_id, f"[WARN] {target.id} by {issuer} count={count} reason={reason or 'n/a'}")
        except Exception:
            pass

    async def unwarn_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can clear warnings.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply to a user's message or provide user_id. Usage: /unwarn (reply) or /unwarn <user_id>")
            return
        db.clear_warnings(chat_id, target.id)
        await update.message.reply_text(f"✅ Cleared warnings for {getattr(target, 'first_name', target.id)}")

    async def warnings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        target = self._parse_target_user(update, context) or update.effective_user
        count = db.get_warnings(chat_id, target.id)
        gs = db.get_group_settings(chat_id)
        await update.message.reply_text(f"Warnings for {getattr(target, 'first_name', target.id)}: {count}/{gs['warn_threshold']}")

    async def mute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can mute.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply or provide user_id. Usage: /mute <minutes> (reply) or /mute <user_id> <minutes>")
            return
        immune, reason_txt = await self._has_immunity(context.bot, chat_id, target.id, issuer)
        if immune:
            await update.message.reply_text(f"❌ {reason_txt}")
            return
        minutes = 10
        if update.message.reply_to_message and context.args:
            try:
                minutes = int(context.args[0])
            except Exception:
                pass
        elif context.args and len(context.args) > 1:
            try:
                minutes = int(context.args[1])
            except Exception:
                pass
        until = datetime.utcnow() + timedelta(minutes=minutes)
        perms = ChatPermissions(
            can_send_messages=False,
            can_send_media_messages=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
        )
        try:
            await context.bot.restrict_chat_member(chat_id, target.id, permissions=perms, until_date=until)
            await update.message.reply_text(f"🔇 Muted {getattr(target, 'first_name', target.id)} for {minutes} minutes.")
        except Exception as e:
            logger.error(f"mute failed: {e}")
            await update.message.reply_text("Failed to mute user. I need admin rights.")
        try:
            await self._log(chat_id, f"[MUTE] {target.id} by {issuer} for {minutes}m")
        except Exception:
            pass

    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can unmute members.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply to a user or provide a username/ID.")
            return
        try:
            perms = ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            )
            await context.bot.restrict_chat_member(chat_id, target.id, permissions=perms)
            await update.message.reply_text(f"🔈 Unmuted {getattr(target, 'first_name', target.id)}")
        except Exception as e:
            logger.warning(f"unmute failed: {e}")
            await update.message.reply_text("Failed to unmute.")
        try:
            await self._log(chat_id, f"[UNMUTE] {target.id} by {issuer}")
        except Exception:
            pass

    async def ban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can ban members.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply to a user or provide a username/ID.")
            return
        immune, reason_txt = await self._has_immunity(context.bot, chat_id, target.id, issuer)
        if immune:
            await update.message.reply_text(f"❌ {reason_txt}")
            return
        try:
            await context.bot.ban_chat_member(chat_id, target.id)
            await update.message.reply_text(f"⛔ Banned {getattr(target, 'first_name', target.id)}")
        except Exception as e:
            logger.warning(f"ban failed: {e}")
            await update.message.reply_text("Failed to ban.")
        try:
            await self._log(chat_id, f"[BAN] {target.id} by {issuer}")
        except Exception:
            pass

    async def tban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can ban members.")
            return
        # Usage: /tban <minutes> [reply/target]
        if not context.args:
            await update.message.reply_text("Usage: /tban <minutes>")
            return
        try:
            minutes = max(1, min(10080, int(context.args[0])))
        except Exception:
            await update.message.reply_text("Usage: /tban <minutes>")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply to a user or provide a username/ID.")
            return
        immune, reason_txt = await self._has_immunity(context.bot, chat_id, target.id, issuer)
        if immune:
            await update.message.reply_text(f"❌ {reason_txt}")
            return
        until = datetime.utcnow() + timedelta(minutes=minutes)
        try:
            await context.bot.ban_chat_member(chat_id, target.id, until_date=until)
            await update.message.reply_text(f"⛔ Temporarily banned {getattr(target, 'first_name', target.id)} for {minutes} minutes")
        except Exception as e:
            logger.warning(f"tban failed: {e}")
            await update.message.reply_text("Failed to temp-ban.")
        try:
            await self._log(chat_id, f"[TBAN] {target.id} by {issuer} for {minutes}m")
        except Exception:
            pass

    async def kick_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can kick members.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply or provide user_id. Usage: /kick (reply) or /kick <user_id>")
            return
        immune, reason_txt = await self._has_immunity(context.bot, chat_id, target.id, issuer)
        if immune:
            await update.message.reply_text(f"❌ {reason_txt}")
            return
        try:
            # Kick = ban then unban
            await context.bot.ban_chat_member(chat_id, target.id)
            await context.bot.unban_chat_member(chat_id, target.id, only_if_banned=True)
            await update.message.reply_text(f"👢 Kicked {getattr(target, 'first_name', target.id)}")
            try:
                await self._log(chat_id, f"[KICK] {target.id} by {issuer}")
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"kick failed: {e}")
            await update.message.reply_text("Failed to kick.")

    async def purge_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # If replying: delete range from replied message to command message. Else: delete N previous messages: /purge <count>
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can purge messages.")
            return
        try:
            if update.message.reply_to_message:
                start_id = update.message.reply_to_message.message_id
                end_id = update.message.message_id
                deleted = 0
                for mid in range(start_id, end_id + 1):
                    try:
                        await context.bot.delete_message(chat_id, mid)
                        deleted += 1
                    except Exception:
                        pass
                await update.effective_chat.send_message(f"🧹 Purged {deleted} messages.")
            else:
                count = 0
                if context.args:
                    try:
                        count = max(1, min(200, int(context.args[0])))
                    except Exception:
                        pass
                if count <= 0:
                    await update.message.reply_text("Reply to a message or use /purge <count>.")
                    return
                deleted = 0
                current_id = update.message.message_id
                for i in range(count + 1):  # include command message
                    mid = current_id - i
                    try:
                        await context.bot.delete_message(chat_id, mid)
                        deleted += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"purge failed: {e}")
            try:
                await update.message.reply_text("Failed to purge.")
            except Exception:
                pass

    async def del_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            return
        try:
            if update.message.reply_to_message:
                try:
                    await context.bot.delete_message(chat_id, update.message.reply_to_message.message_id)
                except Exception:
                    pass
            try:
                await context.bot.delete_message(chat_id, update.message.message_id)
            except Exception:
                pass
            try:
                await self._log(chat_id, f"[DEL] By {issuer} in {chat_id}")
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"del failed: {e}")

    async def tagactives_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can tag actives.")
            return
        minutes = 60
        if context.args:
            try:
                minutes = max(5, min(1440, int(context.args[0])))
            except Exception:
                pass
        user_ids = db.get_active_users(chat_id, within_minutes=minutes)
        if not user_ids:
            await update.message.reply_text("No active users found in the selected window.")
            return
        mentions = []
        for uid in user_ids[:20]:
            try:
                cm = await context.bot.get_chat_member(chat_id, uid)
                name = cm.user.first_name or (cm.user.username or str(uid))
                mentions.append(f"<a href=\"tg://user?id={uid}\">{name}</a>")
            except Exception:
                mentions.append(f"<a href=\"tg://user?id={uid}\">user</a>")
        text = "Active users (last {}m):\n".format(minutes) + ' '.join(mentions)
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    async def group_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Thin wrapper kept for backward references; delegates to unified handler.
        return await self._group_message_handler_unified(update, context)

    async def setautoban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can change this.")
            return
        gs = db.get_group_settings(chat_id)
        if not context.args:
            await update.message.reply_text(f"Auto-ban on repeat: {'ON' if gs.get('auto_ban_on_repeat',1) else 'OFF'}\nUsage: /setautoban on|off")
            return
        val = context.args[0].lower()
        if val not in ['on','off']:
            await update.message.reply_text("Usage: /setautoban on|off")
            return
        db.set_group_setting(chat_id, 'auto_ban_on_repeat', 1 if val=='on' else 0)
        await update.message.reply_text(f"✅ Auto-ban on repeat set to {'ON' if val=='on' else 'OFF'}.")

    async def setresetwarns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can change this.")
            return
        gs = db.get_group_settings(chat_id)
        if not context.args:
            await update.message.reply_text(f"Reset warnings after mute: {'ON' if gs.get('strikes_reset_on_mute',1) else 'OFF'}\nUsage: /setresetwarns on|off")
            return
        val = context.args[0].lower()
        if val not in ['on','off']:
            await update.message.reply_text("Usage: /setresetwarns on|off")
            return
        db.set_group_setting(chat_id, 'strikes_reset_on_mute', 1 if val=='on' else 0)
        await update.message.reply_text(f"✅ Reset warnings after mute set to {'ON' if val=='on' else 'OFF'}.")

    # ===== Group Config Inline UI =====
    def _render_group_config_kb(self, gs: Dict) -> InlineKeyboardMarkup:
        anti = 'ON' if gs.get('anti_links', 0) else 'OFF'
        autoban = 'ON' if gs.get('auto_ban_on_repeat', 1) else 'OFF'
        resetw = 'ON' if gs.get('strikes_reset_on_mute', 1) else 'OFF'
        warn = int(gs.get('warn_threshold', 3))
        mute = int(gs.get('mute_minutes_default', 10))
        rows = [
            [InlineKeyboardButton(f"Anti-links: {anti}", callback_data='gc:toggle:anti_links')],
            [
                InlineKeyboardButton('−', callback_data='gc:dec:warn_threshold'),
                InlineKeyboardButton(f"Warns: {warn}", callback_data='gc:nop'),
                InlineKeyboardButton('+', callback_data='gc:inc:warn_threshold'),
            ],
            [
                InlineKeyboardButton('−', callback_data='gc:dec:mute_minutes_default'),
                InlineKeyboardButton(f"Mute(min): {mute}", callback_data='gc:nop'),
                InlineKeyboardButton('+', callback_data='gc:inc:mute_minutes_default'),
            ],
            [InlineKeyboardButton(f"Auto-ban: {autoban}", callback_data='gc:toggle:auto_ban_on_repeat')],
            [InlineKeyboardButton(f"Reset warns after mute: {resetw}", callback_data='gc:toggle:strikes_reset_on_mute')],
            [InlineKeyboardButton('🔄 Refresh', callback_data='gc:refresh'), InlineKeyboardButton('✖ Close', callback_data='gc:close')],
        ]
        return InlineKeyboardMarkup(rows)

    async def antilinks_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle anti-links protection in the group"""
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('This command only works in groups.')
            return
            
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        if not await self._is_group_admin(context.bot, chat_id, user_id):
            await update.message.reply_text('Only admins can change this setting.')
            return
            
        gs = db.get_group_settings(chat_id)
        current = gs.get('anti_links', 0)
        new_value = 0 if current else 1
        db.set_group_setting(chat_id, 'anti_links', new_value)
        
        status = 'enabled' if new_value else 'disabled'
        await update.message.reply_text(f"✅ Anti-links protection has been {status} for this group.")
        
    async def group_config_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show group configuration menu"""
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('This command only works in groups.')
            return
            
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        
        if not await self._is_group_admin(context.bot, chat_id, user_id):
            await update.message.reply_text('Only admins can configure the group.')
            return
            
        gs = db.get_group_settings(chat_id)
        text = (
            "🔧 *Group Configuration*\n\n"
            "*Quick Commands:*\n"
            "• /antilinks - Toggle link protection\n"
            "• /setwarns [1-10] - Set warning threshold\n"
            "• /setmute [1-10080] - Set mute duration (minutes)\n"
            "• /setautoban [on/off] - Toggle auto-ban on max warns\n"
            "• /setresetwarns [on/off] - Toggle reset warnings after mute\n\n"
            "*Current Settings:*\n"
            f"• Anti-links: `{'✅ ON' if gs.get('anti_links',0) else '❌ OFF'}`\n"
            f"• Warn threshold: `{gs.get('warn_threshold',3)}`\n"
            f"• Mute duration: `{gs.get('mute_minutes_default',10)}` mins\n"
            f"• Auto-ban on max warns: `{'✅ ON' if gs.get('auto_ban_on_repeat',1) else '❌ OFF'}`\n"
            f"• Reset warns after mute: `{'✅ ON' if gs.get('strikes_reset_on_mute',1) else '❌ OFF'}`"
        )
        await update.message.reply_text(
            text, 
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self._render_group_config_kb(gs)
        )

    async def group_config_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data  # gc:action:key
        chat = query.message.chat
        user = update.effective_user
        if chat.type not in ['group', 'supergroup']:
            return
        if not await self._is_group_admin(context.bot, chat.id, user.id):
            await query.reply_text('Admins only.')
            return
        try:
            parts = data.split(':')
            if len(parts) < 2:
                return
            action = parts[1]
            key = parts[2] if len(parts) > 2 else None
            gs = db.get_group_settings(chat.id)
            if action == 'nop':
                return
            elif action == 'close':
                try:
                    await query.message.delete()
                except Exception:
                    pass
                return
            elif action == 'refresh':
                await query.message.edit_text(
                    "Group Configuration\n\n"
                    f"Anti-links: {'ON' if gs.get('anti_links',0) else 'OFF'}\n"
                    f"Warn threshold: {gs.get('warn_threshold',3)}\n"
                    f"Mute minutes: {gs.get('mute_minutes_default',10)}\n"
                    f"Auto-ban on repeat: {'ON' if gs.get('auto_ban_on_repeat',1) else 'OFF'}\n"
                    f"Reset warns after mute: {'ON' if gs.get('strikes_reset_on_mute',1) else 'OFF'}\n",
                    reply_markup=self._render_group_config_kb(gs)
                )
                return
            elif action == 'toggle' and key:
                # boolean toggles
                current = int(gs.get(key, 0))
                db.set_group_setting(chat.id, key, 0 if current else 1)
            elif action in ('inc','dec') and key:
                val = int(gs.get(key, 0))
                if key == 'warn_threshold':
                    val = max(1, min(10, val + (1 if action=='inc' else -1)))
                elif key == 'mute_minutes_default':
                    val = max(1, min(10080, val + (1 if action=='inc' else -1)))
                else:
                    # unknown numeric key: ignore
                    pass
                db.set_group_setting(chat.id, key, val)
            # re-fetch and update UI
            gs = db.get_group_settings(chat.id)
            await query.message.edit_text(
                "Group Configuration\n\n"
                f"Anti-links: {'ON' if gs.get('anti_links',0) else 'OFF'}\n"
                f"Warn threshold: {gs.get('warn_threshold',3)}\n"
                f"Mute minutes: {gs.get('mute_minutes_default',10)}\n"
                f"Auto-ban on repeat: {'ON' if gs.get('auto_ban_on_repeat',1) else 'OFF'}\n"
                f"Reset warns after mute: {'ON' if gs.get('strikes_reset_on_mute',1) else 'OFF'}\n",
                reply_markup=self._render_group_config_kb(gs)
            )
        except Exception as e:
            logger.warning(f"group_config_callback error: {e}")

    async def setwarns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Set per-group warning threshold
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can change this.")
            return
        if not context.args:
            gs = db.get_group_settings(chat_id)
            await update.message.reply_text(f"Current warn threshold: {gs['warn_threshold']}\nUsage: /setwarns <number between 1 and 10>")
            return
        try:
            value = int(context.args[0])
            if value < 1 or value > 10:
                raise ValueError
        except Exception:
            await update.message.reply_text("Invalid number. Use a value between 1 and 10.")
            return
        db.set_group_setting(chat_id, 'warn_threshold', value)
        await update.message.reply_text(f"✅ Warn threshold set to {value}.")

    async def setmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Set default mute duration (minutes) for auto-mutes
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can change this.")
            return
        if not context.args:
            gs = db.get_group_settings(chat_id)
            await update.message.reply_text(f"Current default mute: {gs['mute_minutes_default']} minutes\nUsage: /setmute <minutes between 1 and 10080>")
            return
        try:
            minutes = int(context.args[0])
            if minutes < 1 or minutes > 10080:
                raise ValueError
        except Exception:
            await update.message.reply_text("Invalid minutes. Use a value between 1 and 10080 (7 days).")
            return
        db.set_group_setting(chat_id, 'mute_minutes_default', minutes)
        await update.message.reply_text(f"✅ Default mute duration set to {minutes} minutes.")

    # ========== Events ==========
    async def show_my_events(self, query, user_id: int):
        try:
            events = db.get_user_events(user_id)
            msg = "🎪 Your Hosted Events\n\n"
            kb: List[List[InlineKeyboardButton]] = []
            if events:
                for ev in events[:10]:
                    msg += f"📅 {ev['title']}\n"
                    msg += f"🔗 Code: {ev['event_code']}\n"
                    if ev.get('description'):
                        d = ev['description']
                        msg += f"📝 {d[:50]}{'...' if len(d) > 50 else ''}\n"
                    if ev.get('group_link'):
                        gl = ev['group_link']
                        msg += f"🎪 Group: {gl[:30]}{'...' if len(gl) > 30 else ''}\n"
                        msg += "📱 Referral links redirect to your group!\n"
                    else:
                        msg += "⚠️ No group link set\n"
                    msg += f"📱 Join link: https://t.me/{self.bot_username}?start={ev['event_code']}\n\n"
                    kb.append([InlineKeyboardButton(f"📊 {ev['title']} Stats", callback_data=f"event_{ev['id']}")])
                    if not ev.get('group_link'):
                        kb.append([InlineKeyboardButton("🎪 Set Group Link", callback_data=f"set_group_{ev['id']}")])
                kb.append([InlineKeyboardButton("🔙 Back", callback_data="ref_center")])
            else:
                msg += "No events created yet. Create your first event to start hosting referral programs!"
                kb = [[InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                      [InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_my_events error: {e}")
            await query.edit_message_text("Error loading events. Please try again.")

    async def start_create_event(self, query, context):
        try:
            context.user_data['creating_event'] = True
            context.user_data['event_step'] = 'title'
            await query.edit_message_text(
                "🎪 Create New Event\n\n"
                "Let's create your referral event!\n\n"
                "Please enter the event title:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ref_center")]])
            )
        except Exception as e:
            logger.error(f"start_create_event error: {e}")
            await query.edit_message_text("Error starting event creation. Please try again.")

    async def create_event_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            title = update.message.text.strip()
            if len(title) > 100:
                await update.message.reply_text("❌ Event title too long (max 100).")
                return
            context.user_data['event_title'] = title
            context.user_data['event_step'] = 'description'
            await update.message.reply_text(
                f"✅ Event title: {title}\n\nNow enter a description (or send 'skip' to skip):"
            )
        except Exception as e:
            logger.error(f"create_event_title error: {e}")
            await update.message.reply_text("Error processing title. Please try again.")

    async def create_event_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            description = None
            if update.message.text.strip().lower() != 'skip':
                description = update.message.text.strip()
                if len(description) > 500:
                    await update.message.reply_text("❌ Description too long (max 500).")
                    return
            context.user_data['event_description'] = description
            context.user_data['event_step'] = 'group_link'
            await update.message.reply_text(
                "Optionally set a Telegram group/channel link for this event. If set, referrals will be directed there automatically!\n\n"
                "Enter a group link, or send 'skip' to continue without one.\n\n"
                "Supported formats:\n• https://t.me/your_group\n• https://t.me/joinchat/invite_link\n• @your_group_username"
            )
        except Exception as e:
            logger.error(f"create_event_description error: {e}")
            await update.message.reply_text("Error processing description. Please try again.")

    async def create_event_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            group_link = None
            if update.message.text.strip().lower() != 'skip':
                group_link = update.message.text.strip()
                if not self.is_valid_telegram_link(group_link):
                    await update.message.reply_text(
                        "❌ Invalid Telegram link format. Use https://t.me/<group> or @group_username"
                    )
                    return
            title = context.user_data.get('event_title')
            description = context.user_data.get('event_description')
            host_id = update.effective_user.id
            event_code = db.create_event(host_id=host_id, title=title, description=description, group_link=group_link)
            context.user_data.clear()
            # Fetch numeric event_id for callbacks
            event_id = None
            try:
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT id FROM events WHERE event_code = ?', (event_code,))
                    row = cursor.fetchone()
                    if row:
                        event_id = int(row[0])
            except Exception:
                pass
            msg = (
                "✅ Event created!\n\n"
                f"📅 Title: {title}\n"
                f"🔗 Code: {event_code}\n"
            )
            if group_link:
                msg += f"🎪 Group set: {group_link}\n"
            msg += f"\nShare: https://t.me/{self.bot_username}?start={event_code}"
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=(f"event_{event_id}" if event_id else "ref_center"))],
                  [InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"create_event_group_link error: {e}")
            await update.message.reply_text("Error creating event. Please try again.")

    def is_valid_telegram_link(self, link: str) -> bool:
        if link.startswith("@"):
            return True
        return bool(re.match(r"^https://t.me/[A-Za-z0-9_+/]+$", link))

    async def start_set_group_link(self, query, context, event_id: int):
        try:
            context.user_data['setting_group_link'] = True
            context.user_data['target_event_id'] = event_id
            await query.edit_message_text(
                "🎪 Set Group Link\n\n"
                "Enter the Telegram group or channel link where you want referrals to be directed.\n\n"
                "Supported formats:\n• https://t.me/your_group\n• https://t.me/joinchat/invite_link\n• https://t.me/+invite_link\n• @your_group_username\n\n"
                "Please enter the group link:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ Skip", callback_data="skip_group_link")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="my_events")]
                ])
            )
        except Exception as e:
            logger.error(f"start_set_group_link error: {e}")
            await query.edit_message_text("Error starting group link setup. Please try again.")

    async def set_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            group_link = update.message.text.strip()
            event_id = context.user_data.get('target_event_id')
            if not event_id:
                await update.message.reply_text("❌ Error: No event selected. Please try again.")
                return
            if not self.is_valid_telegram_link(group_link):
                await update.message.reply_text("❌ Invalid Telegram link format.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE events SET group_link = ? WHERE id = ?', (group_link, event_id))
                conn.commit()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                title = row[0] if row else str(event_id)
            msg = (
                "✅ Group link set!\n\n"
                f"📅 Event: {title}\n"
                f"🎪 Group: {group_link}\n"
                "📱 New referrals will be directed to your group."
            )
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=f"event_{event_id}")],
                  [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                  [InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
            context.user_data.clear()
        except Exception as e:
            logger.error(f"set_group_link error: {e}")
            await update.message.reply_text("Error setting group link. Please try again.")

    async def skip_group_link(self, query, context):
        try:
            event_id = context.user_data.get('target_event_id')
            if not event_id:
                await query.edit_message_text("❌ Error: No event selected. Please try again.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                title = row[0] if row else str(event_id)
            msg = (
                "⏭️ Skipped setting a group link. You can add it later from event stats.\n\n"
                f"📅 Event: {title}"
            )
            kb = [[InlineKeyboardButton("📊 View Event", callback_data=f"event_{event_id}")],
                  [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                  [InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
            context.user_data.clear()
        except Exception as e:
            logger.error(f"skip_group_link error: {e}")
            await query.edit_message_text("Error skipping group link. Please try again.")

    async def show_event_stats(self, query, event_id: int):
        try:
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code, title, group_link FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
            if not event_row:
                await query.edit_message_text("❌ Event not found.")
                return
            event_code = event_row[0]
            event_title = event_row[1]
            group_link = event_row[2]

            stats = db.get_event_stats(event_id)
            msg = [
                f"📅 Event: {event_title}",
                f"🔗 Code: {event_code}",
                "",
                f"👥 Total participants: {stats.get('total_participants', 0)}",
                f"🔗 Total referrals: {stats.get('total_referrals', 0)}",
                "",
            ]
            if group_link:
                msg.append(f"🎪 Group: {group_link}")
                msg.append("📱 Referrals will be redirected to this group.")
            if stats.get('top_referrers'):
                msg.append("\n🏆 Top Referrers:")
                for u in stats['top_referrers']:
                    name = u['first_name'] or u['username'] or str(u['user_id'])
                    msg.append(f"• {name}: {u['referral_count']}")
            kb = [[InlineKeyboardButton("🔙 Back", callback_data="ref_center")]]
            try:
                # add My Event Link if participant
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ? LIMIT 1', (event_id, query.from_user.id))
                    is_participant = cursor.fetchone() is not None
                if is_participant:
                    kb.insert(0, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except Exception:
                pass
            await query.edit_message_text("\n".join(msg), reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_event_stats error: {e}")
            await query.edit_message_text("Error loading event stats. Please try again.")

    async def show_my_event_links(self, query, user_id: int):
        try:
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT e.id, e.title, e.event_code, e.group_link
                    FROM events e
                    JOIN event_participants ep ON ep.event_id = e.id
                    WHERE ep.user_id = ?
                    ORDER BY e.created_at DESC
                ''', (user_id,))
                rows = cursor.fetchall()
            if not rows:
                await query.edit_message_text("You have not joined any events yet.")
                return
            msg = "🎯 Your Event Links\n\n"
            kb: List[List[InlineKeyboardButton]] = []
            for (ev_id, title, ev_code, gl) in rows[:10]:
                personal = f"https://t.me/{self.bot_username}?start={db.get_user(user_id)['referral_code']}_{ev_code}"
                msg += f"📅 {title}\n"
                msg += f"🔗 Code: {ev_code}\n"
                msg += f"🎯 Your Link: {personal}\n"
                if gl:
                    msg += f"🎪 Group: {gl}\n"
                msg += "\n"
                kb.append([
                    InlineKeyboardButton("🎯 Get Link", callback_data=f"event_link_{ev_id}"),
                    InlineKeyboardButton("📊 Event Stats", callback_data=f"event_{ev_id}")
                ])
            kb.append([InlineKeyboardButton("🔙 Back", callback_data="ref_center")])
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"show_my_event_links error: {e}")
            await query.edit_message_text("Error loading your event links. Please try again.")

    async def send_event_link(self, query, event_id: int):
        try:
            user_id = query.from_user.id
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
            if not row:
                await query.message.reply_text("❌ Event not found.")
                return
            event_code = row[0]
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.message.reply_text("Please use /start to register first.")
                return
            personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
            await query.message.reply_text(
                f"🎯 Your Event Link:\n{personal_link}\n\nShare this link to invite people directly to this event!"
            )
        except Exception as e:
            logger.error(f"send_event_link error: {e}")
            await query.message.reply_text("Error generating your event link. Please try again.")

    async def join_event_confirm(self, query, event_id: int):
        try:
            user_id = query.from_user.id
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO event_participants (event_id, user_id)
                    VALUES (?, ?)
                ''', (event_id, user_id))
                conn.commit()
                cursor.execute('SELECT title, event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
            if not row:
                await query.edit_message_text("❌ Event not found.")
                return
            event_title = row[0]
            event_code = row[1]
            success_msg = f"🎉 Successfully joined: {event_title}\n\n"
            success_msg += "You can now participate in this event's referral program!\n"
            success_msg += "Use your personal referral link to invite others to this event."
            kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="noop")]]
            try:
                user = db.get_user(user_id)
                if user and user.get('referral_code'):
                    personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                    success_msg += f"\n\n🎯 Your Event Link:\n{personal_link}"
                    kb.insert(0, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except Exception:
                pass
            await query.edit_message_text(success_msg, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"join_event_confirm error: {e}")
            await query.edit_message_text("Error joining event. Please try again.")

    async def end_event_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            user = update.effective_user
            args = context.args
            if not args:
                await update.message.reply_text("Usage: /end_event <event_code>\nYou can find the event code in your event details.")
                return
            event_code = args[0].strip()
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, host_id, title FROM events WHERE event_code = ?', (event_code,))
                row = cursor.fetchone()
            if not row:
                await update.message.reply_text("❌ Event not found.")
                return
            event_id, host_id, title = row
            if host_id != user.id:
                await update.message.reply_text("Only the host can end this event.")
                return
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('UPDATE events SET is_active = 0 WHERE id = ?', (event_id,))
                conn.commit()
            stats = db.get_event_stats(event_id)
            msg = [
                "✅ Event Ended",
                f"📅 Event: {title}",
                "",
                f"👥 Total participants: {stats.get('total_participants', 0)}",
                f"🔗 Total referrals: {stats.get('total_referrals', 0)}",
            ]
            await update.message.reply_text("\n".join(msg))
        except Exception as e:
            logger.error(f"end_event_command error: {e}")
            await update.message.reply_text("Error ending event. Please try again later.")

    # ===== Missing Moderation Actions & Config =====
    async def unmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text("Use this in a group.")
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text("Only admins can unmute.")
            return
        target = self._parse_target_user(update, context)
        if not target:
            await update.message.reply_text("Reply or provide user_id. Usage: /unmute (reply) or /unmute <user_id>")
            return
        perms = ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        )
        try:
            await context.bot.restrict_chat_member(chat_id, target.id, permissions=perms)
            await update.message.reply_text(f"🔈 Unmuted {getattr(target, 'first_name', target.id)}")
        except Exception as e:
            logger.warning(f"unmute failed: {e}")
            await update.message.reply_text("Failed to unmute. I need admin rights.")

    async def setwarns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text('Admins only.')
            return
        if not context.args:
            gs = db.get_group_settings(chat_id)
            await update.message.reply_text(f"Current warn threshold: {gs.get('warn_threshold',3)}")
            return
        try:
            val = max(1, min(10, int(context.args[0])))
            db.set_group_setting(chat_id, 'warn_threshold', val)
            await update.message.reply_text(f"Warn threshold set to {val}.")
        except Exception:
            await update.message.reply_text('Usage: /setwarns <1-10>')

    async def setmute_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text('Admins only.')
            return
        if not context.args:
            gs = db.get_group_settings(chat_id)
            await update.message.reply_text(f"Current mute minutes: {gs.get('mute_minutes_default',10)}")
            return
        try:
            val = max(1, min(1440, int(context.args[0])))
            db.set_group_setting(chat_id, 'mute_minutes_default', val)
            await update.message.reply_text(f"Default mute set to {val} minutes.")
        except Exception:
            await update.message.reply_text('Usage: /setmute <minutes>')

    async def setautoban_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text('Admins only.')
            return
        val: int
        if context.args and context.args[0].lower() in ('on','off'):
            val = 1 if context.args[0].lower() == 'on' else 0
        else:
            gs = db.get_group_settings(chat_id)
            val = 0 if gs.get('auto_ban_on_repeat',1) else 1
        db.set_group_setting(chat_id, 'auto_ban_on_repeat', val)
        await update.message.reply_text(f"Auto-ban on repeat: {'ON' if val else 'OFF'}")

    async def setresetwarns_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        chat_id = update.effective_chat.id
        issuer = update.effective_user.id
        if not await self._is_group_admin(context.bot, chat_id, issuer):
            await update.message.reply_text('Admins only.')
            return
        val: int
        if context.args and context.args[0].lower() in ('on','off'):
            val = 1 if context.args[0].lower() == 'on' else 0
        else:
            gs = db.get_group_settings(chat_id)
            val = 0 if gs.get('strikes_reset_on_mute',1) else 1
        db.set_group_setting(chat_id, 'strikes_reset_on_mute', val)
        await update.message.reply_text(f"Reset warnings after mute: {'ON' if val else 'OFF'}")

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        gs = db.get_group_settings(chat.id)
        lines = [
            '⚙️ Group Settings',
            f"Anti-links: {'ON' if gs.get('anti_links',0) else 'OFF'}",
            f"Warn threshold: {gs.get('warn_threshold',3)}",
            f"Default mute: {gs.get('mute_minutes_default',10)} min",
            f"Auto-ban repeat: {'ON' if gs.get('auto_ban_on_repeat',1) else 'OFF'}",
            f"Reset warns on mute: {'ON' if gs.get('strikes_reset_on_mute',1) else 'OFF'}",
            '— Locks —',
            f"Photos: {'ON' if gs.get('lock_photos',0) else 'OFF'}",
            f"Videos: {'ON' if gs.get('lock_videos',0) else 'OFF'}",
            f"GIFs: {'ON' if gs.get('lock_gifs',0) else 'OFF'}",
            f"Stickers: {'ON' if gs.get('lock_stickers',0) else 'OFF'}",
            f"Documents: {'ON' if gs.get('lock_documents',0) else 'OFF'}",
            f"Voice: {'ON' if gs.get('lock_voice',0) else 'OFF'}",
            f"Audio: {'ON' if gs.get('lock_audio',0) else 'OFF'}",
            f"Forwards: {'ON' if gs.get('lock_forwards',0) else 'OFF'}",
            f"Log chat: {gs.get('log_chat_id') if gs.get('log_chat_id') is not None else 'None'}",
        ]
        await update.message.reply_text("\n".join(lines))

    async def setlogchat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        issuer = update.effective_user.id if update.effective_user else 0
        if not await self._is_group_admin(context.bot, chat.id, issuer):
            await update.message.reply_text('Admins only.')
            return
        # /setlogchat [chat_id|this]
        val = None
        if context.args:
            arg = context.args[0].strip().lower()
            if arg == 'this':
                val = chat.id
            else:
                try:
                    val = int(arg)
                except Exception:
                    await update.message.reply_text('Usage: /setlogchat <chat_id|this>')
                    return
        else:
            val = chat.id
        db.set_group_setting(chat.id, 'log_chat_id', val)
        await update.message.reply_text(f'Log chat set to: {val}')

    async def clearlogchat_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group.')
            return
        issuer = update.effective_user.id if update.effective_user else 0
        if not await self._is_group_admin(context.bot, chat.id, issuer):
            await update.message.reply_text('Admins only.')
            return
        db.set_group_setting(chat.id, 'log_chat_id', None)
        await update.message.reply_text('Log chat cleared.')

    def _normalize_lock_key(self, t: str) -> str | None:
        t = (t or '').strip().lower()
        mapping = {
            'photo': 'lock_photos', 'photos': 'lock_photos',
            'video': 'lock_videos', 'videos': 'lock_videos',
            'gif': 'lock_gifs', 'gifs': 'lock_gifs', 'animation': 'lock_gifs',
            'sticker': 'lock_stickers', 'stickers': 'lock_stickers',
            'document': 'lock_documents', 'documents': 'lock_documents', 'doc': 'lock_documents',
            'voice': 'lock_voice', 'ptt': 'lock_voice',
            'audio': 'lock_audio', 'music': 'lock_audio',
            'forward': 'lock_forwards', 'forwards': 'lock_forwards', 'fw': 'lock_forwards',
        }
        return mapping.get(t)

    async def lock_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group. Usage: /lock <type> on|off')
            return
        issuer = update.effective_user.id if update.effective_user else 0
        if not await self._is_group_admin(context.bot, chat.id, issuer):
            await update.message.reply_text('Admins only.')
            return
        if len(context.args) < 2:
            await update.message.reply_text('Usage: /lock <photos|videos|gifs|stickers|documents|voice|audio|forwards> <on|off>')
            return
        key = self._normalize_lock_key(context.args[0])
        if not key:
            await update.message.reply_text('Unknown type. Allowed: photos, videos, gifs, stickers, documents, voice, audio, forwards')
            return
        val = 1 if context.args[1].lower() == 'on' else 0
        db.set_group_setting(chat.id, key, val)
        await update.message.reply_text(f"{key.replace('lock_','').capitalize()} lock: {'ON' if val else 'OFF'}")

    async def lockall_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.type not in ['group', 'supergroup']:
            await update.message.reply_text('Use this in a group. Usage: /lockall on|off')
            return
        issuer = update.effective_user.id if update.effective_user else 0
        if not await self._is_group_admin(context.bot, chat.id, issuer):
            await update.message.reply_text('Admins only.')
            return
        if not context.args or context.args[0].lower() not in ('on','off'):
            await update.message.reply_text('Usage: /lockall on|off')
            return
        val = 1 if context.args[0].lower() == 'on' else 0
        for key in ('lock_photos','lock_videos','lock_gifs','lock_stickers','lock_documents','lock_voice','lock_audio','lock_forwards'):
            try:
                db.set_group_setting(chat.id, key, val)
            except Exception:
                pass
        await update.message.reply_text(f"All media locks: {'ON' if val else 'OFF'}")

    def _render_group_config_kb(self, gs: dict) -> InlineKeyboardMarkup:
        kb: List[List[InlineKeyboardButton]] = []
        kb.append([
            InlineKeyboardButton(f"Anti-links: {'ON' if gs.get('anti_links',0) else 'OFF'}", callback_data='gc:toggle:anti_links')
        ])
        kb.append([
            InlineKeyboardButton("Warn -", callback_data='gc:dec:warn_threshold'),
            InlineKeyboardButton(f"Warns: {gs.get('warn_threshold',3)}", callback_data='gc:noop'),
            InlineKeyboardButton("Warn +", callback_data='gc:inc:warn_threshold'),
        ])
        kb.append([
            InlineKeyboardButton("Mute -", callback_data='gc:dec:mute_minutes_default'),
            InlineKeyboardButton(f"Mute: {gs.get('mute_minutes_default',10)}m", callback_data='gc:noop'),
            InlineKeyboardButton("Mute +", callback_data='gc:inc:mute_minutes_default'),
        ])
        kb.append([
            InlineKeyboardButton(f"Auto-ban: {'ON' if gs.get('auto_ban_on_repeat',1) else 'OFF'}", callback_data='gc:toggle:auto_ban_on_repeat')
        ])
        kb.append([
            InlineKeyboardButton(f"Reset warns: {'ON' if gs.get('strikes_reset_on_mute',1) else 'OFF'}", callback_data='gc:toggle:strikes_reset_on_mute')
        ])
        # Media locks rows
        kb.append([
            InlineKeyboardButton(f"Photos: {'ON' if gs.get('lock_photos',0) else 'OFF'}", callback_data='gc:toggle:lock_photos'),
            InlineKeyboardButton(f"Videos: {'ON' if gs.get('lock_videos',0) else 'OFF'}", callback_data='gc:toggle:lock_videos'),
            InlineKeyboardButton(f"GIFs: {'ON' if gs.get('lock_gifs',0) else 'OFF'}", callback_data='gc:toggle:lock_gifs'),
        ])
        kb.append([
            InlineKeyboardButton(f"Stickers: {'ON' if gs.get('lock_stickers',0) else 'OFF'}", callback_data='gc:toggle:lock_stickers'),
            InlineKeyboardButton(f"Docs: {'ON' if gs.get('lock_documents',0) else 'OFF'}", callback_data='gc:toggle:lock_documents'),
            InlineKeyboardButton(f"Voice: {'ON' if gs.get('lock_voice',0) else 'OFF'}", callback_data='gc:toggle:lock_voice'),
        ])
        kb.append([
            InlineKeyboardButton(f"Audio: {'ON' if gs.get('lock_audio',0) else 'OFF'}", callback_data='gc:toggle:lock_audio'),
            InlineKeyboardButton(f"Forwards: {'ON' if gs.get('lock_forwards',0) else 'OFF'}", callback_data='gc:toggle:lock_forwards'),
        ])
        kb.append([
            InlineKeyboardButton("Lock all ON", callback_data='gc:lockall:on'),
            InlineKeyboardButton("Lock all OFF", callback_data='gc:lockall:off'),
        ])
        return InlineKeyboardMarkup(kb)


    async def group_message_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Thin wrapper kept for backward references; delegates to unified handler.
        return await self._group_message_handler_unified(update, context)


if __name__ == "__main__":
    bot = MultipurposeBot()
    bot.run()
