import os
import asyncio
from telegram import Bot
from telegram.constants import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

async def list_commands():
    # Get bot token from environment or config
    token = os.getenv('BOT_TOKEN')
    if not token:
        try:
            from config import BOT_TOKEN
            token = BOT_TOKEN
        except ImportError:
            print("Error: BOT_TOKEN not found in environment or config.py")
            return

    bot = Bot(token=token)
    
    try:
        print("Listing all registered commands:")
        
        # List group chat commands
        group_commands = await bot.get_my_commands(scope=BotCommandScopeAllGroupChats())
        print("\nGroup chat commands:")
        for cmd in group_commands:
            print(f"  /{cmd.command} - {cmd.description}")
            
        # List private chat commands
        private_commands = await bot.get_my_commands(scope=BotCommandScopeAllPrivateChats())
        print("\nPrivate chat commands:")
        for cmd in private_commands:
            print(f"  /{cmd.command} - {cmd.description}")
            
        # List default commands
        default_commands = await bot.get_my_commands(scope=BotCommandScopeDefault())
        print("\nDefault commands:")
        for cmd in default_commands:
            print(f"  /{cmd.command} - {cmd.description}")
            
    except Exception as e:
        print(f"Error listing commands: {e}")

if __name__ == "__main__":
    asyncio.run(list_commands())
