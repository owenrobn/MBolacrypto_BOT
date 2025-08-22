import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
from telegram.constants import ParseMode

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class MultipurposeBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        
        if not self.bot_token or not self.bot_username:
            raise ValueError("BOT_TOKEN and BOT_USERNAME must be set in environment")
        
        # Admin IDs from environment variable (comma-separated)
        self.admin_ids = set()
        if os.getenv('ADMIN_IDS'):
            self.admin_ids = {int(x.strip()) for x in os.getenv('ADMIN_IDS').split(',') if x.strip().isdigit()}
        
        # Warning and ban configuration
        self.max_warnings = 3  # Number of warnings before ban
        self.warning_duration = 7 * 24 * 3600  # 7 days in seconds
        
        # Track warnings in memory: {chat_id: {user_id: [warning_data]}}
        self.warnings = {}
        
        # Initialize database
        self._init_database()
    
    def _init_database(self):
        """Initialize database tables if they don't exist."""
        try:
            conn = sqlite3.connect('bot_data.db')
            cursor = conn.cursor()
            
            # Create warnings table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    reason TEXT,
                    timestamp INTEGER NOT NULL,
                    warned_by INTEGER NOT NULL
                )
            ''')
            
            # Create user activity table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_activity (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    last_active INTEGER NOT NULL,
                    join_date INTEGER NOT NULL,
                    PRIMARY KEY (chat_id, user_id)
                )
            ''')
            
            # Create referral table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS referrals (
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL,
                    PRIMARY KEY (referrer_id, referred_id)
                )
            ''')
            
            conn.commit()
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
        finally:
            if conn:
                conn.close()

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
        """Send a message when the command /help is issued."""
        help_text = (
            "🤖 <b>Available Commands</b>\n\n"
            "<b>For Everyone:</b>\n"
            "/start - Start the bot and see welcome message\n"
            "/help - Show this help message\n"
            "/stats - Show group statistics\n"
            "/referral - View referral information\n\n"
            "<b>For Admins:</b>\n"
            "/warn @user [reason] - Warn a user\n"
            "/ban @user [reason] - Ban a user\n"
            "/mute @user [duration] [reason] - Mute a user\n"
            "/unmute @user - Unmute a user"
        )
        await update.message.reply_html(help_text)

    # Admin commands
    async def warn_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Warn a user in the group."""
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide a user ID.")
            return
            
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        chat_id = update.effective_chat.id
        
        # Initialize chat in warnings dict if not exists
        if chat_id not in self.warnings:
            self.warnings[chat_id] = {}
            
        # Initialize user warnings if not exists
        if target_user.id not in self.warnings[chat_id]:
            self.warnings[chat_id][target_user.id] = []
        
        # Add warning
        warning_data = {
            'timestamp': int(time.time()),
            'reason': reason,
            'warned_by': update.effective_user.id
        }
        self.warnings[chat_id][target_user.id].append(warning_data)
        
        # Save to database
        self._save_warning(chat_id, target_user.id, reason, update.effective_user.id)
        
        # Check if user should be banned
        warning_count = len(self.warnings[chat_id][target_user.id])
        
        if warning_count >= self.max_warnings:
            # Ban the user
            try:
                await context.bot.ban_chat_member(chat_id, target_user.id)
                await update.message.reply_text(
                    f"🚨 {target_user.mention_html()} has been banned for reaching {warning_count} warnings.",
                    parse_mode=ParseMode.HTML
                )
                # Clear warnings after ban
                del self.warnings[chat_id][target_user.id]
            except Exception as e:
                logger.error(f"Failed to ban user: {e}")
                await update.message.reply_text("❌ Failed to ban user.")
        else:
            # Notify about the warning
            warnings_left = self.max_warnings - warning_count
            await update.message.reply_html(
                f"⚠️ {target_user.mention_html()} has been warned. "
                f"({warning_count}/{self.max_warnings} warnings)\n\n"
                f"<b>Reason:</b> {reason}\n"
                f"<b>Warnings left before ban:</b> {warnings_left}"
            )

    async def ban_user(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ban a user from the group."""
        if not await self._is_admin(update, context):
            await update.message.reply_text("❌ Only admins can use this command.")
            return
            
        target_user = self._parse_target_user(update, context)
        if not target_user:
            await update.message.reply_text("❌ Please reply to a user or provide a user ID.")
            return
            
        reason = ' '.join(context.args[1:]) if len(context.args) > 1 else "No reason provided"
        chat_id = update.effective_chat.id
        
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            await update.message.reply_html(
                f"🚫 {target_user.mention_html()} has been banned.\n\n"
                f"<b>Reason:</b> {reason}"
            )
            
            # Clear any warnings for this user
            if chat_id in self.warnings and target_user.id in self.warnings[chat_id]:
                del self.warnings[chat_id][target_user.id]
                
        except Exception as e:
            logger.error(f"Failed to ban user: {e}")
            await update.message.reply_text("❌ Failed to ban user.")

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
        """Check if the user is an admin or bot owner."""
        user = update.effective_user
        
        # Check if user is a bot admin
        if user.id in self.admin_ids:
            return True
            
        # Check if user is a group admin
        if update.effective_chat.type in ['group', 'supergroup']:
            try:
                member = await context.bot.get_chat_member(update.effective_chat.id, user.id)
                return member.status in ['administrator', 'creator']
            except Exception as e:
                logger.error(f"Error checking admin status: {e}")
                
        return False
    
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
    
    def add_handlers(self, application):
        """Add all command and message handlers to the application."""
        # Command handlers
        application.add_handler(CommandHandler('start', self.start))
        application.add_handler(CommandHandler('help', self.help_command))
        application.add_handler(CommandHandler('stats', self.show_stats))
        application.add_handler(CommandHandler('referral', self.referral_info))
        
        # Admin commands
        application.add_handler(CommandHandler('warn', self.warn_user))
        application.add_handler(CommandHandler('ban', self.ban_user))
        
        # Message handler for tracking activity
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_message
        ))
    
    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all non-command messages to track activity."""
        user = update.effective_user
        chat = update.effective_chat
        
        # Ignore messages from bots
        if user.is_bot:
            return
            
        # Update user activity
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
    application = Application.builder().token(bot.bot_token).build()
    bot.app = application
    bot.add_handlers(application)
    bot.run()
