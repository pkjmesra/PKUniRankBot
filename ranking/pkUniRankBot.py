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
pkUniRankBot - Telegram Bot for University Ranking with Excel Processing
Compatible with python-telegram-bot v13.15 (Updater architecture)
"""

import os
import logging
import tempfile
from typing import Dict, Tuple, List, Optional
from dataclasses import dataclass
from datetime import datetime
import numpy as np
import pandas as pd
from dotenv import dotenv_values

# Import for telegram bot v13.15
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
    rationale: Dict[str, List[str]] = None
    sources: List[str] = None

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
            'Russia': 0.85, 'South Africa': 0.85,
            'Ireland': 1.0, 'NewZealand': 1.0, 'New Zealand': 1.0
        }
        
        # Parameter rationale templates
        self.parameter_rationale_templates = {
            'academic': [
                "Based on research output and citations",
                "Academic reputation from surveys",
                "Faculty qualifications and awards",
                "Research funding and grants",
                "Publication quality in indexed journals"
            ],
            'graduate': [
                "Employment rate within 6 months of graduation",
                "Average starting salary of graduates",
                "Employer satisfaction surveys",
                "Career services effectiveness",
                "Alumni network strength"
            ],
            'roi': [
                "Return on Investment calculation",
                "Tuition fees relative to earning potential",
                "Financial aid availability",
                "Scholarship opportunities",
                "Cost of living considerations"
            ],
            'fsr': [
                "Student to faculty ratio",
                "Average class sizes",
                "Faculty availability for mentorship",
                "Teaching quality indicators",
                "Student support services"
            ],
            'transparency': [
                "Accreditation status",
                "Data availability and reporting",
                "Institutional recognition",
                "Quality assurance processes",
                "Governance transparency"
            ],
            'visibility': [
                "Web presence and digital footprint",
                "International recognition",
                "Brand strength and reputation",
                "Social media engagement",
                "Media mentions and coverage"
            ]
        }
        
        # Common data sources
        self.common_sources = [
            "QS World University Rankings",
            "Times Higher Education (THE)",
            "Academic Ranking of World Universities (ARWU)",
            "U.S. News & World Report",
            "Forbes College Rankings",
            "National Center for Education Statistics",
            "Institutional websites and reports",
            "Government education databases",
            "Employer surveys and reports",
            "Alumni outcome surveys"
        ]
    
    def load_university_database(self) -> Dict:
        """Load university database with pre-calculated scores"""
        return {
            'bryant university': {
                'country': 'USA',
                'type': 'TEACHING_UNIVERSITY',
                'scores': {'academic': 12, 'graduate': 22, 'roi': 16, 
                          'fsr': 13, 'transparency': 8, 'visibility': 3},
                'description': 'Private business-focused university',
                'rationale': {
                    'academic': ['Strong business program focus', 'Limited research output'],
                    'graduate': ['High business placement rate', 'Strong corporate partnerships'],
                    'roi': ['Competitive tuition for business education', 'Good salary outcomes']
                }
            },
            'massachusetts institute of technology': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 24, 'graduate': 23, 'roi': 22, 
                          'fsr': 14, 'transparency': 9, 'visibility': 5},
                'description': 'World-renowned research university',
                'rationale': {
                    'academic': ['Top research output globally', 'Nobel laureate faculty'],
                    'graduate': ['Highly sought after by employers', 'Exceptional starting salaries'],
                    'roi': ['High earning potential offsets cost', 'Strong financial aid']
                }
            },
            'harvard university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 25, 'graduate': 24, 'roi': 20, 
                          'fsr': 13, 'transparency': 10, 'visibility': 5},
                'description': 'Ivy League research university',
                'rationale': {
                    'academic': ['World-leading research institution', 'Extensive library resources'],
                    'graduate': ['Exceptional career outcomes', 'Powerful alumni network'],
                    'roi': ['Premium brand value', 'Generous financial aid programs']
                }
            },
            'stanford university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 24, 'graduate': 23, 'roi': 21, 
                          'fsr': 14, 'transparency': 9, 'visibility': 5},
                'description': 'Leading research university',
                'rationale': {
                    'academic': ['Silicon Valley research hub', 'Innovation-focused programs'],
                    'graduate': ['Strong tech industry placement', 'Entrepreneurship support'],
                    'roi': ['High tech industry salaries', 'Startup success stories']
                }
            },
            'university of toronto': {
                'country': 'Canada',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 22, 'graduate': 21, 'roi': 18, 
                          'fsr': 13, 'transparency': 9, 'visibility': 4},
                'description': 'Top Canadian research university',
                'rationale': {
                    'academic': ['Leading Canadian research output', 'Strong international collaborations'],
                    'graduate': ['Good employment outcomes in Canada', 'Strong professional networks'],
                    'roi': ['Lower cost than US peers', 'Good Canadian job market access']
                }
            },
            'university of oxford': {
                'country': 'UK',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 25, 'graduate': 24, 'roi': 19, 
                          'fsr': 14, 'transparency': 10, 'visibility': 5},
                'description': 'Historic research university',
                'rationale': {
                    'academic': ['Centuries of academic tradition', 'World-class research facilities'],
                    'graduate': ['Excellent global employment prospects', 'Prestigious alumni network'],
                    'roi': ['International brand recognition', 'Strong scholarship programs']
                }
            },
            'conestoga college': {
                'country': 'Canada',
                'type': 'COLLEGE_POLYTECHNIC',
                'scores': {'academic': 4.0, 'graduate': 20.0, 'roi': 17.5, 
                          'fsr': 12.5, 'transparency': 6.5, 'visibility': 3.5},
                'description': 'Canadian polytechnic institute',
                'rationale': {
                    'academic': ['Applied learning focus', 'Limited research scope'],
                    'graduate': ['Strong industry partnerships', 'Practical skill development'],
                    'roi': ['Affordable tuition', 'Quick entry to workforce']
                }
            },
            'algonquin college': {
                'country': 'Canada',
                'type': 'COLLEGE_POLYTECHNIC',
                'scores': {'academic': 3.5, 'graduate': 19.0, 'roi': 17.0, 
                          'fsr': 12.0, 'transparency': 6.0, 'visibility': 3.0},
                'description': 'Canadian college',
                'rationale': {
                    'academic': ['Vocational education focus', 'Certificate/diploma programs'],
                    'graduate': ['Industry-relevant training', 'Local employment focus'],
                    'roi': ['Cost-effective education', 'Short program duration']
                }
            },
            'north dakota state university': {
                'country': 'USA',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 15.6, 'graduate': 15.0, 'roi': 16.1, 
                          'fsr': 11.0, 'transparency': 9.0, 'visibility': 4.0},
                'description': 'Public research university',
                'rationale': {
                    'academic': ['Regional research strength', 'Specialized programs'],
                    'graduate': ['Strong regional employment', 'Industry connections'],
                    'roi': ['Public university affordability', 'Good value education']
                }
            },
            'university of tokyo': {
                'country': 'Japan',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 23, 'graduate': 21, 'roi': 18, 
                          'fsr': 13, 'transparency': 8, 'visibility': 4},
                'description': 'Top Japanese university',
                'rationale': {
                    'academic': ['Leading Asian research institution', 'Strong STEM programs'],
                    'graduate': ['Excellent domestic employment', 'Corporate Japan connections'],
                    'roi': ['Subsidized tuition in Japan', 'Strong Japanese economy']
                }
            },
            'university of sydney': {
                'country': 'Australia',
                'type': 'RESEARCH_UNIVERSITY',
                'scores': {'academic': 21, 'graduate': 20, 'roi': 17, 
                          'fsr': 12, 'transparency': 8, 'visibility': 4},
                'description': 'Australian research university',
                'rationale': {
                    'academic': ['Strong research in Australia', 'International student focus'],
                    'graduate': ['Good Australia/NZ employment', 'Asia-Pacific opportunities'],
                    'roi': ['International student market', 'Strong Australian education brand']
                }
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
    
    def generate_rationale_for_score(self, param_code: str, score: float, max_score: float, 
                                   university_name: str, country: str, is_estimated: bool) -> List[str]:
        """Generate rationale for a parameter score"""
        rationale = []
        percentage = (score / max_score * 100) if max_score > 0 else 0
        
        # Get base rationale templates
        base_rationale = self.parameter_rationale_templates.get(param_code, [])
        
        # Add score-specific rationale
        if percentage >= 80:
            rationale.append(f"Excellent performance ({percentage:.1f}% of max)")
            rationale.append("Exceeds international benchmarks")
        elif percentage >= 60:
            rationale.append(f"Good performance ({percentage:.1f}% of max)")
            rationale.append("Meets or exceeds most standards")
        elif percentage >= 40:
            rationale.append(f"Average performance ({percentage:.1f}% of max)")
            rationale.append("Room for improvement in some areas")
        else:
            rationale.append(f"Below average performance ({percentage:.1f}% of max)")
            rationale.append("Significant improvement needed")
        
        # Add estimation note if applicable
        if is_estimated:
            rationale.append("Score based on pattern analysis and estimation")
            rationale.append("Actual performance may vary")
        
        # Add country context
        if country:
            rationale.append(f"Context: {country} higher education system")
        
        # Add university type context
        uni_type = self.classify_university_type(university_name)
        rationale.append(f"Institution type: {uni_type.replace('_', ' ').title()}")
        
        return rationale
    
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
    
    def get_sources_for_university(self, university_name: str, is_estimated: bool) -> List[str]:
        """Get data sources for university ranking"""
        sources = []
        
        if not is_estimated:
            sources.extend([
                "Institutional annual reports",
                "Accreditation agency data",
                "Government education statistics",
                "International ranking databases"
            ])
        else:
            sources.extend([
                "Pattern analysis of similar institutions",
                "Country education system benchmarks",
                "Institution type averages",
                "Statistical estimation models"
            ])
        
        # Add common sources
        sources.extend(self.common_sources[:4])
        
        return sources
    
    def rank_university(self, university_name: str, country: str = "") -> UniversityData:
        """Main ranking function for single university"""
        name_lower = university_name.lower()
        is_estimated = name_lower not in self.university_db
        
        # Check database first
        if name_lower in self.university_db:
            data = self.university_db[name_lower]
            scores = data['scores']
            university_type = data['type']
            db_country = data['country']
            db_rationale = data.get('rationale', {})
        else:
            # Estimate scores
            scores = self.estimate_scores(university_name, country)
            university_type = self.classify_university_type(university_name)
            db_country = country if country else "Global"
            db_rationale = {}
        
        # Generate rationale for each parameter
        rationale = {}
        for param_code, score in scores.items():
            max_score = self.parameters[param_code]['max']
            if param_code in db_rationale:
                rationale[param_code] = db_rationale[param_code]
            else:
                rationale[param_code] = self.generate_rationale_for_score(
                    param_code, score, max_score, university_name, db_country, is_estimated
                )
        
        # Get data sources
        sources = self.get_sources_for_university(university_name, is_estimated)
        
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
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            rationale=rationale,
            sources=sources
        )
    
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
                elif 'rank' in col_lower and leap_rank_col is None:
                    leap_rank_col = col
            
            # Validate required columns
            if not university_col:
                raise ValueError("Could not find University Name column in the Excel file.")
            if not country_col:
                raise ValueError("Could not find Country column in the Excel file.")
            
            logger.info(f"Processing Excel: University='{university_col}', Country='{country_col}', Leap Rank='{leap_rank_col}'")
            
            # Calculate scores for each university
            global_scores = []
            
            for idx, row in df.iterrows():
                university_name = row[university_col]
                country = row[country_col]
                leap_rank = row[leap_rank_col] if leap_rank_col and leap_rank_col in row and not pd.isna(row[leap_rank_col]) else None
                
                # Skip empty rows
                if pd.isna(university_name):
                    continue
                
                # Calculate scores using existing ranking function
                university_data = self.rank_university(str(university_name), str(country) if not pd.isna(country) else "")
                
                global_scores.append({
                    'index': idx,
                    'university': university_name,
                    'country': country,
                    'leap_rank': leap_rank,
                    'global_score': university_data.composite,
                    'tier': university_data.tier
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
                
                if len(country_scores) > 0:
                    # Calculate country rank (within country, by global_score)
                    country_ranks = country_scores['global_score'].rank(method='min', ascending=False).astype(int)
                    scores_df.loc[country_mask, 'country_rank'] = country_ranks.values
                    
                    # Check for Leap Rank differences
                    for idx in country_scores.index:
                        leap_rank_val = scores_df.at[idx, 'leap_rank']
                        country_rank_val = scores_df.at[idx, 'country_rank']
                        
                        if leap_rank_val is not None and not pd.isna(leap_rank_val):
                            try:
                                leap_rank_int = int(float(leap_rank_val))
                                if leap_rank_int != country_rank_val:
                                    scores_df.at[idx, 'rank_difference'] = f"Leap:{leap_rank_int} Our:{country_rank_val}"
                            except (ValueError, TypeError):
                                pass
            
            # Merge scores back to original DataFrame
            for idx, row in scores_df.iterrows():
                original_idx = row['index']
                df.at[original_idx, 'Global Score'] = row['global_score']
                df.at[original_idx, 'Global Rank'] = row['global_rank']
                df.at[original_idx, 'Country Rank'] = row['country_rank']
                if row['rank_difference']:
                    df.at[original_idx, 'Rank Difference'] = row['rank_difference']
                else:
                    df.at[original_idx, 'Rank Difference'] = ""
            
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
                
                # Apply formatting
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
                                if cell.value and str(cell.value).strip():  # If there's a difference
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
        
        # Store current ranking data for rationale viewing
        self.user_ranking_data = {}
        
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
        self.dispatcher.add_handler(CommandHandler("rank_excel", self.rank_excel_command))
        
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
        
        # Document handler for Excel files
        self.dispatcher.add_handler(MessageHandler(
            Filters.document.mime_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") |
            Filters.document.mime_type("application/vnd.ms-excel"),
            self.handle_excel_file
        ))
        
        # Callback query handler for buttons
        self.dispatcher.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Message handler for direct ranking
        self.dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, self.handle_direct_message))
        
        # Error handler
        self.dispatcher.add_error_handler(self.error_handler)
    
    def error_handler(self, update: Update, context: CallbackContext):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        try:
            if update and update.effective_message:
                update.effective_message.reply_text(
                    "❌ Sorry, an error occurred. Please try again.",
                    parse_mode=ParseMode.HTML
                )
        except:
            pass
    
    def start(self):
        """Start the bot"""
        print("🤖 pkUniRankBot is starting...")
        print("📊 University Ranking System Ready")
        print("📈 Excel Processing Enabled")
        print("⚡ Bot is running. Press Ctrl+C to stop.")
        
        self.updater.start_polling()
        self.updater.idle()
    
    # Command handlers
    def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        user = update.message.from_user
        welcome_text = f"""
