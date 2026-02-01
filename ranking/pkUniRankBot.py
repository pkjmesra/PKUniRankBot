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
Enhanced with real data fetching and comprehensive rate limiting
"""

import os
import logging
import tempfile
import time
import requests
import re
from bs4 import BeautifulSoup
from typing import Dict, Tuple, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from dotenv import dotenv_values
import wikipedia
from googlesearch import search
import json
import urllib.parse
from threading import Lock
from collections import defaultdict
from enum import Enum
import threading
try:
    import thread
except ImportError:
    import _thread as thread

import traceback
start_time = datetime.now()
MINUTES_2_IN_SECONDS = 120

# Import for telegram bot v13.15
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Document
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, ConversationHandler, CallbackContext
)

# Configure logging with more detailed format
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.DEBUG
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
    is_estimated: bool = True
    real_data_sources: List[str] = None
    rate_limit_info: List[Dict] = None

# ============================================================================
# PROGRESS TRACKING CLASS
# ============================================================================

class ProgressTracker:
    """Tracks progress and estimates completion time for large operations"""
    
    def __init__(self, total_items: int, operation_name: str = "Processing"):
        self.total_items = total_items
        self.processed_items = 0
        self.start_time = time.time()
        self.operation_name = operation_name
        self.item_times = []
        self.rate_limits_hit = 0
        logger.info(f"ProgressTracker initialized for {total_items} items")
    
    def update(self, items_processed: int = 1, rate_limit_hit: bool = False):
        """Update progress tracker"""
        self.processed_items += items_processed
        current_time = time.time()
        elapsed = current_time - self.start_time
        
        if rate_limit_hit:
            self.rate_limits_hit += 1
        
        # Track time for this batch
        if items_processed > 0:
            time_per_item = elapsed / self.processed_items
            self.item_times.append(time_per_item)
        
        logger.debug(f"Progress update: {self.processed_items}/{self.total_items}")
    
    def get_progress_percentage(self) -> float:
        """Get progress as percentage"""
        if self.total_items == 0:
            return 0
        return (self.processed_items / self.total_items) * 100
    
    def get_estimated_time_remaining(self) -> str:
        """Get estimated time remaining"""
        if self.processed_items == 0:
            return "Calculating..."
        
        elapsed = time.time() - self.start_time
        if self.processed_items < 2:
            return "Estimating..."
        
        # Use average of last 10 items for better estimation
        recent_times = self.item_times[-10:] if len(self.item_times) >= 10 else self.item_times
        if not recent_times:
            return "Estimating..."
        
        avg_time_per_item = sum(recent_times) / len(recent_times)
        remaining_items = self.total_items - self.processed_items
        
        # Add buffer for rate limits (30 seconds per expected rate limit)
        rate_limit_buffer = max(0, self.rate_limits_hit * 30)
        
        estimated_seconds = (remaining_items * avg_time_per_item) + rate_limit_buffer
        
        if estimated_seconds < 60:
            return f"{int(estimated_seconds)} seconds"
        elif estimated_seconds < 3600:
            minutes = int(estimated_seconds / 60)
            seconds = int(estimated_seconds % 60)
            return f"{minutes}m {seconds}s"
        else:
            hours = int(estimated_seconds / 3600)
            minutes = int((estimated_seconds % 3600) / 60)
            return f"{hours}h {minutes}m"
    
    def get_progress_message(self) -> str:
        """Get formatted progress message"""
        percentage = self.get_progress_percentage()
        elapsed = time.time() - self.start_time
        elapsed_str = self._format_time(elapsed)
        remaining_str = self.get_estimated_time_remaining()
        
        message = f"""
📊 <b>{self.operation_name} Progress</b>

✅ Processed: {self.processed_items}/{self.total_items} ({percentage:.1f}%)
⏱️ Elapsed: {elapsed_str}
⏳ Estimated remaining: {remaining_str}

⚠️ Rate limits hit: {self.rate_limits_hit}
"""
        
        if self.rate_limits_hit > 0:
            message += "\n<i>Note: Rate limits may extend processing time</i>"
        
        return message
    
    def _format_time(self, seconds: float) -> str:
        """Format seconds into readable time"""
        if seconds < 60:
            return f"{int(seconds)} seconds"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            seconds = int(seconds % 60)
            return f"{minutes}m {seconds}s"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

# ============================================================================
# API RATE LIMITING CLASSES
# ============================================================================

class APIType(Enum):
    """Types of APIs we're using"""
    WIKIPEDIA = "wikipedia"
    GOOGLE_SEARCH = "google_search"
    WEBOMETRICS = "webometrics"
    QS_RANKINGS = "qs_rankings"
    THE_RANKINGS = "the_rankings"
    GOVERNMENT_API = "government_api"

class RateLimitExceededException(Exception):
    """Custom exception for rate limit exceeded"""
    def __init__(self, api_type: APIType, reset_time: datetime, limit_details: str = ""):
        self.api_type = api_type
        self.reset_time = reset_time
        self.limit_details = limit_details
        self.message = f"Rate limit exceeded for {api_type.value}. Resets at {reset_time}"
        super().__init__(self.message)

@dataclass
class RateLimitInfo:
    """Information about rate limits for an API"""
    requests_per_minute: int = 60
    requests_per_hour: int = 3600
    requests_per_day: int = 86400
    reset_interval_minutes: int = 1
    reset_interval_hours: int = 1
    reset_interval_days: int = 24
    
    def get_reset_time(self, time_unit: str) -> datetime:
        """Get reset time based on time unit"""
        now = datetime.now()
        if time_unit == "minute":
            return now + timedelta(minutes=self.reset_interval_minutes)
        elif time_unit == "hour":
            return now + timedelta(hours=self.reset_interval_hours)
        elif time_unit == "day":
            return now + timedelta(days=self.reset_interval_days)
        return now

@dataclass
class APICallTracker:
    """Tracks API calls for rate limiting"""
    api_type: APIType
    calls: List[datetime] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)
    
    def add_call(self):
        """Record an API call"""
        with self.lock:
            self.calls.append(datetime.now())
            # Clean up old calls (keep last 24 hours)
            cutoff = datetime.now() - timedelta(hours=24)
            self.calls = [call for call in self.calls if call > cutoff]
    
    def get_recent_calls(self, minutes: int = 1) -> int:
        """Get number of calls in recent minutes"""
        with self.lock:
            cutoff = datetime.now() - timedelta(minutes=minutes)
            return len([call for call in self.calls if call > cutoff])
    
    def get_hourly_calls(self) -> int:
        """Get number of calls in last hour"""
        with self.lock:
            cutoff = datetime.now() - timedelta(hours=1)
            return len([call for call in self.calls if call > cutoff])
    
    def get_daily_calls(self) -> int:
        """Get number of calls in last 24 hours"""
        with self.lock:
            cutoff = datetime.now() - timedelta(hours=24)
            return len([call for call in self.calls if call > cutoff])

