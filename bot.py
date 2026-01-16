import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Third-party imports
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    JobQueue,
    MessageHandler,
    filters,
)
from telegram.error import BadRequest

# Standard library imports
import json
import os
import sqlite3
import string
import random
from PIL import Image
from io import BytesIO
import numpy as np
import cv2

# Load environment variables
load_dotenv()  # Load environment variables from .env file

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Constants
POINTS_PER_REFERRAL = 10
DAILY_BONUS_MIN = 5
DAILY_BONUS_MAX = 15
MAX_WARNINGS = 3

class ModTier(Enum):
    BASIC = 1
    ADVANCED = 2
    EXPERT = 3

class ModRole(Enum):
    USER = 0
    CLEANER = 1
    MODERATOR = 2
    ADMIN = 3

class Database:  # Assuming this is part of a Database class
    async def award_achievement(self, user_id: int, achievement_name: str) -> bool:
        """Award an achievement to a user."""
        try:
            # Get achievement ID
            achievement = await self.fetch_one(
                "SELECT id FROM achievements WHERE name = ?",
                (achievement_name,)
            )
            
            if not achievement:
                return False
                
            # Check if already has the achievement
            exists = await self.fetch_one(
                "SELECT 1 FROM user_achievements WHERE user_id = ? AND achievement_id = ?",
                (user_id, achievement['id'])
            )
            
            if exists:
                return False
                
            # Award achievement
            await self.execute(
                "INSERT INTO user_achievements (user_id, achievement_id) VALUES (?, ?)",
                (user_id, achievement['id'])
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error awarding achievement: {e}")
            return False

# AI Moderation System
class AIModeration:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("AI_MODERATION_API_KEY")
        
    async def check_text(self, text: str) -> Dict[str, float]:
        """Check text for spam and other violations."""
        if not text:
            return {
                'spam': 0.0,
                'toxic': 0.0,
                'severe_toxic': 0.0,
                'threat': 0.0,
                'insult': 0.0
            }
            
        # In a real implementation, you would call an API or use a local model
        # For now, we'll return dummy scores for other categories
        return {
            'spam': 0.0,
            'toxic': 0.0,
            'severe_toxic': 0.0,
            'threat': 0.0,
            'insult': 0.0
        }
    
    async def check_image(self, image_data: bytes) -> Dict[str, float]:
        """Check image for NSFW and other violations."""
        try:
            # In a real implementation, you would use a proper image moderation API
            # This is a simplified version that does basic checks
            
            # Convert bytes to image
            image = Image.open(BytesIO(image_data))
            
            # Convert to OpenCV format for analysis
            img_array = np.array(image)
            if img_array.shape[-1] == 4:  # RGBA to RGB
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
            else:
                img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)
            
            # Simple checks (replace with actual model inference)
            nsfw_score = 0.0
            violence_score = 0.0
            explicit_score = 0.0
            
            # Check for skin tone percentage (very basic NSFW detection)
            hsv = cv2.cvtColor(img_array, cv2.COLOR_BGR2HSV)
            lower = np.array([0, 48, 80], dtype=np.uint8)
            upper = np.array([20, 255, 255], dtype=np.uint8)
            skin_mask = cv2.inRange(hsv, lower, upper)
            skin_pixels = cv2.countNonZero(skin_mask)
            total_pixels = img_array.shape[0] * img_array.shape[1]
            skin_ratio = skin_pixels / total_pixels
            
            if skin_ratio > 0.3:  # Arbitrary threshold
                nsfw_score = min(skin_ratio, 0.9)
                explicit_score = nsfw_score * 0.7
                
            # Check for bright red colors (violence/blood)
            lower_red = np.array([0, 50, 50], dtype=np.uint8)
            upper_red = np.array([10, 255, 255], dtype=np.uint8)
            red_mask1 = cv2.inRange(hsv, lower_red, upper_red)
            
            lower_red = np.array([170, 50, 50], dtype=np.uint8)
            upper_red = np.array([180, 255, 255], dtype=np.uint8)
            red_mask2 = cv2.inRange(hsv, lower_red, upper_red)
            
            red_pixels = cv2.countNonZero(red_mask1) + cv2.countNonZero(red_mask2)
            red_ratio = red_pixels / total_pixels
            
            if red_ratio > 0.1:  # Arbitrary threshold
                violence_score = min(red_ratio * 2, 0.8)
            
            return {
                'nsfw': nsfw_score,
                'explicit': explicit_score,
                'violence': violence_score,
                'drugs': 0.0,  # Would require more advanced detection
                'error': None
            }
            
        except Exception as e:
            logger.error(f"Error in image moderation: {e}")
            return {
                'nsfw': 0.0,
                'explicit': 0.0,
                'violence': 0.0,
                'drugs': 0.0,
                'error': str(e)
            }

