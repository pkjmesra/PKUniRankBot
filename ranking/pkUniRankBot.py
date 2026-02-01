"""
pkUniRankBot - Telegram Bot for University Ranking
Compatible with python-telegram-bot v13.15 (Updater architecture)
"""

import os
import logging
from typing import Dict, Tuple
from dataclasses import dataclass
from datetime import datetime
import numpy as np
from dotenv import dotenv_values

# Import for telegram bot v13.15
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, ConversationHandler, CallbackContext
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
all_secrets = dotenv_values(".env.dev")
BOT_TOKEN = all_secrets['BOT_TOKEN']

# Conversation states
AWAITING_UNIVERSITY, AWAITING_COUNTRY = range(2)

@dataclass
class UniversityData:
    name: str
    country: str
    type: str
    scores: Dict[str, float]
    composite: float
    tier: str
    error_margin: float
    timestamp: str

class UniversityRankingSystem:
    def __init__(self):
        # Parameter definitions with max scores
        self.parameters = {
            'academic': {'name': 'Academic Reputation & Research', 'max': 25},
            'graduate': {'name': 'Graduate Prospects', 'max': 25},
            'roi': {'name': 'ROI / Affordability', 'max': 20},
            'fsr': {'name': 'Faculty-Student Ratio', 'max': 15},
            'transparency': {'name': 'Transparency & Recognition', 'max': 10},
            'visibility': {'name': 'Visibility & Presence', 'max': 5}
        }
        
        # Tier system
        self.tiers = {
            'A+': (85, 100, "🎖️ WORLD-CLASS"),
            'A': (75, 84.999, "⭐ EXCELLENT"),
            'B': (65, 74.999, "👍 GOOD"),
            'C+': (55, 64.999, "📊 AVERAGE"),
            'C': (45, 54.999, "⚠️ BELOW AVERAGE"),
            'D': (0, 44.999, "🚨 POOR")
        }
        
        # Database of known universities
        self.university_db = self.load_university_database()
        
        # Country quality multipliers
        self.country_multipliers = {
            'USA': 1.2, 'UK': 1.15, 'Canada': 1.1, 'Australia': 1.1,
            'Germany': 1.1, 'Switzerland': 1.15, 'Singapore': 1.1,
            'Japan': 1.05, 'Netherlands': 1.05, 'Sweden': 1.05,
            'France': 1.0, 'Italy': 0.95, 'Spain': 0.95,
            'China': 0.9, 'India': 0.85, 'Brazil': 0.85,
            'Russia': 0.85, 'South Africa': 0.85
        }
    
    def load_university_database(self) -> Dict:
        """Load university database with pre-calculated scores"""
        return {
            'bryant university': {
                'country': 'USA',
                'type': 'TEACHING_UNIVERSITY',
                'scores': {'academic': 12, 'graduate': 22, 'roi': 16, 
                          'fsr': 13, 'transparency': 8, 'visibility': 3},
                'description': 'Private business-focused university'
            },
            'massachusetts institute of technology': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 24, 'graduate': 23, 'roi': 22, 
                          'fsr': 14, 'transparency': 9, 'visibility': 5},
                'description': 'World-renowned research university'
            },
            'harvard university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 25, 'graduate': 24, 'roi': 20, 
                          'fsr': 13, 'transparency': 10, 'visibility': 5},
                'description': 'Ivy League research university'
            },
            'stanford university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 24, 'graduate': 23, 'roi': 21, 
                          'fsr': 14, 'transparency': 9, 'visibility': 5},
                'description': 'Leading research university'
            },
            'university of toronto': {
                'country': 'Canada',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 22, 'graduate': 21, 'roi': 18, 
                          'fsr': 13, 'transparency': 9, 'visibility': 4},
                'description': 'Top Canadian research university'
            },
            'university of oxford': {
                'country': 'UK',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 25, 'graduate': 24, 'roi': 19, 
                          'fsr': 14, 'transparency': 10, 'visibility': 5},
                'description': 'Historic research university'
            },
            'conestoga college': {
                'country': 'Canada',
                'type': 'COLLEGE_POLYTECHNIC',
                'scores': {'academic': 4.0, 'graduate': 20.0, 'roi': 17.5, 
                          'fsr': 12.5, 'transparency': 6.5, 'visibility': 3.5},
                'description': 'Canadian polytechnic institute'
            },
            'algonquin college': {
                'country': 'Canada',
                'type': 'COLLEGE_POLYTECHNIC',
                'scores': {'academic': 3.5, 'graduate': 19.0, 'roi': 17.0, 
                          'fsr': 12.0, 'transparency': 6.0, 'visibility': 3.0},
                'description': 'Canadian college'
            },
            'north dakota state university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 15.6, 'graduate': 15.0, 'roi': 16.1, 
                          'fsr': 11.0, 'transparency': 9.0, 'visibility': 4.0},
                'description': 'Public research university'
            },
            'university of tokyo': {
                'country': 'Japan',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 23, 'graduate': 21, 'roi': 18, 
                          'fsr': 13, 'transparency': 8, 'visibility': 4},
                'description': 'Top Japanese university'
            },
            'university of sydney': {
                'country': 'Australia',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 21, 'graduate': 20, 'roi': 17, 
                          'fsr': 12, 'transparency': 8, 'visibility': 4},
                'description': 'Australian research university'
            }
        }
    
    def classify_university_type(self, name: str) -> str:
        """Classify university based on name patterns"""
        name_lower = name.lower()
        
        if any(word in name_lower for word in ['business school', 'medical school', 'law school']):
            return 'SPECIALIST_SCHOOL'
        elif any(word in name_lower for word in ['college', 'community college', 'polytechnic']):
            return 'COLLEGE_POLYTECHNIC'
        elif any(word in name_lower for word in ['technical', 'applied', 'technology']):
            return 'APPLIED_UNIVERSITY'
        elif 'university' in name_lower:
            if any(word in name_lower for word in ['research', 'institute', 'tech']):
                return 'RESEARCH_UNIVERSITY'
            else:
                return 'TEACHING_UNIVERSITY'
        
        return 'TEACHING_UNIVERSITY'
    
    def estimate_scores(self, name: str, country: str) -> Dict[str, float]:
        """Estimate scores for unknown universities"""
        name_lower = name.lower()
        country_upper = country.upper() if country else "GLOBAL"
        
        # Base scores
        scores = {
            'academic': 12.0,
            'graduate': 15.0,
            'roi': 14.0,
            'fsr': 11.0,
            'transparency': 7.0,
            'visibility': 3.0
        }
        
        # Adjust based on name patterns
        if 'mit' in name_lower or 'massachusetts institute' in name_lower:
            scores = {'academic': 24, 'graduate': 23, 'roi': 22, 
                     'fsr': 14, 'transparency': 9, 'visibility': 5}
        elif 'harvard' in name_lower:
            scores = {'academic': 25, 'graduate': 24, 'roi': 20, 
                     'fsr': 13, 'transparency': 10, 'visibility': 5}
        elif 'stanford' in name_lower:
            scores = {'academic': 24, 'graduate': 23, 'roi': 21, 
                     'fsr': 14, 'transparency': 9, 'visibility': 5}
        elif 'oxford' in name_lower or 'cambridge' in name_lower:
            scores = {'academic': 25, 'graduate': 24, 'roi': 19, 
                     'fsr': 14, 'transparency': 10, 'visibility': 5}
        elif 'university' in name_lower and 'state' in name_lower:
            scores.update({'academic': 15.0, 'roi': 16.0, 'transparency': 9.0, 'visibility': 4.0})
        elif 'university' in name_lower:
            scores.update({'academic': 18.0, 'visibility': 4.0, 'transparency': 8.0})
        elif 'college' in name_lower:
            scores.update({'graduate': 17.0, 'roi': 16.0, 'fsr': 12.0, 'academic': 8.0})
        
        # Apply country multiplier
        if country_upper != "GLOBAL":
            country_mult = self.country_multipliers.get(country_upper, 1.0)
            for key in ['academic', 'graduate', 'roi', 'fsr']:
                scores[key] = min(self.parameters[key]['max'], scores[key] * country_mult)
        
        # Add randomness for estimation error
        for key in scores:
            if key in ['transparency', 'visibility']:
                variation = np.random.uniform(-0.5, 0.5)
            else:
                variation = np.random.uniform(-2.0, 2.0)
            scores[key] = max(0, min(self.parameters[key]['max'], scores[key] + variation))
        
        return {k: round(v, 1) for k, v in scores.items()}
    
    def calculate_composite_score(self, scores: Dict[str, float]) -> float:
        """Calculate composite score"""
        return round(sum(scores.values()), 1)
    
    def get_tier(self, score: float) -> Tuple[str, str]:
        """Determine tier and description"""
        for tier, (low, high, description) in self.tiers.items():
            if low <= score <= high:
                return tier, description
        return 'D', self.tiers['D'][2]
    
    def calculate_error_margin(self, university_name: str, country: str) -> float:
        """Calculate error margin"""
        name_lower = university_name.lower()
        
        if name_lower in self.university_db:
            return round(np.random.uniform(1.0, 3.0), 1)
        else:
            country_mult = 1.0
            if country:
                country_mult = self.country_multipliers.get(country.upper(), 1.0)
            
            base_error = 8.0 / country_mult
            
            if 'university' in name_lower:
                base_error *= 0.9
            elif 'college' in name_lower:
                base_error *= 1.1
            
            return round(min(15.0, max(3.0, base_error + np.random.uniform(-2.0, 2.0))), 1)
    
    def rank_university(self, university_name: str, country: str = "") -> UniversityData:
        """Main ranking function"""
        name_lower = university_name.lower()
        
        # Check database first
        if name_lower in self.university_db:
            data = self.university_db[name_lower]
            scores = data['scores']
            university_type = data['type']
            db_country = data['country']
        else:
            # Estimate scores
            scores = self.estimate_scores(university_name, country)
            university_type = self.classify_university_type(university_name)
            db_country = country if country else "Global"
        
        # Calculate metrics
        composite = self.calculate_composite_score(scores)
        tier, tier_desc = self.get_tier(composite)
        error_margin = self.calculate_error_margin(university_name, country)
        
        return UniversityData(
            name=university_name,
            country=db_country,
            type=university_type.replace('_', ' ').title(),
            scores=scores,
            composite=composite,
            tier=tier,
            error_margin=error_margin,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

class UniRankBot:
    def __init__(self, token: str):
        """Initialize the bot with Updater"""
        self.updater = Updater(token=token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.ranking_system = UniversityRankingSystem()
        
        # Set up handlers
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup all bot handlers"""
        # Command handlers
        self.dispatcher.add_handler(CommandHandler("start", self.start_command))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("rank", self.rank_command))
        self.dispatcher.add_handler(CommandHandler("tiers", self.tiers_command))
        self.dispatcher.add_handler(CommandHandler("parameters", self.parameters_command))
        
        # Conversation handler for interactive ranking
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('rank', self.start_ranking)],
            states={
                AWAITING_UNIVERSITY: [MessageHandler(Filters.text & ~Filters.command, self.get_university)],
                AWAITING_COUNTRY: [MessageHandler(Filters.text & ~Filters.command, self.get_country)]
            },
            fallbacks=[CommandHandler('cancel', self.cancel_ranking)]
        )
        self.dispatcher.add_handler(conv_handler)
        
        # Callback query handler for buttons
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Message handler for direct ranking
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_direct_message))
    
    def start(self):
        """Start the bot"""
        print("🤖 pkUniRankBot is starting...")
        print("📊 University Ranking System Ready")
        print("⚡ Bot is running. Press Ctrl+C to stop.")
        
        self.updater.start_polling()
        self.updater.idle()
    
    # Command handlers
    def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        user = update.message.from_user
        welcome_text = f"""
🎓 Welcome to *pkUniRankBot* {user.first_name}!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

*Available Commands:*
/rank - Rank a university
/tiers - View tier explanations  
/parameters - View ranking parameters
/help - Get help

*How to use:*
1. Send /rank or click the button below
2. Enter university name
3. Enter country (optional)
4. Get detailed ranking report

Click the button below to start ranking!
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = """
*📚 pkUniRankBot Help*

*Ranking Methodology:*
This bot uses a multi-parameter scoring system:
• Academic Reputation & Research (25%)
• Graduate Prospects (25%)  
• ROI / Affordability (20%)
• Faculty-Student Ratio (15%)
• Transparency & Recognition (10%)
• Visibility & Presence (5%)

*Tier System:*
A+ (85-100): World-class
A (75-84): Excellent
B (65-74): Good
C+ (55-64): Average
C (45-54): Below average
D (0-44): Poor

*Commands:*
/start - Start the bot
/rank - Rank a university
/tiers - View tier details
/parameters - View parameter details
/help - This help message

*Example usage:*
Send /rank and follow the prompts
        """
        
        update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    def tiers_command(self, update: Update, context: CallbackContext):
        """Handle /tiers command"""
        tiers_text = """
*🏆 Ranking Tiers & Ranges*

*A+ (85-100)* 🎖️
World-class institutions with exceptional performance across all metrics.

*A (75-84)* ⭐  
Excellent institutions with strong performance and areas of excellence.

*B (65-74)* 👍
Good institutions with solid performance with some excellent areas.

*C+ (55-64)* 📊
Average institutions meeting basic standards.

*C (45-54)* ⚠️
Below average institutions needing significant improvement.

*D (0-44)* 🚨
Poor performance across most metrics.

*Error Margin:* ±2-15 points based on data availability.
        """
        
        update.message.reply_text(tiers_text, parse_mode=ParseMode.MARKDOWN)
    
    def parameters_command(self, update: Update, context: CallbackContext):
        """Handle /parameters command"""
        params_text = """
*📊 Ranking Parameters*

*1. Academic Reputation & Research (25%)*
Research output, citations, academic prestige, faculty quality.

*2. Graduate Prospects (25%)*
Employment rate, starting salary, employer partnerships.

*3. ROI / Affordability (20%)*
Return on Investment = Median Salary / Total Cost.

*4. Faculty-Student Ratio (15%)*
FTE Students / FTE Faculty. Class sizes.

*5. Transparency & Recognition (10%)*
Accreditation, official recognition, data availability.

*6. Visibility & Presence (5%)*
Institutional web presence, brand recognition.

*Scoring:* Each parameter scored 0 to max, composite = sum of all scores.
        """
        
        update.message.reply_text(params_text, parse_mode=ParseMode.MARKDOWN)
    
    def rank_command(self, update: Update, context: CallbackContext):
        """Handle /rank command"""
        if context.args:
            # Direct ranking with arguments
            text = " ".join(context.args)
            parts = text.rsplit(" ", 1)
            
            if len(parts) == 2:
                university_name, country = parts
            else:
                university_name = parts[0]
                country = ""
            
            self.perform_ranking(update, university_name, country)
        else:
            # Start interactive ranking
            self.start_ranking(update, context)
    
    def start_ranking(self, update: Update, context: CallbackContext):
        """Start the ranking conversation"""
        update.message.reply_text(
            "🎓 *University Ranking*\n\nPlease enter *University Name, Country*",
            parse_mode=ParseMode.MARKDOWN
        )
        return AWAITING_UNIVERSITY
    
    def get_university(self, update: Update, context: CallbackContext):
        """Get university name from user"""
        university_name = update.message.text.strip()
        context.user_data['university_name'] = university_name
        
        # Show country selection buttons
        keyboard = [
            [InlineKeyboardButton("🇺🇸 USA", callback_data=f"country_USA_{university_name}")],
            [InlineKeyboardButton("🇬🇧 UK", callback_data=f"country_UK_{university_name}")],
            [InlineKeyboardButton("🇨🇦 Canada", callback_data=f"country_Canada_{university_name}")],
            [InlineKeyboardButton("🇦🇺 Australia", callback_data=f"country_Australia_{university_name}")],
            [InlineKeyboardButton("🇩🇪 Germany", callback_data=f"country_Germany_{university_name}")],
            [InlineKeyboardButton("🇮🇳 India", callback_data=f"country_India_{university_name}")],
            [InlineKeyboardButton("Other/Skip", callback_data=f"country_skip_{university_name}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            f"📝 University: *{university_name}*\n\nNow enter the country (or select from buttons):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        return AWAITING_COUNTRY
    
    def get_country(self, update: Update, context: CallbackContext):
        """Get country from user and perform ranking"""
        university_name = context.user_data.get('university_name', '')
        country = update.message.text.strip()
        
        self.perform_ranking(update, university_name, country)
        return ConversationHandler.END
    
    def cancel_ranking(self, update: Update, context: CallbackContext):
        """Cancel the ranking conversation"""
        update.message.reply_text("Ranking cancelled.")
        return ConversationHandler.END
    
    def handle_direct_message(self, update: Update, context: CallbackContext):
        """Handle direct ranking requests in message format"""
        message = update.message.text.strip()
        
        # Check if message looks like "University, Country" format
        if ',' in message:
            parts = [p.strip() for p in message.split(',', 1)]
            if len(parts) == 2:
                university_name, country = parts
                self.perform_ranking(update, university_name, country)
                return
        
        # Otherwise show help
        update.message.reply_text(
            "To rank a university, use:\n"
            "• /rank command\n"
            "• Or send: *University Name, Country*\n"
            "• Or click the Rank button from /start",
            parse_mode=ParseMode.MARKDOWN
        )
    
    def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        if data == "start_ranking":
            query.edit_message_text(
                "🎓 *University Ranking*\n\nPlease enter the university name:",
                parse_mode=ParseMode.MARKDOWN
            )
            # Note: We can't start conversation from callback in this simple setup
            query.message.reply_text("Please use /rank command to start ranking.")
        
        elif data == "view_tiers":
            self.show_tiers(query)
        
        elif data == "view_parameters":
            self.show_parameters(query)
        
        elif data == "main_menu":
            self.show_main_menu(query)
        
        elif data == "rank_another":
            query.edit_message_text(
                "🎓 *University Ranking*\n\nPlease enter the university name:",
                parse_mode=ParseMode.MARKDOWN
            )
            query.message.reply_text("Please use /rank command to start ranking.")
        
        elif data.startswith("country_"):
            # Handle country selection
            parts = data.split("_")
            if len(parts) >= 3:
                country_code = parts[1]
                university_name = "_".join(parts[2:])  # Handle spaces in university name
                
                if country_code == "skip":
                    country = ""
                else:
                    country = country_code
                
                self.perform_ranking_callback(query, university_name.replace('_', ' '), country)
    
    def perform_ranking(self, update: Update, university_name: str, country: str):
        """Perform ranking and send results"""
        processing_msg = update.message.reply_text(
            f"🔍 *Analyzing {university_name}...*\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country)
            
            # Format results
            results_text = self.format_ranking_results(ranking_data)
            
            # Send results
            processing_msg.edit_text(
                results_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_results_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error ranking university: {e}")
            error_text = f"❌ *Error Ranking University*\n\nSorry, I couldn't analyze *{university_name}*.\n\nPlease try again."
            
            processing_msg.edit_text(
                error_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_error_keyboard()
            )
    
    def perform_ranking_callback(self, query, university_name: str, country: str):
        """Perform ranking from callback"""
        query.edit_message_text(
            f"🔍 *Analyzing {university_name}...*\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.MARKDOWN
        )
        
        try:
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country)
            
            # Format results
            results_text = self.format_ranking_results(ranking_data)
            
            # Send results
            query.edit_message_text(
                results_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_results_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error ranking university: {e}")
            error_text = f"❌ *Error Ranking University*\n\nSorry, I couldn't analyze *{university_name}*.\n\nPlease try again."
            
            query.edit_message_text(
                error_text,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_error_keyboard()
            )
    
    def show_tiers(self, query):
        """Show tiers information"""
        tiers_text = """
*🏆 Ranking Tiers & Ranges*

*A+ (85-100)* 🎖️
World-class institutions with exceptional performance.

*A (75-84)* ⭐  
Excellent institutions with strong performance.

*B (65-74)* 👍
Good institutions with solid performance.

*C+ (55-64)* 📊
Average institutions meeting basic standards.

*C (45-54)* ⚠️
Below average institutions needing improvement.

*D (0-44)* 🚨
Poor performance across most metrics.

*Error Margin:* ±2-15 points based on data availability.
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            tiers_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    def show_parameters(self, query):
        """Show parameters information"""
        params_text = """
*📊 Ranking Parameters*

*1. Academic Reputation & Research (25%)*
Research output, citations, academic prestige.

*2. Graduate Prospects (25%)*
Employment rate, starting salary.

*3. ROI / Affordability (20%)*
Return on Investment = Salary / Cost.

*4. Faculty-Student Ratio (15%)*
Students / Faculty ratio.

*5. Transparency & Recognition (10%)*
Accreditation, data availability.

*6. Visibility & Presence (5%)*
Web presence, brand recognition.

*Scoring:* Each parameter scored 0 to max.
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            params_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    def show_main_menu(self, query):
        """Show main menu"""
        welcome_text = """
🎓 Welcome to *pkUniRankBot*!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

Click the buttons below to get started!
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
    
    def format_ranking_results(self, data: UniversityData) -> str:
        """Format ranking results as Markdown text"""
        # Header
        results = f"""
*🏛️ {data.name}*
*🌍 {data.country}*
*🎓 {data.type}*

*📅 Analysis Date:* {data.timestamp}
*📊 Data Confidence:* ±{data.error_margin} points
        """
        
        # Parameter scores
        results += "\n\n*📈 PARAMETER SCORES:*\n"
        results += "```\n"
        results += f"{'Parameter':<25} {'Score':<8} {'Max':<5} {'%':<6}\n"
        results += "-" * 44 + "\n"
        
        total_score = 0
        for param_code, param_info in self.ranking_system.parameters.items():
            score = data.scores.get(param_code, 0)
            max_score = param_info['max']
            percentage = (score / max_score * 100) if max_score > 0 else 0
            
            short_name = param_info['name']
            if len(short_name) > 24:
                short_name = short_name[:22] + ".."
            
            results += f"{short_name:<25} {score:>5.1f}/{max_score:<4} {percentage:>5.1f}%\n"
            total_score += score
        
        results += "-" * 44 + "\n"
        total_percentage = (total_score / 100) * 100
        results += f"{'TOTAL':<25} {total_score:>5.1f}/100   {total_percentage:>5.1f}%\n"
        results += "```\n"
        
        # Composite score and tier
        results += f"\n*🎯 COMPOSITE SCORE:* {data.composite:.1f}/100\n"
        results += f"*🏆 TIER:* {data.tier}\n"
        
        # Get tier description
        tier_desc = self.ranking_system.tiers.get(data.tier, ("", "", ""))[2]
        results += f"*💡 ASSESSMENT:* {tier_desc}\n"
        
        # Error margin explanation
        if data.error_margin <= 3:
            confidence = "High (Known institution)"
        elif data.error_margin <= 7:
            confidence = "Moderate (Estimated)"
        else:
            confidence = "Low (Limited data)"
        
        results += f"\n*📊 ERROR MARGIN:* ±{data.error_margin} points\n"
        results += f"*🔍 CONFIDENCE:* {confidence}\n"
        
        # Recommendations
        results += "\n*📝 RECOMMENDATIONS:*\n"
        if data.tier in ['A+', 'A']:
            results += "• Maintain strong performance\n• Enhance international partnerships\n• Invest in research\n"
        elif data.tier == 'B':
            results += "• Strengthen research output\n• Improve graduate employment\n• Enhance visibility\n"
        elif data.tier == 'C+':
            results += "• Focus on employability\n• Improve faculty ratio\n• Enhance transparency\n"
        elif data.tier in ['C', 'D']:
            results += "• Urgent improvement needed\n• Focus on core competencies\n• Seek accreditation\n"
        
        return results
    
    def get_results_keyboard(self):
        """Get keyboard for results message"""
        keyboard = [
            [InlineKeyboardButton("🎯 Rank Another University", callback_data="rank_another")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def get_error_keyboard(self):
        """Get keyboard for error message"""
        keyboard = [
            [InlineKeyboardButton("🔄 Try Again", callback_data="start_ranking")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        return InlineKeyboardMarkup(keyboard)

def main():
    """Main function to run the bot"""
    # Check for required packages
    try:
        import telegram
        import numpy
    except ImportError as e:
        print(f"❌ Missing package: {e}")
        print("Install with: pip install python-telegram-bot numpy")
        exit(1)
    
    # Get bot token
    token = BOT_TOKEN
    if token == 'YOUR_BOT_TOKEN_HERE':
        print("\n❌ ERROR: Bot token not set!")
        print("Please set your bot token:")
        print("1. Create a bot with @BotFather on Telegram")
        print("2. Get your bot token")
        print("3. Set it as BOT_TOKEN environment variable")
        print("\nExample: export BOT_TOKEN='123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11'")
        exit(1)
    
    # Create and run bot
    try:
        print("🤖 Starting pkUniRankBot...")
        print(f"📊 Version: python-telegram-bot v{telegram.__version__}")
        
        bot = UniRankBot(token)
        bot.start()
    except Exception as e:
        print(f"❌ Bot error: {e}")

if __name__ == "__main__":
    main()