🎓 Welcome to <b>pkUniRankBot</b> {user.first_name}!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

<b>Available Commands:</b>
/rank - Rank a single university
/rank_excel - Process Excel file with multiple universities
/tiers - View tier explanations  
/parameters - View ranking parameters
/help - Get help

<b>How to use:</b>
• Send /rank for single university ranking
• Send Excel file for bulk ranking
• Click buttons below to explore

<b>Excel Processing:</b>
Send me an Excel file with university names and countries, and I'll add:
• Global Score • Global Rank • Country Rank
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 Process Excel File", callback_data="rank_excel")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        help_text = """
<b>📚 pkUniRankBot Help</b>

<b>Ranking Methodology:</b>
This bot uses a multi-parameter scoring system:
• Academic Reputation & Research (25%)
• Graduate Prospects (25%)  
• ROI / Affordability (20%)
• Faculty-Student Ratio (15%)
• Transparency & Recognition (10%)
• Visibility & Presence (5%)

<b>Tier System:</b>
A+ (85-100): World-class
A (75-84): Excellent
B (65-74): Good
C+ (55-64): Average
C (45-54): Below average
D (0-44): Poor

<b>Commands:</b>
/start - Start the bot
/rank - Rank a single university
/rank_excel - Process Excel file with universities
/tiers - View tier details
/parameters - View parameter details
/help - This help message

