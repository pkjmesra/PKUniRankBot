"""
The MIT License (MIT)

Copyright (c) 2023 pkjmesra

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

"""
"""
pkUniRankBot - Telegram Bot for University Ranking
Compatible with python-telegram-bot v13.15 (Updater architecture)
"""

"""
pkUniRankBot - Telegram Bot for University Ranking with Excel Processing
"""

import os
import logging
import tempfile
import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional
from datetime import datetime
from dotenv import dotenv_values

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Document
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
AWAITING_EXCEL_FILE = range(1)

class UniversityRankingSystem:
    def __init__(self):
        self.parameters = {
            'academic': {'name': 'Academic Reputation & Research', 'max': 25},
            'graduate': {'name': 'Graduate Prospects', 'max': 25},
            'roi': {'name': 'ROI / Affordability', 'max': 20},
            'fsr': {'name': 'Faculty-Student Ratio', 'max': 15},
            'transparency': {'name': 'Transparency & Recognition', 'max': 10},
            'visibility': {'name': 'Visibility & Presence', 'max': 5}
        }
        
        self.country_multipliers = {
            'USA': 1.2, 'UK': 1.15, 'Canada': 1.1, 'Australia': 1.1,
            'Germany': 1.1, 'Switzerland': 1.15, 'Singapore': 1.1,
            'Japan': 1.05, 'Netherlands': 1.05, 'Sweden': 1.05,
            'France': 1.0, 'Italy': 0.95, 'Spain': 0.95,
            'China': 0.9, 'India': 0.85, 'Brazil': 0.85,
            'Russia': 0.85, 'South Africa': 0.85,
            'Ireland': 1.0, 'NewZealand': 1.0, 'New Zealand': 1.0
        }
    
    def normalize_country_name(self, country: str) -> str:
        """Normalize country names for consistent matching"""
        if pd.isna(country):
            return "Unknown"
        
        country_lower = str(country).strip().lower()
        
        country_mapping = {
            'usa': 'USA', 'united states': 'USA', 'united states of america': 'USA',
            'us': 'USA', 'u.s.': 'USA', 'u.s.a.': 'USA',
            'uk': 'UK', 'united kingdom': 'UK', 'britain': 'UK', 'great britain': 'UK',
            'england': 'UK', 'scotland': 'UK', 'wales': 'UK', 'northern ireland': 'UK',
            'canada': 'Canada', 'can': 'Canada',
            'australia': 'Australia', 'aus': 'Australia', 'oz': 'Australia',
            'ireland': 'Ireland', 'republic of ireland': 'Ireland', 'ire': 'Ireland',
            'new zealand': 'New Zealand', 'nz': 'New Zealand', 'newzealand': 'New Zealand',
            'germany': 'Germany', 'deutschland': 'Germany', 'germ': 'Germany'
        }
        
        return country_mapping.get(country_lower, str(country).strip().upper())
    
    def estimate_scores(self, name: str, country: str, leap_rank: Optional[float] = None) -> Dict[str, float]:
        """Estimate scores for universities"""
        if pd.isna(name):
            name = "Unknown University"
        
        name_lower = str(name).lower()
        normalized_country = self.normalize_country_name(country)
        
        # Base scores
        scores = {
            'academic': 12.0,
            'graduate': 15.0,
            'roi': 14.0,
            'fsr': 11.0,
            'transparency': 7.0,
            'visibility': 3.0
        }
        
        # Adjust based on name patterns (top universities)
        if any(word in name_lower for word in ['mit', 'massachusetts institute', 'harvard', 'stanford', 
                                              'oxford', 'cambridge', 'imperial', 'caltech']):
            scores = {'academic': 24, 'graduate': 23, 'roi': 22, 
                     'fsr': 14, 'transparency': 9, 'visibility': 5}
        elif 'university' in name_lower and 'state' in name_lower:
            scores.update({'academic': 16.0, 'roi': 16.0, 'transparency': 9.0, 'visibility': 4.0})
        elif 'university' in name_lower:
            scores.update({'academic': 18.0, 'visibility': 4.0, 'transparency': 8.0})
        elif 'college' in name_lower:
            scores.update({'graduate': 17.0, 'roi': 16.0, 'fsr': 12.0, 'academic': 8.0})
        
        # Apply country multiplier
        if normalized_country != "UNKNOWN":
            country_mult = self.country_multipliers.get(normalized_country, 1.0)
            for key in ['academic', 'graduate', 'roi', 'fsr']:
                scores[key] = min(self.parameters[key]['max'], scores[key] * country_mult)
        
        # Adjust based on Leap rank if available
        if leap_rank is not None and not pd.isna(leap_rank):
            # Better rank (lower number) = higher scores
            rank_factor = max(0.7, min(1.3, 50 / (leap_rank + 20)))
            for key in scores:
                scores[key] = min(self.parameters[key]['max'], scores[key] * rank_factor)
        
        # Add small variation
        for key in scores:
            variation = np.random.uniform(-1.0, 1.0)
            scores[key] = max(0, min(self.parameters[key]['max'], scores[key] + variation))
        
        return {k: round(v, 1) for k, v in scores.items()}
    
    def calculate_composite_score(self, scores: Dict[str, float]) -> float:
        """Calculate composite score"""
        return round(sum(scores.values()), 1)
    
    def process_excel_file(self, excel_path: str) -> str:
        """
        Process Excel file and calculate rankings
        Returns path to output file
        """
        try:
            # Read the Excel file
            df = pd.read_excel(excel_path)
            original_columns = df.columns.tolist()
            
            # Find relevant columns (case-insensitive)
            university_col = None
            country_col = None
            leap_rank_col = None
            
            for col in df.columns:
                col_lower = str(col).lower()
                if 'university' in col_lower or 'name' in col_lower or 'institution' in col_lower:
                    university_col = col
                elif 'country' in col_lower or 'nation' in col_lower:
                    country_col = col
                elif 'leap' in col_lower and 'rank' in col_lower:
                    leap_rank_col = col
                elif 'rank' in col_lower and not university_col:
                    # If no specific Leap rank column, check for any rank column
                    leap_rank_col = col
            
            # Validate required columns
            if not university_col:
                raise ValueError("Could not find University Name column in the Excel file.")
            if not country_col:
                raise ValueError("Could not find Country column in the Excel file.")
            
            logger.info(f"Processing: University column='{university_col}', Country column='{country_col}', Leap Rank column='{leap_rank_col}'")
            
            # Calculate scores for each university
            global_scores = []
            
            for idx, row in df.iterrows():
                university_name = row[university_col]
                country = row[country_col]
                leap_rank = row[leap_rank_col] if leap_rank_col and leap_rank_col in row else None
                
                # Skip empty rows
                if pd.isna(university_name):
                    continue
                
                # Calculate scores
                scores = self.estimate_scores(university_name, country, leap_rank)
                composite_score = self.calculate_composite_score(scores)
                
                global_scores.append({
                    'index': idx,
                    'university': university_name,
                    'country': country,
                    'leap_rank': leap_rank,
                    'global_score': composite_score
                })
            
            # Create DataFrame with scores
            scores_df = pd.DataFrame(global_scores)
            
            # Calculate Global Rank (higher score = better rank = lower rank number)
            scores_df['global_rank'] = scores_df['global_score'].rank(method='min', ascending=False).astype(int)
            
            # Calculate Country Rank for each country
            scores_df['country_rank'] = 0
            scores_df['rank_difference'] = ""
            
            for country in scores_df['country'].unique():
                if pd.isna(country):
                    continue
                    
                country_mask = scores_df['country'] == country
                country_scores = scores_df[country_mask].copy()
                
                # Calculate country rank (within country, by global_score)
                country_ranks = country_scores['global_score'].rank(method='min', ascending=False).astype(int)
                scores_df.loc[country_mask, 'country_rank'] = country_ranks.values
                
                # Check for Leap Rank differences
                for idx in country_scores.index:
                    leap_rank_val = scores_df.at[idx, 'leap_rank']
                    country_rank_val = scores_df.at[idx, 'country_rank']
                    
                    if not pd.isna(leap_rank_val):
                        try:
                            leap_rank_int = int(float(leap_rank_val))
                            if leap_rank_int != country_rank_val:
                                scores_df.at[idx, 'rank_difference'] = f"Leap:{leap_rank_int} Our:{country_rank_val}"
                        except:
                            pass
            
            # Merge scores back to original DataFrame
            for idx, row in scores_df.iterrows():
                original_idx = row['index']
                df.at[original_idx, 'Global Score'] = row['global_score']
                df.at[original_idx, 'Global Rank'] = row['global_rank']
                df.at[original_idx, 'Country Rank'] = row['country_rank']
                if row['rank_difference']:
                    df.at[original_idx, 'Rank Difference'] = row['rank_difference']
            
            # Ensure new columns are at the end
            new_columns = ['Global Score', 'Global Rank', 'Country Rank', 'Rank Difference']
            existing_columns = [col for col in df.columns if col not in new_columns]
            df = df[existing_columns + new_columns]
            
            # Create output file path
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"university_rankings_{timestamp}.xlsx"
            
            # Save with formatting
            with pd.ExcelWriter(output_filename, engine='openpyxl') as writer:
                df.to_excel(writer, sheet_name='Rankings', index=False)
                
                # Apply formatting if openpyxl is available
                try:
                    from openpyxl.styles import PatternFill
                    from openpyxl.utils import get_column_letter
                    
                    workbook = writer.book
                    worksheet = writer.sheets['Rankings']
                    
                    # Yellow fill for Rank Difference cells
                    yellow_fill = PatternFill(start_color='FFFF00', end_color='FFFF00', fill_type='solid')
                    
                    # Find Rank Difference column
                    for col_idx, col_name in enumerate(df.columns, 1):
                        if col_name == 'Rank Difference':
                            for row_idx in range(2, len(df) + 2):
                                cell = worksheet.cell(row=row_idx, column=col_idx)
                                if cell.value:  # If there's a difference
                                    cell.fill = yellow_fill
                            break
                    
                    # Adjust column widths
                    for column in df.columns:
                        column_letter = get_column_letter(list(df.columns).index(column) + 1)
                        max_length = max(
                            df[column].astype(str).apply(len).max(),
                            len(str(column))
                        ) + 2
                        worksheet.column_dimensions[column_letter].width = min(max_length, 30)
                        
                except ImportError:
                    logger.warning("openpyxl not available for advanced formatting")
            
            logger.info(f"Excel processing complete. Output saved to: {output_filename}")
            return output_filename
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise

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
        self.dispatcher.add_handler(CommandHandler("rank_excel", self.rank_excel_command))
        
        # Document handler for Excel files
        self.dispatcher.add_handler(MessageHandler(
            Filters.document.mime_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") |
            Filters.document.mime_type("application/vnd.ms-excel"),
            self.handle_excel_file
        ))
        
        # Callback query handler
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
    
    def start(self):
        """Start the bot"""
        print("🤖 pkUniRankBot is starting...")
        print("📊 University Ranking System Ready")
        print("📈 Excel Processing Enabled")
        print("⚡ Bot is running. Press Ctrl+C to stop.")
        
        self.updater.start_polling()
        self.updater.idle()
    
    def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        welcome_text = """
🎓 *Welcome to pkUniRankBot - Excel Edition!*

I can process Excel files with university rankings and calculate:

1. **Global Scores** - Based on 6 parameters
2. **Global Ranks** - Worldwide ranking
3. **Country Ranks** - Ranking within each country
4. **Rank Comparisons** - Compare with Leap ranks

*How to use:*
1. Send me an Excel file (.xlsx or .xls)
2. Or use /rank_excel to get instructions

The Excel file should have columns for:
- University/Institution Name
- Country
- (Optional) Leap Rank or other ranking

I'll add: Global Score, Global Rank, and Country Rank columns!
        """
        
        keyboard = [
            [InlineKeyboardButton("📊 Rank Excel File", callback_data="rank_excel")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
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
*📚 Excel Processing Help*

*Required Excel Columns:*
1. **University Name** column (any name containing "university", "name", or "institution")
2. **Country** column (any name containing "country" or "nation")

*Optional Column:*
3. **Leap Rank** or any rank column (for comparison)

*What I Do:*
1. Read your Excel file
2. Calculate Global Score (0-100) for each university
3. Calculate Global Rank (worldwide)
4. Calculate Country Rank (within each country)
5. Highlight differences between Leap Rank and our Country Rank
6. Send back updated Excel file

*How to Send Files:*
1. Attach Excel file to a message
2. Or use drag & drop in Telegram

*Supported Formats:*
- .xlsx (Excel 2007+)
- .xls (Excel 97-2003)

*Example Output Columns Added:*
- Global Score (0-100)
- Global Rank (1 = best)
- Country Rank (1 = best in country)
- Rank Difference (if differs from Leap Rank)
        """
        
        update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    def rank_excel_command(self, update: Update, context: CallbackContext):
        """Handle /rank_excel command"""
        instructions = """
*📊 Excel Ranking Instructions*

Please send me an Excel file with university data.

*Required Columns:*
- University/Institution names
- Country names

*Optional Column:*
- Any ranking column (e.g., Leap Rank)

*I will automatically detect:*
- University names (columns with "university", "name", or "institution")
- Countries (columns with "country" or "nation")
- Rankings (columns with "rank" in name)

*Just send me your Excel file now!*
        """
        
        update.message.reply_text(instructions, parse_mode=ParseMode.MARKDOWN)
    
    def handle_excel_file(self, update: Update, context: CallbackContext):
        """Handle incoming Excel files"""
        try:
            # Get the document
            document = update.message.document
            
            # Send processing message
            processing_msg = update.message.reply_text(
                "📥 *File Received!*\n\nProcessing your Excel file...\nThis may take a moment.",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Download the file
            file = context.bot.get_file(document.file_id)
            
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
                file.download(tmp_file.name)
                input_path = tmp_file.name
            
            # Process the Excel file
            output_path = self.ranking_system.process_excel_file(input_path)
            
            # Update processing message
            processing_msg.edit_text(
                "✅ *Processing Complete!*\n\nGenerating ranked Excel file...",
                parse_mode=ParseMode.MARKDOWN
            )
            
            # Send the processed file back
            with open(output_path, 'rb') as result_file:
                context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=result_file,
                    filename=os.path.basename(output_path),
                    caption="🎯 *Ranked Universities Excel File*\n\nAdded columns:\n• Global Score\n• Global Rank\n• Country Rank\n• Rank Difference\n\nYellow highlights show where our Country Rank differs from Leap Rank.",
                    parse_mode=ParseMode.MARKDOWN
                )
            
            # Clean up temporary files
            try:
                os.unlink(input_path)
                os.unlink(output_path)
            except:
                pass
            
            # Send completion message
            update.message.reply_text(
                "✨ *Analysis Complete!*\n\nYour ranked file has been sent above.\n\n"
                "Want to process another file? Just send it!",
                parse_mode=ParseMode.MARKDOWN
            )
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            error_msg = f"❌ *Error Processing File*\n\nSorry, I couldn't process your Excel file.\n\nError: {str(e)}"
            
            if update.message:
                update.message.reply_text(error_msg, parse_mode=ParseMode.MARKDOWN)
            else:
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=error_msg,
                    parse_mode=ParseMode.MARKDOWN
                )
    
    def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        if data == "rank_excel":
            self.rank_excel_command(query.message, context)
        elif data == "help":
            self.help_command(query.message, context)

def main():
    """Main function to run the bot"""
    # Check for required packages
    try:
        import telegram
        import numpy
        import pandas
        import openpyxl
    except ImportError as e:
        print(f"❌ Missing package: {e}")
        print("Install with: pip install python-telegram-bot numpy pandas openpyxl python-dotenv")
        exit(1)
    
    # Get bot token
    token = BOT_TOKEN
    if token == 'YOUR_BOT_TOKEN_HERE':
        print("\n❌ ERROR: Bot token not set!")
        print("Please set your bot token in .env.dev file:")
        print("BOT_TOKEN='your_telegram_bot_token_here'")
        exit(1)
    
    # Create and run bot
    try:
        print("🤖 Starting pkUniRankBot with Excel Processing...")
        print(f"📊 Version: python-telegram-bot v{telegram.__version__}")
        print(f"📈 pandas v{pandas.__version__}")
        
        bot = UniRankBot(token)
        bot.start()
    except Exception as e:
        print(f"❌ Bot error: {e}")

if __name__ == "__main__":
    main()
