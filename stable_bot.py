import os
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
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

class StableRefContestBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        
        if not self.bot_token:
            raise ValueError("BOT_TOKEN not found in environment variables")
        
        logger.info(f"Bot initialized with username: {self.bot_username}")
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command, referral links, and event joins."""
        try:
            user = update.effective_user
            args = context.args
            logger.info(f"Received /start from user {user.id} ({user.first_name})")
            
            # Check if user came via referral link or event join
            referred_by_id = None
            event_id = None
            
            if args and len(args) > 0:
                code = args[0]
                logger.info(f"User came via code: {code}")
                
                # Check if it's an event code
                event = db.get_event_by_code(code)
                if event:
                    event_id = event['id']
                    # Show event join message
                    await self.join_event(update, context, event)
                    return
                
                # Check if it's a referral code with event context
                if '_' in code:
                    parts = code.split('_')
                    if len(parts) == 2:
                        referral_code, event_code = parts
                        referrer = db.get_user_by_referral_code(referral_code)
                        event = db.get_event_by_code(event_code)
                        
                        if referrer and event and referrer['user_id'] != user.id:
                            referred_by_id = referrer['user_id']
                            event_id = event['id']
                else:
                    # Regular referral code
                    referrer = db.get_user_by_referral_code(code)
                    if referrer and referrer['user_id'] != user.id:
                        referred_by_id = referrer['user_id']
            
            # Check if user already exists
            existing_user = db.get_user(user.id)
            
            if not existing_user:
                # Add new user
                logger.info(f"Adding new user: {user.id}")
                user_referral_code = db.add_user(
                    user_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    referred_by=referred_by_id,
                    event_id=event_id
                )
                
                welcome_msg = f"🎉 Welcome to the Referral Contest, {user.first_name}!\n\n"
                
                if referred_by_id:
                    referrer = db.get_user(referred_by_id)
                    welcome_msg += f"✅ You joined through {referrer['first_name']}'s referral link!\n\n"
                
                welcome_msg += f"🔗 Your unique referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}\n\n"
                welcome_msg += "Share your link to earn points in the contest! 🏆"
                
            else:
                user_referral_code = existing_user['referral_code']
                welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
            
            # Create inline keyboard with event hosting options
            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
            logger.info("Successfully sent welcome message")
            
        except Exception as e:
            logger.error(f"Error in start handler: {e}")
            await update.message.reply_text("Welcome! Your bot is working. Please try again.")
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks."""
        try:
            query = update.callback_query
            await query.answer()
            
            user_id = query.from_user.id
            logger.info(f"Button pressed: {query.data} by user {user_id}")
            
            if query.data == "stats":
                await self.show_stats(query, user_id)
            elif query.data == "leaderboard":
                await self.show_leaderboard(query)
            elif query.data == "my_events":
                await self.show_my_events(query, user_id)
            elif query.data == "create_event":
                await self.start_create_event(query, context)
            elif query.data == "help":
                await self.show_help(query)
            elif query.data == "back_to_menu":
                await self.back_to_menu(update, context)
            elif query.data.startswith("event_"):
                event_id = int(query.data.split('_')[1])
                await self.show_event_stats(query, event_id)
            elif query.data.startswith("join_event_"):
                event_id = int(query.data.split('_')[2])
                await self.join_event_confirm(query, event_id)
                
        except Exception as e:
            logger.error(f"Error in button handler: {e}")
            try:
                await query.edit_message_text("An error occurred. Please try again or use /start.")
            except:
                pass
    
    async def show_my_events(self, query, user_id: int):
        """Show user's hosted events."""
        try:
            events = db.get_user_events(user_id)
            
            events_msg = "🎪 Your Hosted Events\n\n"
            
            if events:
                keyboard = []
                for event in events[:10]:  # Show max 10 events
                    events_msg += f"📅 {event['title']}\n"
                    events_msg += f"🔗 Code: {event['event_code']}\n"
                    if event['description']:
                        events_msg += f"📝 {event['description'][:50]}{'...' if len(event['description']) > 50 else ''}\n"
                    events_msg += f"📱 Join link: https://t.me/{self.bot_username}?start={event['event_code']}\n\n"
                    
                    # Add button for event stats
                    keyboard.append([InlineKeyboardButton(f"📊 {event['title']} Stats", callback_data=f"event_{event['id']}")])
                
                # Back button
                keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
            else:
                events_msg += "No events created yet. Create your first event to start hosting referral contests!"
                keyboard = [
                    [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(events_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_my_events: {e}")
            await query.edit_message_text("Error loading events. Please try again.")
    
    async def start_create_event(self, query, context):
        """Start the event creation process."""
        try:
            # Store user state for event creation
            context.user_data['creating_event'] = True
            context.user_data['event_step'] = 'title'
            
            await query.edit_message_text(
                "🎪 Create New Event\n\n"
                "Let's create your referral contest event!\n\n"
                "Please enter the event title:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]])
            )
            
        except Exception as e:
            logger.error(f"Error in start_create_event: {e}")
            await query.edit_message_text("Error starting event creation. Please try again.")
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages for event creation flow."""
        try:
            user_data = context.user_data
            
            if user_data.get('creating_event'):
                if user_data.get('event_step') == 'title':
                    await self.create_event_title(update, context)
                elif user_data.get('event_step') == 'description':
                    await self.create_event_description(update, context)
            
        except Exception as e:
            logger.error(f"Error in handle_text_message: {e}")
    
    async def create_event_title(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event title input."""
        try:
            title = update.message.text.strip()
            
            if len(title) > 100:
                await update.message.reply_text("❌ Event title is too long. Please keep it under 100 characters.")
                return
            
            context.user_data['event_title'] = title
            context.user_data['event_step'] = 'description'
            
            await update.message.reply_text(
                f"✅ Event title: {title}\n\n"
                "Now enter a description for your event (or send 'skip' to skip):"
            )
            
        except Exception as e:
            logger.error(f"Error in create_event_title: {e}")
            await update.message.reply_text("Error processing title. Please try again.")
    
    async def create_event_description(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event description input."""
        try:
            description = None
            
            if update.message.text.strip().lower() != 'skip':
                description = update.message.text.strip()
                if len(description) > 500:
                    await update.message.reply_text("❌ Description is too long. Please keep it under 500 characters.")
                    return
            
            # Create the event
            title = context.user_data['event_title']
            user_id = update.effective_user.id
            
            event_code = db.create_event(user_id, title, description)
            
            success_msg = f"🎉 Event Created Successfully!\n\n"
            success_msg += f"📅 Title: {title}\n"
            if description:
                success_msg += f"📝 Description: {description}\n"
            success_msg += f"🔗 Event Code: {event_code}\n"
            success_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n\n"
            success_msg += "Share this link for people to join your event directly!\n"
            success_msg += "You can also share personalized referral links within this event."
            
            # Get event ID for stats button
            event = db.get_event_by_code(event_code)
            
            keyboard = [
                [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event['id']}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(success_msg, reply_markup=reply_markup)
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in create_event_description: {e}")
            await update.message.reply_text("Error creating event. Please try again.")
    
    async def show_event_stats(self, query, event_id: int):
        """Show detailed statistics for an event."""
        try:
            # Get event info
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
            
            if not event_row:
                await query.edit_message_text("❌ Event not found.")
                return
            
            event_code = event_row[1]  # event_code is the second column
            event_title = event_row[2]  # title is the third column
            
            stats = db.get_event_stats(event_id)
            
            stats_msg = f"📊 Event Statistics: {event_title}\n\n"
            stats_msg += f"🔗 Event Code: {event_code}\n"
            stats_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n\n"
            stats_msg += f"👥 Total Participants: {stats['total_participants']}\n"
            stats_msg += f"🔄 Total Referrals: {stats['total_referrals']}\n\n"
            
            if stats['top_referrers']:
                stats_msg += "🏆 Top Referrers:\n"
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                for i, referrer in enumerate(stats['top_referrers'][:5]):
                    if referrer['referral_count'] > 0:
                        name = referrer['first_name'] or referrer['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        stats_msg += f"{medal} {name}: {referrer['referral_count']} referrals\n"
            
            if stats['recent_participants']:
                stats_msg += f"\n👋 Recent Participants:\n"
                for participant in stats['recent_participants'][:5]:
                    name = participant['first_name'] or participant['username'] or "Unknown"
                    stats_msg += f"• {name}\n"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh Stats", callback_data=f"event_{event_id}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(stats_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_event_stats: {e}")
            await query.edit_message_text("Error loading event stats. Please try again.")
    
    async def join_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE, event: dict):
        """Handle event join process."""
        try:
            user = update.effective_user
            
            join_msg = f"🎪 Join Event: {event['title']}\n\n"
            if event['description']:
                join_msg += f"📝 {event['description']}\n\n"
            join_msg += f"👤 Hosted by: {event['host_name']}\n\n"
            join_msg += "Would you like to join this event?"
            
            keyboard = [
                [InlineKeyboardButton("✅ Join Event", callback_data=f"join_event_{event['id']}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(join_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in join_event: {e}")
            await update.message.reply_text("Error processing event join. Please try again.")
    
    async def join_event_confirm(self, query, event_id: int):
        """Confirm event join."""
        try:
            user_id = query.from_user.id
            
            # Add user to event participants
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO event_participants (event_id, user_id)
                    VALUES (?, ?)
                ''', (event_id, user_id))
                conn.commit()
                
                # Get event info
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                event_title = cursor.fetchone()[0]
            
            success_msg = f"🎉 Successfully joined: {event_title}\n\n"
            success_msg += "You can now participate in this event's referral contest!\n"
            success_msg += "Use your personal referral link to invite others to this event."
            
            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in join_event_confirm: {e}")
            await query.edit_message_text("Error joining event. Please try again.")
    
    async def show_stats(self, query, user_id: int):
        """Show user's referral statistics."""
        try:
            user = db.get_user(user_id)
            if not user:
                await query.edit_message_text("❌ User not found. Please use /start to register.")
                return
            
            stats = db.get_referral_stats(user_id)
            
            stats_msg = f"📊 Your Referral Stats\n\n"
            stats_msg += f"🔗 Your referral code: {user['referral_code']}\n"
            stats_msg += f"📱 Your link: https://t.me/{self.bot_username}?start={user['referral_code']}\n\n"
            stats_msg += f"👥 Total referrals: {stats['total_referrals']}\n\n"
            
            if stats['referred_users']:
                stats_msg += "🎯 Recent referrals:\n"
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
            
            await query.edit_message_text(stats_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_stats: {e}")
            await query.edit_message_text("Error loading stats. Please try again.")
    
    async def show_leaderboard(self, query):
        """Show contest leaderboard."""
        try:
            leaderboard = db.get_leaderboard(10)
            
            leaderboard_msg = "🏆 Contest Leaderboard\n\n"
            
            if leaderboard:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                
                for i, user in enumerate(leaderboard):
                    if user['referral_count'] > 0:
                        name = user['first_name'] or user['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        leaderboard_msg += f"{medal} {name}: {user['referral_count']} referrals\n"
            else:
                leaderboard_msg += "No participants yet. Be the first to start referring!"
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(leaderboard_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_leaderboard: {e}")
            await query.edit_message_text("Error loading leaderboard. Please try again.")
    
    async def show_help(self, query):
        """Show help information."""
        try:
            help_msg = """ℹ️ How the Referral Contest Works

🎯 Goal: Invite as many people as possible using your unique referral link!

📋 How to participate:
1. Get your unique referral link from the main menu
2. Share it with friends, family, and social media
3. When someone joins using your link, you get a point!
4. Check the leaderboard to see your ranking

🎪 Event Hosting:
• Create your own referral contests
• Track all participants in your events
• View detailed event statistics
• Share event join links

🏆 Commands:
• /start - Get your referral link and main menu
• Use the buttons to navigate

💡 Tips:
• Share your link on social media for maximum reach
• Create events for specific contests or campaigns
• The more people join through your link, the higher you rank!
• Check your stats regularly to track progress

Good luck! 🍀"""
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(help_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_help: {e}")
            await query.edit_message_text("Help information temporarily unavailable.")
    
    async def back_to_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle back to menu callback."""
        try:
            query = update.callback_query
            await query.answer()
            
            # Clear any ongoing event creation state
            context.user_data.clear()
            
            user = query.from_user
            existing_user = db.get_user(user.id)
            
            if existing_user:
                user_referral_code = existing_user['referral_code']
                welcome_msg = f"👋 Main Menu - {user.first_name}\n\n"
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
                
                # Create inline keyboard with all features
                keyboard = [
                    [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                    [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                    [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                    [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                    [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(welcome_msg, reply_markup=reply_markup)
            else:
                await query.edit_message_text("Please use /start to register first.")
                
        except Exception as e:
            logger.error(f"Error in back_to_menu: {e}")
            await query.edit_message_text("Please use /start to return to the main menu.")
    
    def run(self):
        """Start the bot with improved error handling."""
        try:
            # Create application with updated configuration
            application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            application.add_handler(CommandHandler("start", self.start))
            application.add_handler(CallbackQueryHandler(self.button_handler))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
            
            logger.info("🚀 Stable referral contest bot with event hosting is starting...")
            
            # Run with improved polling configuration
            application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
        except Exception as e:
            logger.error(f"Critical error starting bot: {e}")
            raise

if __name__ == "__main__":
    try:
        bot = StableRefContestBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your BOT_TOKEN and BOT_USERNAME")
        print("2. Installed the required dependencies: pip install -r requirements.txt")
