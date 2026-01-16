import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class DebugBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        
        if not self.bot_token:
            raise ValueError("BOT_TOKEN not found in environment variables")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command with basic response."""
        try:
            user = update.effective_user
            logger.info(f"Received /start from user {user.id} ({user.first_name})")
            
            # Send a very simple message without any special formatting
            simple_msg = f"Hello {user.first_name}! Your bot is working correctly."
            
            await update.message.reply_text(simple_msg)
            logger.info("Successfully sent response")
            
        except Exception as e:
            logger.error(f"Error in start handler: {e}")
            # Try to send a basic error message
            try:
                await update.message.reply_text("Bot is working but encountered an error.")
            except Exception as e2:
                logger.error(f"Failed to send error message: {e2}")
    
    def run(self):
        """Start the bot."""
        application = Application.builder().token(self.bot_token).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", self.start))
        
        logger.info("Debug bot is starting...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    try:
        bot = DebugBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
