import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
from telegram.constants import ParseMode
from dotenv import load_dotenv

from database import Database

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()


class MultipurposeBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        if not self.bot_token:
            raise ValueError("BOT_TOKEN environment variable is not set")

        self.bot_username = os.getenv('BOT_USERNAME', '').lstrip('@')
        self.admin_ids = set()

        self.app = Application.builder().token(self.bot_token).build()

        self.add_handlers()

        self.db_path = os.getenv('DB_PATH', 'bot_data.db')
        self.db = Database(self.db_path)
        logger.info(f"Bot initialized with token: {self.bot_token[:10]}...")

        self.max_warnings = 3
        self.warning_duration = 7 * 24 * 3600
        self.warnings = {}
        self.group_admins: Dict[int, set[int]] = {}
        self.pending_settings: Dict[tuple, str] = {}
    
    # Core command handlers
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a welcome message when the command /start is issued."""
        user = update.effective_user
        chat = update.effective_chat
        
        welcome_message = (
            f"👋 Welcome {user.mention_html()}!\n\n"
            "I'm your group management bot. Here's what I can do:\n"
            "• Track member activity and statistics\n"
            "• Manage warnings and bans\n"
            "• Handle referrals and rewards\n\n"
            "Use /help to see all available commands."
        )
        
        if chat.type == 'private':
            await update.message.reply_html(welcome_message)
        else:
            await update.message.reply_html(
                f"👋 Hi {user.mention_html()}! I'm here to help manage this group."
            )
            
        # Track user join
        await self._update_user_activity(chat.id, user.id)
        
        # Handle referral if any
        if context.args and context.args[0].startswith('ref'):
            try:
                referrer_id = int(context.args[0][3:])
                await self._handle_referral(user.id, referrer_id, chat.id)
            except (ValueError, IndexError):
                pass

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = "🤖 <b>Group Moderation Panel</b>\n\nUse the buttons below to manage the bot."
        keyboard = [
            [
                InlineKeyboardButton("📣 Referral Event", callback_data="help_ref_menu"),
                InlineKeyboardButton("⚙️ Automation", callback_data="help_auto_menu")
            ],
            [
                InlineKeyboardButton("🛡 Moderation settings", callback_data="help_mod_menu")
            ],
            [InlineKeyboardButton("❌ Close", callback_data="help_close")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_html(text, reply_markup=reply_markup)

    async def help_button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data
        chat = update.effective_chat
        user = update.effective_user
        
        if data == "help_close":
            await query.delete_message()
            return
        
        if data == "help_main":
            await self.help_command(update, context)
            return
        
        if data == "help_mod_menu":
            warn_limit = self._get_warn_limit(chat.id)
            link_policy = self.db.get_setting(chat.id, "link_policy") or "none"
            post_policy = self.db.get_setting(chat.id, "post_policy") or "everyone"
            text = (
                "🛡 <b>Moderation Settings</b>\n\n"
                f"Warn limit before ban: {warn_limit}\n"
                f"Link policy: {link_policy}\n"
                f"Who can post: {post_policy}\n\n"
                "Use the buttons to change values."
            )
            keyboard = [
                [
                    InlineKeyboardButton("➖ Warn limit", callback_data="help_mod_warn_dec"),
                    InlineKeyboardButton("➕ Warn limit", callback_data="help_mod_warn_inc"),
                ],
                [InlineKeyboardButton("Links", callback_data="help_mod_links")],
                [InlineKeyboardButton("Who can post", callback_data="help_mod_posts")],
                [InlineKeyboardButton("🔙 Back", callback_data="help_main")],
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
            return
        
        if data == "help_ref_menu":
            text = "📣 <b>Referral Event</b>\n\nUse the buttons to control the event."
            keyboard = [
                [
                    InlineKeyboardButton("▶️ Start event", callback_data="help_ref_start"),
                    InlineKeyboardButton("⏹ End event", callback_data="help_ref_end")
                ],
                [InlineKeyboardButton("🏆 Show leaderboard", callback_data="help_ref_top")],
                [InlineKeyboardButton("🔙 Back", callback_data="help_main")]
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
            return
        
        if data == "help_auto_menu":
            text = "⚙️ <b>Automation Messages</b>\n\nChoose which text to change."
            keyboard = [
                [
                    InlineKeyboardButton("👋 Welcome message", callback_data="help_auto_welcome"),
                    InlineKeyboardButton("🚪 Goodbye message", callback_data="help_auto_goodbye"),
                ],
                [
                    InlineKeyboardButton("⚠️ Warn message", callback_data="help_auto_warn"),
                    InlineKeyboardButton("⛔ Ban message", callback_data="help_auto_ban"),
                ],
                [InlineKeyboardButton("🧹 Delete message", callback_data="help_auto_delete")],
                [InlineKeyboardButton("🔙 Back", callback_data="help_main")]
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
            return
        
        if data in {"help_ref_start", "help_ref_end", "help_ref_top"}:
            if not await self._is_admin(update, context):
                await query.edit_message_text("❌ Only admins can manage referral events.")
                return
            
            if data == "help_ref_start":
                if self.db.start_referral_event(chat.id):
                    await query.edit_message_text("✅ Referral event started for this group.")
                else:
                    await query.edit_message_text("❌ Failed to start referral event.")
                return
            
            if data == "help_ref_end":
                if self.db.end_referral_event(chat.id):
                    await query.edit_message_text("✅ Referral event ended for this group.")
                else:
                    await query.edit_message_text("ℹ️ No active referral event to end.")
                return
            
            if data == "help_ref_top":
                leaderboard = self.db.get_referral_leaderboard(chat.id, limit=10, use_event_window=True)
                if not leaderboard:
                    await query.edit_message_text("No referrals recorded for this group yet.")
                    return
                
                event = self.db.get_referral_event(chat.id)
                if event and event.get("end_ts") is None:
                    header = "🏆 Referral Event Leaderboard\n\n"
                elif event:
                    header = "🏆 Referral Event Results\n\n"
                else:
                    header = "🏆 Referral Leaderboard (all-time)\n\n"
                
                lines = [header]
                for i, row in enumerate(leaderboard, start=1):
                    name = row.get("first_name") or "User"
                    username = row.get("username")
                    if username:
                        name = f"{name} (@{username})"
                    lines.append(f"{i}. {name} — {row['ref_count']} referrals")
                
                await query.edit_message_text("\n".join(lines))
                return
        
        if data.startswith("help_auto_"):
            key_map = {
                "help_auto_welcome": "welcome_message",
                "help_auto_goodbye": "goodbye_message",
                "help_auto_warn": "warn_message",
                "help_auto_ban": "ban_message",
                "help_auto_delete": "delete_message",
            }
            setting_key = key_map.get(data)
            if not setting_key:
                return
            
            if not await self._is_moderator(update, context):
                await query.edit_message_text("❌ Only admins or moderators can change automation texts.")
                return
            
            self.pending_settings[(chat.id, user.id)] = setting_key
            await query.edit_message_text(
                "✏️ Send the new text now.\n\n"
                "You can use {user} in the message to mention the target user.",
                parse_mode=ParseMode.HTML
            )
            return

        if data in {"help_mod_warn_inc", "help_mod_warn_dec", "help_mod_links", "help_mod_posts"}:
            if not await self._is_admin(update, context):
                await query.edit_message_text("❌ Only admins can change moderation settings.")
                return
            
            warn_limit = self._get_warn_limit(chat.id)
            link_policy = self.db.get_setting(chat.id, "link_policy") or "none"
            post_policy = self.db.get_setting(chat.id, "post_policy") or "everyone"
            
            if data == "help_mod_warn_inc":
                warn_limit = min(warn_limit + 1, 10)
                self.db.set_setting(chat.id, "warn_limit", str(warn_limit))
            elif data == "help_mod_warn_dec":
                warn_limit = max(warn_limit - 1, 1)
                self.db.set_setting(chat.id, "warn_limit", str(warn_limit))
            elif data == "help_mod_links":
                order = ["none", "block_all", "allow_admins"]
                idx = order.index(link_policy) if link_policy in order else 0
                link_policy = order[(idx + 1) % len(order)]
                self.db.set_setting(chat.id, "link_policy", link_policy)
            elif data == "help_mod_posts":
                order = ["everyone", "staff_only"]
                idx = order.index(post_policy) if post_policy in order else 0
                post_policy = order[(idx + 1) % len(order)]
                self.db.set_setting(chat.id, "post_policy", post_policy)
            
            text = (
                "🛡 <b>Moderation Settings</b>\n\n"
                f"Warn limit before ban: {warn_limit}\n"
                f"Link policy: {link_policy}\n"
                f"Who can post: {post_policy}\n\n"
                "Use the buttons to change values."
            )
            keyboard = [
                [
                    InlineKeyboardButton("➖ Warn limit", callback_data="help_mod_warn_dec"),
                    InlineKeyboardButton("➕ Warn limit", callback_data="help_mod_warn_inc"),
                ],
                [InlineKeyboardButton("Links", callback_data="help_mod_links")],
                [InlineKeyboardButton("Who can post", callback_data="help_mod_posts")],
                [InlineKeyboardButton("🔙 Back", callback_data="help_main")],
            ]
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )

    async def moderation_button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        chat = update.effective_chat
        user = update.effective_user
        if not chat or chat.type not in ["group", "supergroup"]:
            return
        parts = data.split(":")
        if len(parts) < 3:
            return
        action = parts[1]
        try:
            target_id = int(parts[2])
        except ValueError:
            return
        if action in ["warn", "unwarn", "warns", "mute", "unmute", "ban", "unban", "kick"]:
            if not await self._is_moderator(update, context):
                await query.answer("Only moderators can use these buttons.", show_alert=True)
                return
        try:
            member = await context.bot.get_chat_member(chat.id, target_id)
            target_user = member.user
        except Exception:
            await query.answer("User not found in this chat.", show_alert=True)
            return
        if action == "warn":
            await self._apply_warning(
                chat_id=chat.id,
                target_user=target_user,
                staff_id=user.id,
                reason="No reason provided",
                origin_message=None,
                context=context,
            )
            await query.answer("User warned.")
        elif action == "unwarn":
            removed = await self._remove_last_warning(chat.id, target_user.id)
            if chat.id in self.warnings and target_user.id in self.warnings[chat.id]:
                if self.warnings[chat.id][target_user.id]:
                    self.warnings[chat.id][target_user.id].pop()
            if removed:
                await context.bot.send_message(
                    chat.id,
                    f"✅ Last warning removed for {target_user.mention_html()}.",
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("Warning removed.")
            else:
                await query.answer("No warnings found for this user.", show_alert=True)
        elif action == "warns":
            warnings = self.db.get_warnings(chat.id, target_user.id)
            if not warnings:
                await context.bot.send_message(
                    chat.id,
                    f"✅ {target_user.mention_html()} has no warnings.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                lines = [
                    f"⚠️ Warnings for {target_user.mention_html()} ({len(warnings)}):"
                ]
                for w in warnings:
                    ts = datetime.fromtimestamp(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"- {ts}: {w['reason']}")
                await context.bot.send_message(
                    chat.id,
                    "\n".join(lines),
                    parse_mode=ParseMode.HTML,
                )
            await query.answer()
        elif action == "mute":
            duration = 3600
            until_date = int(time.time()) + duration
            try:
                await context.bot.restrict_chat_member(
                    chat.id,
                    target_user.id,
                    permissions=ChatPermissions(
                        can_send_messages=False,
                        can_send_media_messages=False,
                        can_send_other_messages=False,
                        can_add_web_page_previews=False,
                    ),
                    until_date=until_date,
                )
                await context.bot.send_message(
                    chat.id,
                    f"🔇 {target_user.mention_html()} has been muted.",
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("User muted.")
            except Exception as e:
                logger.error(f"Failed to mute user via button: {e}")
                await query.answer("Failed to mute user.", show_alert=True)
        elif action == "unmute":
            try:
                await context.bot.restrict_chat_member(
                    chat.id,
                    target_user.id,
                    permissions=ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                    until_date=0,
                )
                await context.bot.send_message(
                    chat.id,
                    f"🔊 {target_user.mention_html()} has been unmuted.",
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("User unmuted.")
            except Exception as e:
                logger.error(f"Failed to unmute user via button: {e}")
                await query.answer("Failed to unmute user.", show_alert=True)
        elif action == "ban":
            try:
                await context.bot.ban_chat_member(chat.id, target_user.id)
                template = self.db.get_setting(chat.id, "ban_message")
                if not template:
                    template = "🚫 {user} has been banned."
                text = template.replace("{user}", target_user.mention_html())
                await context.bot.send_message(
                    chat.id,
                    text,
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("User banned.")
            except Exception as e:
                logger.error(f"Failed to ban user via button: {e}")
                await query.answer("Failed to ban user.", show_alert=True)
        elif action == "unban":
            try:
                await context.bot.unban_chat_member(chat.id, target_user.id)
                await context.bot.send_message(
                    chat.id,
                    f"✅ User {target_user.mention_html()} has been unbanned.",
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("User unbanned.")
            except Exception as e:
                logger.error(f"Failed to unban user via button: {e}")
                await query.answer("Failed to unban user.", show_alert=True)
        elif action == "kick":
            try:
                await context.bot.ban_chat_member(chat.id, target_user.id)
                await context.bot.unban_chat_member(chat.id, target_user.id)
                await context.bot.send_message(
                    chat.id,
                    f"👢 {target_user.mention_html()} has been kicked and can rejoin.",
                    parse_mode=ParseMode.HTML,
                )
                await query.answer("User kicked.")
            except Exception as e:
                logger.error(f"Failed to kick user via button: {e}")
                await query.answer("Failed to kick user.", show_alert=True)

    async def warn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide a user ID.")
            return
            
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        await self._apply_warning(
            chat_id=update.effective_chat.id,
            target_user=target_user,
            staff_id=update.effective_user.id,
            reason=reason,
            origin_message=update.message,
            context=context,
        )

    async def ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user from the group."""
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide a user ID.")
            return
            
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        chat_id = update.effective_chat.id
        
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            template = self.db.get_setting(chat_id, "ban_message")
            if not template:
                template = "🚫 {user} has been banned.\nReason: {reason}"
            text = template.replace("{user}", target_user.mention_html())
            text = text.replace("{reason}", reason)
            await update.message.reply_html(text)
            
            # Clear any warnings for this user
            if chat_id in self.warnings and target_user.id in self.warnings[chat_id]:
                del self.warnings[chat_id][target_user.id]
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            await update.message.reply_text("❌ Failed to ban user.")

    async def reload_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        chat = update.effective_chat
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            self.group_admins[chat.id] = {m.user.id for m in admins}
            await update.message.reply_text("✅ Admin list reloaded.")
        except Exception as e:
            logger.error(f"Error reloading admins: {e}")
            await update.message.reply_text("❌ Failed to reload admin list.")

    async def settings_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        days = int(self.warning_duration / 86400)
        chat_id = update.effective_chat.id
        max_warns = self._get_warn_limit(chat_id)
        text = (
            "⚙️ Group settings\n\n"
            f"Max warnings before ban: {max_warns}\n"
            f"Warning window: {days} days"
        )
        await update.message.reply_text(text)

    async def modpanel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a user to open moderation panel.")
            return
        target_user = update.message.reply_to_message.from_user
        chat = update.effective_chat
        keyboard = [
            [
                InlineKeyboardButton(
                    "⚠ Warn", callback_data=f"mod:warn:{target_user.id}"
                ),
                InlineKeyboardButton(
                    "♻ Unwarn", callback_data=f"mod:unwarn:{target_user.id}"
                ),
                InlineKeyboardButton(
                    "📋 Warns", callback_data=f"mod:warns:{target_user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔇 Mute", callback_data=f"mod:mute:{target_user.id}"
                ),
                InlineKeyboardButton(
                    "🔊 Unmute", callback_data=f"mod:unmute:{target_user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🚫 Ban", callback_data=f"mod:ban:{target_user.id}"
                ),
                InlineKeyboardButton(
                    "👢 Kick", callback_data=f"mod:kick:{target_user.id}"
                ),
                InlineKeyboardButton(
                    "✅ Unban", callback_data=f"mod:unban:{target_user.id}"
                ),
            ],
        ]
        await update.message.reply_html(
            f"🛡 Moderation actions for {target_user.mention_html()} in {chat.title or chat.id}:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def mute_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a user to mute.")
            return
        target_user = update.message.reply_to_message.from_user
        if target_user.is_bot:
            await update.message.reply_text("❌ You cannot mute bots.")
            return
        chat_id = update.effective_chat.id
        duration = 3600
        reason = "No reason provided"
        if context.args:
            token = context.args[0].lower()
            try:
                if token.endswith("m"):
                    duration = int(token[:-1]) * 60
                elif token.endswith("h"):
                    duration = int(token[:-1]) * 3600
                elif token.endswith("d"):
                    duration = int(token[:-1]) * 86400
                else:
                    duration = int(token) * 60
                if len(context.args) > 1:
                    reason = " ".join(context.args[1:])
            except ValueError:
                reason = " ".join(context.args)
        until_date = int(time.time()) + duration
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                target_user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False,
                ),
                until_date=until_date,
            )
            if duration < 60:
                duration_str = f"{duration} seconds"
            elif duration < 3600:
                minutes = duration // 60
                duration_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
            elif duration < 86400:
                hours = duration // 3600
                duration_str = f"{hours} hour{'s' if hours != 1 else ''}"
            else:
                days = duration // 86400
                duration_str = f"{days} day{'s' if days != 1 else ''}"
            await update.message.reply_html(
                f"🔇 {target_user.mention_html()} has been muted for {duration_str}.\n"
                f"<b>Reason:</b> {reason}"
            )
        except Exception as e:
            logger.error(f"Failed to mute user: {e}")
            await update.message.reply_text("❌ Failed to mute user.")

    async def unmute_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a user to unmute.")
            return
        target_user = update.message.reply_to_message.from_user
        chat_id = update.effective_chat.id
        try:
            await context.bot.restrict_chat_member(
                chat_id,
                target_user.id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
                until_date=0,
            )
            await update.message.reply_html(
                f"🔊 {target_user.mention_html()} has been unmuted."
            )
        except Exception as e:
            logger.error(f"Failed to unmute user: {e}")
            await update.message.reply_text("❌ Failed to unmute user.")

    async def kick_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide a user ID.")
            return
        chat_id = update.effective_chat.id
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            await context.bot.unban_chat_member(chat_id, target_user.id)
            await update.message.reply_html(
                f"👢 {target_user.mention_html()} has been kicked and can rejoin."
            )
        except Exception as e:
            logger.error(f"Failed to kick user: {e}")
            await update.message.reply_text("❌ Failed to kick user.")

    async def unban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        chat_id = update.effective_chat.id
        user_id = None
        if update.message.reply_to_message:
            user_id = update.message.reply_to_message.from_user.id
        elif context.args and context.args[0].isdigit():
            user_id = int(context.args[0])
        if not user_id:
            await update.message.reply_text("❌ Provide a user ID or reply to a user.")
            return
        try:
            await context.bot.unban_chat_member(chat_id, user_id)
            await update.message.reply_text(f"✅ User {user_id} has been unbanned.")
        except Exception as e:
            logger.error(f"Failed to unban user: {e}")
            await update.message.reply_text("❌ Failed to unban user.")

    async def _remove_last_warning(self, chat_id: int, user_id: int) -> bool:
        try:
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM warnings
                WHERE chat_id = ? AND user_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (chat_id, user_id),
            )
            row = cursor.fetchone()
            if not row:
                return False
            warning_id = row[0]
            cursor.execute("DELETE FROM warnings WHERE id = ?", (warning_id,))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error removing warning: {e}")
            return False
        finally:
            if "conn" in locals() and conn:
                conn.close()

    async def _apply_warning(
        self,
        chat_id: int,
        target_user: Any,
        staff_id: int,
        reason: str,
        origin_message: Optional[telegram.Message],
        context: ContextTypes.DEFAULT_TYPE,
    ):
        now = int(time.time())
        try:
            self.db.add_warning(chat_id, target_user.id, reason, staff_id)
        except Exception as e:
            logger.error(f"Error saving warning: {e}")
        warnings = self.db.get_warnings(chat_id, target_user.id)
        valid_warnings = [
            w for w in warnings if w.get("timestamp", 0) >= now - self.warning_duration
        ]
        count = len(valid_warnings)
        limit = self._get_warn_limit(chat_id)
        template = self.db.get_setting(chat_id, "warn_message")
        if not template:
            template = (
                "⚠️ {user} has been warned.\n"
                "Reason: {reason}\n"
                "Warnings: {count}/{limit}"
            )
        text = template.replace("{user}", target_user.mention_html())
        text = text.replace("{reason}", reason)
        text = text.replace("{count}", str(count))
        text = text.replace("{limit}", str(limit))
        if origin_message is not None:
            await origin_message.reply_html(text)
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        if count >= limit:
            try:
                await context.bot.ban_chat_member(chat_id, target_user.id)
                ban_template = self.db.get_setting(chat_id, "ban_message")
                if not ban_template:
                    ban_template = "🚫 {user} has been banned.\nReason: {reason}"
                ban_text = ban_template.replace("{user}", target_user.mention_html())
                ban_text = ban_text.replace("{reason}", reason)
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=ban_text,
                    parse_mode=ParseMode.HTML,
                )
                self.db.clear_warnings(chat_id, target_user.id)
                if chat_id in self.warnings and target_user.id in self.warnings[chat_id]:
                    del self.warnings[chat_id][target_user.id]
            except Exception as e:
                logger.error(f"Failed to auto-ban user after warnings: {e}")

    async def unwarn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a user to unwarn.")
            return
        target_user = update.message.reply_to_message.from_user
        chat_id = update.effective_chat.id
        removed = await self._remove_last_warning(chat_id, target_user.id)
        if chat_id in self.warnings and target_user.id in self.warnings[chat_id]:
            if self.warnings[chat_id][target_user.id]:
                self.warnings[chat_id][target_user.id].pop()
        if removed:
            await update.message.reply_html(
                f"✅ Last warning removed for {target_user.mention_html()}."
            )
        else:
            await update.message.reply_text("ℹ️ No warnings found for this user.")

    async def warns_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a user to view warnings.")
            return
        target_user = update.message.reply_to_message.from_user
        chat_id = update.effective_chat.id
        warnings = self.db.get_warnings(chat_id, target_user.id)
        if not warnings:
            await update.message.reply_html(
                f"✅ {target_user.mention_html()} has no warnings."
            )
            return
        lines = [
            f"⚠️ Warnings for {target_user.mention_html()} ({len(warnings)}):"
        ]
        for w in warnings:
            ts = datetime.fromtimestamp(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"- {ts}: {w['reason']}")
        await update.message.reply_html("\n".join(lines))

    async def delwarn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.warn_user(update, context)
        if update.message.reply_to_message:
            chat_id = update.effective_chat.id
            msg_id = update.message.reply_to_message.message_id
            try:
                await context.bot.delete_message(chat_id, msg_id)
            except Exception as e:
                logger.error(f"Failed to delete message for delwarn: {e}")

    async def delete_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_cleaner(update, context):
            await update.message.reply_text("❌ Only cleaners can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a message to delete it.")
            return
        chat_id = update.effective_chat.id
        msg_id = update.message.reply_to_message.message_id
        try:
            await context.bot.delete_message(chat_id, msg_id)
            template = self.db.get_setting(chat_id, "delete_message")
            if template:
                text = template.replace("{user}", update.message.reply_to_message.from_user.mention_html())
                await update.message.reply_html(text)
        except Exception as e:
            logger.error(f"Failed to delete message: {e}")
            await update.message.reply_text("❌ Failed to delete message.")

    async def log_delete_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_cleaner(update, context):
            await update.message.reply_text("❌ Only cleaners can use this command.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a message to delete it.")
            return
        reply = update.message.reply_to_message
        chat_id = update.effective_chat.id
        msg_id = reply.message_id
        author = reply.from_user
        text = reply.text or reply.caption or "<no text>"
        try:
            await context.bot.delete_message(chat_id, msg_id)
        except Exception as e:
            logger.error(f"Failed to delete message for logdel: {e}")
        log_text = (
            f"🛃 Deleted message from {author.mention_html()}:\n"
            f"{text}"
        )
        await update.message.reply_html(log_text)

    def _get_user_message_count(self, chat_id: int, user_id: int) -> int:
        try:
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT message_count FROM user_activity
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            )
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"Error getting user message count: {e}")
            return 0
        finally:
            if "conn" in locals() and conn:
                conn.close()

    def _get_warn_limit(self, chat_id: int) -> int:
        value = self.db.get_setting(chat_id, "warn_limit")
        try:
            return int(value)
        except (TypeError, ValueError):
            return self.max_warnings

    async def _apply_message_policies(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        user = update.effective_user
        message = update.message

        link_policy = self.db.get_setting(chat.id, "link_policy") or "none"
        post_policy = self.db.get_setting(chat.id, "post_policy") or "everyone"

        async def is_staff() -> bool:
            if await self._is_admin(update, context):
                return True
            role = self.db.get_role(chat.id, user.id)
            return role in ["moderator", "cleaner"]

        if post_policy == "staff_only":
            if not await is_staff():
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        has_link = False
        entities = []
        if message and message.entities:
            entities.extend(message.entities)
        if message and message.caption_entities:
            entities.extend(message.caption_entities)
        for e in entities:
            t = getattr(e, "type", None)
            if t in ["url", "text_link"]:
                has_link = True
                break

        if link_policy in ["block_all", "allow_admins"] and has_link:
            allowed = False
            if link_policy == "allow_admins":
                allowed = await self._is_admin(update, context)
            if not allowed:
                try:
                    await message.delete()
                except Exception:
                    pass

    async def info_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_cleaner(update, context):
            await update.message.reply_text("❌ Only cleaners can use this command.")
            return
        target_user = self._parse_target_user(update, context) or update.effective_user
        chat_id = update.effective_chat.id
        warnings = self.db.get_warnings(chat_id, target_user.id)
        warns_count = len(warnings)
        referrals = self._get_referral_count(target_user.id)
        messages = self._get_user_message_count(chat_id, target_user.id)
        max_warns = self._get_warn_limit(chat_id)
        text = (
            f"👤 User info\n\n"
            f"ID: <code>{target_user.id}</code>\n"
            f"Name: {target_user.full_name}\n"
            f"Username: @{target_user.username}" if target_user.username else f"Username: none"
        )
        text += (
            f"\n\nMessages in chat: {messages}\n"
            f"Referrals: {referrals}\n"
            f"Warnings: {warns_count}/{max_warns}"
        )
        await update.message.reply_html(text)

    async def infopvt_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        target_user = self._parse_target_user(update, context) or update.effective_user
        chat_id = update.effective_chat.id
        warnings = self.db.get_warnings(chat_id, target_user.id)
        warns_count = len(warnings)
        referrals = self._get_referral_count(target_user.id)
        messages = self._get_user_message_count(chat_id, target_user.id)
        max_warns = self._get_warn_limit(chat_id)
        text = (
            f"👤 User info\n\n"
            f"ID: {target_user.id}\n"
            f"Name: {target_user.full_name}\n"
            f"Username: @{target_user.username}" if target_user.username else f"Username: none"
        )
        text += (
            f"\n\nMessages in chat: {messages}\n"
            f"Referrals: {referrals}\n"
            f"Warnings: {warns_count}/{max_warns}"
        )
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=text,
            )
            await update.message.reply_text("📬 User info sent in private chat.")
        except Exception as e:
            logger.error(f"Failed to send private info: {e}")
            await update.message.reply_text("❌ Failed to send info in private.")

    async def staff_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
            
            # Get database roles
            db_staff = self.db.get_chat_staff(chat.id)
            
            lines = ["👮🏻 <b>Staff List</b>"]
            
            # Group Admins
            lines.append("\n<b>Admins:</b>")
            for a in admins:
                if not a.user.is_bot:
                    name = a.user.full_name
                    if a.user.username:
                        name = f'<a href="tg://user?id={a.user.id}">{name}</a> (@{a.user.username})'
                    lines.append(f"• {name}")
            
            # Moderators from DB
            mods = [s for s in db_staff if s['role'] == 'moderator']
            if mods:
                lines.append("\n<b>Moderators:</b>")
                for m in mods:
                    name = m['first_name'] or "Unknown"
                    if m['username']:
                        name = f'<a href="tg://user?id={m["user_id"]}">{name}</a> (@{m["username"]})'
                    lines.append(f"• {name}")
            
            # Cleaners from DB
            cleaners = [s for s in db_staff if s['role'] == 'cleaner']
            if cleaners:
                lines.append("\n<b>Cleaners:</b>")
                for c in cleaners:
                    name = c['first_name'] or "Unknown"
                    if c['username']:
                        name = f'<a href="tg://user?id={c["user_id"]}">{name}</a> (@{c["username"]})'
                    lines.append(f"• {name}")
                    
            await update.message.reply_html("\n".join(lines))
            
        except Exception as e:
            logger.error(f"Failed to fetch staff: {e}")
            await update.message.reply_text("❌ Failed to fetch staff list.")

    async def me_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        chat_id = update.effective_chat.id
        warnings = self.db.get_warnings(chat_id, user.id)
        warns_count = len(warnings)
        referrals = self._get_referral_count(user.id)
        messages = self._get_user_message_count(chat_id, user.id)
        max_warns = self._get_warn_limit(chat_id)
        text = (
            f"👤 Your info\n\n"
            f"ID: {user.id}\n"
            f"Name: {user.full_name}\n"
            f"Username: @{user.username}" if user.username else f"Username: none"
        )
        text += (
            f"\n\nMessages in this chat: {messages}\n"
            f"Referrals: {referrals}\n"
            f"Warnings: {warns_count}/{max_warns}\n"
            f"Group ID: {chat_id}"
        )
        try:
            await context.bot.send_message(chat_id=user.id, text=text)
            if update.effective_chat.type != "private":
                await update.message.reply_text("📬 I sent your info in private.")
        except Exception as e:
            logger.error(f"Failed to send /me info: {e}")
            await update.message.reply_text("❌ Failed to send info in private.")

    async def send_html_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /send <HTML message>")
            return
        text = " ".join(context.args)
        chat_id = update.effective_chat.id
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
            try:
                await context.bot.delete_message(chat_id, update.message.message_id)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to send HTML message: {e}")
            await update.message.reply_text("❌ Failed to send message.")

    async def intervention_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        chat = update.effective_chat
        requester = update.effective_user
        text = (
            f"🚨 Intervention requested in chat {chat.title or chat.id}\n"
            f"Chat ID: {chat.id}\n"
            f"Requested by: {requester.full_name} (ID {requester.id})"
        )
        for admin_id in self.admin_ids:
            try:
                await context.bot.send_message(chat_id=admin_id, text=text)
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
        await update.message.reply_text("✅ Intervention request sent to bot admins.")

    async def geturl_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Please reply to a message to get its URL.")
            return
        chat = update.effective_chat
        msg = update.message.reply_to_message
        if chat.username:
            url = f"https://t.me/{chat.username}/{msg.message_id}"
        else:
            chat_id = chat.id
            if str(chat_id).startswith("-100"):
                internal = str(chat_id)[4:]
            else:
                internal = str(abs(chat_id))
            url = f"https://t.me/c/{internal}/{msg.message_id}"
        await update.message.reply_text(url)

    def _get_inactive_users(self, chat_id: int, threshold: int) -> List[Tuple[int, int]]:
        try:
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, last_active
                FROM user_activity
                WHERE chat_id = ? AND last_active < ?
                ORDER BY last_active ASC
                """,
                (chat_id, threshold),
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting inactive users: {e}")
            return []
        finally:
            if "conn" in locals() and conn:
                conn.close()

    async def inactives_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        days = 30
        if context.args and context.args[0].isdigit():
            days = int(context.args[0])
        now = int(time.time())
        threshold = now - days * 86400
        chat_id = update.effective_chat.id
        rows = self._get_inactive_users(chat_id, threshold)
        if not rows:
            await update.message.reply_text("No inactive users found for this period.")
            return
        lines = [f"🕵🏻 Inactive users in last {days} days:"]
        for user_id, last_active in rows:
            dt = datetime.fromtimestamp(last_active).strftime("%Y-%m-%d")
            lines.append(f"- {user_id} (last active {dt})")
        text = "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=update.effective_user.id, text=text)
            await update.message.reply_text("📬 Inactive user list sent in private.")
        except Exception as e:
            logger.error(f"Failed to send inactives list: {e}")
            await update.message.reply_text("❌ Failed to send inactive list in private.")

    async def pin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_moderator(update, context):
            await update.message.reply_text("❌ Only moderators can use this command.")
            return
        chat_id = update.effective_chat.id
        if context.args:
            text = " ".join(context.args)
            msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            try:
                await context.bot.pin_chat_message(chat_id, msg.message_id)
            except Exception as e:
                logger.error(f"Failed to pin message: {e}")
                await update.message.reply_text("❌ Failed to pin message.")
        elif update.message.reply_to_message:
            try:
                await context.bot.pin_chat_message(
                    chat_id, update.message.reply_to_message.message_id
                )
            except Exception as e:
                logger.error(f"Failed to pin message: {e}")
                await update.message.reply_text("❌ Failed to pin message.")
        else:
            await update.message.reply_text(
                "Reply to a message or provide text to pin.\nUsage: /pin [message]"
            )

    async def editpin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        if not context.args:
            await update.message.reply_text("Usage: /editpin <new pinned message text>")
            return
        chat = update.effective_chat
        try:
            chat_obj = await context.bot.get_chat(chat.id)
            pinned = chat_obj.pinned_message
            if not pinned:
                await update.message.reply_text("No pinned message to edit.")
                return
            text = " ".join(context.args)
            await context.bot.edit_message_text(
                chat_id=chat.id,
                message_id=pinned.message_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Failed to edit pinned message: {e}")
            await update.message.reply_text("❌ Failed to edit pinned message.")

    async def delpin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        chat_id = update.effective_chat.id
        try:
            await context.bot.unpin_chat_message(chat_id)
            await update.message.reply_text("✅ Pinned message removed.")
        except Exception as e:
            logger.error(f"Failed to remove pinned message: {e}")
            await update.message.reply_text("❌ Failed to remove pinned message.")

    async def repin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        chat = update.effective_chat
        try:
            chat_obj = await context.bot.get_chat(chat.id)
            pinned = chat_obj.pinned_message
            if not pinned:
                await update.message.reply_text("No pinned message to repin.")
                return
            await context.bot.unpin_chat_message(chat.id, pinned.message_id)
            await context.bot.pin_chat_message(chat.id, pinned.message_id)
            await update.message.reply_text("✅ Pinned message repinned with notification.")
        except Exception as e:
            logger.error(f"Failed to repin message: {e}")
            await update.message.reply_text("❌ Failed to repin message.")

    async def pinned_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        try:
            chat_obj = await context.bot.get_chat(chat.id)
            pinned = chat_obj.pinned_message
            if not pinned:
                await update.message.reply_text("No pinned message set.")
                return
            if chat.username:
                url = f"https://t.me/{chat.username}/{pinned.message_id}"
            else:
                chat_id = chat.id
                if str(chat_id).startswith("-100"):
                    internal = str(chat_id)[4:]
                else:
                    internal = str(abs(chat_id))
                url = f"https://t.me/c/{internal}/{pinned.message_id}"
            await update.message.reply_text(f"📌 Pinned message: {url}")
        except Exception as e:
            logger.error(f"Failed to get pinned message: {e}")
            await update.message.reply_text("❌ Failed to fetch pinned message.")

    def _get_all_users(self, chat_id: int) -> List[Tuple[int, int]]:
        try:
            conn = sqlite3.connect("bot_data.db")
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT user_id, message_count
                FROM user_activity
                WHERE chat_id = ?
                ORDER BY message_count DESC
                """,
                (chat_id,),
            )
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting user list: {e}")
            return []
        finally:
            if "conn" in locals() and conn:
                conn.close()

    async def list_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.args and context.args[0].lower() == "roles":
            await self.list_roles_command(update, context)
            return
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
        chat_id = update.effective_chat.id
        rows = self._get_all_users(chat_id)
        if not rows:
            await update.message.reply_text("No users tracked yet.")
            return
        lines = ["📝 Users and message counts:"]
        for user_id, count in rows:
            lines.append(f"- {user_id}: {count} messages")
        text = "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=update.effective_user.id, text=text)
            await update.message.reply_text("📬 User list sent in private.")
        except Exception as e:
            logger.error(f"Failed to send user list: {e}")
            await update.message.reply_text("❌ Failed to send user list in private.")

    async def list_roles_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        try:
            admins = await context.bot.get_chat_administrators(chat.id)
        except Exception as e:
            logger.error(f"Failed to fetch roles: {e}")
            await update.message.reply_text("❌ Failed to fetch roles.")
            return
        lines = ["🕵🏻 Roles in this chat:", "Admins and moderators:"]
        for a in admins:
            name = a.user.full_name
            if a.user.username:
                name += f" (@{a.user.username})"
            lines.append(f"- {name}")
        await update.message.reply_text("\n".join(lines))

    async def graphic_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_stats(update, context)

    async def trend_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.show_stats(update, context)

    # Stats and tracking
    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user or group statistics."""
        chat_id = update.effective_chat.id
        now = int(time.time())
        
        # Get time ranges
        day_start = now - 86400  # 24 hours
        week_start = now - 604800  # 7 days
        month_start = now - 2592000  # 30 days
        
        # Get stats from database
        stats = {
            'daily': self._get_active_users(chat_id, day_start, now),
            'weekly': self._get_active_users(chat_id, week_start, now),
            'monthly': self._get_active_users(chat_id, month_start, now),
            'all_time': self._get_active_users(chat_id, 0, now)
        }
        
        # Format message
        message = [
            "📊 <b>Group Statistics</b>\n\n",
            f"👥 <b>Active Members (Last 24h):</b> {stats['daily']}\n",
            f"📅 <b>Active Members (Last 7 days):</b> {stats['weekly']}\n",
            f"📆 <b>Active Members (Last 30 days):</b> {stats['monthly']}\n",
            f"🏆 <b>All-Time Members:</b> {stats['all_time']}\n\n",
            "<i>Note: Active members are users who have sent at least one message.</i>"
        ]
        
        # Add top active members if in private chat
        if update.effective_chat.type == 'private':
            top_active = self._get_top_active_users(chat_id, 5)
            if top_active:
                message.append("\n\n<b>🏆 Top Active Members:</b>")
                for i, (user_id, count) in enumerate(top_active, 1):
                    try:
                        user = await context.bot.get_chat_member(chat_id, user_id)
                        name = user.user.mention_html()
                    except:
                        name = f"User {user_id}"
                    message.append(f"\n{i}. {name}: {count} messages")
        
        await update.message.reply_html("".join(message))

    # Referral system
    async def referral_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show referral information and user's referral stats."""
        user = update.effective_user
        chat = update.effective_chat
        
        # Get user's referral stats
        ref_count = self._get_referral_count(user.id)
        ref_link = f"https://t.me/{self.bot_username}?start=ref{user.id}"
        
        message = [
            "🤝 <b>Referral Program</b>\n\n",
            f"📊 <b>Your Referrals:</b> {ref_count}\n\n",
            "<b>How it works:</b>\n",
            "1. Share your referral link with friends\n",
            "2. When they join using your link, you both get rewards\n\n",
            f"<b>Your Referral Link:</b>\n<code>{ref_link}</code>"
        ]
        
        await update.message.reply_html("".join(message))
    
    async def chat_member_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        data = update.chat_member
        old = data.old_chat_member.status
        new = data.new_chat_member.status
        user = data.new_chat_member.user
        
        if user.is_bot:
            return
        
        if old in ["left", "kicked"] and new in ["member", "administrator"]:
            template = self.db.get_setting(chat.id, "welcome_message")
            if not template:
                template = "👋 Welcome {user} to the group!"
            text = template.replace("{user}", user.mention_html())
            await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
        elif old in ["member", "administrator"] and new in ["left", "kicked"]:
            template = self.db.get_setting(chat.id, "goodbye_message")
            if not template:
                template = "👋 {user} has left the group."
            text = template.replace("{user}", user.mention_html())
            await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
    
    async def referral_top(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show top referrers for this group or current event."""
        chat = update.effective_chat
        if chat.type not in ["group", "supergroup"]:
            await update.message.reply_text("This command can only be used in groups.")
            return
        
        leaderboard = self.db.get_referral_leaderboard(chat.id, limit=10, use_event_window=True)
        
        if not leaderboard:
            await update.message.reply_text("No referrals recorded for this group yet.")
            return
        
        event = self.db.get_referral_event(chat.id)
        if event and event.get("end_ts") is None:
            header = "🏆 Referral Event Leaderboard\n"
        elif event:
            header = "🏆 Referral Event Results\n"
        else:
            header = "🏆 Referral Leaderboard (all-time)\n"
        
        lines = [header]
        for i, row in enumerate(leaderboard, start=1):
            name = row.get("first_name") or "User"
            username = row.get("username")
            if username:
                name = f"{name} (@{username})"
            lines.append(f"{i}. {name} — {row['ref_count']} referrals")
        
        await update.message.reply_text("\n".join(lines))
    
    async def referral_event_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Manage referral event for this group."""
        chat = update.effective_chat
        user = update.effective_user
        
        if not context.args:
            event = self.db.get_referral_event(chat.id)
            if not event:
                await update.message.reply_text("No referral event configured for this group.")
                return
            
            status = "active" if event["end_ts"] is None else "ended"
            start_dt = datetime.fromtimestamp(event["start_ts"]).strftime("%Y-%m-%d %H:%M")
            if event["end_ts"]:
                end_dt = datetime.fromtimestamp(event["end_ts"]).strftime("%Y-%m-%d %H:%M")
                text = f"Referral event is {status}.\nStart: {start_dt}\nEnd: {end_dt}"
            else:
                text = f"Referral event is {status}.\nStart: {start_dt}"
            await update.message.reply_text(text)
            return
        
        subcommand = context.args[0].lower()
        
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can manage referral events.")
            return
        
        if subcommand == "start":
            if self.db.start_referral_event(chat.id):
                await update.message.reply_text("✅ Referral event started for this group.")
            else:
                await update.message.reply_text("❌ Failed to start referral event.")
        elif subcommand in ["stop", "end"]:
            if self.db.end_referral_event(chat.id):
                await update.message.reply_text("✅ Referral event ended for this group.")
            else:
                await update.message.reply_text("ℹ️ No active referral event to end.")
        else:
            await update.message.reply_text("Usage: /refevent start|stop|end|status")
    
    async def _handle_referral(self, user_id: int, referrer_id: int, chat_id: int):
        """Handle a new referral."""
        if user_id == referrer_id:
            return  # Can't refer yourself
            
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            # Check if this referral already exists
            cursor.execute('''
                SELECT 1 FROM referrals 
                WHERE referrer_id = ? AND referred_id = ?
            ''', (referrer_id, user_id))
            
            if not cursor.fetchone():
                # Add new referral
                cursor.execute('''
                    INSERT INTO referrals (referrer_id, referred_id, timestamp)
                    VALUES (?, ?, ?)
                ''', (referrer_id, user_id, int(time.time())))
                
                conn.commit()
                logger.info(f"New referral: {referrer_id} -> {user_id}")
                
        except Exception as e:
            logger.error(f"Error handling referral: {e}")
        finally:
            if conn:
                conn.close()
    
    def _get_referral_count(self, user_id: int) -> int:
        """Get the number of successful referrals for a user."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM referrals 
                WHERE referrer_id = ?
            ''', (user_id,))
            
            return cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"Error getting referral count: {e}")
            return 0
        finally:
            if conn:
                conn.close()

    # Database helper methods
    def _save_warning(self, chat_id: int, user_id: int, reason: str, warned_by: int):
        """Save warning to the database."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO warnings (chat_id, user_id, reason, timestamp, warned_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (chat_id, user_id, reason, int(time.time()), warned_by))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Error saving warning: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def _get_active_users(self, chat_id: int, start_time: int, end_time: int) -> int:
        """Get number of active users in a time range."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(DISTINCT user_id) 
                FROM user_activity 
                WHERE chat_id = ? AND last_active BETWEEN ? AND ?
            ''', (chat_id, start_time, end_time))
            
            return cursor.fetchone()[0] or 0
        except Exception as e:
            logger.error(f"Error getting active users: {e}")
            return 0
        finally:
            if conn:
                conn.close()
    
    def _get_top_active_users(self, chat_id: int, limit: int = 5) -> list:
        """Get top active users by message count."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT user_id, message_count 
                FROM user_activity 
                WHERE chat_id = ? 
                ORDER BY message_count DESC 
                LIMIT ?
            ''', (chat_id, limit))
            
            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error getting top active users: {e}")
            return []
        finally:
            if conn:
                conn.close()
    
    async def _update_user_activity(self, chat_id: int, user_id: int):
        """Update user activity in the database."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            now = int(time.time())
            
            # Check if user exists
            cursor.execute('''
                SELECT 1 FROM user_activity 
                WHERE chat_id = ? AND user_id = ?
            ''', (chat_id, user_id))
            
            if cursor.fetchone():
                # Update existing user
                cursor.execute('''
                    UPDATE user_activity 
                    SET message_count = message_count + 1, last_active = ?
                    WHERE chat_id = ? AND user_id = ?
                ''', (now, chat_id, user_id))
            else:
                # Add new user
                cursor.execute('''
                    INSERT INTO user_activity (chat_id, user_id, message_count, last_active, join_date)
                    VALUES (?, ?, 1, ?, ?)
                ''', (chat_id, user_id, now, now))
            
            conn.commit()
        except Exception as e:
            logger.error(f"Error updating user activity: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    # Helper methods
    async def _is_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if the user is a group admin or creator."""
        user = update.effective_user
        
        # Check if user is a group admin
        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
                return member.status in ['administrator', 'creator']
            except Exception as e:
                logger.error(f"Error checking admin status: {e}")
                
        return False

    async def _is_moderator(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if user is a moderator or higher (admin)."""
        if await self._is_admin(update, context):
            return True
            
        # Check database role
        role = self.db.get_role(update.effective_chat.id, update.effective_user.id)
        return role in ['moderator']

    async def _is_cleaner(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if user is a cleaner or higher (mod/admin)."""
        if await self._is_admin(update, context):
            return True
            
        # Check database role
        role = self.db.get_role(update.effective_chat.id, update.effective_user.id)
        return role in ['moderator', 'cleaner']

    # Role Management Commands
    async def addmod_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide ID.")
            return
            
        if self.db.add_role(update.effective_chat.id, target_user.id, 'moderator', update.effective_user.id):
            await update.message.reply_html(f"✅ {target_user.mention_html()} is now a Moderator.")
        else:
            await update.message.reply_text("❌ Failed to add moderator.")

    async def addcleaner_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide ID.")
            return
            
        if self.db.add_role(update.effective_chat.id, target_user.id, 'cleaner', update.effective_user.id):
            await update.message.reply_html(f"✅ {target_user.mention_html()} is now a Cleaner.")
        else:
            await update.message.reply_text("❌ Failed to add cleaner.")

    async def remove_role_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide ID.")
            return
            
        if self.db.remove_role(update.effective_chat.id, target_user.id):
            await update.message.reply_html(f"✅ Removed roles from {target_user.mention_html()}.")
        else:
            await update.message.reply_text("ℹ️ User had no roles.")
    
    def _parse_target_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Parse target user from message reply or command arguments."""
        if update.message.reply_to_message:
            return update.message.reply_to_message.from_user
            
        if context.args and context.args[0].isdigit():
            try:
                user_id = int(context.args[0])
                # Return a minimal user object
                from collections import namedtuple
                User = namedtuple('User', ['id', 'mention_html'])
                return User(id=user_id, mention_html=lambda: f"<a href='tg://user?id={user_id}'>user</a>")
            except (ValueError, IndexError):
                pass
                
        return None
    
    def add_handlers(self):
        """Add all command and message handlers to the application."""
        # Command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("stats", self.show_stats))
        self.app.add_handler(CommandHandler("referral", self.referral_info))
        self.app.add_handler(CommandHandler("reftop", self.referral_top))
        self.app.add_handler(CommandHandler("refevent", self.referral_event_command))
        self.app.add_handler(CommandHandler("mod", self.modpanel_command))
        
        # Role Management
        self.app.add_handler(CommandHandler("addmod", self.addmod_command))
        self.app.add_handler(CommandHandler("addcleaner", self.addcleaner_command))
        self.app.add_handler(CommandHandler("remove", self.remove_role_command))
        
        # Admin/Mod commands
        self.app.add_handler(CommandHandler("warn", self.warn_user))
        self.app.add_handler(CommandHandler("ban", self.ban_user))
        self.app.add_handler(CommandHandler("mute", self.mute_user))
        self.app.add_handler(CommandHandler("unmute", self.unmute_user))
        self.app.add_handler(CommandHandler("kick", self.kick_user))
        self.app.add_handler(CommandHandler("unban", self.unban_user))
        self.app.add_handler(CommandHandler("unwarn", self.unwarn_user))
        self.app.add_handler(CommandHandler("warns", self.warns_user))
        
        # Cleaner/Info commands
        self.app.add_handler(CommandHandler("del", self.delete_message))
        self.app.add_handler(CommandHandler("info", self.info_command))
        self.app.add_handler(CommandHandler("staff", self.staff_command))
        self.app.add_handler(CommandHandler("me", self.me_command))
        
        # Other admin commands
        self.app.add_handler(CommandHandler("pin", self.pin_command))
        
        # Help / control panel buttons
        self.app.add_handler(CallbackQueryHandler(self.help_button_handler, pattern="^help_"))
        self.app.add_handler(CallbackQueryHandler(self.moderation_button_handler, pattern="^mod:"))
        
        # Chat member updates for joins/leaves
        self.app.add_handler(ChatMemberHandler(self.chat_member_update, ChatMemberHandler.CHAT_MEMBER))
        
        # Message handler for tracking activity
        self.app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_message
        ))

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all non-command messages to track activity."""
        user = update.effective_user
        chat = update.effective_chat
        
        if user.is_bot:
            return

        key = (chat.id, user.id)
        if key in self.pending_settings:
            setting_key = self.pending_settings.pop(key)
            text = update.message.text.strip()
            if self.db.set_setting(chat.id, setting_key, text):
                await update.message.reply_text("✅ Saved automation message.")
            else:
                await update.message.reply_text("❌ Failed to save automation message.")
            return

        await self._apply_message_policies(update, context)
            
        await self._update_user_activity(chat.id, user.id)

    def set_webhook(self, url: str):
        """Set webhook for the bot."""
        if not self.app:
            return False
        try:
            self.app.bot.set_webhook(url=url)
            return True
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")
            return False

    def run(self, webhook_url: str = None):
        """Run the bot."""
        if not self.app:
            logger.error("Bot not initialized!")
            return

        try:
            if webhook_url:
                # Webhook mode (for production)
                logger.info("Starting bot in webhook mode...")
                self.app.run_webhook(
                    listen="0.0.0.0",
                    port=int(os.environ.get('PORT', '10000')),
                    webhook_url=webhook_url
                )
            else:
                # Polling mode (for development)
                logger.info("Starting bot in polling mode...")
                self.app.run_polling()
        except Exception as e:
            logger.critical(f"Error in bot run loop: {e}", exc_info=True)
            raise

if __name__ == '__main__':
    bot = MultipurposeBot()
    bot.run()