# Anti-Raid System
class AntiRaid:
    def __init__(self, db: Database):
        self.db = db
        self.new_users = {}  # Format: {chat_id: {user_id: join_time}}
        
    async def check_raid(self, chat_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Check if a new user join might be part of a raid."""
        # Get anti-raid settings
        settings = await self.db.get_chat_settings(chat_id)
        
        if not settings.get('antiraid_enabled', False):
            return None
            
        # Initialize chat in new_users if not exists
        if chat_id not in self.new_users:
            self.new_users[chat_id] = {}
            
        # Add new user to tracking
        current_time = datetime.now()
        self.new_users[chat_id][user_id] = current_time
        
        # Get raid settings
        time_window = settings.get('antiraid_time_window', 60)  # seconds
        threshold = settings.get('antiraid_threshold', 5)  # max users in time window
        
        # Count new users in the time window
        recent_users = [
            uid for uid, join_time in self.new_users[chat_id].items()
            if (current_time - join_time).total_seconds() <= time_window
        ]
        
        if len(recent_users) >= threshold:
            # Possible raid detected
            action = settings.get('antiraid_action', 'mute')
            duration = settings.get('antiraid_duration', 3600)  # 1 hour
            
            return {
                'action': action,
                'duration': duration,
                'users': recent_users,
                'count': len(recent_users),
                'time_window': time_window
            }
            
        return None
        
    async def cleanup_old_entries(self):
        """Clean up old entries from the new_users dictionary."""
        current_time = datetime.now()
        for chat_id in list(self.new_users.keys()):
            # Remove entries older than 1 hour
            self.new_users[chat_id] = {
                uid: join_time for uid, join_time in self.new_users[chat_id].items()
                if (current_time - join_time).total_seconds() <= 3600
            }
            # Remove empty chat entries
            if not self.new_users[chat_id]:
                del self.new_users[chat_id]

# Reputation System
class ReputationSystem:
    def __init__(self, db: Database):
        self.db = db
        self.cooldown = 3600  # 1 hour cooldown in seconds
        
    async def add_reputation(
        self, 
        from_user_id: int, 
        to_user_id: int, 
        chat_id: int,
        amount: int = 1
    ) -> Dict[str, Any]:
        """Add reputation to a user."""
        if from_user_id == to_user_id:
            return {
                'success': False, 
                'message': "You can't give reputation to yourself."
            }
            
        # Check cooldown
        last_given = await self.db.fetch_one(
            "SELECT last_reputation_given FROM user_reputation WHERE user_id = ? AND chat_id = ?",
            (from_user_id, chat_id)
        )
        
        if last_given and last_given['last_reputation_given']:
            last_time = datetime.strptime(last_given['last_reputation_given'], '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last_time).total_seconds() < self.cooldown:
                remaining = int(self.cooldown - (datetime.now() - last_time).total_seconds())
                return {
                    'success': False,
                    'message': f"You're on cooldown. Try again in {remaining//60} minutes."
                }
        
        # Update or insert reputation
        await self.db.execute("""
            INSERT INTO user_reputation (user_id, chat_id, reputation, last_reputation_given)
            VALUES (?, ?, COALESCE((SELECT reputation FROM user_reputation WHERE user_id = ? AND chat_id = ?), 0) + ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET 
                reputation = reputation + ?,
                last_reputation_given = CURRENT_TIMESTAMP
        """, (to_user_id, chat_id, to_user_id, chat_id, amount, amount))
        
        # Update the giver's last_given timestamp
        await self.db.execute("""
            INSERT OR REPLACE INTO user_reputation (user_id, chat_id, last_reputation_given)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        """, (from_user_id, chat_id,))
        
        # Get updated reputation
        result = await self.db.fetch_one(
            "SELECT reputation FROM user_reputation WHERE user_id = ? AND chat_id = ?",
            (to_user_id, chat_id)
        )
        
        return {
            'success': True,
            'new_reputation': result['reputation'] if result else amount,
            'message': f"✅ Reputation added! New total: {result['reputation'] if result else amount}"
        }
        
    async def get_reputation(self, user_id: int, chat_id: int) -> int:
        """Get a user's reputation in a chat."""
        result = await self.db.fetch_one(
            "SELECT reputation FROM user_reputation WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        return result['reputation'] if result else 0

# Game System
class GameSystem:
    def __init__(self, db: Database):
        self.db = db
        
    async def start_trivia(
        self, 
        chat_id: int, 
        user_id: int,
        category: Optional[str] = None,
        difficulty: str = "medium"
    ) -> Dict[str, Any]:
        """Start a new trivia game."""
        # Check for existing active game
        active_game = await self.db.fetch_one(
            "SELECT * FROM games WHERE chat_id = ? AND game_type = 'trivia' AND is_active = 1",
            (chat_id,)
        )
        
        if active_game:
            return {
                'success': False,
                'message': 'There is already an active trivia game in this chat!'
            }
        
        # In a real implementation, you would fetch questions from an API or database
        # This is a simplified version with hardcoded questions
        questions = {
            'general': [
                {
                    'question': 'What is the capital of France?',
                    'options': ['London', 'Berlin', 'Paris', 'Madrid'],
                    'correct': 2,
                    'difficulty': 'easy'
                },
                {
                    'question': 'Which planet is known as the Red Planet?',
                    'options': ['Venus', 'Mars', 'Jupiter', 'Saturn'],
                    'correct': 1,
                    'difficulty': 'easy'
                },
                {
                    'question': 'What is the largest mammal in the world?',
                    'options': ['African Elephant', 'Blue Whale', 'Giraffe', 'Polar Bear'],
                    'correct': 1,
                    'difficulty': 'medium'
                }
            ],
            'science': [
                {
                    'question': 'What is the chemical symbol for gold?',
                    'options': ['Go', 'Au', 'Ag', 'Ge'],
                    'correct': 1,
                    'difficulty': 'easy'
                }
            ],
            'history': [
                {
                    'question': 'In which year did World War II end?',
                    'options': ['1943', '1944', '1945', '1946'],
                    'correct': 2,
                    'difficulty': 'medium'
                }
            ]
        }
        
        # Filter questions by category and difficulty
        available_questions = []
        for cat, qs in questions.items():
            if category and cat.lower() != category.lower():
                continue
            for q in qs:
                if difficulty and q['difficulty'] != difficulty:
                    continue
                available_questions.append(q)
        
        if not available_questions:
            return {
                'success': False,
                'message': 'No questions found for the specified category and difficulty.'
            }
            
        # Select a random question
        question = random.choice(available_questions)
        
        # Create game record
        game_id = await self.db.create_game(
            'trivia',
            chat_id,
            user_id,
            {
                'question': question['question'],
                'options': question['options'],
                'correct': question['correct'],
                'difficulty': question.get('difficulty', 'medium'),
                'category': category or 'general',
                'participants': {},
                'start_time': datetime.now().isoformat()
            }
        )
        
        # Format options for display
        options_text = "\n".join(
            f"{i+1}. {option}" 
            for i, option in enumerate(question['options'])
        )
        
        return {
            'success': True,
            'game_id': game_id,
            'question': question['question'],
            'options': question['options'],
            'formatted_options': options_text,
            'difficulty': question.get('difficulty', 'medium').capitalize(),
            'category': category.capitalize() if category else 'General Knowledge'
        }
    
    async def process_trivia_answer(
        self, 
        game_id: int, 
        user_id: int, 
        answer: int
    ) -> Dict[str, Any]:
        """Process a user's answer to a trivia question."""
        # Get game data
        game = await self.db.get_game(game_id)
        if not game or game['game_type'] != 'trivia' or not game['is_active']:
            return {
                'success': False,
                'message': 'Game not found or has already ended.'
            }
            
        data = game['data']
        
        # Check if user already answered
        if str(user_id) in data['participants']:
            return {
                'success': False,
                'message': 'You have already answered this question!'
            }
            
        # Check if answer is valid
        if answer < 0 or answer >= len(data['options']):
            return {
                'success': False,
                'message': 'Invalid answer choice.'
            }
            
        # Record answer
        is_correct = (answer == data['correct'])
        data['participants'][str(user_id)] = {
            'answer': answer,
            'correct': is_correct,
            'timestamp': datetime.now().isoformat()
        }
        
        # Update game data
        await self.db.update_game(game_id, data)
        
        # Check if all expected players have answered
        # In a real implementation, you might have a list of expected players
        # For now, we'll just return the result immediately
        
        # End the game
        await self.db.end_game(game_id)
        
        # Prepare results
        correct_answer = data['options'][data['correct']]
        user_answer = data['options'][answer]
        
        # Calculate points (simplified)
        points = 10
        if data.get('difficulty') == 'hard':
            points = 20
        elif data.get('difficulty') == 'easy':
            points = 5
            
        # Update user's points if correct
        if is_correct:
            await self.db.execute(
                "UPDATE users SET points = points + ? WHERE user_id = ?",
                (points, user_id)
            )
        
        # Check for achievements
        if is_correct:
            await self.db._award_achievement(user_id, "Trivia Master")
            
        return {
            'success': True,
            'correct': is_correct,
            'correct_answer': correct_answer,
            'user_answer': user_answer,
            'points': points if is_correct else 0,
            'message': (
                f"✅ Correct! You earned {points} points!" 
                if is_correct 
                else f"❌ Incorrect! The correct answer was: {correct_answer}"
            )
        }

# Main Bot Class
class MBolacryptobot:
    def _safe_format(self, message, **kwargs):
        """Safely format a message, handling potential backslashes in values."""
        safe_kwargs = {}
        for key, value in kwargs.items():
            if isinstance(value, str):
                safe_kwargs[key] = value.replace('\\', '\\\\')
            else:
                safe_kwargs[key] = value
        return message.format(**safe_kwargs)

    def get_uptime(self) -> str:
        """Get the bot's uptime as a formatted string."""
        if not hasattr(self, '_start_time'):
            self._start_time = datetime.now()
        
        uptime = datetime.now() - self._start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0 and days == 0:  # Only show minutes if less than a day
            parts.append(f"{minutes}m")
        if len(parts) < 2:  # If we only have one time unit, add seconds for more precision
            parts.append(f"{seconds}s")
        
        return " ".join(parts[:2])  # Return max 2 time units for brevity
    
    def __init__(self, token: str):
        self.token = token
        self.db = Database()
        self.application = Application.builder().token(token).build()
        
        # Initialize other components
        self.ai_moderation = AIModeration()
        self.anti_raid = AntiRaid(self.db)
        self.reputation_system = ReputationSystem(self.db)
        self.game_system = GameSystem(self.db)
        
        # Register handlers
        self._register_handlers()
        
        # Add new chat members handler
        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self._handle_new_chat_members)
        )
        
        # Schedule tasks
        self._schedule_tasks()

    async def _handle_new_chat_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle when the bot is added to a group."""
        # Check if the bot itself was added to the group
        new_members = update.message.new_chat_members
        bot_id = context.bot.id
        
        for member in new_members:
            if member.id == bot_id:
                # Bot was added to the group
                chat = update.effective_chat
                user = update.effective_user
                
                # Make the user who added the bot an admin
                await self.db.add_group_admin(chat.id, user.id)
                
                # Send welcome message
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=f"👋 Thanks for adding me to the group, {user.mention_html()}! "
                        f"You've been granted admin privileges for this group. "
                        f"Use /help to see available commands."
                )
                break

    async def add_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Add a user as admin for the current group."""
        try:
            # Check if the user has permission to add admins
            if not await self.check_admin(update, context):
                await update.message.reply_text("❌ You need to be an admin to use this command.")
                return
            
            # Check if the command is used in a group
            if update.effective_chat.type == 'private':
                await update.message.reply_text("This command can only be used in groups.")
                return
            
            # Check if replying to a message
            if not update.message.reply_to_message:
                await update.message.reply_text(
                    "Please reply to a user's message to make them an admin.\n"
                    "Example: Reply to someone's message with `/addadmin`"
                )
                return
            
            target_user = update.message.reply_to_message.from_user
            chat_id = update.effective_chat.id
            
            # Don't allow adding bots as admins
            if target_user.is_bot:
                await update.message.reply_text("❌ Bots cannot be made admins.")
                return
            
            # Add as group admin
            await self.db.add_group_admin(chat_id, target_user.id)
            
            await update.message.reply_text(
                f"✅ {target_user.mention_html()} has been added as a group admin!",
                parse_mode='HTML'
            )
        
        except Exception as e:
            logger.error(f"Error in add_admin: {e}")
            await update.message.reply_text("❌ An error occurred while processing your request.")

    async def check_admin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if the user has admin privileges in the current chat.
        
        Returns:
            bool: True if user is an admin, False otherwise
        """
        user = update.effective_user
        chat = update.effective_chat
        
        # In private chats, only global admins can use admin commands
        if chat.type == 'private':
            # If you still want some global admin functionality, implement is_global_admin
            # return await self.is_global_admin(user.id)
            return False  # Or handle private chat admin differently
        
        # In groups, check if user is a group admin
        is_admin = await self.db.is_group_admin(chat.id, user.id)
        
        # Also check if user is a Telegram group admin (optional but recommended)
        if not is_admin:
            try:
                member = await chat.get_member(user.id)
                is_admin = member.status in ['creator', 'administrator']
            except Exception as e:
                logger.error(f"Error checking admin status: {e}")
        
        return is_admin

    async def list_admins(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List all admins for the current group."""
        try:
            chat = update.effective_chat
            
            # Only works in groups
            if chat.type == 'private':
                await update.message.reply_text("This command can only be used in groups.")
                return
            
            # Get all group admins from the database
            admin_ids = await self.db.get_group_admins(chat.id)
            
            if not admin_ids:
                await update.message.reply_text("No admins found for this group.")
                return
            
            # Get admin usernames
            admins = []
            for user_id in admin_ids:
                try:
                    user = await context.bot.get_chat_member(chat.id, user_id)
                    name = user.user.mention_html() if user.user.username else user.user.full_name
                    admins.append(f"• {name}")
                except Exception as e:
                    logger.error(f"Error getting user info for {user_id}: {e}")
                    admins.append(f"• User ID: {user_id}")
            
            await update.message.reply_text(
                f"👑 <b>Group Admins</b>:\n\n" + "\n".join(admins),
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Error in list_admins: {e}")
            await update.message.reply_text("❌ An error occurred while fetching admin list.")

    def _schedule_tasks(self):
        """Schedule periodic tasks."""
        def cleanup_wrapper(context: ContextTypes.DEFAULT_TYPE):
            self.db.cleanup_anti_raid()
        
        # Remove any existing jobs to prevent duplicates
        for job in self.application.job_queue.jobs():
            if job.name == "cleanup_anti_raid":
                job.schedule_removal()
        
        # Schedule anti-raid cleanup every 5 minutes
        self.application.job_queue.run_repeating(
            cleanup_wrapper,
            interval=300,  # 5 minutes
            first=10,  # Start first job in 10 seconds
            name="cleanup_anti_raid"
        )
        
    def _register_handlers(self):
        """Register all handlers."""
        # Command handlers for groups
        group_handlers = [
            CommandHandler("start", self.start_cmd),
            CommandHandler("help", self.help_cmd),
            CommandHandler("profile", self.profile_cmd),
            CommandHandler("referral", self.referral_cmd),
            CommandHandler("leaderboard", self.leaderboard_cmd),
            CommandHandler("daily", self.daily_cmd),
            CommandHandler("rep", self.rep_cmd),
            CommandHandler("warn", self.warn_cmd),
            CommandHandler("mute", self.mute_cmd),
            CommandHandler("unmute", self.unmute_cmd),
            CommandHandler("ban", self.ban_cmd),
            CommandHandler("unban", self.unban_cmd),
            CommandHandler("settings", self.settings_cmd),
            CommandHandler("trivia", self.trivia_cmd),
            CommandHandler("answer", self.answer_cmd),
            CommandHandler("poll", self.poll_cmd),
            CommandHandler("vote", self.vote_cmd),
        ]
        
        # Add group handlers with filter
        for handler in group_handlers:
            self.application.add_handler(handler, group=1)  # Group 1 for group chats
        
        # Add DM-specific handlers
        dm_handlers = [
            CommandHandler("start", self.start_dm, filters.ChatType.PRIVATE),
            CommandHandler("help", self.help_dm, filters.ChatType.PRIVATE),
            CommandHandler("profile", self.profile_dm, filters.ChatType.PRIVATE),
            CommandHandler("referral", self.referral_dm, filters.ChatType.PRIVATE),
            CommandHandler("leaderboard", self.leaderboard_dm, filters.ChatType.PRIVATE),
            CommandHandler("daily", self.daily_dm, filters.ChatType.PRIVATE),
            CommandHandler("settings", self.settings_dm, filters.ChatType.PRIVATE),
        ]
        
        for handler in dm_handlers:
            self.application.add_handler(handler, group=2)  # Group 2 for DMs
        
        # Callback query handler (for buttons)
        self.application.add_handler(
            CallbackQueryHandler(self.button_callback), 
            group=3
        )
        
        # Message handlers
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message),
            group=4
        )
        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, self.new_member_handler),
            group=4
        )
        self.application.add_handler(
            MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, self.left_member_handler),
            group=4
        )
        self.application.add_handler(CommandHandler("addadmin", self.add_admin))
        self.application.add_handler(CommandHandler("admins", self.list_admins))
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
            # Add conversation handler for adding questions
        add_question_conv = ConversationHandler(
            entry_points=[CommandHandler('addquestion', self.add_question_cmd)],
            states={
                'AWAITING_QUESTION': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_question_text)],
                'AWAITING_CATEGORY': [CallbackQueryHandler(self.handle_category_selection)],
                'AWAITING_DIFFICULTY': [CallbackQueryHandler(self.handle_difficulty_selection)],
                'AWAITING_OPTIONS': [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_options)],
            },
            fallbacks=[
                CommandHandler('cancel', self.cancel_question_creation),
                CallbackQueryHandler(self.cancel_question_creation, pattern="^cancel$")
            ]
        )
        self.application.add_handler(add_question_conv)
        self.application.add_handler(CommandHandler("listquestions", self.list_questions_cmd))
        self.application.add_handler(CommandHandler("deletequestion", self.delete_question_cmd))
        self.application.add_handler(CommandHandler("triviastats", self.trivia_stats_cmd))

    async def show_dm_menu(self, update: Update, text: str = None) -> None:
        """Show the main menu in DMs with buttons."""
        keyboard = [
            [InlineKeyboardButton("👤 My Profile", callback_data="menu_profile")],
            [InlineKeyboardButton("📊 Leaderboard", callback_data="menu_leaderboard")],
            [InlineKeyboardButton("🎁 Daily Bonus", callback_data="claim_daily")],
            [InlineKeyboardButton("👥 Refer Friends", callback_data="menu_referral")],
            [InlineKeyboardButton("🎮 Play Trivia", callback_data="menu_trivia")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")],
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = text or "Welcome! What would you like to do?"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

    async def start_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command in private chat."""
        user = update.effective_user
        self.db.get_or_create_user(user)
        
        # Create welcome message
        bot_name = "MBolacryptobot"
        welcome_message = f"""👋 <b>Welcome to {bot_name}!</b>

    I'm your personal crypto assistant. Here's what I can do in private chat:

    - Answer your crypto questions
    - Provide market updates
    - Help with trading
    - Manage your account settings
    - And much more!

    Type /help to see all available commands.

    <i>Note: Some features may require you to interact with me in a group chat.</i>"""
        
        # Send welcome message
        await update.message.reply_text(
            welcome_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        
        # Add any additional DM-specific logic here
        # For example, you could:
        # - Show account status
        # - Display quick action buttons
        # - Check for new features or updates

    async def help_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show help message in DMs with buttons."""
        help_text = (
            "🤖 <b>Bot Help</b>\n\n"
            "Use the buttons below to navigate:\n"
            "• <b>Profile</b> - View your stats and achievements\n"
            "• <b>Leaderboard</b> - See top users\n"
            "• <b>Daily Bonus</b> - Claim your daily reward\n"
            "• <b>Refer Friends</b> - Get your referral link\n"
            "• <b>Play Trivia</b> - Test your knowledge\n"
            "• <b>Settings</b> - Configure your preferences"
        )
        await self.show_dm_menu(update, help_text)

    async def profile_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show user profile in DMs."""
        await self.show_user_profile(update, update.effective_user.id)

    async def referral_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show referral info in DMs."""
        user = update.effective_user
        bot_username = (await self.application.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user.id}"
        
        keyboard = [
            [InlineKeyboardButton("📤 Share Referral Link", 
                            url=f"https://t.me/share/url?url={referral_link}&text=Join%20me%20on%20this%20awesome%20bot!")],
            [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
        ]
        
        await update.message.reply_text(
            f"👥 <b>Refer Friends & Earn Points</b>\n\n"
            f"Invite your friends using this link:\n"
            f"<code>{referral_link}</code>\n\n"
            "You'll earn points for each friend who joins!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    async def leaderboard_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show leaderboard in DMs."""
        await self.show_leaderboard(update)

    async def daily_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle daily bonus in DMs."""
        result = await self.db.get_daily_bonus(update.effective_user.id)
        
        if result['success']:
            text = (
                f"🎉 <b>Daily Bonus Claimed!</b>\n\n"
                f"• Points earned: {result['points']}\n"
                f"• Streak: {result['streak']} days\n"
                f"• Next bonus in: 24 hours"
            )
        else:
            hours_left = result.get('hours_until_next', 24)
            text = (
                f"⏳ <b>Bonus Already Claimed</b>\n\n"
                f"Come back in {hours_left} hours to claim your next daily bonus!\n"
                f"Current streak: {result.get('streak', 0)} days"
            )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="menu_main")]
        ]
        
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    async def settings_dm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show settings in DMs."""
        user_settings = await self.db.get_user_settings(update.effective_user.id)
        
        settings_text = "⚙️ <b>Your Settings</b>\n\n"
        
        keyboard = []
        for setting, value in user_settings.items():
            if isinstance(value, bool):
                icon = "✅" if value else "❌"
                keyboard.append([
                    InlineKeyboardButton(
                        f"{icon} {setting.replace('_', ' ').title()}",
                        callback_data=f"toggle_setting_{setting}"
                    )
                ])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
        
        await update.message.reply_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    async def show_main_menu(self, update: Update, text: str = None) -> None:
        """Show the main menu."""
        keyboard = [
            [InlineKeyboardButton("🎮 Play Trivia", callback_data="menu_trivia")],
            [InlineKeyboardButton("📊 My Profile", callback_data="menu_profile")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="menu_leaderboard")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = text or "What would you like to do?"
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

    async def show_back_button(self, update: Update, text: str, back_to: str = "menu_main") -> None:
        """Show a message with a back button."""
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=back_to)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

    async def start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command with button menu."""
        user = update.effective_user
        chat = update.effective_chat
        self.db.get_or_create_user(user)
        
        # Handle referral if any
        if context.args:
            try:
                referrer_id = int(context.args[0])
                if referrer_id != user.id:  # Prevent self-referral
                    # Handle the referral logic here
                    pass
            except (ValueError, IndexError):
                pass  # This handles invalid referral IDs
        
        # Use a static bot name for now
        bot_name = "MBolacryptobot"
        welcome_message = f"""👋 <b>Welcome to {bot_name}!</b>

    I'm your personal crypto assistant. Here's what I can do:

    - Answer your crypto questions
    - Provide market updates
    - Help with trading
    - And much more!

    Type /help to see all available commands."""
        
        await update.message.reply_text(
            welcome_message,
            parse_mode='HTML',
            disable_web_page_preview=True
        )

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show help message with available commands."""
        help_text = (
            "🤖 <b>Quiz Bot Help</b>\n\n"
            "Welcome to the Quiz Bot! Here's how to use me:\n\n"
            "🎮 <b>Play Trivia</b> - Test your knowledge in various categories\n"
            "📊 <b>My Profile</b> - View your stats and achievements\n"
            "🏆 <b>Leaderboard</b> - See who's on top\n"
            "⚙️ <b>Settings</b> - Configure your preferences\n\n"
            "Just use the buttons below to get started!"
        )
        
        # Reuse the main menu keyboard
        await self.show_main_menu(update, help_text)

    async def profile_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /profile command."""
        user = update.effective_user
        target_user = user
        
        # Check if replying to a message or mentioned a user
        if update.message.reply_to_message:
            target_user = update.message.reply_to_message.from_user
        elif context.args:
            try:
                # Try to parse user ID or username from arguments
                username = context.args[0].lstrip('@')
                # In a real implementation, you would look up the user
                # For now, we'll just show the sender's profile
                pass
            except (ValueError, IndexError):
                pass
                
        # Get user data
        user_data = self.db.get_or_create_user(target_user)
        stats = await self.db.get_user_stats(target_user.id, update.effective_chat.id)
        
        # Format profile
        profile_text = f"""
            <b>👤 {target_user.full_name}</b> ({f'@{target_user.username}' if target_user.username else 'No username'})

            <b>🏆 Points:</b> {user_data.get('points', 0)}
            <b>⭐ Reputation:</b> {await self.reputation_system.get_reputation(target_user.id, update.effective_chat.id)}
            <b>📊 Referrals:</b> {stats.get('referrals', 0)}
            <b>🏅 Achievements:</b> {stats.get('achievements', 0)}
            <b>🔥 Streak:</b> {stats.get('daily_streak', 0)} days
            """

            # Add warning count for moderators
        if await self.is_admin(update.effective_user.id, update.effective_chat.id):
            profile_text += f"\n<b>⚠️ Warnings:</b> {user_data.get('warnings', 0)}/{MAX_WARNINGS}"
            
        # Add referral link for own profile
        if target_user.id == user.id:
            bot_username = (await self.application.bot.get_me()).username
            profile_text += f"\n<b>🔗 Referral Link:</b> https://t.me/{bot_username}?start={user.id}"
            
        await update.message.reply_text(profile_text, parse_mode=ParseMode.HTML)

    async def show_user_profile(self, update: Update, user_id: int, refresh: bool = False) -> None:
        """Show user profile with stats and options."""
        try:
            query = update.callback_query
            if query:
                await query.answer()
            
            # Get user data
            user_data = await self.db.get_user_data(user_id)
            if not user_data:
                await self.show_back_button(update, "❌ User not found.")
                return
                
            # Get user stats
            stats = await self.db.get_user_stats(user_id)
            
            # Format join date
            join_date = datetime.fromisoformat(user_data['join_date'])
            days_since_join = (datetime.now() - join_date).days
            
            # Create profile text
            profile_text = (
                f"👤 <b>User Profile</b>\n\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
                f"👋 <b>Name:</b> {user_data.get('first_name', 'Unknown')}\n"
                f"📅 <b>Member since:</b> {join_date.strftime('%Y-%m-%d')} ({days_since_join} days ago)\n\n"
                f"🏆 <b>Points:</b> {stats.get('points', 0):,}\n"
                f"⭐ <b>Reputation:</b> {stats.get('reputation', 0):,}\n"
                f"🎮 <b>Games played:</b> {stats.get('games_played', 0):,}\n"
                f"✅ <b>Correct answers:</b> {stats.get('correct_answers', 0):,}\n"
                f"👥 <b>Referrals:</b> {stats.get('referrals', 0):,}\n"
            )
            
            # Add achievements if any
            achievements = await self.db.get_user_achievements(user_id)
            if achievements:
                profile_text += "\n🏅 <b>Achievements:</b>\n"
                for i, ach in enumerate(achievements[:3], 1):  # Show top 3
                    profile_text += f"{i}. {ach['name']} - {ach['description']}\n"
                if len(achievements) > 3:
                    profile_text += f"... and {len(achievements) - 3} more\n"
            
            # Create buttons
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_profile")],
                [InlineKeyboardButton("🎁 Daily Bonus", callback_data="claim_daily")],
                [InlineKeyboardButton("👥 Referrals", callback_data="show_referral")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="menu_leaderboard")],
                [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
            ]
            
            # Edit or send new message
            if query:
                await query.edit_message_text(
                    profile_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    profile_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Error showing user profile: {e}")
            error_text = "❌ An error occurred while loading the profile."
            if query:
                await query.edit_message_text(
                    error_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                    ])
                )
            else:
                await update.message.reply_text(error_text)

    async def referral_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /referral command."""
        user = update.effective_user
        user_data = await self.db.get_or_create_user(user)
        
        bot_username = (await self.application.bot.get_me()).username
        referral_link = f"https://t.me/{bot_username}?start={user.id}"
        
        await update.message.reply_text(
            f"🔗 <b>Your Referral Link:</b>\n\n"
            f"<code>{referral_link}</code>\n\n"
            "Share this link with friends to earn points when they join using it!",
            parse_mode=ParseMode.HTML
        )

    async def leaderboard_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /leaderboard command."""
        chat_id = update.effective_chat.id
        leaderboard = await self.db.get_leaderboard(chat_id, limit=10)
        
        if not leaderboard:
            await update.message.reply_text("No leaderboard data available yet.")
            return
            
        leaderboard_text = "<b>🏆 Leaderboard</b>\n\n"
        
        for i, user in enumerate(leaderboard, 1):
            name = user.get('username')
            if not name:
                name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                if not name:
                    name = f"User #{user['user_id']}"
                else:
                    name = f"<i>{name}</i>"
            else:
                name = f"@{name}"
                
            leaderboard_text += (
                f"{i}. {name} - {user.get('points', 0)} points "
                f"(+{user.get('referrals', 0)} refs)\n"
            )
                
            leaderboard_text += (
                f"{i}. {name} - {user.get('points', 0)} points "
                f"(+{user.get('referrals', 0)} refs)\n"
            )
            
        await update.message.reply_text(leaderboard_text, parse_mode=ParseMode.HTML)

    async def show_leaderboard(self, update: Update, page: int = 1) -> None:
        """Show the leaderboard with pagination."""
        query = update.callback_query
        if query:
            await query.answer()
        
        try:
            items_per_page = 10
            offset = (page - 1) * items_per_page
            
            # Get leaderboard data
            leaderboard = await self.db.get_leaderboard(
                limit=items_per_page,
                offset=offset
            )
            
            if not leaderboard:
                await self.show_back_button(update, "No leaderboard data available.")
                return
                
            # Get current user's position
            user_position = await self.db.get_user_rank(update.effective_user.id)
            
            # Build leaderboard text
            leaderboard_text = "🏆 <b>Leaderboard</b> 🏆\n\n"
            
            # Add top 3 with medals
            medals = ["🥇", "🥈", "🥉"]
            for i, user in enumerate(leaderboard[:3], 1):
                leaderboard_text += f"{medals[i-1]} <b>{user['name']}</b> - {user['points']:,} points\n"
            
            # Add rest of the users
            for i, user in enumerate(leaderboard[3:], 4):
                leaderboard_text += f"{i}. {user['name']} - {user['points']:,} points\n"
            
            # Add user's position if not in top 10
            if user_position and user_position > 10:
                leaderboard_text += f"\n...\n{user_position}. You - {user_position['points']:,} points"
            
            # Create pagination buttons
            keyboard = []
            
            # Navigation buttons
            nav_buttons = []
            if page > 1:
                nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"leaderboard_{page-1}"))
            
            nav_buttons.append(InlineKeyboardButton(f"Page {page}", callback_data="noop"))
            
            # Check if there are more pages
            if len(leaderboard) == items_per_page:
                nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"leaderboard_{page+1}"))
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Add time period buttons
            time_buttons = [
                InlineKeyboardButton("🏆 All Time", callback_data="leaderboard_all"),
                InlineKeyboardButton("📅 This Month", callback_data="leaderboard_month"),
                InlineKeyboardButton("📊 Today", callback_data="leaderboard_today")
            ]
            keyboard.append(time_buttons)
            
            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
            
            # Edit or send message
            if query:
                await query.edit_message_text(
                    leaderboard_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    leaderboard_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Error showing leaderboard: {e}")
            error_text = "❌ An error occurred while loading the leaderboard."
            if query:
                await query.edit_message_text(
                    error_text,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                    ])
                )
            else:
                await update.message.reply_text(error_text)

    async def daily_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /daily command."""
        user = update.effective_user
        result = await self.db.get_daily_bonus(user.id)
        
        await update.message.reply_text(
            result['message'],
            parse_mode=ParseMode.HTML
        )

    async def rep_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /rep command to give reputation."""
        if not update.message.reply_to_message:
            await update.message.reply_text("Please reply to a user's message to give them reputation.")
            return
            
        from_user = update.effective_user
        to_user = update.message.reply_to_message.from_user
        
        if from_user.id == to_user.id:
            await update.message.reply_text("You can't give reputation to yourself!")
            return
            
        result = await self.reputation_system.add_reputation(
            from_user.id,
            to_user.id,
            update.effective_chat.id
        )
        
        await update.message.reply_text(result['message'])

    async def warn_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /warn command."""
        # Check if user has permission
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text("Please reply to a user's message to warn them.")
            return
            
        target_user = update.effective_user
        admin_user = update.effective_user
        
        # Don't allow warning bots or admins
        if target_user.is_bot:
            await update.message.reply_text("You can't warn bots.")
            return
            
        if await self.is_admin(target_user.id, update.effective_chat.id):
            await update.message.reply_text("You can't warn other admins.")
            return
            
        # Get reason from command arguments
        reason = ' '.join(context.args) if context.args else "No reason provided"
        
        # Add warning
        result = await self.db.add_warning(
            target_user.id,
            admin_user.id,
            update.effective_chat.id,
            reason
        )
        
        # Format response
        response = (
            f"⚠️ <b>User warned:</b> {target_user.mention_html()}\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Warnings:</b> {result['warnings']}/{result['warn_limit']}"
        )
        
        if result['warnings'] >= result['warn_limit']:
            # Mute or ban user based on settings
            if result['action'] == 'mute':
                # Mute the user
                mute_duration = 3600  # 1 hour
                until_date = int((datetime.now() + timedelta(seconds=mute_duration)).timestamp())
                
                try:
                    await self.application.bot.restrict_chat_member(
                        chat_id=update.effective_chat.id,
                        user_id=target_user.id,
                        permissions=ChatPermissions(
                            can_send_messages=False,
                            can_send_media_messages=False,
                            can_send_other_messages=False,
                            can_add_web_page_previews=False
                        ),
                        until_date=until_date
                    )
                    
                    response += (
                        f"\n\n🚫 User has been muted for reaching the warning limit. "
                        f"Mute will expire in 1 hour."
                    )
                    
                    # Update user's mute status in database
                    await self.db.execute(
                        """
                        UPDATE users 
                        SET is_muted = 1, 
                            mute_until = datetime('now', '+1 hour') 
                        WHERE user_id = ?
                        """,
                        (target_user.id,)
                    )
                    
                except TelegramError as e:
                    logger.error(f"Error muting user: {e}")
                    response += "\n\n⚠️ Failed to mute user. Please check bot permissions."
            
            elif result['action'] == 'ban':
                # Ban the user
                try:
                    await self.application.bot.ban_chat_member(
                        chat_id=update.effective_chat.id,
                        user_id=target_user.id
                    )
                    
                    response += "\n\n🔨 User has been banned for reaching the warning limit."
                    
                    # Update user's ban status in database
                    await self.db.execute(
                        "UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?",
                        ("Reached warning limit", target_user.id)
                    )
                    
                except TelegramError as e:
                    logger.error(f"Error banning user: {e}")
                    response += "\n\n⚠️ Failed to ban user. Please check bot permissions."
        
        await update.message.reply_text(response, parse_mode=ParseMode.HTML)

    async def mute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /mute command."""
        # Check permissions
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text("Please reply to a user's message to mute them.")
            return
            
        target_user = update.effective_user
        admin_user = update.effective_user
        
        # Don't allow muting bots or admins
        if target_user.is_bot:
            await update.message.reply_text("You can't mute bots.")
            return
            
        if await self.is_admin(target_user.id, update.effective_chat.id):
            await update.message.reply_text("You can't mute other admins.")
            return
            
        # Parse duration (default: 1 hour)
        duration = 3600  # 1 hour in seconds
        reason = "No reason provided"
        
        if context.args:
            try:
                # Try to parse duration (e.g., "1h", "30m", "1d")
                duration_str = context.args[0].lower()
                if duration_str.endswith('m'):
                    duration = int(duration_str[:-1]) * 60
                elif duration_str.endswith('h'):
                    duration = int(duration_str[:-1]) * 3600
                elif duration_str.endswith('d'):
                    duration = int(duration_str[:-1]) * 86400
                else:
                    duration = int(duration_str) * 60  # Default to minutes
                    
                # Get reason from remaining arguments
                if len(context.args) > 1:
                    reason = ' '.join(context.args[1:])
            except (ValueError, IndexError):
                # If parsing fails, treat all arguments as reason
                reason = ' '.join(context.args)
        
        # Mute the user
        until_date = int((datetime.now() + timedelta(seconds=duration)).timestamp())
        
        try:
            await self.application.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )
            
            # Update user's mute status in database
            await self.db.execute(
                """
                UPDATE users 
                SET is_muted = 1, 
                    mute_until = datetime('now', ? || ' seconds') 
                WHERE user_id = ?
                """,
                (duration, target_user.id)
            )
            
            # Format duration for display
            if duration < 60:
                duration_str = f"{duration} seconds"
            elif duration < 3600:
                minutes = duration // 60
                duration_str = f"{minutes} minute{'s' if minutes > 1 else ''}"
            elif duration < 86400:
                hours = duration // 3600
                duration_str = f"{hours} hour{'s' if hours > 1 else ''}"
            else:
                days = duration // 86400
                duration_str = f"{days} day{'s' if days > 1 else ''}"
            
            await update.message.reply_text(
                f"🔇 {target_user.mention_html()} has been muted for {duration_str}.\n"
                f"<b>Reason:</b> {reason}",
                parse_mode=ParseMode.HTML
            )
            
        except TelegramError as e:
            logger.error(f"Error muting user: {e}")
            await update.message.reply_text(
                "Failed to mute user. Please check bot permissions."
            )

    async def unmute_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unmute command."""
        # Check permissions
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text("Please reply to a user's message to unmute them.")
            return
            
        target_user = update.effective_user
        
        # Unmute the user
        try:
            # Restore default permissions
            await self.application.bot.restrict_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True
                )
            )
            
            # Update user's mute status in database
            await self.db.execute(
                "UPDATE users SET is_muted = 0, mute_until = NULL WHERE user_id = ?",
                (target_user.id,)
            )
            
            await update.message.reply_text(
                f"🔊 {target_user.mention_html()} has been unmuted.",
                parse_mode=ParseMode.HTML
            )
            
        except TelegramError as e:
            logger.error(f"Error unmuting user: {e}")
            await update.message.reply_text(
                "Failed to unmute user. Please check bot permissions."
            )

    async def ban_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ban command."""
        # Check permissions
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Check if replying to a message
        if not update.message.reply_to_message:
            await update.message.reply_text("Please reply to a user's message to ban them.")
            return
            
        target_user = update.effective_user
        admin_user = update.effective_user
        
        # Don't allow banning bots or admins
        if target_user.is_bot:
            await update.message.reply_text("You can't ban bots.")
            return
            
        if await self.is_admin(target_user.id, update.effective_chat.id):
            await update.message.reply_text("You can't ban other admins.")
            return
            
        # Get reason from command arguments
        reason = ' '.join(context.args) if context.args else "No reason provided"
        
        try:
            # Ban the user
            await self.application.bot.ban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=target_user.id
            )
            
            # Update user's ban status in database
            await self.db.execute(
                "UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?",
                (reason, target_user.id)
            )
            
            await update.message.reply_text(
                f"🔨 {target_user.mention_html()} has been banned.\n"
                f"<b>Reason:</b> {reason}",
                parse_mode=ParseMode.HTML
            )
            
        except TelegramError as e:
            logger.error(f"Error banning user: {e}")
            await update.message.reply_text(
                "Failed to ban user. Please check bot permissions."
            )

    async def unban_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /unban command."""
        # Check permissions
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Check if a user ID or username was provided
        if not context.args:
            await update.message.reply_text("Please provide a user ID or username to unban.")
            return
            
        target = ' '.join(context.args)
        
        try:
            # Try to parse as user ID
            if target.isdigit():
                user_id = int(target)
            else:
                # Try to get user by username (without @)
                username = target.lstrip('@')
                user = await self.db.fetch_one(
                    "SELECT user_id FROM users WHERE username = ?",
                    (username,)
                )
                if not user:
                    await update.message.reply_text("User not found.")
                    return
                user_id = user['user_id']
            
            # Unban the user
            await self.application.bot.unban_chat_member(
                chat_id=update.effective_chat.id,
                user_id=user_id
            )
            
            # Update user's ban status in database
            await self.db.execute(
                "UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?",
                (user_id,)
            )
            
            await update.message.reply_text(
                f"✅ User ID {user_id} has been unbanned."
            )
            
        except TelegramError as e:
            logger.error(f"Error unbanning user: {e}")
            await update.message.reply_text(
                "Failed to unban user. Please check the user ID and try again."
            )

    async def settings_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /settings command."""
        # Check permissions
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to use this command.")
            return
            
        # Get current settings
        settings = await self.db.get_chat_settings(update.effective_chat.id)
        
        # Create keyboard with settings options
        keyboard = [
            [
                InlineKeyboardButton(
                    f"🛡️ Anti-Spam: {'ON' if settings.get('antispam_enabled', True) else 'OFF'}",
                    callback_data="toggle_antispam"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🌊 Anti-Flood: {'ON' if settings.get('antiflood_enabled', True) else 'OFF'}",
                    callback_data="toggle_antiflood"
                )
            ],
            [
                InlineKeyboardButton(
                    "📝 Edit Welcome Message",
                    callback_data="edit_welcome"
                )
            ],
            [
                InlineKeyboardButton(
                    "📝 Edit Goodbye Message",
                    callback_data="edit_goodbye"
                )
            ],
            [
                InlineKeyboardButton(
                    "📜 Edit Rules",
                    callback_data="edit_rules"
                )
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "⚙️ <b>Group Settings</b>\n\n"
            "Use the buttons below to configure the bot for this group:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

    async def start_trivia(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category: str) -> None:
        """Start a trivia game with the selected category."""
        query = update.callback_query
        user = update.effective_user
        
        # Get a question from your question database
        result = await self.game_system.start_trivia(
            update.effective_chat.id,
            user.id,
            category=category,
            difficulty="medium"
        )
        
        if not result['success']:
            await self.show_back_button(
                update,
                f"❌ {result.get('message', 'Failed to start trivia.')}",
                "menu_trivia"
            )
            return
        
        # Create answer buttons
        keyboard = []
        for i, option in enumerate(result['options']):
            keyboard.append([InlineKeyboardButton(
                option,
                callback_data=f"trivia_answer_{result['game_id']}_{i}"
            )])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_trivia")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"🎲 <b>Trivia Time!</b> ({result['difficulty'].title()})\n\n"
            f"<b>Category:</b> {result['category']}\n"
            f"<b>Question:</b> {result['question']}\n\n"
            "Select an answer:",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )

    async def start_trivia_game(self, update: Update, context: ContextTypes.DEFAULT_TYPE, category_id: str) -> None:
        """Start a new trivia game with the specified category."""
        query = update.callback_query
        user = update.effective_user
        
        try:
            # Get a new trivia question
            question_data = await self.db.get_trivia_question(category_id)
            if not question_data:
                await query.answer("No questions available in this category. Please try another one.", show_alert=True)
                return
                
            # Store the game state
            game_id = f"trivia_{user.id}_{int(datetime.now().timestamp())}"
            game_state = {
                'game_id': game_id,
                'user_id': user.id,
                'question_id': question_data['id'],
                'category': question_data['category'],
                'question': question_data['question'],
                'correct_answer': question_data['correct_answer'],
                'options': question_data['options'],
                'start_time': datetime.now().isoformat(),
                'answered': False
            }
            
            # Save game state (you'll need to implement this in your database class)
            await self.db.save_game_state(game_state)
            
            # Create answer buttons
            keyboard = []
            for i, option in enumerate(question_data['options']):
                callback_data = f"answer_{game_id}_{question_data['id']}_{i}"
                keyboard.append([InlineKeyboardButton(option, callback_data=callback_data)])
            
            # Add a cancel button
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_trivia")])
            
            # Send the question
            await query.edit_message_text(
                f"❓ <b>Question:</b> {question_data['question']}\n\n"
                f"📚 <b>Category:</b> {question_data['category']}\n"
                f"⏱ <b>Time to answer:</b> 30 seconds",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
            # Schedule timeout
            context.job_queue.run_once(
                self.trivia_timeout,
                30,  # 30 seconds to answer
                data={'game_id': game_id, 'chat_id': query.message.chat_id, 'message_id': query.message.message_id}
            )
            
        except Exception as e:
            logger.error(f"Error starting trivia game: {e}")
            await query.answer("An error occurred while starting the game. Please try again.", show_alert=True)

    async def trivia_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /trivia command to start a new trivia game."""
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("Only admins can start trivia games.")
            return
        
        # Check if there are any questions in this chat
        question = await self.db.get_trivia_question(update.effective_chat.id)
        if not question:
            await update.message.reply_text(
                "No trivia questions found for this chat. "
                "Use /addquestion to add some questions first."
            )
            return
        
        # Create a new game
        game_id = f"trivia_{update.effective_chat.id}_{int(time.time())}"
        success = await self.db.save_game_state(
            game_id=game_id,
            chat_id=update.effective_chat.id,
            question_id=question['id']
        )
        
        if not success:
            await update.message.reply_text("Failed to start trivia game. Please try again.")
            return
        
        # Format options with numbers
        options_text = "\n".join(
            f"{i+1}. {opt['text']}" 
            for i, opt in enumerate(question['options'])
        )
        
        # Send the question
        message = await update.message.reply_text(
            f"🎲 <b>Trivia Time!</b>\n\n"
            f"📝 <b>Question:</b> {question['question']}\n\n"
            f"{options_text}\n\n"
            f"📚 <b>Category:</b> {question['category']}\n"
            f"📊 <b>Difficulty:</b> {question['difficulty'].capitalize()}\n\n"
            "Reply with the number of your answer!",
            parse_mode=ParseMode.HTML
        )
        
        # Store message ID for later updates
        context.user_data['trivia_message_id'] = message.message_id
        context.user_data['current_game_id'] = game_id
        context.user_data['current_question'] = question
        context.user_data['start_time'] = time.time()
        
        # Schedule timeout
        context.job_queue.run_once(
            self.trivia_timeout,
            30,  # 30 seconds to answer
            context=update.effective_chat.id
        )
        
        return 'AWAITING_ANSWER'

    async def process_trivia_answer(self, update: Update, game_id: str, question_id: int, selected_option: int) -> None:
        """Process a user's answer to a trivia question."""
        query = update.callback_query
        user = update.effective_user
        
        try:
            # Get game state
            game_state = await self.db.get_game_state(game_id)
            if not game_state or game_state['answered']:
                await query.answer("This question has already been answered or has expired.", show_alert=True)
                return
                
            if game_state['user_id'] != user.id:
                await query.answer("This is not your game!", show_alert=True)
                return
                
            # Mark as answered to prevent double answers
            game_state['answered'] = True
            await self.db.update_game_state(game_id, {'answered': True})
            
            # Check if answer is correct
            is_correct = (game_state['options'][selected_option] == game_state['correct_answer'])
            points_earned = 0
            
            if is_correct:
                # Calculate points based on time taken
                time_taken = (datetime.now() - datetime.fromisoformat(game_state['start_time'])).total_seconds()
                points_earned = max(1, 10 - int(time_taken // 3))  # Faster answers get more points
                
                # Update user's score
                await self.db.update_user_score(user.id, points_earned)
                
                # Check for achievements
                await self.check_achievements(user.id, 'trivia_correct')
            
            # Update game stats
            await self.db.update_game_stats(
                user.id,
                is_correct=is_correct,
                category=game_state['category']
            )
            
            # Prepare result message
            result_message = (
                f"✅ <b>Correct!</b> (+{points_earned} points)\n\n"
                if is_correct else
                "❌ <b>Incorrect!</b>\n\n"
            )
            
            result_message += (
                f"<b>Question:</b> {game_state['question']}\n"
                f"<b>Your answer:</b> {game_state['options'][selected_option]}\n"
            )
            
            if not is_correct:
                result_message += f"<b>Correct answer:</b> {game_state['correct_answer']}\n"
            
            # Create keyboard for next question or return to menu
            keyboard = [
                [InlineKeyboardButton("➡️ Next Question", callback_data=f"next_question_{game_state['category']}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
            ]
            
            await query.edit_message_text(
                result_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error processing trivia answer: {e}")
            await query.answer("An error occurred. Please try again.", show_alert=True)

    async def trivia_timeout(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle trivia question timeout."""
        job = context.job
        game_id = job.data['game_id']
        
        try:
            # Get game state
            game_state = await self.db.get_game_state(game_id)
            if not game_state or game_state['answered']:
                return
                
            # Mark as answered
            await self.db.update_game_state(game_id, {'answered': True})
            
            # Send timeout message
            await context.bot.edit_message_text(
                chat_id=job.data['chat_id'],
                message_id=job.data['message_id'],
                text=(
                    f"⏱ <b>Time's up!</b>\n\n"
                    f"<b>Question:</b> {game_state['question']}\n"
                    f"<b>Correct answer:</b> {game_state['correct_answer']}\n\n"
                    "Click the button below to continue:"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ Next Question", 
                        callback_data=f"next_question_{game_state['category']}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="menu_main")]
                ]),
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error in trivia timeout: {e}")

    async def show_next_question(self, update: Update, category: str) -> None:
        """Show the next question in the current trivia session."""
        query = update.callback_query
        await query.answer()
        
        try:
            # Get a new question in the same category
            question_data = await self.db.get_trivia_question(category)
            if not question_data:
                await query.answer("No more questions available in this category.", show_alert=True)
                await self.show_main_menu(update)
                return
                
            user = update.effective_user
            game_id = f"trivia_{user.id}_{int(datetime.now().timestamp())}"
            
            # Store the game state
            game_state = {
                'game_id': game_id,
                'user_id': user.id,
                'question_id': question_data['id'],
                'category': question_data['category'],
                'question': question_data['question'],
                'correct_answer': question_data['correct_answer'],
                'options': question_data['options'],
                'start_time': datetime.now().isoformat(),
                'answered': False
            }
            
            await self.db.save_game_state(game_state)
            
            # Create answer buttons
            keyboard = []
            for i, option in enumerate(question_data['options']):
                callback_data = f"answer_{game_id}_{question_data['id']}_{i}"
                keyboard.append([InlineKeyboardButton(option, callback_data=callback_data)])
            
            # Add a cancel button
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="menu_trivia")])
            
            # Send the question
            await query.edit_message_text(
                f"❓ <b>Question:</b> {question_data['question']}\n\n"
                f"📚 <b>Category:</b> {question_data['category']}\n"
                f"⏱ <b>Time to answer:</b> 30 seconds",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            
            # Schedule timeout
            context = query.message._bot_data['context']
            context.job_queue.run_once(
                self.trivia_timeout,
                30,  # 30 seconds to answer
                data={'game_id': game_id, 'chat_id': query.message.chat_id, 'message_id': query.message.message_id}
            )
            
        except Exception as e:
            logger.error(f"Error showing next question: {e}")
            await query.answer("An error occurred. Please try again.", show_alert=True)
            await self.show_main_menu(update)

    async def answer_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /answer command (for text-based answers)."""
        # Check if in a private chat
        if update.effective_chat.type != ChatType.PRIVATE:
            await update.message.reply_text(
                "Please answer in a private chat with the bot."
            )
            return
            
        # Get the answer from the message text
        if not context.args:
            await update.message.reply_text(
                "Please provide an answer. Example: /answer 2"
            )
            return
            
        try:
            answer = int(context.args[0]) - 1  # Convert to 0-based index
            if answer < 0:
                raise ValueError
                
            # Get user's active games
            games = await self.db.get_active_games(
                update.effective_chat.id,
                game_type="trivia"
            )
            
            if not games:
                await update.message.reply_text(
                    "You don't have any active trivia games. Start one with /trivia"
                )
                return
                
            # Process answer for the most recent game
            game = games[0]
            result = await self.game_system.process_trivia_answer(
                game['id'],
                update.effective_user.id,
                answer
            )
            
            if not result['success']:
                safe_message = str(result.get('message', 'An error occurred')).replace('\\', '\\\\')
                await update.message.reply_text(
                    text=f"❌ {safe_message}",
                    parse_mode=ParseMode.HTML
                )
                return
                
            # Send result
            if result['correct']:
                await update.message.reply_text(
                    f"✅ {result['message']}\n"
                    f"🎉 You earned {result['points']} points!",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"❌ {result['message']}",
                    parse_mode=ParseMode.HTML
                )
                
        except (ValueError, IndexError):
            await update.message.reply_text(
                "Please provide a valid answer number. Example: /answer 2"
            )

            # Add these methods to the MBolacryptobot class

    async def add_question_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /addquestion command to start the question creation process."""
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("Only admins can add questions.")
            return
            
        # Store the chat_id and user_id in context for the conversation
        context.user_data['add_question'] = {
            'chat_id': update.effective_chat.id,
            'created_by': update.effective_user.id,
            'step': 'awaiting_question'
        }
        
        await update.message.reply_text(
            "📝 Let's add a new trivia question!\n\n"
            "Please send me the question text:"
        )
        return 'AWAITING_QUESTION'

    async def handle_question_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the question text input."""
        question_data = context.user_data.get('add_question', {})
        if not question_data or question_data.get('step') != 'awaiting_question':
            return await self.cancel_question_creation(update, context)
        
        question_text = update.message.text
        if len(question_text) > 255:
            await update.message.reply_text(
                "Question is too long. Please keep it under 255 characters."
            )
            return 'AWAITING_QUESTION'
        
        context.user_data['add_question'].update({
            'question_text': question_text,
            'step': 'awaiting_category'
        })
        
        # Show category keyboard
        keyboard = [
            [InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] 
            for cat in ["General", "Science", "History", "Movies", "Music", "Sports"]
        ]
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
        
        await update.message.reply_text(
            "📚 Select a category for this question:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return 'AWAITING_CATEGORY'

    async def handle_category_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle category selection from inline keyboard."""
        query = update.callback_query
        await query.answer()
        
        if query.data == "cancel":
            return await self.cancel_question_creation(update, context)
        
        if not query.data.startswith("cat_"):
            return
        
        category = query.data[4:]  # Remove 'cat_' prefix
        context.user_data['add_question'].update({
            'category': category,
            'step': 'awaiting_difficulty'
        })
        
        # Show difficulty keyboard
        keyboard = [
            [InlineKeyboardButton(diff, callback_data=f"diff_{diff.lower()}")] 
            for diff in ["Easy", "Medium", "Hard"]
        ]
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel")])
        
        await query.edit_message_text(
            f"📊 Selected category: {category}\n\n"
            "How difficult is this question?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return 'AWAITING_DIFFICULTY'

    async def handle_difficulty_selection(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle difficulty selection and start collecting options."""
        query = update.callback_query
        await query.answer()
        
        if query.data == "cancel":
            return await self.cancel_question_creation(update, context)
        
        if not query.data.startswith("diff_"):
            return
        
        difficulty = query.data[5:]  # Remove 'diff_' prefix
        context.user_data['add_question'].update({
            'difficulty': difficulty,
            'options': [],
            'step': 'awaiting_options',
            'current_option': 1
        })
        
        await query.edit_message_text(
            f"📊 Difficulty: {difficulty.capitalize()}\n\n"
            "Now let's add the answer options. First, send me the correct answer:"
        )
        return 'AWAITING_OPTIONS'

    async def handle_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the input of question options."""
        question_data = context.user_data.get('add_question', {})
        if not question_data or question_data.get('step') != 'awaiting_options':
            return await self.cancel_question_creation(update, context)
        
        option_text = update.message.text
        if len(option_text) > 100:
            await update.message.reply_text(
                "Option is too long. Please keep it under 100 characters."
            )
            return 'AWAITING_OPTIONS'
        
        is_correct = (len(question_data['options']) == 0)  # First option is correct
        
        question_data['options'].append({
            'text': option_text,
            'is_correct': is_correct
        })
        
        # If we have 4 options, we're done
        if len(question_data['options']) >= 4:
            return await self.finish_question_creation(update, context)
        
        # Otherwise, ask for the next option
        question_data['current_option'] += 1
        option_num = question_data['current_option']
        
        await update.message.reply_text(
            f"Option {option_num}/4 - Enter an incorrect answer:"
        )
        return 'AWAITING_OPTIONS'

    async def finish_question_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Save the question to the database and confirm to the user."""
        question_data = context.user_data.get('add_question', {})
        if not question_data or 'options' not in question_data:
            return await self.cancel_question_creation(update, context)
        
        # Save to database
        question_id = await self.db.add_trivia_question(
            chat_id=question_data['chat_id'],
            question_text=question_data['question_text'],
            category=question_data['category'],
            difficulty=question_data['difficulty'],
            options=question_data['options'],
            created_by=question_data['created_by']
        )
        
        if not question_id:
            await update.message.reply_text(
                "❌ Failed to save the question. Please try again."
            )
        else:
            # Format options for display
            options_text = []
            for i, opt in enumerate(question_data['options'], 1):
                prefix = "✅" if opt['is_correct'] else "❌"
                options_text.append(f"{prefix} {i}. {opt['text']}")
            
            await update.message.reply_text(
                "🎉 Question added successfully!\n\n"
                f"📝 {question_data['question_text']}\n\n" +
                "\n".join(options_text) +
                f"\n\nCategory: {question_data['category']}\n"
                f"Difficulty: {question_data['difficulty'].capitalize()}"
            )
        
        # Clean up
        if 'add_question' in context.user_data:
            del context.user_data['add_question']
        
        return ConversationHandler.END

    async def cancel_question_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Cancel the question creation process."""
        if 'add_question' in context.user_data:
            del context.user_data['add_question']
        
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text("❌ Question creation cancelled.")
        else:
            await update.message.reply_text("Question creation cancelled.")
        
        return ConversationHandler.END

    async def list_questions_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List all trivia questions in this chat."""
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("Only admins can list questions.")
            return

        questions = await self.db.get_chat_questions(update.effective_chat.id)
        if not questions:
            await update.message.reply_text("No questions found for this chat.")
            return

        response = "📚 Trivia Questions:\n\n"
        for i, q in enumerate(questions, 1):
            response += f"{i}. {q['question_text']} ({q['category']}, {q['difficulty']})\n"
        
        await update.message.reply_text(response)

    async def delete_question_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Delete a trivia question by ID."""
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("Only admins can delete questions.")
            return

        if not context.args:
            await update.message.reply_text("Please provide a question ID to delete.")
            return

        try:
            question_id = int(context.args[0])
            success = await self.db.delete_question(question_id)
            if success:
                await update.message.reply_text("✅ Question deleted successfully!")
            else:
                await update.message.reply_text("❌ Failed to delete question or question not found.")
        except ValueError:
            await update.message.reply_text("Please provide a valid question ID (number).")

    async def trivia_stats_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show trivia statistics."""
        stats = await self.db.get_trivia_stats(update.effective_chat.id)
        
        if not stats:
            await update.message.reply_text("No trivia statistics available yet.")
            return

        response = "📊 Trivia Statistics:\n\n"
        response += f"Total Questions: {stats['total_questions']}\n"
        response += f"Total Games Played: {stats['total_games']}\n"
        response += f"Most Active Category: {stats['top_category']} ({stats['top_category_count']} questions)\n"
        response += f"Top Player: {stats['top_player_name']} ({stats['top_player_score']} points)\n"
        
        await update.message.reply_text(response)

            

    async def poll_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /poll command."""
        # Check if user has permission
        if not await self.is_admin(update.effective_user.id, update.effective_chat.id):
            await update.message.reply_text("You don't have permission to create polls.")
            return
            
        # Parse command arguments
        # Format: /poll "Question" "Option 1" "Option 2" ...
        if not context.args or len(context.args) < 3:
            await update.message.reply_text(
                "Usage: /poll \"Question\" \"Option 1\" \"Option 2\" [\"Option 3\" ...]"
            )
            return
            
        try:
            # Parse question and options
            question = context.args[0].strip('"\'')
            options = [opt.strip('"\'') for opt in context.args[1:]]
            
            if len(options) < 2:
                await update.message.reply_text("A poll must have at least 2 options.")
                return
                
            if len(options) > 10:
                await update.message.reply_text("A poll can have at most 10 options.")
                return
                
            # Create poll in database
            poll_id = await self.db.create_poll(
                chat_id=update.effective_chat.id,
                question=question,
                options=options,
                created_by=update.effective_user.id,
                is_anonymous=True,
                allows_multiple_answers=False,
                duration_minutes=1440  # 24 hours
            )
            
            # Create keyboard with vote buttons
            keyboard = []
            for i, option in enumerate(options):
                keyboard.append([
                    InlineKeyboardButton(
                        f"✅ {option}",
                        callback_data=f"poll_vote_{poll_id}_{i}"
                    )
                ])
                
            # Add view results button
            keyboard.append([
                InlineKeyboardButton(
                    "📊 View Results",
                    callback_data=f"poll_results_{poll_id}"
                )
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send poll message
            message = await update.message.reply_text(
                f"📊 <b>Poll:</b> {question}\n\n"
                "Click the buttons below to vote:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            
            # Update poll with message ID
            await self.db.execute(
                "UPDATE polls SET message_id = ? WHERE id = ?",
                (message.message_id, poll_id)
            )
            
            # Get poll results
            results = await self.db.get_poll_results(poll_id)
            total_votes = sum(results)
            
            results_text = []
            for i, option in enumerate(options):
                count = results[i]
                percentage = (count / total_votes * 100) if total_votes > 0 else 0
                bar_length = int(percentage / 5)  # Each 5% is one character
                bar = '█' * bar_length + '░' * (20 - bar_length)
                safe_option = str(option).replace('\\', '\\\\')
                results_text.append(
                    f"{i+1}. {safe_option}\n"
                    f"   {bar} {percentage:.1f}% ({count} votes)"
                )
            
            # Send results message
            await message.reply_text(
                "📊 <b>Poll Results:</b>\n\n"
                "\n".join(results_text),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Error creating poll: {e}")
            await update.message.reply_text(
                "An error occurred while creating the poll. Please try again."
            )

    async def vote_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /vote command (for text-based voting)."""
        # Check if in a private chat
        if update.effective_chat.type != ChatType.PRIVATE:
            await update.message.reply_text(
                "Please vote in a private chat with the bot."
            )
            return
            
        # Parse command arguments
        if len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /vote <poll_id> <option_number> [option_number ...]"
            )
            return
            
        try:
            poll_id = int(context.args[0])
            option_indices = [int(opt) - 1 for opt in context.args[1:]]  # Convert to 0-based
            
            # Get poll details
            poll = await self.db.fetch_one(
                "SELECT * FROM polls WHERE id = ? AND is_active = 1",
                (poll_id,)
            )
            
            if not poll:
                await update.message.reply_text("Poll not found or has ended.")
                return
                
            # Validate option indices
            options = json.loads(poll['options'])
            for idx in option_indices:
                if idx < 0 or idx >= len(options):
                    await update.message.reply_text(
                        f"Invalid option number. Please choose between 1 and {len(options)}."
                    )
                    return
                    
            # Cast vote
            success = await self.db.vote_in_poll(
                poll_id=poll_id,
                user_id=update.effective_user.id,
                option_indices=option_indices
            )
            
            if success:
                await update.message.reply_text("✅ Your vote has been recorded!")
            else:
                await update.message.reply_text(
                    "You have already voted in this poll."
                )
                
        except (ValueError, IndexError):
            await update.message.reply_text(
                "Invalid input. Usage: /vote <poll_id> <option_number> [option_number ...]"
            )
        except Exception as e:
            logger.error(f"Error processing vote: {e}")
            await update.message.reply_text(
                "An error occurred while processing your vote. Please try again."
            )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle all button callbacks with comprehensive error handling and logging."""
        query = update.callback_query
        await query.answer()
        data = query.data
        user = update.effective_user
        chat = update.effective_chat

        try:
            logger.info(f"Button pressed: {data} by {user.id} in chat {chat.id if chat else 'private'}")

            # ===== MAIN MENU NAVIGATION =====
            if data == "menu_main":
                if chat.type == ChatType.PRIVATE:
                    await self.show_dm_menu(update)
                else:
                    await self.show_main_menu(update)

            # ===== PROFILE MENU =====
            elif data == "menu_profile":
                await self.show_user_profile(update, user.id)
            
            # ===== LEADERBOARD =====
            elif data == "menu_leaderboard":
                await self.show_leaderboard(update)
                
            # ===== DAILY BONUS =====
            elif data == "claim_daily":
                result = await self.db.get_daily_bonus(user.id)
                if result['success']:
                    text = (
                        f"🎉 <b>Daily Bonus Claimed!</b>\n\n"
                        f"• Points earned: {result['points']}\n"
                        f"• Streak: {result['streak']} days\n"
                        f"• Next bonus in: 24 hours"
                    )
                else:
                    hours_left = result.get('hours_until_next', 24)
                    text = (
                        f"⏳ <b>Already Claimed</b>\n\n"
                        f"Come back in {hours_left} hours for your next bonus!\n"
                        f"Current streak: {result.get('streak', 0)} days"
                    )
                
                keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="menu_main")]]
                await query.edit_message_text(
                    text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )

            # ===== REFERRAL MENU =====
            elif data == "menu_referral":
                bot_username = (await self.application.bot.get_me()).username
                referral_link = f"https://t.me/{bot_username}?start={user.id}"
                
                keyboard = [
                    [InlineKeyboardButton("📤 Share Link", 
                                    url=f"https://t.me/share/url?url={referral_link}&text=Join%20me%20on%20this%20awesome%20bot!")],
                    [InlineKeyboardButton("🔙 Back", callback_data="menu_main")]
                ]
                
                await query.edit_message_text(
                    f"👥 <b>Refer Friends & Earn Points</b>\n\n"
                    f"Invite friends using this link:\n"
                    f"<code>{referral_link}</code>\n\n"
                    "You'll earn points for each friend who joins!",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )

            # ===== TRIVIA MENU =====
            elif data == "menu_trivia":
                categories = await self.db.get_trivia_categories()
                keyboard = []
                row = []
                
                for i, cat in enumerate(categories, 1):
                    row.append(InlineKeyboardButton(
                        cat['name'],
                        callback_data=f"start_trivia_{cat['id']}"
                    ))
                    if i % 2 == 0:
                        keyboard.append(row)
                        row = []
                if row:  # Add any remaining buttons
                    keyboard.append(row)
                    
                keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
                
                await query.edit_message_text(
                    "🎮 <b>Trivia Categories</b>\n\n"
                    "Choose a category to start playing:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )

            # ===== SETTINGS MENU =====
            elif data == "menu_settings":
                user_settings = await self.db.get_user_settings(user.id)
                keyboard = []
                
                for setting, value in user_settings.items():
                    if isinstance(value, bool):
                        icon = "✅" if value else "❌"
                        keyboard.append([
                            InlineKeyboardButton(
                                f"{icon} {setting.replace('_', ' ').title()}",
                                callback_data=f"toggle_setting_{setting}"
                            )
                        ])
                
                keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="menu_main")])
                
                await query.edit_message_text(
                    "⚙️ <b>Your Settings</b>\n\n"
                    "Toggle settings below:",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode=ParseMode.HTML
                )

            # ===== TOGGLE SETTINGS =====
            elif data.startswith("toggle_setting_"):
                setting = data.replace("toggle_setting_", "")
                await self.db.toggle_setting(user.id, setting)
                
                # Show updated settings
                await self.button_callback(update, context, "menu_settings")

            # ===== TRIVIA GAME =====
            elif data.startswith("start_trivia_"):
                category_id = data.replace("start_trivia_", "")
                await self.start_trivia_game(update, context, category_id)

            # ===== POLL VOTING =====
            elif data.startswith("poll_vote_"):
                # Extract poll_id and option_index from data
                _, poll_id, option_idx = data.split("_")
                await self.db.vote_in_poll(
                    poll_id=int(poll_id),
                    user_id=user.id,
                    option_indices=[int(option_idx)]
                )
                await query.answer("Vote recorded!")
                
            # ===== POLL RESULTS =====
            elif data.startswith("poll_results_"):
                poll_id = int(data.replace("poll_results_", ""))
                await self.show_poll_results(update, poll_id)

        except Exception as e:
            logger.error(f"Error in button_callback: {e}", exc_info=True)
            try:
                await query.answer("❌ An error occurred. Please try again.")
            except:
                pass

    async def settings_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle settings callback."""
        query = update.callback_query
        await query.answer()
        
        # Get current settings
        settings = await self.db.get_chat_settings(query.message.chat_id)
        
        # Update message with current settings
        await query.edit_message_text(
            text=(
                "⚙️ <b>Group Settings</b>\n\n"
                f"🛡️ <b>Anti-Spam:</b> {'✅ ON' if settings.get('antispam_enabled', True) else '❌ OFF'}\n"
                f"🌊 <b>Anti-Flood:</b> {'✅ ON' if settings.get('antiflood_enabled', True) else '❌ OFF'}\n\n"
                "Use the buttons below to toggle settings:"
            ),
            reply_markup=query.message.reply_markup,
            parse_mode=ParseMode.HTML
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle all incoming messages."""
        user = update.effective_user
        chat = update.effective_chat
        message = update.effective_message
        
        # Skip if message is from a bot
        if user.is_bot:
            return
            
        # Check if we're waiting for user input (for settings)
        if 'awaiting_input' in context.user_data:
            setting_type = context.user_data['awaiting_input']
            new_value = message.text
            
            if setting_type == 'welcome_message':
                await self.db.update_chat_settings(
                    chat.id,
                    {'welcome_message': new_value}
                )
                await message.reply_text("✅ Welcome message updated!")
                
            elif setting_type == 'goodbye_message':
                await self.db.update_chat_settings(
                    chat.id,
                    {'goodbye_message': new_value}
                )
                await message.reply_text("✅ Goodbye message updated!")
                
            elif setting_type == 'rules':
                await self.db.update_chat_settings(
                    chat.id,
                    {'rules': new_value}
                )
                await message.reply_text("✅ Rules updated!")
            
            # Clear the awaiting input state
            del context.user_data['awaiting_input']
            return
        
        # Check if user is banned
        user_data = await self.db.get_or_create_user(user)
        if user_data.get('is_banned'):
            try:
                await message.delete()
            except:
                pass
            return
                
        # Check if user is muted
        if user_data.get('is_muted'):
            mute_until = user_data.get('mute_until')
            if mute_until:
                mute_time = datetime.strptime(mute_until, '%Y-%m-%d %H:%M:%S')
                if datetime.now() < mute_time:
                    try:
                        await message.delete()
                    except:
                        pass
                    return
                else:
                    # Mute has expired, update database
                    await self.db.execute(
                        "UPDATE users SET is_muted = 0, mute_until = NULL WHERE user_id = ?",
                        (user.id,)
                    )
            else:
                # No mute time specified, assume permanent
                try:
                    await message.delete()
                except:
                    pass
                return
        
        # Check for blacklisted words
        if message.text:
            settings = await self.db.get_chat_settings(chat.id)
            if settings.get('antispam_enabled', True):
                blacklisted = await self.db.check_blacklist(chat.id, message.text)
                if blacklisted:
                    # Delete the message
                    try:
                        await message.delete()
                    except:
                        pass
                    
                    # Warn the user
                    result = await self.db.add_warning(
                        user.id,
                        self.application.bot.id,  # Bot as admin
                        chat.id,
                        f"Used blacklisted word(s): {', '.join(blacklisted)}"
                    )
                    
                    # Notify user if this is their first warning
                    if result['warnings'] == 1:
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=(
                                f"⚠️ <b>Warning</b>\n\n"
                                f"You have been warned in {chat.title} for using blacklisted words.\n"
                                f"<b>Words:</b> {', '.join(blacklisted)}\n\n"
                                f"Warnings: {result['warnings']}/{result['warn_limit']}\n"
                                f"Further violations may result in a mute or ban."
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    
                    return
        
        # Check for spam using AI moderation
        if message.text and len(message.text) > 10:  # Only check longer messages
            scores = await self.ai_moderation.check_text(message.text)
            
            if scores['spam'] > 0.7:  # High spam probability
                # Delete the message
                try:
                    await message.delete()
                except:
                    pass
                
                # Log the action
                logger.info(f"Deleted potential spam from user {user.id} in chat {chat.id}")
                
                # Optionally, warn the user
                await context.bot.send_message(
                    chat_id=user.id,
                    text="⚠️ Your message was deleted because it was flagged as potential spam.",
                    parse_mode=ParseMode.HTML
                )
                return
        
        # Check for image spam
        if message.photo:
            # Get the largest available photo
            photo = message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            image_data = await file.download_as_bytearray()
            
            # Check image with AI moderation
            scores = await self.ai_moderation.check_image(image_data)
            
            if scores['nsfw'] > 0.7:  # High NSFW probability
                # Delete the message
                try:
                    await message.delete()
                except:
                    pass
                
                # Log the action
                logger.info(f"Deleted potential NSFW image from user {user.id} in chat {chat.id}")
                
                # Warn the user
                await context.bot.send_message(
                    chat_id=user.id,
                    text="⚠️ Your image was deleted because it may contain inappropriate content.",
                    parse_mode=ParseMode.HTML
                )
                return
        
        # Update user's last activity
        await self.db.execute(
            "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?",
            (user.id,)
        )
        
        # Check for achievements
        # For example, award "Chatterbox" achievement after 100 messages
        message_count = await self.db.fetch_one(
            "SELECT COUNT(*) as count FROM messages WHERE user_id = ?",
            (user.id,)
        )
        
        if message_count and message_count['count'] >= 100:
            await self.db._award_achievement(user.id, "Chatterbox")

    async def new_member_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle new chat members."""
        chat = update.effective_chat
        new_members = update.message.new_chat_members
        
        for member in new_members:
            # Skip if the new member is the bot itself
            if member.id == context.bot.id:
                # Send welcome message
                await update.message.reply_text(
                    "👋 Thanks for adding me to the group! Use /help to see what I can do.\n\n"
                    "To get started, make me an admin with the following permissions:\n"
                    "- Delete messages\n"
                    "- Ban users\n"
                    "- Invite users via link\n"
                    "- Pin messages\n\n"
                    "Then use /settings to configure me for your group!"
                )
                continue
                
            # Skip bots
            if member.is_bot:
                continue
                
            # Add user to database
            await self.db.get_or_create_user(member)
            
            # Check for raid
            raid_check = await self.anti_raid.check_raid(chat.id, member.id)
            if raid_check:
                if raid_check['action'] == 'ban':
                    try:
                        # Ban all users in the raid
                        for user_id in raid_check['users']:
                            await context.bot.ban_chat_member(
                                chat_id=chat.id,
                                user_id=user_id
                            )
                        
                        # Notify group
                        await update.message.reply_text(
                            f"🚨 <b>Anti-Raid Protection Activated!</b>\n\n"
                            f"Detected {raid_check['count']} new users in {raid_check['time_window']} seconds.\n"
                            f"All suspected raid accounts have been banned."
                        )
                    except Exception as e:
                        logger.error(f"Error handling raid: {e}")
                continue
            
            # Get chat settings
            settings = await self.db.get_chat_settings(chat.id)
            
            # Send welcome message if enabled
            if settings and settings.get('welcome_message'):
                welcome_text = settings['welcome_message'].format(
                    mention=member.mention_html(),
                    chat_title=chat.title,
                    first_name=member.first_name,
                    username=f"@{member.username}" if member.username else member.first_name
                )
                
                await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)

    async def left_member_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle when a member leaves the chat."""
        chat = update.effective_chat
        left_member = update.message.left_chat_member
        
        # Skip if the left member is a bot or doesn't exist
        if not left_member or left_member.is_bot:
            return
            
        # Update user status
        await self.db.execute(
            "UPDATE users SET last_activity = CURRENT_TIMESTAMP WHERE user_id = ?",
            (left_member.id,)
        )
        
        # Send goodbye message if enabled
        settings = await self.db.get_chat_settings(chat.id)
        if settings and settings.get('goodbye_message'):
            goodbye_text = settings['goodbye_message'].format(
                mention=left_member.mention_html(),
                chat_title=chat.title,
                first_name=left_member.first_name,
                username=f"@{left_member.username}" if left_member.username else left_member.first_name
            )
            
            await update.message.reply_html(goodbye_text)

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors in the telegram.PTBError and telegram.TelegramError."""
        logger.error("Exception while handling an update:", exc_info=context.error)
        
        try:
            # Notify the user about the error
            if update and hasattr(update, 'effective_message'):
                await update.effective_message.reply_text(
                    "❌ An error occurred while processing your request. Please try again later."
                )
        except Exception as e:
            logger.error(f"Error in error handler: {e}")
    
    def run(self):
        """Run the bot."""
        # Set bot commands
        commands = [
            ("start", "Start the bot"),
            ("help", "Show help message")
        ]
        
        # Schedule tasks
        self._schedule_tasks()
        
        # Run the application
        self.application.run_polling()

async def main_async():
    # Load environment variables
    load_dotenv(override=True)
    
    # Get the token
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    
    if not token:
        raise ValueError(
            "No bot token found in environment variables. "
            "Please set either TELEGRAM_BOT_TOKEN or BOT_TOKEN in your .env file."
        )
    
    # Create and run the bot
    bot = MBolacryptobot(token)
    bot.run()

def main():
    """Main function to start the bot."""
    # Load environment variables
    load_dotenv(override=True)
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('BOT_TOKEN')
    if not token:
        raise ValueError("No bot token found in environment variables. Please set either TELEGRAM_BOT_TOKEN or BOT_TOKEN in your .env file.")
    
    # Create and run the bot
    bot = MBolacryptobot(token)
    
    try:
        # Run the bot with the event loop
        bot.run()
    except KeyboardInterrupt:
        print("\nBot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise

if __name__ == "__main__":
    main()