# Referral Contest Bot

A Telegram bot that hosts referral contests, allowing users to generate unique referral links, track referrals, and compete on a leaderboard.

## Features

- 🔗 **Unique Referral Links**: Each user gets a unique referral code and link
- 📊 **Statistics Tracking**: Users can view their referral stats and progress
- 🏆 **Leaderboard**: Real-time leaderboard showing top referrers
- 👥 **User Management**: Automatic user registration and referral tracking
- 💾 **Data Persistence**: SQLite database for storing user data and referrals

## Setup Instructions

### 1. Create a Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow the instructions to create your bot
4. Copy the bot token provided by BotFather
5. Note down your bot's username (without @)

### 2. Configure Environment

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` file and add your bot credentials:
   ```
   BOT_TOKEN=your_actual_bot_token_here
   BOT_USERNAME=your_bot_username_here
   ```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Bot

```bash
python bot.py
```

## How It Works

### For Participants

1. **Join**: Users start by sending `/start` to the bot
2. **Get Link**: Each user receives a unique referral link
3. **Share**: Users share their referral links to invite others
4. **Track**: Users can check their stats and see who joined through their link
5. **Compete**: View the leaderboard to see rankings

### For Referrals

1. **Click Link**: New users click on a referral link (format: `https://t.me/yourbotname?start=REFERRALCODE`)
2. **Auto-Track**: The bot automatically tracks who referred whom
3. **Instant Credit**: Referrers get instant credit for successful referrals

## Bot Commands

- `/start` - Initialize bot, get referral link, or join via referral
- Interactive buttons for:
  - 📊 **My Stats** - View personal referral statistics
  - 🏆 **Leaderboard** - See contest rankings
  - ℹ️ **Help** - Get information about the contest

## Database Structure

The bot uses SQLite with two main tables:

- **users**: Stores user information and referral codes
- **referrals**: Tracks referral relationships

## File Structure

```
ref-contest-bot/
├── bot.py              # Main bot application
├── database.py         # Database operations
├── requirements.txt    # Python dependencies
├── .env.example       # Environment variables template
├── .env              # Your actual environment variables (create this)
├── contest.db        # SQLite database (created automatically)
└── README.md         # This file
```

## Customization

You can easily customize the bot by modifying:

- **Messages**: Edit the text messages in `bot.py`
- **Database**: Extend the database schema in `database.py`
- **Features**: Add new commands and functionality
- **Rewards**: Implement point systems or rewards for top referrers

## Security Notes

- Keep your `.env` file secure and never commit it to version control
- The bot token should be kept private
- Consider implementing rate limiting for production use

## Troubleshooting

1. **Bot not responding**: Check if the bot token is correct in `.env`
2. **Database errors**: Ensure the bot has write permissions in the directory
3. **Import errors**: Make sure all dependencies are installed with `pip install -r requirements.txt`

## Support

If you encounter any issues, check the console output for error messages. The bot includes comprehensive logging to help diagnose problems.
