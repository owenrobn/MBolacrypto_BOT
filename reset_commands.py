import os
import asyncio
import logging
from telegram import Bot
from telegram.constants import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def reset_commands():
    # Get bot token from environment or config
    token = os.getenv('BOT_TOKEN')
    if not token:
        try:
            from config import BOT_TOKEN
            token = BOT_TOKEN
        except ImportError:
            logger.error("BOT_TOKEN not found in environment or config.py")
            print("❌ Error: BOT_TOKEN not found in environment or config.py")
            return False

    if not token or token.strip() == 'YOUR_BOT_TOKEN':
        logger.error("Invalid or default BOT_TOKEN provided")
        print("❌ Error: Please set a valid BOT_TOKEN in environment variables or config.py")
        return False

    bot = Bot(token=token)
    
    try:
        logger.info("Starting command reset process...")
        print("🔍 Starting command reset process...")
        
        # List current commands before deletion (for logging)
        try:
            group_commands = await bot.get_my_commands(scope=BotCommandScopeAllGroupChats())
            private_commands = await bot.get_my_commands(scope=BotCommandScopeAllPrivateChats())
            default_commands = await bot.get_my_commands(scope=BotCommandScopeDefault())
            
            logger.info(f"Found {len(group_commands)} group commands, {len(private_commands)} private commands, {len(default_commands)} default commands")
            
            if not any([group_commands, private_commands, default_commands]):
                logger.info("No commands found to delete")
                print("ℹ️  No commands found to delete")
                return True
                
        except Exception as e:
            logger.warning(f"Could not list current commands: {e}")
        
        # Delete all commands in all scopes
        logger.info("Deleting commands from all scopes...")
        print("🗑️  Deleting commands from all scopes...")
        
        try:
            await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
            logger.info("Deleted group chat commands")
            await asyncio.sleep(0.5)
            
            await bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
            logger.info("Deleted private chat commands")
            await asyncio.sleep(0.5)
            
            await bot.delete_my_commands(scope=BotCommandScopeDefault())
            logger.info("Deleted default commands")
            
            # Verify deletion
            await asyncio.sleep(1)
            group_after = await bot.get_my_commands(scope=BotCommandScopeAllGroupChats())
            private_after = await bot.get_my_commands(scope=BotCommandScopeAllPrivateChats())
            default_after = await bot.get_my_commands(scope=BotCommandScopeDefault())
            
            if not any([group_after, private_after, default_after]):
                logger.info("Successfully cleared all commands")
                print("✅ All bot commands have been cleared successfully!")
                return True
            else:
                logger.warning("Some commands might still exist after deletion")
                print("⚠️  Commands cleared, but some might still be visible in Telegram's cache.")
                print("   It may take a few minutes for changes to fully propagate.")
                return True
                
        except Exception as e:
            logger.error(f"Error during command deletion: {e}", exc_info=True)
            print(f"❌ Error clearing commands: {e}")
            return False
            
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"❌ An unexpected error occurred: {e}")
        return False

if __name__ == "__main__":
    try:
        asyncio.run(reset_commands())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        exit(1)
