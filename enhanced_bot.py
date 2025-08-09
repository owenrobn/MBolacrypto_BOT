import os
import logging
import sqlite3
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from database import Database
import re

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

class EnhancedRefContestBot:
    def __init__(self):
        self.bot_token = os.getenv('BOT_TOKEN')
        self.bot_username = os.getenv('BOT_USERNAME')
        
        if not self.bot_token:
            raise ValueError("BOT_TOKEN not found in environment variables")
        
        logger.info(f"Enhanced bot initialized with username: {self.bot_username}")
    
    def is_valid_telegram_link(self, link: str) -> bool:
        """Validate if the provided link is a valid Telegram group/channel link."""
        telegram_patterns = [
            r'^https://t\.me/[a-zA-Z0-9_]+$',  # Public groups/channels
            r'^https://t\.me/joinchat/[a-zA-Z0-9_-]+$',  # Private groups via invite link
            r'^https://t\.me/\+[a-zA-Z0-9_-]+$',  # New format private groups
            r'^@[a-zA-Z0-9_]+$'  # Username format
        ]
        
        for pattern in telegram_patterns:
            if re.match(pattern, link.strip()):
                return True
        return False
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command, referral links, and event joins with group redirect."""
        try:
            user = update.effective_user
            args = context.args
            logger.info(f"Received /start from user {user.id} ({user.first_name})")
            
            # Check if user came via referral link or event join
            referred_by_id = None
            event_id = None
            redirect_to_group = None
            
            if args and len(args) > 0:
                code = args[0]
                logger.info(f"User came via code: {code}")
                
                # Check if it's an event code
                event = db.get_event_by_code(code)
                if event:
                    event_id = event['id']
                    redirect_to_group = event.get('group_link')
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
                            redirect_to_group = event.get('group_link')
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
                
                # If there's a group link to redirect to, show it prominently
                if redirect_to_group:
                    welcome_msg += f"🎪 You've been invited to join a special group!\n"
                    welcome_msg += f"👆 Click the button below to join the group\n\n"
                
                welcome_msg += f"🔗 Your unique referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}\n\n"
                welcome_msg += "Share your link to earn points in the contest! 🏆"
                
            else:
                user_referral_code = existing_user['referral_code']
                welcome_msg = f"👋 Welcome back, {user.first_name}!\n\n"
                
                # If there's a group link to redirect to, show it prominently
                if redirect_to_group:
                    welcome_msg += f"🎪 You've been invited to join a special group!\n"
                    welcome_msg += f"👆 Click the button below to join the group\n\n"
                
                welcome_msg += f"🔗 Your referral code: {user_referral_code}\n"
                welcome_msg += f"📱 Your referral link: https://t.me/{self.bot_username}?start={user_referral_code}"
            
            # Create inline keyboard
            keyboard = []
            
            # If there's a group to redirect to, add join group button first
            if redirect_to_group:
                # Format the group link properly
                group_link = redirect_to_group
                if group_link.startswith('@'):
                    group_link = f"https://t.me/{group_link[1:]}"
                
                keyboard.append([InlineKeyboardButton("🎪 Join Group", url=group_link)])
                keyboard.append([])  # Empty row for spacing
            
            # Add regular menu buttons
            keyboard.extend([
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard")],
                [InlineKeyboardButton("🎯 My Event Links", callback_data="my_event_links")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("➕ Create Event", callback_data="create_event")],
                [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(welcome_msg, reply_markup=reply_markup)
            logger.info("Successfully sent welcome message with group redirect")
            
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
            elif query.data.startswith("event_link_"):
                event_id = int(query.data.split('_')[2])
                await self.send_event_link(query, event_id)
            elif query.data == "my_event_links":
                await self.show_my_event_links(query, user_id)
            elif query.data.startswith("join_event_"):
                event_id = int(query.data.split('_')[2])
                await self.join_event_confirm(query, event_id)
            elif query.data.startswith("set_group_"):
                event_id = int(query.data.split('_')[2])
                await self.start_set_group_link(query, context, event_id)
            elif query.data == "skip_group_link":
                await self.skip_group_link(query, context)
                
        except Exception as e:
            logger.error(f"Error in button handler: {e}")
            try:
                await query.edit_message_text("An error occurred. Please try again or use /start.")
            except:
                pass
    
    async def show_my_events(self, query, user_id: int):
        """Show user's hosted events with group link management."""
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
                    
                    # Show group link status
                    if event.get('group_link'):
                        events_msg += f"🎪 Group: {event['group_link'][:30]}{'...' if len(event['group_link']) > 30 else ''}\n"
                        events_msg += f"📱 Referral links redirect to your group!\n"
                    else:
                        events_msg += f"⚠️ No group link set\n"
                    
                    events_msg += f"📱 Join link: https://t.me/{self.bot_username}?start={event['event_code']}\n\n"
                    
                    # Add buttons for event management
                    keyboard.append([InlineKeyboardButton(f"📊 {event['title']} Stats", callback_data=f"event_{event['id']}")])
                    if not event.get('group_link'):
                        keyboard.append([InlineKeyboardButton(f"🎪 Set Group Link", callback_data=f"set_group_{event['id']}")])
                
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
    
    async def start_set_group_link(self, query, context, event_id: int):
        """Start the group link setting process."""
        try:
            # Store user state for group link setting
            context.user_data['setting_group_link'] = True
            context.user_data['target_event_id'] = event_id
            
            await query.edit_message_text(
                "🎪 Set Group Link\n\n"
                "Enter the Telegram group or channel link where you want referrals to be directed.\n\n"
                "Supported formats:\n"
                "• https://t.me/your_group\n"
                "• https://t.me/joinchat/invite_link\n"
                "• https://t.me/+invite_link\n"
                "• @your_group_username\n\n"
                "Please enter the group link:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⏭️ Skip", callback_data="skip_group_link")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="my_events")]
                ])
            )
            
        except Exception as e:
            logger.error(f"Error in start_set_group_link: {e}")
            await query.edit_message_text("Error starting group link setup. Please try again.")
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages for event creation and group link setting."""
        try:
            user_data = context.user_data
            
            if user_data.get('creating_event'):
                if user_data.get('event_step') == 'title':
                    await self.create_event_title(update, context)
                elif user_data.get('event_step') == 'description':
                    await self.create_event_description(update, context)
                elif user_data.get('event_step') == 'group_link':
                    await self.create_event_group_link(update, context)
            elif user_data.get('setting_group_link'):
                await self.set_group_link(update, context)
            
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
            
            context.user_data['event_description'] = description
            context.user_data['event_step'] = 'group_link'
            
            await update.message.reply_text(
                "📝 Description saved!\n\n"
                "🎪 Now, would you like to set a group link?\n\n"
                "If you set a group link, when people use referral links for this event, "
                "they'll be directed to join your group automatically!\n\n"
                "Enter a Telegram group/channel link, or send 'skip' to create the event without a group link:\n\n"
                "Supported formats:\n"
                "• https://t.me/your_group\n"
                "• https://t.me/joinchat/invite_link\n"
                "• @your_group_username"
            )
            
        except Exception as e:
            logger.error(f"Error in create_event_description: {e}")
            await update.message.reply_text("Error processing description. Please try again.")
    
    async def create_event_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle event group link input during creation."""
        try:
            group_link = None
            
            if update.message.text.strip().lower() != 'skip':
                group_link = update.message.text.strip()
                
                if not self.is_valid_telegram_link(group_link):
                    await update.message.reply_text(
                        "❌ Invalid Telegram link format.\n\n"
                        "Please use one of these formats:\n"
                        "• https://t.me/your_group\n"
                        "• https://t.me/joinchat/invite_link\n"
                        "• https://t.me/+invite_link\n"
                        "• @your_group_username\n\n"
                        "Or send 'skip' to create without a group link."
                    )
                    return
            
            # Create the event
            title = context.user_data['event_title']
            description = context.user_data.get('event_description')
            user_id = update.effective_user.id
            
            event_code = db.create_event(user_id, title, description, group_link)
            
            success_msg = f"🎉 Event Created Successfully!\n\n"
            success_msg += f"📅 Title: {title}\n"
            if description:
                success_msg += f"📝 Description: {description}\n"
            if group_link:
                success_msg += f"🎪 Group Link: {group_link}\n"
                success_msg += f"✨ Referral links will now redirect users to your group!\n"
            success_msg += f"🔗 Event Code: {event_code}\n"
            success_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n\n"
            
            if group_link:
                success_msg += "🚀 How it works:\n"
                success_msg += "• Share your event join link or participant referral links\n"
                success_msg += "• New users will be registered and then directed to your group\n"
                success_msg += "• All referrals are tracked automatically!\n\n"
            
            success_msg += "Share this link for people to join your event directly!"
            
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
            logger.error(f"Error in create_event_group_link: {e}")
            await update.message.reply_text("Error creating event. Please try again.")
    
    async def set_group_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle setting group link for existing event."""
        try:
            group_link = update.message.text.strip()
            event_id = context.user_data.get('target_event_id')
            
            if not self.is_valid_telegram_link(group_link):
                await update.message.reply_text(
                    "❌ Invalid Telegram link format.\n\n"
                    "Please use one of these formats:\n"
                    "• https://t.me/your_group\n"
                    "• https://t.me/joinchat/invite_link\n"
                    "• https://t.me/+invite_link\n"
                    "• @your_group_username"
                )
                return
            
            # Update the event with the group link
            success = db.update_event_group_link(event_id, group_link)
            
            if success:
                success_msg = f"✅ Group Link Set Successfully!\n\n"
                success_msg += f"🎪 Group: {group_link}\n\n"
                success_msg += "🚀 From now on, when people use referral links for this event, "
                success_msg += "they'll be registered in the bot and then directed to join your group!\n\n"
                success_msg += "All referrals will be tracked automatically."
                
                keyboard = [
                    [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event_id}")],
                    [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(success_msg, reply_markup=reply_markup)
            else:
                await update.message.reply_text("❌ Error setting group link. Please try again.")
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in set_group_link: {e}")
            await update.message.reply_text("Error setting group link. Please try again.")
    
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
• Set group links to redirect referrals to your groups
• Track all participants in your events
• View detailed event statistics

🏆 Commands:
• /start - Get your referral link and main menu
• Use the buttons to navigate

💡 Tips:
• Share your link on social media for maximum reach
• Create events for specific contests or campaigns
• Set group links to automatically direct referrals to your groups
• The more people join through your link, the higher you rank!

Good luck! 🍀"""
            
            # Back button
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(help_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_help: {e}")
            await query.edit_message_text("Help information temporarily unavailable.")
    
    async def show_event_stats(self, query, event_id: int):
        """Show detailed statistics for an event with group link info."""
        try:
            # Get event info
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
            
            if not event_row:
                await query.edit_message_text("❌ Event not found.")
                return
            
            event_code = event_row[1]  # event_code
            event_title = event_row[2]  # title
            group_link = event_row[4]  # group_link
            
            stats = db.get_event_stats(event_id)
            
            stats_msg = f"📊 Event Statistics: {event_title}\n\n"
            stats_msg += f"🔗 Event Code: {event_code}\n"
            stats_msg += f"📱 Join Link: https://t.me/{self.bot_username}?start={event_code}\n"
            
            if group_link:
                stats_msg += f"🎪 Target Group: {group_link}\n"
                stats_msg += f"✨ Referrals redirect to group automatically!\n"
            else:
                stats_msg += f"⚠️ No group link set\n"
            
            stats_msg += f"\n👥 Total Participants: {stats['total_participants']}\n"
            stats_msg += f"🔄 Total Referrals: {stats['total_referrals']}\n\n"

            # If the requester is a participant, show their personal event link and stats
            try:
                user_id = query.from_user.id
                with sqlite3.connect(db.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?', (event_id, user_id))
                    is_participant = cursor.fetchone() is not None
                if is_participant:
                    user = db.get_user(user_id)
                    if user and user.get('referral_code'):
                        personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                        per_stats = db.get_user_referrals_in_event(user_id, event_id)
                        stats_msg += f"🎯 Your Event Link:\n{personal_link}\n"
                        stats_msg += f"📈 Your referrals in this event: {per_stats['referral_count']}\n\n"
            except Exception as e:
                logger.warning(f"Could not compute personal event link: {e}")

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
            
            # Add group link management button if no group link is set
            if not group_link:
                keyboard.insert(1, [InlineKeyboardButton("🎪 Set Group Link", callback_data=f"set_group_{event_id}")])

            # If participant, add quick button to get personal event link
            try:
                if is_participant:
                    keyboard.insert(1, [InlineKeyboardButton("🎯 My Event Link", callback_data=f"event_link_{event_id}")])
            except NameError:
                # is_participant not defined due to earlier failure; ignore
                pass
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(stats_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in show_event_stats: {e}")
            await query.edit_message_text("Error loading event stats. Please try again.")
    
    async def join_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE, event: dict):
        """Handle event join process with group link redirect."""
        try:
            user = update.effective_user
            
            join_msg = f"🎪 Join Event: {event['title']}\n\n"
            if event['description']:
                join_msg += f"📝 {event['description']}\n\n"
            join_msg += f"👤 Hosted by: {event['host_name']}\n"
            
            if event.get('group_link'):
                join_msg += f"🎪 This event has a group: {event['group_link']}\n"
                join_msg += f"✨ You'll be directed to the group after joining!\n"
            
            join_msg += f"\nWould you like to join this event?"
            
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
        """Confirm event join with group redirect."""
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
                
                # Get event info including group link and event code
                cursor.execute('SELECT title, group_link, event_code FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
                event_title = event_row[0]
                group_link = event_row[1]
                event_code = event_row[2]
            
            success_msg = f"🎉 Successfully joined: {event_title}\n\n"
            success_msg += "You can now participate in this event's referral contest!\n"
            success_msg += "Use your personal referral link to invite others to this event."
            
            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            
            # If there's a group link, add join group button
            if group_link:
                success_msg += f"\n\n🎪 Don't forget to join the event group!"
                
                # Format the group link properly
                formatted_link = group_link
                if group_link.startswith('@'):
                    formatted_link = f"https://t.me/{group_link[1:]}"
                
                keyboard.insert(0, [InlineKeyboardButton("🎪 Join Event Group", url=formatted_link)])

            # Show the user's personal event link inline
            user = db.get_user(user_id)
            if user and user.get('referral_code'):
                personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                success_msg += f"\n\n🎯 Your Event Link:\n{personal_link}"
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_msg, reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Error in join_event_confirm: {e}")
            await query.edit_message_text("Error joining event. Please try again.")

    async def show_my_event_links(self, query, user_id: int):
        """List all events the user participates in and provide personal referral links."""
        try:
            # Fetch events where the user is a participant
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT e.id, e.title, e.event_code, e.group_link
                    FROM events e
                    JOIN event_participants ep ON ep.event_id = e.id
                    WHERE ep.user_id = ?
                    ORDER BY e.created_at DESC
                ''', (user_id,))
                rows = cursor.fetchall()
            
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.edit_message_text("Please use /start to register first.")
                return
            
            if not rows:
                keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
                await query.edit_message_text(
                    "You are not participating in any events yet. Join an event to get your custom link!",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
            msg = "🎯 Your Event Participation Links\n\n"
            keyboard = []
            for (event_id, title, event_code, group_link) in rows[:10]:
                personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
                msg += f"📅 {title}\n"
                msg += f"🔗 Link: {personal_link}\n"
                if group_link:
                    msg += f"🎪 Group: {group_link}\n"
                msg += "\n"
                # Add buttons to get link again or open stats
                keyboard.append([
                    InlineKeyboardButton("🎯 Get Link", callback_data=f"event_link_{event_id}"),
                    InlineKeyboardButton("📊 Event Stats", callback_data=f"event_{event_id}")
                ])
            
            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")])
            
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        
        except Exception as e:
            logger.error(f"Error in show_my_event_links: {e}")
            await query.edit_message_text("Error loading your event links. Please try again.")

    async def send_event_link(self, query, event_id: int):
        """Send the current user's personal referral link for a specific event."""
        try:
            user_id = query.from_user.id
            # Verify participation
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT event_code FROM events WHERE id = ?', (event_id,))
                row = cursor.fetchone()
                if not row:
                    await query.message.reply_text("❌ Event not found.")
                    return
                event_code = row[0]
                cursor.execute('SELECT 1 FROM event_participants WHERE event_id = ? AND user_id = ?', (event_id, user_id))
                if cursor.fetchone() is None:
                    await query.message.reply_text("You need to join this event first to get your link.")
                    return
            user = db.get_user(user_id)
            if not user or not user.get('referral_code'):
                await query.message.reply_text("Please use /start to register first.")
                return
            personal_link = f"https://t.me/{self.bot_username}?start={user['referral_code']}_{event_code}"
            await query.message.reply_text(
                f"🎯 Your Event Link:\n{personal_link}\n\nShare this link to invite people directly to this event!"
            )
        except Exception as e:
            logger.error(f"Error in send_event_link: {e}")
            await query.message.reply_text("Error generating your event link. Please try again.")
    
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
    
    async def group_leaderboard_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /leaderboard command in groups - only for group admins."""
        try:
            # Check if this is a group chat
            if update.effective_chat.type not in ['group', 'supergroup']:
                await update.message.reply_text("This command only works in groups where I'm an admin.")
                return
            
            # Check if the user is an admin in this group
            user_id = update.effective_user.id
            chat_id = update.effective_chat.id
            
            try:
                chat_member = await context.bot.get_chat_member(chat_id, user_id)
                if chat_member.status not in ['administrator', 'creator']:
                    await update.message.reply_text("Only group administrators can request the leaderboard.")
                    return
            except Exception as e:
                logger.error(f"Error checking admin status: {e}")
                await update.message.reply_text("I need admin permissions to check your admin status.")
                return
            
            # Check if bot is admin in the group
            try:
                bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
                if bot_member.status != 'administrator':
                    await update.message.reply_text("I need to be an admin in this group to show the leaderboard.")
                    return
            except Exception as e:
                logger.error(f"Error checking bot admin status: {e}")
                await update.message.reply_text("I need admin permissions in this group.")
                return
            
            # Get leaderboard data
            leaderboard = db.get_leaderboard(10)
            
            leaderboard_msg = f"🏆 Referral Contest Leaderboard\n"
            leaderboard_msg += f"📊 Requested by {update.effective_user.first_name}\n\n"
            
            if leaderboard:
                medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
                
                for i, user in enumerate(leaderboard):
                    if user['referral_count'] > 0:
                        name = user['first_name'] or user['username'] or "Unknown"
                        medal = medals[i] if i < len(medals) else "🏅"
                        leaderboard_msg += f"{medal} {name}: {user['referral_count']} referrals\n"
                
                leaderboard_msg += f"\n🚀 Want to join the contest? Start the bot @{self.bot_username} to get your referral link!"
            else:
                leaderboard_msg += "No participants yet. Be the first to start referring!\n\n"
                leaderboard_msg += f"🚀 Start the contest: @{self.bot_username}"
            
            await update.message.reply_text(leaderboard_msg)
            logger.info(f"Group leaderboard requested by admin {user_id} in group {chat_id}")
            
        except Exception as e:
            logger.error(f"Error in group_leaderboard_command: {e}")
            await update.message.reply_text("Error loading leaderboard. Please try again later.")
    
    async def skip_group_link(self, query, context):
        """Handle skipping group link setting for existing event."""
        try:
            event_id = context.user_data.get('target_event_id')
            
            if not event_id:
                await query.edit_message_text("❌ Error: No event selected. Please try again.")
                return
            
            # Get event info for confirmation
            with sqlite3.connect(db.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT title FROM events WHERE id = ?', (event_id,))
                event_row = cursor.fetchone()
                
                if not event_row:
                    await query.edit_message_text("❌ Event not found.")
                    return
                
                event_title = event_row[0]
            
            success_msg = f"⏭️ Group Link Skipped\n\n"
            success_msg += f"📅 Event: {event_title}\n\n"
            success_msg += "Your event will continue without a group link. "
            success_msg += "You can always add a group link later from the event management menu.\n\n"
            success_msg += "Referral links will direct users to the bot instead of a specific group."
            
            keyboard = [
                [InlineKeyboardButton("📊 View Event Stats", callback_data=f"event_{event_id}")],
                [InlineKeyboardButton("🎪 My Events", callback_data="my_events")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(success_msg, reply_markup=reply_markup)
            
            # Clear user state
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"Error in skip_group_link: {e}")
            await query.edit_message_text("Error skipping group link. Please try again.")
    
    def run(self):
        """Start the bot with improved error handling."""
        try:
            # Create application with updated configuration
            application = Application.builder().token(self.bot_token).build()
            
            # Add handlers
            application.add_handler(CommandHandler("start", self.start))
            application.add_handler(CommandHandler("leaderboard", self.group_leaderboard_command))
            application.add_handler(CallbackQueryHandler(self.button_handler))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
            
            logger.info("🚀 Enhanced referral contest bot with group links is starting...")
            
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
        bot = EnhancedRefContestBot()
        bot.run()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        print(f"Error: {e}")
        print("\nMake sure you have:")
        print("1. Created a .env file with your BOT_TOKEN and BOT_USERNAME")
        print("2. Installed the required dependencies: pip install -r requirements.txt")