<b>Excel File Format:</b>
Send Excel file with columns for:
• University/Institution names
• Country names
• (Optional) Leap Rank or other ranking

I'll add: Global Score, Global Rank, and Country Rank columns!
        """
        
        update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    
    def tiers_command(self, update: Update, context: CallbackContext):
        """Handle /tiers command"""
        tiers_text = """
<b>🏆 Ranking Tiers & Ranges</b>

<b>A+ (85-100)</b> 🎖️
World-class institutions with exceptional performance across all metrics.

<b>A (75-84)</b> ⭐  
Excellent institutions with strong performance and areas of excellence.

<b>B (65-74)</b> 👍
Good institutions with solid performance with some excellent areas.

<b>C+ (55-64)</b> 📊
Average institutions meeting basic standards.

<b>C (45-54)</b> ⚠️
Below average institutions needing significant improvement.

<b>D (0-44)</b> 🚨
Poor performance across most metrics.

<b>Error Margin:</b> ±2-15 points based on data availability.
        """
        
        update.message.reply_text(tiers_text, parse_mode=ParseMode.HTML)
    
    def parameters_command(self, update: Update, context: CallbackContext):
        """Handle /parameters command"""
        params_text = """
<b>📊 Ranking Parameters</b>

<b>1. Academic Reputation & Research (25%)</b>
Research output, citations, academic prestige, faculty quality.

