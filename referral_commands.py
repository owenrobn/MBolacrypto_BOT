from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import logging
from typing import Optional, Tuple, Dict, Any

logger = logging.getLogger(__name__)

class ReferralCommands:
    def __init__(self, db_connection=None):
        """Initialize with a database connection."""
        self.db = db_connection  # Store the database connection directly
    
    async def my_referrals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show user's referral statistics in the current group."""
        user = update.effective_user
        chat = update.effective_chat
        
        # This command only works in groups
        if chat.type == 'private':
            await update.message.reply_text(
                "This command works in groups only. Use /ref to get your personal referral link."
            )
            return
            
        if not self.db:
            logger.error("Database connection not initialized")
            await update.message.reply_text("Database error. Please try again later.")
            return
            
        try:
            async with self.db.execute('''
                SELECT 
                    COUNT(*) as total_refs,
                    SUM(CASE WHEN gr.is_active = 1 THEN 1 ELSE 0 END) as active_refs,
                    COALESCE(SUM(gr.reward), 0) as total_earned
                FROM group_referrals gr
                WHERE gr.referrer_id = ? AND gr.group_id = ?
            ''', (user.id, chat.id)) as cursor:
                
                stats = await cursor.fetchone()
                
                if not stats or not stats[0]:
                    await update.message.reply_text(
                        f"📊 *Your Referral Stats in {chat.title}*\n\n"
                        "You don't have any referrals in this group yet.\n"
                        f"Share your referral link to invite others!",
                        parse_mode='Markdown'
                    )
                    return
                    
                total_refs, active_refs, total_earned = stats
                
                # Get user's rank in the group
                async with self.db.execute('''
                    WITH ranked_refs AS (
                        SELECT 
                            referrer_id,
                            COUNT(*) as ref_count,
                            ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) as rank
                        FROM group_referrals
                        WHERE group_id = ?
                        GROUP BY referrer_id
                    )
                    SELECT rank FROM ranked_refs WHERE referrer_id = ?
                ''', (chat.id, user.id)) as rank_cursor:
                    
                    rank_result = await rank_cursor.fetchone()
                    rank = rank_result[0] if rank_result else "N/A"
                    
                    # Get referral link for this group
                    bot_username = (await context.bot.get_me()).username
                    referral_code = f"GROUP{chat.id}_{user.id}"
                    invite_link = f"https://t.me/{bot_username}?start={referral_code}"
                    
                    # Format the response
                    response = (
                        f"👥 *Your Referral Stats in {chat.title}*\n\n"
                        f"🏆 Rank: #{rank}\n"
                        f"👥 Total Referrals: {total_refs}\n"
                        f"✅ Active Referrals: {active_refs}\n"
                        f"💰 Total Earned: {total_earned} points\n\n"
                        f"*Invite more people to earn more rewards!*\n"
                        f"`{invite_link}`"
                    )
                    
                    # Create share button
                    keyboard = [
                        [
                            InlineKeyboardButton(
                                "📤 Share Referral Link",
                                switch_inline_query=f"Join {chat.title} with my referral link! {invite_link}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "🏆 Leaderboard",
                                callback_data=f"leaderboard_group_{chat.id}"
                            )
                        ]
                    ]
                    
                    await update.message.reply_text(
                        response,
                        parse_mode='Markdown',
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        disable_web_page_preview=True
                    )
                    
        except Exception as e:
            logger.error(f"Error in my_referrals: {e}")
            await update.message.reply_text("An error occurred while fetching your referrals. Please try again later.")