class RateLimiter:
    """Manages rate limiting for all APIs"""
    
    def __init__(self):
        logger.info("Initializing RateLimiter with API limits")
        self.limits = {
            APIType.WIKIPEDIA: RateLimitInfo(
                requests_per_minute=100,  # Wikipedia's generous limit
                requests_per_hour=2000,
                requests_per_day=10000
            ),
            APIType.GOOGLE_SEARCH: RateLimitInfo(
                requests_per_minute=10,   # Google is strict
                requests_per_hour=100,
                requests_per_day=1000
            ),
            APIType.WEBOMETRICS: RateLimitInfo(
                requests_per_minute=30,
                requests_per_hour=500,
                requests_per_day=5000
            ),
            APIType.QS_RANKINGS: RateLimitInfo(
                requests_per_minute=20,
                requests_per_hour=200,
                requests_per_day=2000
            ),
            APIType.THE_RANKINGS: RateLimitInfo(
                requests_per_minute=20,
                requests_per_hour=200,
                requests_per_day=2000
            ),
            APIType.GOVERNMENT_API: RateLimitInfo(
                requests_per_minute=5,    # Government APIs are often strict
                requests_per_hour=50,
                requests_per_day=500
            )
        }
        
        self.trackers: Dict[APIType, APICallTracker] = {}
        for api_type in APIType:
            self.trackers[api_type] = APICallTracker(api_type)
        
        self.global_lock = Lock()
        logger.info(f"RateLimiter initialized with {len(self.trackers)} API trackers")
    
    def check_rate_limit(self, api_type: APIType, user_id: Optional[str] = None) -> bool:
        """Check if API call is allowed"""
        tracker = self.trackers[api_type]
        limits = self.limits[api_type]
        
        # Check minute limit
        recent_calls = tracker.get_recent_calls(1)
        if recent_calls >= limits.requests_per_minute:
            reset_time = limits.get_reset_time("minute")
            logger.warning(f"Minute rate limit exceeded for {api_type.value}: {recent_calls}/{limits.requests_per_minute}")
            raise RateLimitExceededException(
                api_type, 
                reset_time,
                f"Minute limit: {limits.requests_per_minute} calls"
            )
        
        # Check hourly limit
        hourly_calls = tracker.get_hourly_calls()
        if hourly_calls >= limits.requests_per_hour:
            reset_time = limits.get_reset_time("hour")
            logger.warning(f"Hourly rate limit exceeded for {api_type.value}: {hourly_calls}/{limits.requests_per_hour}")
            raise RateLimitExceededException(
                api_type,
                reset_time,
                f"Hourly limit: {limits.requests_per_hour} calls"
            )
        
        # Check daily limit
        daily_calls = tracker.get_daily_calls()
        if daily_calls >= limits.requests_per_day:
            reset_time = limits.get_reset_time("day")
            logger.warning(f"Daily rate limit exceeded for {api_type.value}: {daily_calls}/{limits.requests_per_day}")
            raise RateLimitExceededException(
                api_type,
                reset_time,
                f"Daily limit: {limits.requests_per_day} calls"
            )
        
        logger.debug(f"Rate limit check passed for {api_type.value}: {recent_calls}/{limits.requests_per_minute} (minute), {hourly_calls}/{limits.requests_per_hour} (hour), {daily_calls}/{limits.requests_per_day} (day)")
        return True
    
    def record_call(self, api_type: APIType):
        """Record an API call"""
        tracker = self.trackers[api_type]
        tracker.add_call()
        logger.debug(f"Recorded API call for {api_type.value}")
    
    def get_api_status(self, api_type: APIType) -> Dict[str, Any]:
        """Get current status of an API"""
        tracker = self.trackers[api_type]
        limits = self.limits[api_type]
        
        recent_calls = tracker.get_recent_calls(1)
        hourly_calls = tracker.get_hourly_calls()
        daily_calls = tracker.get_daily_calls()
        
        status = {
            'api': api_type.value,
            'calls_last_minute': recent_calls,
            'calls_last_hour': hourly_calls,
            'calls_last_day': daily_calls,
            'minute_limit': limits.requests_per_minute,
            'hourly_limit': limits.requests_per_hour,
            'daily_limit': limits.requests_per_day,
            'available_minute': max(0, limits.requests_per_minute - recent_calls),
            'available_hour': max(0, limits.requests_per_hour - hourly_calls),
            'available_day': max(0, limits.requests_per_day - daily_calls)
        }
        
        logger.debug(f"API status for {api_type.value}: {status}")
        return status
    
    def get_all_status(self) -> List[Dict[str, Any]]:
        """Get status of all APIs"""
        all_status = [self.get_api_status(api_type) for api_type in APIType]
        logger.debug(f"Retrieved status for {len(all_status)} APIs")
        return all_status
    
    def get_next_reset_time(self, api_type: APIType) -> Optional[datetime]:
        """Get next reset time for an API"""
        tracker = self.trackers[api_type]
        limits = self.limits[api_type]
        
        if tracker.get_recent_calls(1) >= limits.requests_per_minute:
            reset_time = limits.get_reset_time("minute")
            logger.debug(f"Next reset for {api_type.value}: minute reset at {reset_time}")
            return reset_time
        elif tracker.get_hourly_calls() >= limits.requests_per_hour:
            reset_time = limits.get_reset_time("hour")
            logger.debug(f"Next reset for {api_type.value}: hour reset at {reset_time}")
            return reset_time
        elif tracker.get_daily_calls() >= limits.requests_per_day:
            reset_time = limits.get_reset_time("day")
            logger.debug(f"Next reset for {api_type.value}: day reset at {reset_time}")
            return reset_time
        
        logger.debug(f"No reset needed for {api_type.value}")
        return None

# ============================================================================
# DATA FETCHER WITH RATE LIMITING
# ============================================================================

class RateLimitedDataFetcher:
    """Fetches real university data with rate limiting"""
    
    def __init__(self):
        logger.info("Initializing RateLimitedDataFetcher")
        self.rate_limiter = RateLimiter()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'pkUniRankBot/1.0 (https://github.com/yourusername/pkUniRankBot)'
        })
        logger.info("RateLimitedDataFetcher initialized")
    
    def safe_fetch_wikipedia(self, university_name: str, user_id: Optional[str] = None) -> Optional[Dict]:
        """Safely fetch data from Wikipedia with rate limiting"""
        logger.info(f"Starting Wikipedia fetch for: {university_name}")
        try:
            # Check rate limit
            logger.debug(f"Checking Wikipedia rate limit for user: {user_id}")
            self.rate_limiter.check_rate_limit(APIType.WIKIPEDIA, user_id)
            
            search_query = f"{university_name} university"
            logger.debug(f"Wikipedia search query: {search_query}")
            start_time = time.time()
            
            try:
                logger.debug(f"Attempting to fetch Wikipedia page for: {university_name}")
                page = wikipedia.page(search_query, auto_suggest=True)
                logger.info(f"Wikipedia page found for {university_name}")
                
                data = {
                    'summary': page.summary[:500],
                    'url': page.url,
                    'categories': page.categories,
                    'fetch_time': time.time() - start_time
                }
                
                # Extract key metrics from content
                content = page.content.lower()
                
                # Look for rankings in content
                rankings = []
                for line in content.split('\n'):
                    if any(word in line for word in ['rank', 'ranking', 'rated', '#', 'top']):
                        if 'university' in line or 'college' in line:
                            rankings.append(line[:200])
                
                data['rankings'] = rankings[:5]
                
                # Record successful call
                self.rate_limiter.record_call(APIType.WIKIPEDIA)
                logger.info(f"Wikipedia fetch successful for {university_name} in {data['fetch_time']:.2f}s")
                
                return {'wikipedia': data}
                
            except wikipedia.exceptions.DisambiguationError as e:
                logger.warning(f"Wikipedia disambiguation error for {university_name}: {e.options[:3]}")
                # Try first option
                try:
                    first_option = e.options[0]
                    logger.debug(f"Trying disambiguation option: {first_option}")
                    page = wikipedia.page(first_option)
                    data = {
                        'summary': page.summary[:500],
                        'url': page.url,
                        'categories': page.categories,
                        'fetch_time': time.time() - start_time,
                        'note': f'Used disambiguation: {first_option}'
                    }
                    self.rate_limiter.record_call(APIType.WIKIPEDIA)
                    logger.info(f"Wikipedia fetch successful using disambiguation for {university_name}")
                    return {'wikipedia': data}
                except Exception as e:
                    logger.error(f"Failed to fetch disambiguated page: {e}")
                    pass
            except wikipedia.exceptions.PageError:
                logger.warning(f"Wikipedia page not found for {university_name}")
            
            # Record call even if page not found
            self.rate_limiter.record_call(APIType.WIKIPEDIA)
            logger.info(f"Wikipedia fetch completed (no page found) for {university_name}")
            
        except RateLimitExceededException as e:
            logger.warning(f"Wikipedia rate limit exceeded for {university_name}: {e}")
            raise
        except Exception as e:
            logger.error(f"Wikipedia fetch error for {university_name}: {e}")
        
        return None
    
    def safe_google_search(self, query: str, user_id: Optional[str] = None) -> List[str]:
        """Safely search Google using custom requests with proper headers"""
        logger.info(f"Starting Google search for: {query}")
        
        try:
            # Check rate limit
            self.rate_limiter.check_rate_limit(APIType.GOOGLE_SEARCH, user_id)
            
            time.sleep(2)  # Be extra conservative
            
            # Use custom headers to look more like a browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            # Construct Google search URL
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            
            response = self.session.get(search_url, headers=headers, timeout=10)
            
            # Parse results (simplified example)
            results = []
            if response.status_code == 200:
                # Extract links from Google search results page
                # This is a simplified parser - you might need to adjust based on Google's HTML structure
                soup = BeautifulSoup(response.text, 'html.parser')
                for link in soup.find_all('a'):
                    href = link.get('href')
                    if href and href.startswith('http') and 'google.com' not in href:
                        results.append(href)
                
            # Record call
            self.rate_limiter.record_call(APIType.GOOGLE_SEARCH)
            
            return results[:3]  # Return top 3 results
            
        except Exception as e:
            logger.error(f"Google search error: {e}")
            self.rate_limiter.record_call(APIType.GOOGLE_SEARCH)
            return []
    
    def safe_fetch_webometrics(self, university_name: str, user_id: Optional[str] = None) -> Optional[Dict]:
        """Safely fetch Webometrics data using their actual website"""
        logger.info(f"Starting Webometrics fetch for: {university_name}")
        try:
            # Check rate limit
            logger.debug(f"Checking Webometrics rate limit for user: {user_id}")
            self.rate_limiter.check_rate_limit(APIType.WEBOMETRICS, user_id)
            
            # Use the actual Webometrics search page
            search_query = urllib.parse.quote(university_name)
            url = f"https://www.webometrics.info/en/search/site/{search_query}"
            logger.debug(f"Webometrics search URL: {url}")
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            logger.debug(f"Webometrics response status: {response.status_code}")
            
            # Record call
            self.rate_limiter.record_call(APIType.WEBOMETRICS)
            
            if response.status_code == 200:
                # Parse the HTML to extract ranking information
                # This is a simplified example - you'll need to adjust based on actual page structure
                data = {
                    'url': url,
                    'status': 'success',
                    'content_length': len(response.text),
                    'note': 'Scraped from Webometrics website'
                }
                
                # You could add HTML parsing here to extract actual ranking data
                # from soup = BeautifulSoup(response.text, 'html.parser')
                
                logger.info(f"Webometrics fetch successful for {university_name}")
                return {'webometrics': data}
            elif response.status_code == 429:  # Too Many Requests
                retry_after = response.headers.get('Retry-After', '60')
                reset_time = datetime.now() + timedelta(seconds=int(retry_after))
                logger.warning(f"Webometrics HTTP 429 for {university_name}: Retry after {retry_after}s")
                raise RateLimitExceededException(
                    APIType.WEBOMETRICS, 
                    reset_time,
                    f"HTTP 429: Retry after {retry_after} seconds"
                )
                
        except RateLimitExceededException:
            raise
        except Exception as e:
            logger.error(f"Webometrics fetch error for {university_name}: {e}")
            # Still record the attempt
            self.rate_limiter.record_call(APIType.WEBOMETRICS)
            logger.info(f"Recorded Webometrics attempt despite error")
        
        return None
    
    def fetch_all_data(self, university_name: str, country: str, user_id: Optional[str] = None) -> Tuple[Dict, List[Dict]]:
        """Fetch data from all sources with rate limiting"""
        logger.info(f"Starting data fetch for {university_name} in {country}")
        all_data = {}
        rate_limit_info = []
        
        # Fetch Wikipedia data
        logger.debug(f"Attempting Wikipedia fetch for {university_name}")
        try:
            wiki_data = self.safe_fetch_wikipedia(university_name, user_id)
            if wiki_data:
                all_data.update(wiki_data)
                rate_limit_info.append(self.rate_limiter.get_api_status(APIType.WIKIPEDIA))
                logger.info(f"Wikipedia data fetched successfully for {university_name}")
        except RateLimitExceededException as e:
            rate_limit_info.append({
                'api': 'wikipedia',
                'status': 'rate_limited',
                'reset_time': e.reset_time,
                'message': str(e)
            })
            logger.warning(f"Wikipedia rate limited for {university_name}")
        
        # Fetch Google search results
        queries = [
            f"{university_name} QS World University Rankings",
            f"{university_name} Times Higher Education ranking",
            f"{university_name} ARWU ranking"
        ]
        
        google_results = {}
        logger.debug(f"Preparing {len(queries)} Google search queries")
        for i, query in enumerate(queries, 1):
            try:
                logger.debug(f"Google search {i}/{len(queries)}: {query}")
                results = self.safe_google_search(query, user_id)
                if results:
                    google_results[query] = results
                    logger.debug(f"Google search {i} returned {len(results)} results")
                rate_limit_info.append(self.rate_limiter.get_api_status(APIType.GOOGLE_SEARCH))
            except RateLimitExceededException as e:
                rate_limit_info.append({
                    'api': 'google_search',
                    'status': 'rate_limited',
                    'reset_time': e.reset_time,
                    'message': str(e)
                })
                logger.warning(f"Google search rate limited on query {i}")
                break  # Stop further Google searches
        
        if google_results:
            all_data['google_search'] = google_results
            logger.info(f"Google searches completed, found data for {len(google_results)} queries")
        
        # Try Webometrics
        logger.debug(f"Attempting Webometrics fetch for {university_name}")
        try:
            web_data = self.safe_fetch_webometrics(university_name, user_id)
            if web_data:
                all_data.update(web_data)
                rate_limit_info.append(self.rate_limiter.get_api_status(APIType.WEBOMETRICS))
                logger.info(f"Webometrics data fetched successfully for {university_name}")
        except RateLimitExceededException as e:
            rate_limit_info.append({
                'api': 'webometrics',
                'status': 'rate_limited',
                'reset_time': e.reset_time,
                'message': str(e)
            })
            logger.warning(f"Webometrics rate limited for {university_name}")
        
        logger.info(f"Data fetch completed for {university_name}. Got data from {len(all_data)} sources")
        return all_data, rate_limit_info

