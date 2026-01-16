import os
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from dotenv import load_dotenv
from database import Database

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
db = Database()

class SimpleRefContestBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        
        if not self.bot_token:
            raise ValueError("BOT_TOKEN not found in environment variables")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command and referral links."""
        user = update.effective_user
        args = context.args
        
        # Check if user came via referral link
        referred_by_id = None
        if args and len(args) > 0:
            referral_code = args[0]
            referrer = db.get_user_by_referral_code(referral_code)
            if referrer and referrer['user_id'] != user.id:
                referred_by_id = referrer['user_id']
        
        # Check if user already exists
        existing_user = db.get_user(user.id)
        
        if not existing_user:
            # Add new user
            user_referral_code = db.add_user(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                last_name=user.last_name,
                referred_by=referred_by_id
            )
            
            welcome_msg = f"🎉 Welcome to the Referral Contest, {user.first_name}!\n\n"
            
            if referred_by_id:
                referrer = db.get_user(referred_by_id)
                welcome_msg += f"✅ You joined through {referrer['first_name']}'s referral link!\n\n"
            
            welcome_msg += f"🔗 Your unique referral code: `{user_referral_code}`\n"
            welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}\n\n"
            welcome_msg += "Share your link to earn points in the contest! 🏆"
            
        else:
            user_referral_code = existing_user['referral_code']
            welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"
            welcome_msg += f"🔗 Your referral code: `{user_referral_code}`\n"
            welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
        
        # Create inline keyboard
        keyboard = [
            [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
            [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks."""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        if query.data == "stats":
            await self.show_stats(query, user_id)
        elif query.data == "leaderboard":
            await self.show_leaderboard(query)
        elif query.data == "help":
            await self.show_help(query)
        elif query.data == "back_to_menu":
            await self.back_to_menu(update, context)
    
    async def show_stats(self, query, user_id: int):
        """Show user's referral statistics."""
        user = db.get_user(user_id)
        if not user:
            await query.edit_message_text("❌ User not found. Please use /start to register.")
            return
        
        stats = db.get_referral_stats(user_id)
        
        stats_msg = f"📊 **Your Referral Stats**\n\n"
        stats_msg += f"🔗 Your referral code: `{user['referral_code']}`\n"
        stats_msg += f"📱 Your link: https://t.me/{self.bot_username}?start={user['referral_code']}\n\n"
        stats_msg += f"👥 Total referrals: **{stats['total_referrals']}**\n\n"
        
        if stats['referred_users']:
            stats_msg += "🎯 **Recent referrals:**\n"
            for i, referred_user in enumerate(stats['referred_users'][:5], 1):
                name = referred_user['first_name'] or referred_user['username'] or "Unknown"
                stats_msg += f"{i}. {name}\n"
            
            if len(stats['referred_users']) > 5:
                stats_msg += f"... and {len(stats['referred_users']) - 5} more!\n"
        else:
            stats_msg += "🔄 No referrals yet. Share your link to start earning!"
        
        # Back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(stats_msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def show_leaderboard(self, query):
        """Show contest leaderboard."""
        leaderboard = db.get_leaderboard(10)
        
        leaderboard_msg = "🏆 **Contest Leaderboard**\n\n"
        
        if leaderboard:
            medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
            
            for i, user in enumerate(leaderboard):
                if user['referral_count'] > 0:
                    name = user['first_name'] or user['username'] or "Unknown"
                    medal = medals[i] if i < len(medals) else "🏅"
                    leaderboard_msg += f"{medal} {name}: **{user['referral_count']}** referrals\n"
        else:
            leaderboard_msg += "No participants yet. Be the first to start referring!"
        
        # Back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(leaderboard_msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def show_help(self, query):
        """Show help information."""
        help_msg = """ℹ️ **How the Referral Contest Works**

🎯 **Goal:** Invite as many people as possible using your unique referral link!

📋 **How to participate:**
1. Get your unique referral link from the main menu
2. Share it with friends, family, and social media
3. When someone joins using your link, you get a point!
4. Check the leaderboard to see your ranking

🏆 **Commands:**
• /start - Get your referral link and main menu
• Use the buttons to navigate

💡 **Tips:**
• Share your link on social media for maximum reach
• The more people join through your link, the higher you rank!
• Check your stats regularly to track progress

Good luck! 🍀"""
        
        # Back button
        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(help_msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back to menu callback."""
        query = update.callback_query
        await query.answer()
        
        user = query.from_user
        existing_user = db.get_user(user.id)
        
        if existing_user:
            user_referral_code = existing_user['referral_code']
            welcome_msg = f"👋 Main Menu - {user.first_name}\n\n"
            welcome_msg += f"🔗 Your referral code: `{user_referral_code}`\n"
            welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
            
            # Create inline keyboard
            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(welcome_msg, reply_markup=reply_markup, parse_mode='Markdown')
    
    def run(self):
        """Start the bot."""
        application = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CallbackQueryHandler(self.button_handler))
        
        logger.info("Simple bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try:
        bot = SimpleRefContestBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your BOT_TOKEN and BOT_USERNAME")
        print("2. Installed the required dependencies: pip install -r requirements.txt")