<b>2. Graduate Prospects (25%)</b>
Employment rate, starting salary, employer partnerships.

<b>3. ROI / Affordability (20%)</b>
Return on Investment = Median Salary / Total Cost.

<b>4. Faculty-Student Ratio (15%)</b>
FTE Students / FTE Faculty. Class sizes.

<b>5. Transparency & Recognition (10%)</b>
Accreditation, official recognition, data availability.

<b>6. Visibility & Presence (5%)</b>
Institutional web presence, brand recognition.

<b>Scoring:</b> Each parameter scored 0 to max, composite = sum of all scores.
        """
        
        update.message.reply_text(params_text, parse_mode=ParseMode.HTML)
    
    def rank_excel_command(self, update: Update, context: CallbackContext):
        """Handle /rank_excel command"""
        instructions = """
<b>📊 Excel Ranking Instructions</b>

Please send me an Excel file (.xlsx or .xls) with university data.

<b>Required Columns:</b>
- University/Institution names
- Country names

<b>Optional Column:</b>
- Any ranking column (e.g., Leap Rank)

<b>I will automatically detect columns and add:</b>
- Global Score (0-100)
- Global Rank (1 = best worldwide)
- Country Rank (1 = best in country)
- Rank Difference (highlighted if differs from Leap Rank)