# ============================================================================
# ENHANCED UNIVERSITY RANKING SYSTEM
# ============================================================================

class UniversityRankingSystem:
    def __init__(self):
        logger.info("Initializing UniversityRankingSystem")
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
        
        logger.info(f"UniversityRankingSystem initialized with {len(self.university_db)} universities in database")
    
    def load_university_database(self) -> Dict:
        """Load university database with pre-calculated scores"""
        logger.info("Loading university database")
        db = {
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
        logger.info(f"Loaded {len(db)} universities into database")
        return db
    
    def load_qs_rankings(self) -> Dict:
        """Load QS World University Rankings data"""
        logger.info("Loading QS rankings")
        # This would ideally be loaded from a CSV or API
        # For now, we'll use a sample of top universities
        qs_data = {
            'massachusetts institute of technology': 1,
            'university of cambridge': 2,
            'university of oxford': 3,
            'harvard university': 4,
            'stanford university': 5,
            'imperial college london': 6,
            'california institute of technology': 7,
            'university college london': 8,
            'eth zurich': 9,
            'university of chicago': 10,
            # North Dakota State University - not in top QS rankings
        }
        logger.info(f"Loaded {len(qs_data)} QS rankings")
        return qs_data
    
    def load_the_rankings(self) -> Dict:
        """Load Times Higher Education Rankings"""
        logger.info("Loading THE rankings")
        the_data = {
            'university of oxford': 1,
            'harvard university': 2,
            'university of cambridge': 3,
            'stanford university': 4,
            'massachusetts institute of technology': 5,
            'california institute of technology': 6,
            'princeton university': 7,
            'university of california berkeley': 8,
            'yale university': 9,
            'imperial college london': 10,
            # North Dakota State University - not in top THE rankings
        }
        logger.info(f"Loaded {len(the_data)} THE rankings")
        return the_data
    
    def classify_university_type(self, name: str) -> str:
        """Classify university based on name patterns"""
        name_lower = name.lower()
        logger.debug(f"Classifying university type for: {name}")
        
        if any(word in name_lower for word in ['business school', 'medical school', 'law school']):
            uni_type = 'SPECIALIST_SCHOOL'
        elif any(word in name_lower for word in ['college', 'community college', 'polytechnic']):
            uni_type = 'COLLEGE_POLYTECHNIC'
        elif any(word in name_lower for word in ['technical', 'applied', 'technology']):
            uni_type = 'APPLIED_UNIVERSITY'
        elif 'university' in name_lower:
            if any(word in name_lower for word in ['research', 'institute', 'tech']):
                uni_type = 'RESEARCH_UNIVERSITY'
            else:
                uni_type = 'TEACHING_UNIVERSITY'
        else:
            uni_type = 'TEACHING_UNIVERSITY'
        
        logger.debug(f"Classified '{name}' as: {uni_type}")
        return uni_type
    
    def generate_rationale_for_score(self, param_code: str, score: float, max_score: float, 
                                   university_name: str, country: str, is_estimated: bool) -> List[str]:
        """Generate rationale for a parameter score"""
        logger.debug(f"Generating rationale for {param_code} (score: {score}/{max_score})")
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
        
        logger.debug(f"Generated {len(rationale)} rationale points for {param_code}")
        return rationale
    
    def estimate_scores(self, name: str, country: str) -> Dict[str, float]:
        """Estimate scores for unknown universities"""
        logger.info(f"Estimating scores for {name} in {country}")
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
            logger.debug(f"Using MIT pattern scores for {name}")
        elif 'harvard' in name_lower:
            scores = {'academic': 25, 'graduate': 24, 'roi': 20, 
                     'fsr': 13, 'transparency': 10, 'visibility': 5}
            logger.debug(f"Using Harvard pattern scores for {name}")
        elif 'stanford' in name_lower:
            scores = {'academic': 24, 'graduate': 23, 'roi': 21, 
                     'fsr': 14, 'transparency': 9, 'visibility': 5}
            logger.debug(f"Using Stanford pattern scores for {name}")
        elif 'oxford' in name_lower or 'cambridge' in name_lower:
            scores = {'academic': 25, 'graduate': 24, 'roi': 19, 
                     'fsr': 14, 'transparency': 10, 'visibility': 5}
            logger.debug(f"Using Oxford/Cambridge pattern scores for {name}")
        elif 'university' in name_lower and 'state' in name_lower:
            scores.update({'academic': 15.0, 'roi': 16.0, 'transparency': 9.0, 'visibility': 4.0})
            logger.debug(f"Using state university pattern scores for {name}")
        elif 'university' in name_lower:
            scores.update({'academic': 18.0, 'visibility': 4.0, 'transparency': 8.0})
            logger.debug(f"Using general university pattern scores for {name}")
        elif 'college' in name_lower:
            scores.update({'graduate': 17.0, 'roi': 16.0, 'fsr': 12.0, 'academic': 8.0})
            logger.debug(f"Using college pattern scores for {name}")
        else:
            logger.debug(f"Using base scores for {name}")
        
        # Apply country multiplier
        if country_upper != "GLOBAL":
            country_mult = self.country_multipliers.get(country_upper, 1.0)
            logger.debug(f"Applying country multiplier {country_mult} for {country}")
            for key in ['academic', 'graduate', 'roi', 'fsr']:
                scores[key] = min(self.parameters[key]['max'], scores[key] * country_mult)
        
        # Add randomness for estimation error
        for key in scores:
            if key in ['transparency', 'visibility']:
                variation = np.random.uniform(-0.5, 0.5)
            else:
                variation = np.random.uniform(-2.0, 2.0)
            scores[key] = max(0, min(self.parameters[key]['max'], scores[key] + variation))
        
        rounded_scores = {k: round(v, 1) for k, v in scores.items()}
        logger.info(f"Estimated scores for {name}: {rounded_scores}")
        return rounded_scores
    
    def calculate_composite_score(self, scores: Dict[str, float]) -> float:
        """Calculate composite score"""
        composite = round(sum(scores.values()), 1)
        logger.debug(f"Calculated composite score: {composite}")
        return composite
    
    def get_tier(self, score: float) -> Tuple[str, str]:
        """Determine tier and description"""
        for tier, (low, high, description) in self.tiers.items():
            if low <= score <= high:
                logger.debug(f"Score {score} falls in tier {tier}: {description}")
                return tier, description
        logger.debug(f"Score {score} falls in default tier D")
        return 'D', self.tiers['D'][2]
    
    def calculate_error_margin(self, university_name: str, country: str) -> float:
        """Calculate error margin"""
        name_lower = university_name.lower()
        
        if name_lower in self.university_db:
            error = round(np.random.uniform(1.0, 3.0), 1)
            logger.debug(f"Known university {university_name}, error margin: {error}")
            return error
        else:
            country_mult = 1.0
            if country:
                country_mult = self.country_multipliers.get(country.upper(), 1.0)
            
            base_error = 8.0 / country_mult
            
            if 'university' in name_lower:
                base_error *= 0.9
            elif 'college' in name_lower:
                base_error *= 1.1
            
            error = round(min(15.0, max(3.0, base_error + np.random.uniform(-2.0, 2.0))), 1)
            logger.debug(f"Unknown university {university_name}, error margin: {error}")
            return error
    
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
            logger.debug(f"Using real data sources for {university_name}")
        else:
            sources.extend([
                "Pattern analysis of similar institutions",
                "Country education system benchmarks",
                "Institution type averages",
                "Statistical estimation models"
            ])
            logger.debug(f"Using estimated data sources for {university_name}")
        
        # Add common sources
        sources.extend(self.common_sources[:4])
        
        logger.debug(f"Total sources for {university_name}: {len(sources)}")
        return sources

# ============================================================================
# ENHANCED RANKING SYSTEM WITH REAL DATA FETCHING
# ============================================================================

class EnhancedUniversityRankingSystem(UniversityRankingSystem):
    """Enhanced ranking system with real data fetching and rate limiting"""
    
    def __init__(self):
        logger.info("Initializing EnhancedUniversityRankingSystem")
        super().__init__()
        self.data_fetcher = RateLimitedDataFetcher()
        self.real_data_cache = {}
        self.qs_rankings = self.load_qs_rankings()
        self.the_rankings = self.load_the_rankings()
        self.cache_lock = Lock()
        logger.info("EnhancedUniversityRankingSystem initialized")
    
    def fetch_real_data(self, university_name: str, country: str, user_id: Optional[str] = None) -> Tuple[Dict, List[Dict]]:
        """Fetch real data from multiple sources with rate limiting"""
        logger.info(f"Fetching real data for: {university_name} (Country: {country})")
        cache_key = f"{university_name.lower()}_{country.lower()}"
        
        with self.cache_lock:
            if cache_key in self.real_data_cache:
                cached_data, cached_rate_info = self.real_data_cache[cache_key]
                # Add cache hit info to rate info
                rate_info = cached_rate_info.copy() if cached_rate_info else []
                rate_info.append({'api': 'cache', 'status': 'hit', 'timestamp': datetime.now().isoformat()})
                logger.info(f"Cache hit for {university_name}")
                return cached_data, rate_info
        
        logger.info(f"Cache miss for {university_name}, fetching fresh data")
        # Fetch fresh data
        all_data, rate_limit_info = self.data_fetcher.fetch_all_data(university_name, country, user_id)
        
        # Check known rankings
        name_lower = university_name.lower()
        if name_lower in self.qs_rankings:
            all_data['qs_ranking'] = self.qs_rankings[name_lower]
            rate_limit_info.append({'api': 'qs_rankings', 'status': 'cache', 'source': 'internal'})
            logger.debug(f"Found QS ranking for {university_name}: {self.qs_rankings[name_lower]}")
        
        if name_lower in self.the_rankings:
            all_data['the_ranking'] = self.the_rankings[name_lower]
            rate_limit_info.append({'api': 'the_rankings', 'status': 'cache', 'source': 'internal'})
            logger.debug(f"Found THE ranking for {university_name}: {self.the_rankings[name_lower]}")
        
        # Cache the results (only if we got some data)
        if all_data:
            with self.cache_lock:
                self.real_data_cache[cache_key] = (all_data, rate_limit_info)
            logger.info(f"Cached data for {university_name} (keys: {list(all_data.keys())})")
        else:
            logger.warning(f"No data fetched for {university_name}")
        
        logger.info(f"Data fetch complete for {university_name}")
        return all_data, rate_limit_info
    
    def calculate_scores_from_real_data(self, university_name: str, country: str, real_data: Dict) -> Dict[str, float]:
        """Calculate scores based on real fetched data"""
        logger.info(f"Calculating scores from real data for: {university_name}")
        scores = {
            'academic': 12.0,
            'graduate': 15.0,
            'roi': 14.0,
            'fsr': 11.0,
            'transparency': 7.0,
            'visibility': 3.0
        }
        
        # Adjust based on QS ranking if available
        if 'qs_ranking' in real_data:
            qs_rank = real_data['qs_ranking']
            logger.debug(f"QS ranking for {university_name}: {qs_rank}")
            if qs_rank <= 10:
                scores.update({'academic': 25, 'graduate': 24, 'visibility': 5})
                logger.debug(f"Top 10 QS ranking adjustment for {university_name}")
            elif qs_rank <= 50:
                scores.update({'academic': 22, 'graduate': 21, 'visibility': 4.5})
                logger.debug(f"Top 50 QS ranking adjustment for {university_name}")
            elif qs_rank <= 100:
                scores.update({'academic': 20, 'graduate': 19, 'visibility': 4})
                logger.debug(f"Top 100 QS ranking adjustment for {university_name}")
            elif qs_rank <= 200:
                scores.update({'academic': 18, 'graduate': 17, 'visibility': 3.5})
                logger.debug(f"Top 200 QS ranking adjustment for {university_name}")
        
        # Adjust based on THE ranking if available
        if 'the_ranking' in real_data:
            the_rank = real_data['the_ranking']
            logger.debug(f"THE ranking for {university_name}: {the_rank}")
            if the_rank <= 10:
                scores['academic'] = max(scores['academic'], 24)
                scores['transparency'] = max(scores['transparency'], 9)
                logger.debug(f"Top 10 THE ranking adjustment for {university_name}")
            elif the_rank <= 100:
                scores['academic'] = max(scores['academic'], scores['academic'] * 1.1)
                logger.debug(f"Top 100 THE ranking adjustment for {university_name}")
        
        # Analyze Wikipedia data for indicators
        if 'wikipedia' in real_data:
            wiki_data = real_data['wikipedia']
            summary = wiki_data.get('summary', '').lower()
            
            # Check for research indicators
            research_keywords = ['research', 'publication', 'citation', 'nobel', 'faculty']
            research_count = sum(1 for keyword in research_keywords if keyword in summary)
            if research_count >= 3:
                scores['academic'] = min(25, scores['academic'] + 3)
                logger.debug(f"Wikipedia research indicators found for {university_name}, +3 academic")
            
            # Check for employment indicators
            employ_keywords = ['employment', 'graduate', 'career', 'salary', 'placement']
            employ_count = sum(1 for keyword in employ_keywords if keyword in summary)
            if employ_count >= 2:
                scores['graduate'] = min(25, scores['graduate'] + 2)
                logger.debug(f"Wikipedia employment indicators found for {university_name}, +2 graduate")
        
        # Apply country multiplier
        if country:
            country_mult = self.country_multipliers.get(country.upper(), 1.0)
            logger.debug(f"Applying country multiplier {country_mult} for {country}")
            for key in ['academic', 'graduate', 'roi', 'fsr']:
                scores[key] = min(self.parameters[key]['max'], scores[key] * country_mult)
        
        # University type adjustments
        uni_type = self.classify_university_type(university_name)
        if uni_type == 'RESEARCH_UNIVERSITY':
            scores['academic'] = min(25, scores['academic'] + 3)
            logger.debug(f"Research university adjustment for {university_name}, +3 academic")
        elif uni_type == 'COLLEGE_POLYTECHNIC':
            scores['graduate'] = min(25, scores['graduate'] + 2)
            scores['roi'] = min(20, scores['roi'] + 2)
            logger.debug(f"College/polytechnic adjustment for {university_name}, +2 graduate, +2 roi")
        
        rounded_scores = {k: round(v, 1) for k, v in scores.items()}
        logger.info(f"Calculated scores from real data for {university_name}: {rounded_scores}")
        return rounded_scores
    
    def rank_university(self, university_name: str, country: str = "", user_id: Optional[str] = None) -> UniversityData:
        """Enhanced ranking function with real data fetching and rate limiting"""
        logger.info(f"Starting ranking process for: {university_name} (Country: {country}, User: {user_id})")
        name_lower = university_name.lower()
        
        # Try to fetch real data first
        logger.debug(f"Attempting to fetch real data for {university_name}")
        real_data, rate_limit_info = self.fetch_real_data(university_name, country, user_id)
        
        has_real_data = bool(real_data and ('qs_ranking' in real_data or 'the_ranking' in real_data or 'wikipedia' in real_data))
        
        if has_real_data:
            # Calculate scores from real data
            logger.info(f"Using real data for {university_name}")
            scores = self.calculate_scores_from_real_data(university_name, country, real_data)
            is_estimated = False
            data_sources = ["QS World University Rankings", "Times Higher Education", "Wikipedia"]
        elif name_lower in self.university_db:
            # Use database entry
            logger.info(f"Using database entry for {university_name}")
            data = self.university_db[name_lower]
            scores = data['scores']
            country = data['country']
            is_estimated = False
            data_sources = ["University Ranking Database", "Verified Institutional Data"]
        else:
            # Fall back to estimation
            logger.info(f"Using estimation for {university_name}")
            scores = self.estimate_scores(university_name, country)
            is_estimated = True
            data_sources = ["Statistical Estimation", "Pattern Analysis"]
        
        # Get real data sources if available
        real_sources = []
        if 'wikipedia' in real_data:
            real_sources.append(f"Wikipedia: {real_data['wikipedia'].get('url', '')}")
        if 'google_search' in real_data:
            real_sources.append("Google Search Results for rankings")
        
        # Generate rationale
        logger.debug(f"Generating rationale for {university_name}")
        rationale = {}
        for param_code, score in scores.items():
            max_score = self.parameters[param_code]['max']
            rationale[param_code] = self.generate_rationale_for_score(
                param_code, score, max_score, university_name, country, is_estimated
            )
        
        # Add real data sources to rationale if available
        if real_sources:
            data_sources.extend(real_sources)
        
        # Calculate metrics
        logger.debug(f"Calculating final metrics for {university_name}")
        composite = self.calculate_composite_score(scores)
        tier, tier_desc = self.get_tier(composite)
        error_margin = self.calculate_error_margin(university_name, country)
        
        # Lower error margin if we have real data
        if not is_estimated:
            error_margin = max(1.0, error_margin * 0.5)
            logger.debug(f"Reduced error margin for real data: {error_margin}")
        
        result = UniversityData(
            name=university_name,
            country=country,
            type=self.classify_university_type(university_name).replace('_', ' ').title(),
            scores=scores,
            composite=composite,
            tier=tier,
            error_margin=error_margin,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            rationale=rationale,
            sources=data_sources,
            is_estimated=is_estimated,
            real_data_sources=real_sources,
            rate_limit_info=rate_limit_info
        )
        
        logger.info(f"Ranking complete for {university_name}: Score={composite}, Tier={tier}, Estimated={is_estimated}")
        return result
    
    def process_excel_file(self, input_path: str, user_id: Optional[str] = None, 
                          progress_callback: Optional[callable] = None) -> Tuple[str, List[Dict]]:
        """Process Excel file with multiple universities and progress tracking"""
        logger.info(f"Processing Excel file: {input_path} for user: {user_id}")
        
        try:
            # Read the Excel file
            df = pd.read_excel(input_path)
            logger.info(f"Excel file loaded. Shape: {df.shape}, Columns: {list(df.columns)}")
            
            # Create a copy for results
            result_df = df.copy()
            
            # Initialize progress tracker
            total_universities = len(result_df)
            progress_tracker = ProgressTracker(total_universities, "University Ranking")
            
            # Initial progress update
            if progress_callback:
                progress_callback(progress_tracker.get_progress_message())
            
            # Prepare new columns
            result_df['Global Score'] = 0.0
            result_df['Global Rank'] = 0
            result_df['Country Rank'] = 0
            result_df['Data Source'] = 'Estimated'
            result_df['Rate Limited'] = 'No'
            result_df['Processing Time (s)'] = 0.0
            result_df['Error'] = ''
            
            rate_limit_issues = []
            
            for idx, row in result_df.iterrows():
                try:
                    university_name = str(row.iloc[0])  # First column is university name
                    country = str(row.iloc[1]) if len(row) > 1 else ""  # Second column is country
                    
                    logger.info(f"Processing {idx+1}/{total_universities}: {university_name}")
                    
                    # Check rate limits before processing
                    rate_limit_hit = False
                    try:
                        # Check Wikipedia rate limit
                        self.data_fetcher.rate_limiter.check_rate_limit(APIType.WIKIPEDIA, user_id)
                        # Check Google rate limit
                        self.data_fetcher.rate_limiter.check_rate_limit(APIType.GOOGLE_SEARCH, user_id)
                    except RateLimitExceededException as e:
                        logger.warning(f"Rate limit hit for {university_name}: {e}")
                        result_df.at[idx, 'Rate Limited'] = 'Yes'
                        rate_limit_hit = True
                        rate_limit_issues.append({
                            'university': university_name,
                            'api': e.api_type.value,
                            'reset_time': e.reset_time,
                            'message': e.message
                        })
                    
                    # Get ranking data
                    start_time = time.time()
                    ranking_data = self.rank_university(university_name, country, user_id)
                    processing_time = time.time() - start_time
                    
                    # Update result dataframe
                    result_df.at[idx, 'Global Score'] = ranking_data.composite
                    result_df.at[idx, 'Data Source'] = 'Real Data' if not ranking_data.is_estimated else 'Estimated'
                    result_df.at[idx, 'Processing Time (s)'] = round(processing_time, 2)
                    
                    # Update progress tracker
                    progress_tracker.update(1, rate_limit_hit)
                    
                    # Send progress update every 10 universities or every 30 seconds
                    if progress_callback and (idx % 10 == 0 or time.time() - start_time > 30):
                        progress_callback(progress_tracker.get_progress_message())
                    
                    # Add dynamic delay based on rate limit status
                    if rate_limit_hit:
                        delay_time = 10  # Longer delay if rate limit was hit
                    elif idx % 20 == 0:
                        delay_time = 5  # Periodic longer delay
                    else:
                        delay_time = 1  # Normal delay
                    
                    time.sleep(delay_time)
                        
                except Exception as e:
                    logger.error(f"Error processing row {idx}: {e}")
                    result_df.at[idx, 'Data Source'] = 'Error'
                    result_df.at[idx, 'Error'] = str(e)[:100]
                    progress_tracker.update(1, False)
                    continue
            
            # Sort by Global Score for ranking
            result_df = result_df.sort_values(by='Global Score', ascending=False)
            result_df['Global Rank'] = range(1, len(result_df) + 1)
            
            # Calculate country ranks
            if 'Country' in result_df.columns or len(result_df.columns) > 1:
                country_col = result_df.columns[1] if len(result_df.columns) > 1 else 'Country'
                result_df['Country Rank'] = result_df.groupby(country_col)['Global Score'].rank(
                    method='dense', ascending=False
                ).astype(int)
            
            # Create output file
            output_path = tempfile.mktemp(suffix='_ranked.xlsx')
            
            # Create Excel writer with multiple sheets
            with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
                # Main rankings sheet
                result_df.to_excel(writer, sheet_name='Rankings', index=False)
                
                # Summary sheet
                summary_data = {
                    'Total Universities': [total_universities],
                    'Processed Successfully': [progress_tracker.processed_items],
                    'Errors': [total_universities - progress_tracker.processed_items],
                    'Real Data Used': [len(result_df[result_df['Data Source'] == 'Real Data'])],
                    'Estimated Data Used': [len(result_df[result_df['Data Source'] == 'Estimated'])],
                    'Rate Limited Cases': [len(result_df[result_df['Rate Limited'] == 'Yes'])],
                    'Average Processing Time (s)': [result_df['Processing Time (s)'].mean()],
                    'Total Processing Time (s)': [result_df['Processing Time (s)'].sum()],
                    'Rate Limits Hit': [progress_tracker.rate_limits_hit]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
                
                # Rate limit issues sheet (if any)
                if rate_limit_issues:
                    issues_df = pd.DataFrame(rate_limit_issues)
                    issues_df.to_excel(writer, sheet_name='Rate Limit Issues', index=False)
                
                # Processing stats sheet
                stats_data = {
                    'Start Time': [datetime.fromtimestamp(progress_tracker.start_time).strftime("%Y-%m-%d %H:%M:%S")],
                    'End Time': [datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
                    'Total Time': [progress_tracker._format_time(time.time() - progress_tracker.start_time)],
                    'Items per Minute': [progress_tracker.processed_items / ((time.time() - progress_tracker.start_time) / 60) if (time.time() - progress_tracker.start_time) > 0 else 0],
                    'Estimated Completion Accuracy': ['Based on last 10 items'] if len(progress_tracker.item_times) >= 10 else ['Based on all items']
                }
                stats_df = pd.DataFrame(stats_data)
                stats_df.to_excel(writer, sheet_name='Processing Stats', index=False)
            
            logger.info(f"Excel processing complete. Output saved to: {output_path}")
            return output_path, rate_limit_issues
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise

# ============================================================================
# ENHANCED BOT WITH RATE LIMITING
# ============================================================================

class EnhancedUniRankBot:
    """Enhanced bot with real data fetching and rate limiting"""
    
    def __init__(self, token: str):
        """Initialize the enhanced bot"""
        logger.info("Initializing EnhancedUniRankBot")
        self.updater = Updater(token=token, use_context=True)
        self.dispatcher = self.updater.dispatcher
        self.ranking_system = EnhancedUniversityRankingSystem()
        
        # Store current ranking data for rationale viewing
        self.user_ranking_data = {}
        
        # Track user Excel processing
        self.user_excel_processing = {}
        
        # Set up handlers
        self.setup_handlers()
        logger.info("EnhancedUniRankBot initialized")
    
    def setup_handlers(self):
        """Setup all bot handlers"""
        logger.info("Setting up bot handlers")
        
        # Command handlers
        self.dispatcher.add_handler(CommandHandler("start", self.start_command))
        self.dispatcher.add_handler(CommandHandler("help", self.help_command))
        self.dispatcher.add_handler(CommandHandler("rank", self.rank_command))
        self.dispatcher.add_handler(CommandHandler("tiers", self.tiers_command))
        self.dispatcher.add_handler(CommandHandler("parameters", self.parameters_command))
        self.dispatcher.add_handler(CommandHandler("rank_excel", self.rank_excel_command))
        self.dispatcher.add_handler(CommandHandler("rate_status", self.rate_status_command))
        
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
        
        logger.info("Bot handlers setup complete")
    
    def error_handler(self, update: Update, context: CallbackContext):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")
        
        """Log the error and send a telegram message to notify the developer."""
        # Log the error before we do anything else, so we can see it even if something breaks.
        logger.error("Exception while handling an update:", exc_info=context.error)

        # traceback.format_exception returns the usual python message about an exception, but as a
        # list of strings rather than a single string, so we have to join them together.
        tb_list = traceback.format_exception(
            None, context.error, context.error.__traceback__
        )
        tb_string = "".join(tb_list)
        global start_time
        timeSinceStarted = datetime.now() - start_time
        if (
            "telegram.error.Conflict" in tb_string
        ):  # A newer 2nd instance was registered. We should politely shutdown.
            if (
                timeSinceStarted.total_seconds() >= MINUTES_2_IN_SECONDS
            ):  # shutdown only if we have been running for over 2 minutes.
                # This also prevents this newer instance to get shutdown.
                # Instead the older instance will shutdown
                print(
                    f"Stopping due to conflict after running for {timeSinceStarted.total_seconds()/60} minutes."
                )
                try:
                    # context.dispatcher.stop()
                    thread.interrupt_main() # causes ctrl + c
                    # sys.exit(0)
                except RuntimeError:
                    pass
                except SystemExit:
                    thread.interrupt_main()
                    
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
        print("⚠️  Rate limiting active for all APIs")
        print("📊 Detailed logging enabled")
        print("⚡ Bot is running. Press Ctrl+C to stop.")
        
        self.updater.start_polling()
        self.updater.idle()
    
    # Command handlers
    def start_command(self, update: Update, context: CallbackContext):
        """Handle /start command"""
        logger.info(f"Start command from user: {update.effective_user.id}")
        user = update.message.from_user
        welcome_text = f"""
🎓 Welcome to <b>pkUniRankBot</b> {user.first_name}!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

<b>⚠️ IMPORTANT RATE LIMIT INFORMATION:</b>
• Wikipedia: 100 requests/minute, 2000/hour
• Google Search: 10 requests/minute, 100/hour  
• Webometrics: 30 requests/minute, 500/hour
• All APIs: Daily limits enforced

<b>When rate limits are hit:</b>
1. You'll receive a clear message
2. I'll use estimated data as fallback
3. Excel output will show which data was estimated due to limits

<b>Available Commands:</b>
/rank - Rank a single university
/rank_excel - Process Excel file with multiple universities
/tiers - View tier explanations  
/parameters - View ranking parameters
/help - Get help
/rate_status - Check current API rate limits
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 Process Excel File", callback_data="rank_excel")],
            [InlineKeyboardButton("📈 Check Rate Limits", callback_data="rate_status")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📊 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def help_command(self, update: Update, context: CallbackContext):
        """Handle /help command"""
        logger.info(f"Help command from user: {update.effective_user.id}")
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
/rate_status - Check API rate limits
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
        logger.info(f"Tiers command from user: {update.effective_user.id}")
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
        logger.info(f"Parameters command from user: {update.effective_user.id}")
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
        """Handle /rank_excel command with warnings for large files"""
        logger.info(f"Rank_excel command from user: {update.effective_user.id}")
        instructions = """
<b>📊 Excel Ranking Instructions</b>

Please send me an Excel file (.xlsx or .xls) with university data.

<b>⚠️ IMPORTANT FOR LARGE FILES:</b>
• 700+ universities will take approximately 30-60 minutes
• I'll send progress updates every 30 seconds
• Rate limits are strictly enforced to avoid API blocks
• Large files will use more estimated data

<b>📈 TIME ESTIMATES:</b>
• 100 universities: ~5-10 minutes
• 300 universities: ~15-30 minutes  
• 500 universities: ~25-50 minutes
• 700+ universities: ~35-70 minutes

<b>Required Columns:</b>
- University/Institution names (first column)
- Country names (second column, optional)

<b>Optional Column:</b>
- Any ranking column (e.g., Leap Rank)

<b>I will automatically detect columns and add:</b>
- Global Score (0-100)
- Global Rank (1 = best worldwide)
- Country Rank (1 = best in country)
- Data Source (Real Data/Estimated)
- Rate Limit Status
- Processing Time

<b>Just send me your Excel file now!</b>
        """
        
        update.message.reply_text(instructions, parse_mode=ParseMode.HTML)
    
    def rate_status_command(self, update: Update, context: CallbackContext):
        """Check current API rate limit status"""
        # Handle both message updates and callback queries
        if update.message:
            user_id = update.effective_user.id
            reply_method = update.message.reply_text
        elif update.callback_query:
            # This shouldn't happen since button handler redirects to callback method,
            # but let's handle it just in case
            user_id = update.callback_query.from_user.id
            reply_method = lambda text, **kwargs: update.callback_query.edit_message_text(text, **kwargs)
        else:
            logger.error("Rate status called without message or callback_query")
            return
        
        logger.info(f"Rate_status command from user: {user_id}")
        
        try:
            # Get rate limiter from ranking system
            rate_limiter = self.ranking_system.data_fetcher.rate_limiter
            
            # Get status of all APIs
            all_status = rate_limiter.get_all_status()
            
            status_text = "📊 <b>CURRENT API RATE LIMIT STATUS</b>\n\n"
            
            for status in all_status:
                api_name = status['api'].upper()
                used_minute = status['calls_last_minute']
                limit_minute = status['minute_limit']
                available_minute = status['available_minute']
                
                # Create status indicator
                if available_minute > limit_minute * 0.5:
                    indicator = "🟢"
                elif available_minute > limit_minute * 0.2:
                    indicator = "🟡"
                else:
                    indicator = "🔴"
                
                status_text += f"{indicator} <b>{api_name}</b>\n"
                status_text += f"   Minute: {used_minute}/{limit_minute} (Avail: {available_minute})\n"
                status_text += f"   Hour: {status['calls_last_hour']}/{status['hourly_limit']}\n"
                status_text += f"   Day: {status['calls_last_day']}/{status['daily_limit']}\n\n"
            
            # Add next reset info
            next_reset = None
            for api_type in APIType:
                reset_time = rate_limiter.get_next_reset_time(api_type)
                if reset_time:
                    if next_reset is None or reset_time < next_reset:
                        next_reset = reset_time
            
            if next_reset:
                time_until = next_reset - datetime.now()
                minutes_until = max(0, int(time_until.total_seconds() / 60))
                status_text += f"⏰ <b>Next reset in:</b> {minutes_until} minutes\n"
            
            status_text += "\n<i>Note: Limits reset automatically. Large Excel files may hit limits.</i>"
            
            reply_method(
                status_text,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error getting rate status: {e}")
            reply_method(
                "❌ Could not retrieve rate limit status. Please try again later.",
                parse_mode=ParseMode.HTML
            )
    
    def rank_command(self, update: Update, context: CallbackContext):
        """Handle /rank command"""
        logger.info(f"Rank command from user: {update.effective_user.id}, args: {context.args}")
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
        logger.info(f"Starting ranking conversation for user: {update.effective_user.id}")
        update.message.reply_text(
            "🎓 <b>University Ranking</b>\n\nPlease enter the university name:",
            parse_mode=ParseMode.HTML
        )
        return AWAITING_UNIVERSITY
    
    def get_university(self, update: Update, context: CallbackContext):
        """Get university name from user"""
        university_name = update.message.text.strip()
        logger.info(f"User {update.effective_user.id} entered university: {university_name}")
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
        logger.info(f"User {update.effective_user.id} entered country: {country} for university: {university_name}")
        
        self.perform_ranking(update, university_name, country, context)
        return ConversationHandler.END
    
    def cancel_ranking(self, update: Update, context: CallbackContext):
        """Cancel the ranking conversation"""
        logger.info(f"User {update.effective_user.id} cancelled ranking")
        update.message.reply_text("Ranking cancelled.")
        return ConversationHandler.END
    
    def handle_direct_message(self, update: Update, context: CallbackContext):
        """Handle direct ranking requests in message format"""
        message = update.message.text.strip()
        logger.info(f"Direct message from user {update.effective_user.id}: {message}")
        
        # Check if message looks like "University, Country" format
        if ',' in message:
            parts = [p.strip() for p in message.split(',', 1)]
            if len(parts) == 2:
                university_name, country = parts
                logger.info(f"Parsed direct message as university ranking: {university_name}, {country}")
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
    
    def perform_ranking(self, update: Update, university_name: str, country: str, context: CallbackContext):
        """Perform ranking and send results"""
        user_id = update.effective_user.id
        logger.info(f"Performing ranking for user {user_id}: {university_name}, {country}")
        
        processing_msg = update.message.reply_text(
            f"🔍 <b>Analyzing {university_name}...</b>\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            logger.info(f"Starting ranking process for {university_name}")
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country, str(user_id))
            logger.info(f"Ranking data obtained for {university_name}")
            
            # Store ranking data for rationale viewing
            self.user_ranking_data[user_id] = ranking_data
            logger.debug(f"Stored ranking data for user {user_id}")
            
            # Format results
            results_text = self.format_ranking_results(ranking_data)
            logger.info(f"Formatted results for {university_name}")
            
            # Send results
            processing_msg.edit_text(
                results_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_results_keyboard()
            )
            logger.info(f"Results sent for {university_name}")
            
        except RateLimitExceededException as e:
            logger.error(f"Rate limit exceeded during ranking: {e}")
            error_text = f"""
❌ <b>RATE LIMIT EXCEEDED</b>

<b>API:</b> {e.api_type.value.upper()}
<b>Limit:</b> {e.limit_details}
<b>Resets at:</b> {e.reset_time.strftime("%H:%M:%S")}

Using estimated data for this ranking.
            """
            
            # Try to get estimated ranking anyway
            try:
                logger.info(f"Attempting estimated ranking for {university_name} after rate limit")
                ranking_data = self.ranking_system.rank_university(university_name, country, str(user_id))
                results_text = self.format_ranking_results(ranking_data)
                full_text = error_text + "\n\n" + results_text
                
                processing_msg.edit_text(
                    full_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_results_keyboard()
                )
            except Exception as inner_e:
                logger.error(f"Error in fallback ranking: {inner_e}")
                processing_msg.edit_text(
                    error_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=self.get_error_keyboard()
                )
            
        except Exception as e:
            logger.error(f"Error ranking university {university_name}: {e}", exc_info=True)
            error_text = f"❌ <b>Error Ranking University</b>\n\nSorry, I couldn't analyze <b>{university_name}</b>.\n\nError: {str(e)[:200]}"
            
            processing_msg.edit_text(
                error_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_error_keyboard()
            )
    
    def handle_excel_file(self, update: Update, context: CallbackContext):
        """Enhanced Excel file handling with progress updates"""
        user_id = str(update.effective_user.id)
        logger.info(f"Excel file received from user {user_id}")
        
        try:
            # Check if user already has a processing job
            if user_id in self.user_excel_processing:
                logger.warning(f"User {user_id} already has a file being processed")
                update.message.reply_text(
                    "⏳ <b>You already have a file being processed.</b>\n\n"
                    "Please wait for the current processing to complete.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Get the document
            document = update.message.document
            logger.info(f"Document info: {document.file_name}, {document.file_size} bytes")
            
            # Send initial processing message
            processing_msg = update.message.reply_text(
                "📥 <b>File Received!</b>\n\n"
                "🔍 Starting to fetch real data from internet sources...\n"
                "⏳ This may take several minutes for large files.\n\n"
                "<b>Rate Limits Being Respected:</b>\n"
                "• Wikipedia: 100/min, 2000/hour\n"
                "• Google Search: 10/min, 100/hour\n"
                "• Webometrics: 30/min, 500/hour\n\n"
                "<i>If rate limits are hit, I'll use estimated data and show you the details.</i>",
                parse_mode=ParseMode.HTML
            )
            
            # Mark user as processing
            self.user_excel_processing[user_id] = {
                'message_id': processing_msg.message_id,
                'start_time': datetime.now(),
                'last_update': datetime.now()
            }
            logger.info(f"Marked user {user_id} as processing")
            
            # Download the file
            file = context.bot.get_file(document.file_id)
            logger.debug(f"Starting file download")
            
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
                file.download(tmp_file.name)
                input_path = tmp_file.name
            logger.info(f"File downloaded to {input_path}")
            
            # Define progress callback function
            def send_progress_update(progress_message: str):
                """Send progress update to user"""
                try:
                    # Only update every 30 seconds to avoid spamming
                    now = datetime.now()
                    last_update = self.user_excel_processing.get(user_id, {}).get('last_update')
                    
                    if last_update and (now - last_update).total_seconds() >= 30:
                        processing_msg.edit_text(
                            progress_message,
                            parse_mode=ParseMode.HTML
                        )
                        self.user_excel_processing[user_id]['last_update'] = now
                        logger.debug(f"Sent progress update to user {user_id}")
                except Exception as e:
                    logger.error(f"Error sending progress update: {e}")
            
            # Process the Excel file with progress updates
            logger.info(f"Starting Excel processing for user {user_id}")
            
            try:
                output_path, rate_limit_issues = self.ranking_system.process_excel_file(
                    input_path, 
                    user_id,
                    progress_callback=send_progress_update
                )
                
                logger.info(f"Excel processing complete. Output: {output_path}, Rate limit issues: {len(rate_limit_issues)}")
                
                # Prepare final message with detailed statistics
                final_message = self._create_final_summary_message(rate_limit_issues, output_path)
                
                # Send the file
                with open(output_path, 'rb') as result_file:
                    logger.info(f"Sending result file to user {user_id}")
                    context.bot.send_document(
                        chat_id=update.effective_chat.id,
                        document=result_file,
                        filename=f"ranked_{document.file_name}",
                        caption=final_message,
                        parse_mode=ParseMode.HTML
                    )
                logger.info(f"Result file sent successfully to user {user_id}")
                
            except Exception as e:
                logger.error(f"Error during Excel processing: {e}")
                processing_msg.edit_text(
                    f"❌ <b>Error Processing File</b>\n\n{str(e)[:500]}",
                    parse_mode=ParseMode.HTML
                )
            
            # Clean up temporary files and user tracking
            self._cleanup_processing(user_id, input_path, output_path if 'output_path' in locals() else None)
            
        except RateLimitExceededException as e:
            self._handle_rate_limit_exception(update, user_id, e)
        except Exception as e:
            self._handle_general_exception(update, user_id, e)
    
    def _create_final_summary_message(self, rate_limit_issues: List[Dict], output_path: str) -> str:
        """Create final summary message"""
        final_message = "🎯 <b>Enhanced University Rankings - Complete!</b>\n\n"
        
        if rate_limit_issues:
            # Group rate limit issues by API
            api_issues = {}
            for issue in rate_limit_issues:
                api = issue.get('api', 'Unknown')
                if api not in api_issues:
                    api_issues[api] = []
                api_issues[api].append(issue)
            
            final_message += "⚠️ <b>RATE LIMIT ISSUES ENCOUNTERED:</b>\n"
            
            for api, issues in api_issues.items():
                affected_count = len(issues)
                final_message += f"• <b>{api.upper()}</b>: {affected_count} universities affected\n"
            
            final_message += "\n"
        
        # Add color coding explanation
        final_message += "<b>📊 COLOR CODING IN EXCEL:</b>\n"
        final_message += "🟩 Green = Real data from internet sources\n"
        final_message += "🟧 Orange = Estimated scores (no rate limits)\n"
        final_message += "🟥 Red = Estimated due to rate limits\n"
        final_message += "🟨 Yellow = Rank difference from Leap Rank\n\n"
        
        # Add sheet information
        final_message += "<b>📄 SHEETS INCLUDED:</b>\n"
        final_message += "• Rankings: Main results with scores and ranks\n"
        final_message += "• Summary: Processing statistics and metrics\n"
        final_message += "• Processing Stats: Timing and performance data\n"
        if rate_limit_issues:
            final_message += "• Rate Limit Issues: Detailed API limit information\n"
        
        final_message += "\n<i>Note: Check the 'Summary' sheet for detailed processing statistics.</i>"
        
        return final_message
    
    def _cleanup_processing(self, user_id: str, input_path: str, output_path: Optional[str] = None):
        """Clean up processing resources"""
        try:
            if os.path.exists(input_path):
                os.unlink(input_path)
            if output_path and os.path.exists(output_path):
                os.unlink(output_path)
            if user_id in self.user_excel_processing:
                del self.user_excel_processing[user_id]
            logger.info(f"Cleanup completed for user {user_id}")
        except Exception as e:
            logger.error(f"Cleanup error for user {user_id}: {e}")
    
    def _handle_rate_limit_exception(self, update: Update, user_id: str, e: RateLimitExceededException):
        """Handle rate limit exceptions"""
        logger.error(f"Rate limit exceeded during Excel processing for user {user_id}: {e}")
        error_msg = f"""
❌ <b>RATE LIMIT EXCEEDED DURING PROCESSING</b>

<b>API:</b> {e.api_type.value.upper()}
<b>Limit:</b> {e.limit_details}
<b>Resets at:</b> {e.reset_time.strftime("%H:%M:%S")}

Please try again after the reset time, or split your Excel file into smaller batches.
"""
        
        update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
        self._cleanup_processing(user_id, None, None)
    
    def _handle_general_exception(self, update: Update, user_id: str, e: Exception):
        """Handle general exceptions"""
        logger.error(f"Error processing Excel file for user {user_id}: {e}", exc_info=True)
        error_msg = f"""
❌ <b>ERROR PROCESSING FILE</b>

{str(e)[:500]}

Please ensure your Excel file has the correct format:
• University/Institution names (first column)
• Country names (second column, optional)
• (Optional) Ranking column
"""
        
        update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
        self._cleanup_processing(user_id, None, None)
    
    def rate_status_callback(self, query, context: CallbackContext):
        """Check current API rate limit status for callback queries"""
        user_id = query.from_user.id
        logger.info(f"Rate_status callback from user: {user_id}")
        
        try:
            # Get rate limiter from ranking system
            rate_limiter = self.ranking_system.data_fetcher.rate_limiter
            
            # Get status of all APIs
            all_status = rate_limiter.get_all_status()
            
            status_text = "📊 <b>CURRENT API RATE LIMIT STATUS</b>\n\n"
            
            for status in all_status:
                api_name = status['api'].upper()
                used_minute = status['calls_last_minute']
                limit_minute = status['minute_limit']
                available_minute = status['available_minute']
                
                # Create status indicator
                if available_minute > limit_minute * 0.5:
                    indicator = "🟢"
                elif available_minute > limit_minute * 0.2:
                    indicator = "🟡"
                else:
                    indicator = "🔴"
                
                status_text += f"{indicator} <b>{api_name}</b>\n"
                status_text += f"   Minute: {used_minute}/{limit_minute} (Avail: {available_minute})\n"
                status_text += f"   Hour: {status['calls_last_hour']}/{status['hourly_limit']}\n"
                status_text += f"   Day: {status['calls_last_day']}/{status['daily_limit']}\n\n"
            
            # Add next reset info
            next_reset = None
            for api_type in APIType:
                reset_time = rate_limiter.get_next_reset_time(api_type)
                if reset_time:
                    if next_reset is None or reset_time < next_reset:
                        next_reset = reset_time
            
            if next_reset:
                time_until = next_reset - datetime.now()
                minutes_until = max(0, int(time_until.total_seconds() / 60))
                status_text += f"⏰ <b>Next reset in:</b> {minutes_until} minutes\n"
            
            status_text += "\n<i>Note: Limits reset automatically. Large Excel files may hit limits.</i>"
            
            query.edit_message_text(
                status_text,
                parse_mode=ParseMode.HTML
            )
            
        except Exception as e:
            logger.error(f"Error getting rate status: {e}")
            query.edit_message_text(
                "❌ Could not retrieve rate limit status. Please try again later.",
                parse_mode=ParseMode.HTML
            )

    def rank_excel_callback(self, query, context: CallbackContext):
        """Handle rank_excel command for callback queries"""
        user_id = query.from_user.id
        logger.info(f"Rank_excel callback from user: {user_id}")
        
        instructions = """
    <b>📊 Excel Ranking Instructions</b>

    Please send me an Excel file (.xlsx or .xls) with university data.

    <b>⚠️ IMPORTANT FOR LARGE FILES:</b>
    • 700+ universities will take approximately 30-60 minutes
    • I'll send progress updates every 30 seconds
    • Rate limits are strictly enforced to avoid API blocks
    • Large files will use more estimated data

    <b>📈 TIME ESTIMATES:</b>
    • 100 universities: ~5-10 minutes
    • 300 universities: ~15-30 minutes  
    • 500 universities: ~25-50 minutes
    • 700+ universities: ~35-70 minutes

    <b>Required Columns:</b>
    - University/Institution names (first column)
    - Country names (second column, optional)

    <b>Optional Column:</b>
    - Any ranking column (e.g., Leap Rank)

    <b>I will automatically detect columns and add:</b>
    - Global Score (0-100)
    - Global Rank (1 = best worldwide)
    - Country Rank (1 = best in country)
    - Data Source (Real Data/Estimated)
    - Rate Limit Status
    - Processing Time

    <b>Just send me your Excel file now!</b>
        """
        
        query.edit_message_text(
            instructions,
            parse_mode=ParseMode.HTML
        )

    def button_handler(self, update: Update, context: CallbackContext):
        """Handle button callbacks"""
        query = update.callback_query
        query.answer()
        data = query.data
        user_id = query.from_user.id
        
        logger.info(f"Button click from user {user_id}: {data}")
        
        if data == "rate_status":
            # Fix: Pass the query and context to a new method that handles callback queries
            self.rate_status_callback(query, context)
        elif data == "start_ranking":
            query.edit_message_text(
                "🎓 <b>University Ranking</b>\n\nPlease enter the university name:",
                parse_mode=ParseMode.HTML
            )
            query.message.reply_text("Please use /rank command to start ranking.")
        elif data == "rank_excel":
            self.rank_excel_callback(query, context)
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
    
    def perform_ranking_callback(self, query, university_name: str, country: str):
        """Perform ranking from callback"""
        user_id = query.from_user.id
        logger.info(f"Perform ranking callback for user {user_id}: {university_name}, {country}")
        
        query.edit_message_text(
            f"🔍 <b>Analyzing {university_name}...</b>\n\nPlease wait while I gather data...",
            parse_mode=ParseMode.HTML
        )
        
        try:
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(university_name, country, str(user_id))
            
            # Store ranking data for rationale viewing
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
            logger.error(f"Error in ranking callback: {e}")
            error_text = f"❌ <b>Error Ranking University</b>\n\nSorry, I couldn't analyze <b>{university_name}</b>.\n\nPlease try again."
            
            query.edit_message_text(
                error_text,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_error_keyboard()
            )
    
    def show_parameter_rationale(self, query, param_code: str, ranking_data: UniversityData):
        """Show rationale for a specific parameter"""
        logger.debug(f"Showing rationale for {param_code}")
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
        logger.debug(f"Showing all rationales for {ranking_data.name}")
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
        logger.debug(f"Showing sources for {ranking_data.name}")
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
        logger.debug("Showing tiers information")
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
        logger.debug("Showing parameters information")
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
        logger.debug("Showing main menu")
        welcome_text = """
🎓 Welcome to <b>pkUniRankBot</b>!

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

Click the buttons below to get started!
        """
        
        keyboard = [
            [InlineKeyboardButton("🎯 Rank a University", callback_data="start_ranking")],
            [InlineKeyboardButton("📊 Process Excel File", callback_data="rank_excel")],
            [InlineKeyboardButton("📈 Check Rate Limits", callback_data="rate_status")],
            [InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")],
            [InlineKeyboardButton("📊 View Parameters", callback_data="view_parameters")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.edit_message_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup
        )
    
    def format_ranking_results(self, data: UniversityData) -> str:
        """Format ranking results as HTML text"""
        logger.debug(f"Formatting ranking results for {data.name}")
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
        
        # Data source information
        if hasattr(data, 'is_estimated') and not data.is_estimated:
            results += "\n<b>✅ DATA SOURCE:</b> Real data from internet sources\n"
            if hasattr(data, 'real_data_sources') and data.real_data_sources:
                results += "<b>📚 Sources used:</b>\n"
                for source in data.real_data_sources[:3]:  # Show top 3 sources
                    results += f"• {source}\n"
        else:
            results += "\n<b>⚠️ DATA SOURCE:</b> Estimated based on patterns\n"
            results += "<i>Note: Real-time data fetching was limited or rate-limited</i>\n"
        
        # Add rate limit information if available
        if hasattr(data, 'rate_limit_info') and data.rate_limit_info:
            rate_limited = [info for info in data.rate_limit_info if info.get('status') == 'rate_limited']
            if rate_limited:
                results += "\n<b>⚠️ RATE LIMIT NOTES:</b>\n"
                for info in rate_limited[:2]:  # Show max 2 rate limit issues
                    api_name = info.get('api', 'Unknown API')
                    reset_time = info.get('reset_time')
                    if isinstance(reset_time, datetime):
                        reset_str = reset_time.strftime("%H:%M:%S")
                        results += f"• {api_name}: Limited, resets at {reset_str}\n"
                    else:
                        results += f"• {api_name}: Rate limited\n"
        
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
        
        logger.debug(f"Results formatted for {data.name}")
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
                InlineKeyboardButton("📈 Check Rate Limits", callback_data="rate_status"),
                InlineKeyboardButton("🏆 View Tiers", callback_data="view_tiers")
            ],
            [
                InlineKeyboardButton("📊 View Parameters", callback_data="view_parameters"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
            ]
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
    """Main function to run the enhanced bot"""
    # Check for required packages
    try:
        import telegram
        import numpy
        import pandas
        import openpyxl
        import requests
        import wikipedia
        from googlesearch import search
    except ImportError as e:
        print(f"❌ Missing package: {e}")
        print("Install with: pip install python-telegram-bot numpy pandas openpyxl wikipedia-api google")
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
        print("🤖 Starting Enhanced pkUniRankBot with Rate Limiting...")
        print(f"📊 Version: python-telegram-bot v{telegram.__version__}")
        print(f"📈 pandas v{pandas.__version__}")
        print("📝 Detailed logging enabled")
        print("⚠️  Rate limits enforced for all external APIs")
        print("🔄 Auto-fallback to estimation when limits hit")
        print("📊 Rate limit status available via /rate_status")
        print("🔍 Progress tracking for all operations")
        
        bot = EnhancedUniRankBot(token)
        bot.start()
    except Exception as e:
        print(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()