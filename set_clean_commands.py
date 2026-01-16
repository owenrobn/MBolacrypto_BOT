import os
import asyncio
from telegram import Bot, BotCommand
from telegram.constants import BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

async def set_clean_commands():
    # Try to get token from environment variable or config
    bot_token = os.getenv('BOT_TOKEN')
    
    # If not in environment, try to get from config file
    if not bot_token and os.path.exists('config.py'):
        import config
        bot_token = getattr(config, 'BOT_TOKEN', None)
    
    if not bot_token:
        print("Error: BOT_TOKEN not found in environment or config")
        return
        
    print(f"Using bot token: {bot_token[:10]}...")
    bot = Bot(token=bot_token)
    
    # Define group commands (admin only)
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
    
    # Set commands for all scopes
    await bot.set_my_commands(group_cmds, scope=BotCommandScopeAllGroupChats())
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(private_cmds, scope=BotCommandScopeDefault())
    
    print("✓ Clean command set has been applied")

if __name__ == "__main__":
    asyncio.run(set_clean_commands()))