<b>Just send me your Excel file now!</b>
        """
        
        update.message.reply_text(instructions, parse_mode=ParseMode.HTML)
    
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
            
            self.perform_ranking(update, university_name, country, context)
        else:
            # Start interactive ranking
            self.start_ranking(update, context)
    
    def start_ranking(self, update: Update, context: CallbackContext):
        """Start the ranking conversation"""
        update.message.reply_text(
            "🎓 <b>University Ranking</b>\n\nPlease enter the university name:",
            parse_mode=ParseMode.HTML
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
            f"📝 University: <b>{university_name}</b>\n\nNow enter the country (or select from buttons):",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
        
        return AWAITING_COUNTRY
    
    def get_country(self, update: Update, context: CallbackContext):
        """Get country from user and perform ranking"""
        university_name = context.user_data.get('university_name', '')
        country = update.message.text.strip()
        
        self.perform_ranking(update, university_name, country, context)
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
                self.perform_ranking(update, university_name, country, context)
                return
        
        # Otherwise show help
        update.message.reply_text(
            "To rank a university, use:\n"
            "• /rank command\n"
            "• Or send: <b>University Name, Country</b>\n"
            "• Or click the Rank button from /start",
            parse_mode=ParseMode.HTML
        )
    
    def handle_excel_file(self, update: Update, context: CallbackContext):
        """Handle incoming Excel files"""
        try:
            # Get the document
            document = update.message.document
            
            # Send processing message
            processing_msg = update.message.reply_text(
                "📥 <b>File Received!</b>\n\nProcessing your Excel file...\nThis may take a moment.",
                parse_mode=ParseMode.HTML
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
                "✅ <b>Processing Complete!</b>\n\nGenerating ranked Excel file...",
                parse_mode=ParseMode.HTML
            )
            
            # Send the processed file back
            with open(output_path, 'rb') as result_file:
                context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=result_file,
                    filename=os.path.basename(output_path),
                    caption="🎯 <b>Ranked Universities Excel File</b>\n\nAdded columns:\n• Global Score\n• Global Rank\n• Country Rank\n• Rank Difference\n\nYellow highlights show where our Country Rank differs from Leap Rank.",
                    parse_mode=ParseMode.HTML
                )
            
            # Clean up temporary files
            try:
                os.unlink(input_path)
                os.unlink(output_path)
            except:
                pass
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            error_msg = f"❌ <b>Error Processing File</b>\n\nSorry, I couldn't process your Excel file.\n\nError: {str(e)}"
            
            if update.message:
                update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
            else:
                context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=error_msg,
                    parse_mode=ParseMode.HTML
                )
    
    def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        query.answer()
        
        data = query.data
        
        if data == "start_ranking":
            query.edit_message_text(
                "🎓 <b>University Ranking</b>\n\nPlease enter the university name:",
                parse_mode=ParseMode.HTML
            )
            query.message.reply_text("Please use /rank command to start ranking.")
        
        elif data == "rank_excel":
            self.rank_excel_command(query.message, context)
        
        elif data == "view_tiers":
            self.show_tiers(query)
        
        elif data == "view_parameters":
            self.show_parameters(query)
        
        elif data == "main_menu":
            self.show_main_menu(query)
        
        elif data == "rank_another":
            query.edit_message_text(
                "🎓 <b>University Ranking</b>\n\nPlease enter the university name:",
                parse_mode=ParseMode.HTML
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
        
        elif data.startswith("rationale_"):
            # Handle rationale viewing
            parts = data.split("_")
            if len(parts) >= 3:
                param_code = parts[1]
                user_id = query.from_user.id
                
                if user_id in self.user_ranking_data:
                    ranking_data = self.user_ranking_data[user_id]
                    self.show_parameter_rationale(query, param_code, ranking_data)
        
        elif data == "view_all_rationales":
            # Show all parameter rationales
            user_id = query.from_user.id
            if user_id in self.user_ranking_data:
                ranking_data = self.user_ranking_data[user_id]
                self.show_all_rationales(query, ranking_data)
        
        elif data == "view_sources":
            # Show composite score sources
            user_id = query.from_user.id
            if user_id in self.user_ranking_data:
                ranking_data = self.user_ranking_data[user_id]
                self.show_sources(query, ranking_data)
    
    def perform_ranking(self, update: Update, university_name: str, country: str, context: CallbackContext):
        """Perform ranking and send results"""
        processing_msg = update.message.reply_text(
            f"🔍 <b>Analyzing {university_name}...</b>\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country)
            
            # Store ranking data for rationale viewing
            user_id = update.effective_user.id
            self.user_ranking_data[user_id] = ranking_data
            
            # Format results
            results_text = self.format_ranking_results(ranking_data)
            
            # Send results
            processing_msg.edit_text(
                results_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_results_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error ranking university: {e}")
            error_text = f"❌ <b>Error Ranking University</b>\n\nSorry, I couldn't analyze <b>{university_name}</b>.\n\nPlease try again."
            
            processing_msg.edit_text(
                error_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_error_keyboard()
            )
    
    def perform_ranking_callback(self, query, university_name: str, country: str):
        """Perform ranking from callback"""
        query.edit_message_text(
            f"🔍 <b>Analyzing {university_name}...</b>\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country)
            
            # Store ranking data for rationale viewing
            user_id = query.from_user.id
            self.user_ranking_data[user_id] = ranking_data
            
            # Format results
            results_text = self.format_ranking_results(ranking_data)
            
            # Send results
            query.edit_message_text(
                results_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_results_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error ranking university: {e}")
            error_text = f"❌ <b>Error Ranking University</b>\n\nSorry, I couldn't analyze <b>{university_name}</b>.\n\nPlease try again."
            
            query.edit_message_text(
                error_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_error_keyboard()
            )
    
    def show_parameter_rationale(self, query, param_code: str, ranking_data: UniversityData):
        """Show rationale for a specific parameter"""
        param_info = self.ranking_system.parameters.get(param_code, {})
        param_name = param_info.get('name', param_code)
        score = ranking_data.scores.get(param_code, 0)
        max_score = param_info.get('max', 1)
        percentage = (score / max_score * 100) if max_score > 0 else 0
        
        # Get rationale
        rationale_list = ranking_data.rationale.get(param_code, ["No rationale available"])
        
        # Format rationale text
        rationale_text = f"""
