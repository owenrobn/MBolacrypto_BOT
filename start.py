#!/usr/bin/env python3
"""
Startup script for multipurpose Telegram bot on Render.
Runs the bot once; python-telegram-bot manages its own asyncio loop.
"""

import os
import sys
import time
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
    """Main startup function. Single run; no internal restart loop."""
    try:
        logger.info("🚀 Starting multipurpose bot...")
        # Initialize and run bot (blocking)
        bot = MultipurposeBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Startup script for multipurpose Telegram bot on Render.
Runs the bot once; python-telegram-bot manages its own asyncio loop.
"""

import os
import sys
import time
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
    """Main startup function. Single run; no internal restart loop."""
    try:
        logger.info("🚀 Starting multipurpose bot...")
        # Initialize and run bot (blocking)
        bot = MultipurposeBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot crashed with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
