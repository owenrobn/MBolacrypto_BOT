#!/usr/bin/env python3
"""
Startup script for the Telegram bot on Render.
"""

import os
import sys
import logging
from multipurpose_bot import MultipurposeBot

# Configure logging for production
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

def main():
    """Main startup function."""
    try:
        logger.info("🚀 Starting bot...")
        # Initialize and run bot
        bot = MultipurposeBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