<b>📋 {param_name} - Score Rationale</b>
<b>Score:</b> {score:.1f}/{max_score} ({percentage:.1f}%)

<b>🔍 Rationale:</b>
"""
        
        for i, item in enumerate(rationale_list, 1):
            rationale_text += f"{i}. {item}\n"
        
        # Add back button
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Results", callback_data=f"rationale_back_{param_code}")],
            [InlineKeyboardButton("📊 View All Parameters", callback_data="view_all_rationales")],
            [InlineKeyboardButton("📚 View Sources", callback_data="view_sources")],
            [InlineKeyboardButton("🎯 Rank Another", callback_data="rank_another")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            rationale_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def show_all_rationales(self, query, ranking_data: UniversityData):
        """Show all parameter rationales in one view"""
        rationales_text = f"""
<b>📊 All Parameter Rationales for {ranking_data.name}</b>
<b>Composite Score:</b> {ranking_data.composite:.1f}/100
<b>Tier:</b> {ranking_data.tier}

"""
        
        for param_code, param_info in self.ranking_system.parameters.items():
            score = ranking_data.scores.get(param_code, 0)
            max_score = param_info['max']
            percentage = (score / max_score * 100) if max_score > 0 else 0
            
            rationales_text += f"<b>{param_info['name']}</b>\n"
            rationales_text += f"Score: {score:.1f}/{max_score} ({percentage:.1f}%)\n"
            
            # Show first 2 rationale points
            rationale_list = ranking_data.rationale.get(param_code, [])
            if rationale_list:
                for i in range(min(2, len(rationale_list))):
                    rationales_text += f"  • {rationale_list[i]}\n"
            
            rationales_text += "\n"
        
        rationales_text += "<b>💡 View detailed rationale for each parameter using the buttons below</b>"
        
        # Create parameter-specific buttons
        keyboard = []
        for param_code, param_info in self.ranking_system.parameters.items():
            short_name = param_info['name'].split('&')[0].strip()
            if len(short_name) > 15:
                short_name = short_name[:13] + ".."
            keyboard.append([InlineKeyboardButton(
                f"🔍 {short_name}",
                callback_data=f"rationale_{param_code}"
            )])
        
        keyboard.append([
            InlineKeyboardButton("📚 View Sources", callback_data="view_sources"),
            InlineKeyboardButton("🔙 Back to Results", callback_data="view_all_back")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            rationales_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def show_sources(self, query, ranking_data: UniversityData):
        """Show data sources for composite score"""
        sources_text = f"""
