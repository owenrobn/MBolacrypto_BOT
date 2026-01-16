# Telegram Referral Contest Bot

A powerful Telegram bot for running referral contests with tiered rewards, admin controls, and real-time tracking.

## 🌟 Features

### Enhanced Referral System
- **Tiered Referral Rewards** - Multiple reward tiers based on number of referrals
- **Streak Bonuses** - Reward users for consecutive days of referring new users
- **Referral Analytics** - Track referral performance and user engagement
- **Flexible Reward System** - Configure points, multipliers, and bonuses
- **Referral Links** - Unique referral codes for each user

### Admin Features
- **User Management** - View, ban, and manage users
- **Points System** - Manually adjust user points
- **Broadcast Messages** - Send announcements to all users
- **Contest Management** - Create and manage multiple contests
- **Real-time Analytics** - Monitor bot performance and user engagement
- **Export Data** - Export user and referral data

### User Features
- **Referral Dashboard** - Track personal referral stats
- **Leaderboard** - Compete with other users
- **Achievements** - Unlock badges and rewards
- **Profile Management** - Update profile and settings

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL
- Telegram Bot Token from [@BotFather](https://t.me/botfather)

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/ref-contest-bot.git
   cd ref-contest-bot
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Configure environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

4. Set up the database:
   ```bash
   alembic upgrade head
   ```

5. Run the bot:
   ```bash
   python -m bot
   ```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file with the following variables:

```env
# Bot Configuration
BOT_TOKEN=your_telegram_bot_token
ADMIN_IDS=123456789,987654321  # Comma-separated list of admin user IDs

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/ref_contest

# Logging
LOG_LEVEL=INFO
LOG_FILE=bot.log

# Webhook (optional)
WEBHOOK_URL=https://yourdomain.com/webhook
WEBHOOK_PORT=8443
WEBHOOK_LISTEN=0.0.0.0
SSL_CERT=/path/to/cert.pem
SSL_PRIV=/path/to/private.key
```

## 🎮 Bot Commands

### User Commands
- `/start` - Start the bot and get your referral link
- `/help` - Show help information
- `/referral` - Get your referral stats and link
- `/leaderboard` - View the current leaderboard
- `/profile` - View and edit your profile

### Admin Commands
- `/admin` - Show admin panel
- `/broadcast` - Send message to all users
- `/addpoints` - Add points to a user
- `/user <id/username>` - Get user information
- `/stats` - View bot statistics
- `/export` - Export user data

## 🛠 Admin Panel

The admin panel provides a user-friendly interface to manage the bot:

1. **Dashboard** - Overview of bot statistics
2. **User Management** - View and manage users
3. **Contest Management** - Create and manage contests
4. **Broadcast** - Send messages to all users
5. **Analytics** - View detailed statistics and reports

## 📊 Database Schema

### Key Tables
- `users` - User information and settings
- `referrals` - Referral relationships
- `referral_tiers` - Reward tiers and requirements
- `contests` - Contest information
- `user_points` - Points earned by users
- `user_referral_stats` - Referral statistics

## 🔄 Database Migrations

This project uses Alembic for database migrations. To create a new migration:

```bash
alembic revision --autogenerate -m "description of changes"
alembic upgrade head
```

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📧 Contact

For questions or support, please contact [Your Name] at [your.email@example.com]
