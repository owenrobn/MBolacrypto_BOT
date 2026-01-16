import logging
import os
import sys

from multipurpose_bot import MultipurposeBot


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    bot = MultipurposeBot()
    webhook_url = os.getenv("WEBHOOK_URL") or None

    try:
        bot.run(webhook_url=webhook_url)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Shutdown requested...")


if __name__ == "__main__":
    main()