<b>📚 Data Sources & Methodology for {ranking_data.name}</b>

<b>Composite Score Calculation:</b>
Sum of all parameter scores (max 100 points)

<b>Parameter Weighting:</b>
Academic Reputation & Research: 25%
Graduate Prospects: 25%
ROI / Affordability: 20%
Faculty-Student Ratio: 15%
Transparency & Recognition: 10%
Visibility & Presence: 5%

<b>Data Sources Used:</b>
"""
        
        for i, source in enumerate(ranking_data.sources, 1):
            sources_text += f"{i}. {source}\n"
        
        # Add confidence information
        if ranking_data.error_margin <= 3:
            confidence = "High"
            sources_text += f"\n<b>🔍 Data Confidence:</b> {confidence}\n"
            sources_text += "<b>📊 Note:</b> Based on verified institutional data\n"
        elif ranking_data.error_margin <= 7:
            confidence = "Moderate"
            sources_text += f"\n<b>🔍 Data Confidence:</b> {confidence}\n"
            sources_text += "<b>📊 Note:</b> Based on estimation with reliable proxies\n"
        else:
            confidence = "Low"
            sources_text += f"\n<b>🔍 Data Confidence:</b> {confidence}\n"
            sources_text += "<b>📊 Note:</b> Based on statistical estimation and patterns\n"
        
        sources_text += f"<b>📈 Error Margin:</b> ±{ranking_data.error_margin} points\n"
        
        # Add back button
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Results", callback_data="sources_back")],
            [InlineKeyboardButton("📊 View All Rationales", callback_data="view_all_rationales")],
            [InlineKeyboardButton("🎯 Rank Another", callback_data="rank_another")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            sources_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def show_tiers(self, query):
        """Show tiers information"""
        tiers_text = """
<b>🏆 Ranking Tiers & Ranges</b>

<b>A+ (85-100)</b> 🎖️
World-class institutions with exceptional performance.

<b>A (75-84)</b> ⭐  
Excellent institutions with strong performance.

<b>B (65-74)</b> 👍
Good institutions with solid performance.

<b>C+ (55-64)</b> 📊
Average institutions meeting basic standards.

<b>C (45-54)</b> ⚠️
Below average institutions needing improvement.

<b>D (0-44)</b> 🚨
Poor performance across most metrics.

<b>Error Margin:</b> ±2-15 points based on data availability.
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            tiers_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def show_parameters(self, query):
        """Show parameters information"""
        params_text = """
<b>📊 Ranking Parameters</b>

<b>1. Academic Reputation & Research (25%)</b>
Research output, citations, academic prestige.

<b>2. Graduate Prospects (25%)</b>
Employment rate, starting salary.

<b>3. ROI / Affordability (20%)</b>
Return on Investment = Salary / Cost.

<b>4. Faculty-Student Ratio (15%)</b>
Students / Faculty ratio.

<b>5. Transparency & Recognition (10%)</b>
Accreditation, data availability.

<b>6. Visibility & Presence (5%)</b>
Web presence, brand recognition.

<b>Scoring:</b> Each parameter scored 0 to max.
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            params_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def show_main_menu(self, query):
        """Show main menu"""
        welcome_text = """
🎓 Welcome to <b>pkUniRankBot</b>!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

Click the buttons below to get started!
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 Process Excel File", callback_data="rank_excel")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def format_ranking_results(self, data: UniversityData) -> str:
        """Format ranking results as HTML text"""
        # Header
        results = f"""
<b>🏛️ {data.name}</b>
<b>🌍 {data.country}</b>
<b>🎓 {data.type}</b>

<b>📅 Analysis Date:</b> {data.timestamp}
<b>📊 Data Confidence:</b> ±{data.error_margin} points
        """
        
        # Parameter scores
        results += "\n\n<b>📈 PARAMETER SCORES:</b>\n"
        results += "<pre>\n"
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
        results += "</pre>\n"
        
        # Composite score and tier
        results += f"\n<b>🎯 COMPOSITE SCORE:</b> {data.composite:.1f}/100\n"
        results += f"<b>🏆 TIER:</b> {data.tier}\n"
        
        # Get tier description
        tier_desc = self.ranking_system.tiers.get(data.tier, ("", "", ""))[2]
        results += f"<b>💡 ASSESSMENT:</b> {tier_desc}\n"
        
        # Error margin explanation
        if data.error_margin <= 3:
            confidence = "High (Known institution)"
        elif data.error_margin <= 7:
            confidence = "Moderate (Estimated)"
        else:
            confidence = "Low (Limited data)"
        
        results += f"\n<b>📊 ERROR MARGIN:</b> ±{data.error_margin} points\n"
        results += f"<b>🔍 CONFIDENCE:</b> {confidence}\n"
        
        # Recommendations
        results += "\n<b>📝 RECOMMENDATIONS:</b>\n"
        if data.tier in ['A+', 'A']:
            results += "• Maintain strong performance\n• Enhance international partnerships\n• Invest in research\n"
        elif data.tier == 'B':
            results += "• Strengthen research output\n• Improve graduate employment\n• Enhance visibility\n"
        elif data.tier == 'C+':
            results += "• Focus on employability\n• Improve faculty ratio\n• Enhance transparency\n"
        elif data.tier in ['C', 'D']:
            results += "• Urgent improvement needed\n• Focus on core competencies\n• Seek accreditation\n"
        
        # Add rationale prompt
        results += "\n<b>🔍 Want to see the rationale behind each score?</b>\n"
        results += "Use the buttons below to explore parameter rationales and data sources!"
        
        return results
    
    def get_results_keyboard(self):
        """Get keyboard for results message with rationale options"""
        keyboard = [
            [
                InlineKeyboardButton("📋 View All Rationales", callback_data="view_all_rationales"),
                InlineKeyboardButton("📚 View Sources", callback_data="view_sources")
            ],
            [InlineKeyboardButton("🎯 Rank Another University", callback_data="rank_another")],
            [
                InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers"),
                InlineKeyboardButton("📈 View Parameters", callback_data="view_parameters")
            ],
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
