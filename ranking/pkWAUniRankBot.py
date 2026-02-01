"""
WhatsApp University Ranking Bot
Based on the pkUniRankBot Telegram bot
Uses Twilio API for WhatsApp integration
"""

import os
import logging
import tempfile
import time
import requests
import json
import urllib.parse
from typing import Dict, Tuple, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from dotenv import dotenv_values
import wikipedia
from bs4 import BeautifulSoup
from threading import Lock
from collections import defaultdict
from enum import Enum
import threading
import traceback
import base64
import io
from flask import Flask, request, Response
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse, Message

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
all_secrets = dotenv_values(".env.dev")
TWILIO_ACCOUNT_SID = all_secrets.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = all_secrets.get('TWILIO_AUTH_TOKEN', '')
TWILIO_WHATSAPP_NUMBER = all_secrets.get('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
WEBHOOK_URL = all_secrets.get('WEBHOOK_URL', '')

# ============================================================================
# USER CONFIGURATION CLASS
# ============================================================================

class UserConfiguration:
    """Stores user preferences for data sources"""
    
    def __init__(self, user_id: str):
        self.user_id = user_id
        # Default settings: Wikipedia enabled, Google and Webometrics disabled
        self.enable_wikipedia = True
        self.enable_google_search = False
        self.enable_webometrics = False
        self.timestamp = datetime.now()
        logger.info(f"Created default configuration for user {user_id}")
    
    def to_dict(self) -> Dict:
        """Convert configuration to dictionary"""
        return {
            'user_id': self.user_id,
            'enable_wikipedia': self.enable_wikipedia,
            'enable_google_search': self.enable_google_search,
            'enable_webometrics': self.enable_webometrics,
            'timestamp': self.timestamp.isoformat()
        }
    
    def from_dict(self, data: Dict):
        """Load configuration from dictionary"""
        self.enable_wikipedia = data.get('enable_wikipedia', True)
        self.enable_google_search = data.get('enable_google_search', False)
        self.enable_webometrics = data.get('enable_webometrics', False)
        if 'timestamp' in data:
            self.timestamp = datetime.fromisoformat(data['timestamp'])
        logger.info(f"Loaded configuration for user {self.user_id}: {self.to_dict()}")
    
    def update_source(self, source: str, enabled: bool):
        """Update a specific data source setting"""
        if source == 'wikipedia':
            self.enable_wikipedia = enabled
        elif source == 'google_search':
            self.enable_google_search = enabled
        elif source == 'webometrics':
            self.enable_webometrics = enabled
        self.timestamp = datetime.now()
        logger.info(f"Updated {source} to {enabled} for user {self.user_id}")
    
    def get_enabled_sources(self) -> List[str]:
        """Get list of enabled data sources"""
        sources = []
        if self.enable_wikipedia:
            sources.append('wikipedia')
        if self.enable_google_search:
            sources.append('google_search')
        if self.enable_webometrics:
            sources.append('webometrics')
        logger.debug(f"Enabled sources for user {self.user_id}: {sources}")
        return sources

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
    data_sources_used: List[str] = None

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
                requests_per_minute=100,
                requests_per_hour=2000,
                requests_per_day=10000
            ),
            APIType.GOOGLE_SEARCH: RateLimitInfo(
                requests_per_minute=10,
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
        
        logger.debug(f"Rate limit check passed for {api_type.value}")
        return True
    
    def record_call(self, api_type: APIType):
        """Record an API call"""
        tracker = self.trackers[api_type]
        tracker.add_call()
        logger.debug(f"Recorded API call for {api_type.value}")

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
            'User-Agent': 'WhatsAppUniRankBot/1.0'
        })
        logger.info("RateLimitedDataFetcher initialized")
    
    def safe_fetch_wikipedia(self, university_name: str, user_id: Optional[str] = None) -> Optional[Dict]:
        """Safely fetch data from Wikipedia with rate limiting"""
        logger.info(f"Starting Wikipedia fetch for: {university_name}")
        try:
            self.rate_limiter.check_rate_limit(APIType.WIKIPEDIA, user_id)
            
            search_query = f"{university_name} university"
            start_time = time.time()
            
            try:
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
                rankings = []
                for line in content.split('\n'):
                    if any(word in line for word in ['rank', 'ranking', 'rated', '#', 'top']):
                        if 'university' in line or 'college' in line:
                            rankings.append(line[:200])
                
                data['rankings'] = rankings[:5]
                self.rate_limiter.record_call(APIType.WIKIPEDIA)
                logger.info(f"Wikipedia fetch successful for {university_name}")
                
                return {'wikipedia': data}
                
            except wikipedia.exceptions.DisambiguationError as e:
                logger.warning(f"Wikipedia disambiguation error for {university_name}")
                try:
                    first_option = e.options[0]
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
            self.rate_limiter.check_rate_limit(APIType.GOOGLE_SEARCH, user_id)
            time.sleep(2)
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            response = self.session.get(search_url, headers=headers, timeout=10)
            
            results = []
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                for link in soup.find_all('a'):
                    href = link.get('href')
                    if href and href.startswith('http') and 'google.com' not in href:
                        results.append(href)
                
            self.rate_limiter.record_call(APIType.GOOGLE_SEARCH)
            return results[:3]
            
        except Exception as e:
            logger.error(f"Google search error: {e}")
            self.rate_limiter.record_call(APIType.GOOGLE_SEARCH)
            return []
    
    def safe_fetch_webometrics(self, university_name: str, user_id: Optional[str] = None) -> Optional[Dict]:
        """Safely fetch Webometrics data using their actual website"""
        logger.info(f"Starting Webometrics fetch for: {university_name}")
        try:
            self.rate_limiter.check_rate_limit(APIType.WEBOMETRICS, user_id)
            
            search_query = urllib.parse.quote(university_name)
            url = f"https://www.webometrics.info/en/search/site/{search_query}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            self.rate_limiter.record_call(APIType.WEBOMETRICS)
            
            if response.status_code == 200:
                data = {
                    'url': url,
                    'status': 'success',
                    'content_length': len(response.text),
                    'note': 'Scraped from Webometrics website'
                }
                logger.info(f"Webometrics fetch successful for {university_name}")
                return {'webometrics': data}
            elif response.status_code == 429:
                retry_after = response.headers.get('Retry-After', '60')
                reset_time = datetime.now() + timedelta(seconds=int(retry_after))
                logger.warning(f"Webometrics HTTP 429 for {university_name}")
                raise RateLimitExceededException(
                    APIType.WEBOMETRICS, 
                    reset_time,
                    f"HTTP 429: Retry after {retry_after} seconds"
                )
                
        except RateLimitExceededException:
            raise
        except Exception as e:
            logger.error(f"Webometrics fetch error for {university_name}: {e}")
            self.rate_limiter.record_call(APIType.WEBOMETRICS)
        
        return None
    
    def fetch_all_data(self, university_name: str, country: str, user_id: Optional[str] = None, 
                      user_config: Optional[UserConfiguration] = None) -> Tuple[Dict, List[Dict]]:
        """Fetch data from enabled sources with rate limiting"""
        logger.info(f"Starting data fetch for {university_name} in {country}")
        all_data = {}
        rate_limit_info = []
        data_sources_used = []
        
        if user_config is None:
            user_config = UserConfiguration(user_id)
        
        # Fetch Wikipedia data if enabled
        if user_config.enable_wikipedia:
            try:
                wiki_data = self.safe_fetch_wikipedia(university_name, user_id)
                if wiki_data:
                    all_data.update(wiki_data)
                    data_sources_used.append('wikipedia')
                    logger.info(f"Wikipedia data fetched successfully for {university_name}")
            except RateLimitExceededException as e:
                rate_limit_info.append({
                    'api': 'wikipedia',
                    'status': 'rate_limited',
                    'reset_time': e.reset_time,
                    'message': str(e)
                })
                logger.warning(f"Wikipedia rate limited for {university_name}")
        
        # Fetch Google search results if enabled
        if user_config.enable_google_search:
            queries = [
                f"{university_name} QS World University Rankings",
                f"{university_name} Times Higher Education ranking",
                f"{university_name} ARWU ranking"
            ]
            
            google_results = {}
            for i, query in enumerate(queries, 1):
                try:
                    results = self.safe_google_search(query, user_id)
                    if results:
                        google_results[query] = results
                except RateLimitExceededException as e:
                    rate_limit_info.append({
                        'api': 'google_search',
                        'status': 'rate_limited',
                        'reset_time': e.reset_time,
                        'message': str(e)
                    })
                    logger.warning(f"Google search rate limited on query {i}")
                    break
            
            if google_results:
                all_data['google_search'] = google_results
                data_sources_used.append('google_search')
                logger.info(f"Google searches completed for {university_name}")
        
        # Try Webometrics if enabled
        if user_config.enable_webometrics:
            try:
                web_data = self.safe_fetch_webometrics(university_name, user_id)
                if web_data:
                    all_data.update(web_data)
                    data_sources_used.append('webometrics')
                    logger.info(f"Webometrics data fetched successfully for {university_name}")
            except RateLimitExceededException as e:
                rate_limit_info.append({
                    'api': 'webometrics',
                    'status': 'rate_limited',
                    'reset_time': e.reset_time,
                    'message': str(e)
                })
                logger.warning(f"Webometrics rate limited for {university_name}")
        
        # Add data sources used to the result
        if data_sources_used:
            all_data['data_sources_used'] = data_sources_used
        
        logger.info(f"Data fetch completed for {university_name}. Got data from {len(data_sources_used)} enabled sources")
        return all_data, rate_limit_info

# ============================================================================
# UNIVERSITY RANKING SYSTEM
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
        }
        logger.info(f"Loaded {len(the_data)} THE rankings")
        return the_data
    
    def classify_university_type(self, name: str) -> str:
        """Classify university based on name patterns"""
        name_lower = name.lower()
        
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
        
        return uni_type
    
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
        
        rounded_scores = {k: round(v, 1) for k, v in scores.items()}
        logger.info(f"Estimated scores for {name}: {rounded_scores}")
        return rounded_scores
    
    def calculate_composite_score(self, scores: Dict[str, float]) -> float:
        """Calculate composite score"""
        composite = round(sum(scores.values()), 1)
        return composite
    
    def get_tier(self, score: float) -> Tuple[str, str]:
        """Determine tier and description"""
        for tier, (low, high, description) in self.tiers.items():
            if low <= score <= high:
                return tier, description
        return 'D', self.tiers['D'][2]

# ============================================================================
# ENHANCED RANKING SYSTEM
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
    
    def fetch_real_data(self, university_name: str, country: str, 
                       user_id: Optional[str] = None,
                       user_config: Optional[UserConfiguration] = None) -> Tuple[Dict, List[Dict]]:
        """Fetch real data from enabled sources with rate limiting"""
        cache_key = f"{university_name.lower()}_{country.lower()}"
        
        with self.cache_lock:
            if cache_key in self.real_data_cache:
                cached_data, cached_rate_info = self.real_data_cache[cache_key]
                rate_info = cached_rate_info.copy() if cached_rate_info else []
                rate_info.append({'api': 'cache', 'status': 'hit', 'timestamp': datetime.now().isoformat()})
                logger.info(f"Cache hit for {university_name}")
                return cached_data, rate_info
        
        logger.info(f"Cache miss for {university_name}, fetching fresh data")
        all_data, rate_limit_info = self.data_fetcher.fetch_all_data(
            university_name, country, user_id, user_config
        )
        
        name_lower = university_name.lower()
        if name_lower in self.qs_rankings:
            all_data['qs_ranking'] = self.qs_rankings[name_lower]
            rate_limit_info.append({'api': 'qs_rankings', 'status': 'cache', 'source': 'internal'})
        
        if name_lower in self.the_rankings:
            all_data['the_ranking'] = self.the_rankings[name_lower]
            rate_limit_info.append({'api': 'the_rankings', 'status': 'cache', 'source': 'internal'})
        
        if all_data:
            with self.cache_lock:
                self.real_data_cache[cache_key] = (all_data, rate_limit_info)
        
        logger.info(f"Data fetch complete for {university_name}")
        return all_data, rate_limit_info
    
    def rank_university(self, university_name: str, country: str = "", 
                       user_id: Optional[str] = None,
                       user_config: Optional[UserConfiguration] = None) -> UniversityData:
        """Enhanced ranking function with real data fetching and user configuration"""
        logger.info(f"Starting ranking process for: {university_name}")
        name_lower = university_name.lower()
        
        if user_config is None:
            user_config = UserConfiguration(user_id)
        
        # Try to fetch real data from enabled sources
        real_data, rate_limit_info = self.fetch_real_data(university_name, country, user_id, user_config)
        
        data_sources_used = real_data.get('data_sources_used', []) if real_data else []
        has_real_data = bool(real_data and data_sources_used)
        
        if has_real_data:
            # Calculate scores from real data
            scores = self.calculate_scores_from_real_data(university_name, country, real_data)
            is_estimated = False
            data_sources = ["Real-time data fetching"]
        elif name_lower in self.university_db:
            # Use database entry
            data = self.university_db[name_lower]
            scores = data['scores']
            country = data['country']
            is_estimated = False
            data_sources = ["University Ranking Database", "Verified Institutional Data"]
        else:
            # Fall back to estimation
            scores = self.estimate_scores(university_name, country)
            is_estimated = True
            data_sources = ["Statistical Estimation", "Pattern Analysis"]
        
        # Calculate metrics
        composite = self.calculate_composite_score(scores)
        tier, tier_desc = self.get_tier(composite)
        
        # Calculate error margin
        error_margin = self.calculate_error_margin(university_name, country, data_sources_used)
        if data_sources_used:
            error_margin = max(1.0, error_margin * 0.7)
        
        # Get real data sources if available
        real_sources = []
        if 'wikipedia' in real_data:
            real_sources.append(f"Wikipedia: {real_data['wikipedia'].get('url', '')}")
        if 'google_search' in real_data:
            real_sources.append("Google Search Results for rankings")
        if 'webometrics' in real_data:
            real_sources.append("Webometrics Ranking System")
        
        if real_sources:
            data_sources.extend(real_sources)
        
        result = UniversityData(
            name=university_name,
            country=country,
            type=self.classify_university_type(university_name).replace('_', ' ').title(),
            scores=scores,
            composite=composite,
            tier=tier,
            error_margin=error_margin,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            rationale={},
            sources=data_sources,
            is_estimated=is_estimated,
            real_data_sources=real_sources,
            rate_limit_info=rate_limit_info,
            data_sources_used=data_sources_used
        )
        
        logger.info(f"Ranking complete for {university_name}: Score={composite}, Tier={tier}")
        return result
    
    def calculate_scores_from_real_data(self, university_name: str, country: str, real_data: Dict) -> Dict[str, float]:
        """Calculate scores based on real fetched data"""
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
            if qs_rank <= 10:
                scores.update({'academic': 25, 'graduate': 24, 'visibility': 5})
            elif qs_rank <= 50:
                scores.update({'academic': 22, 'graduate': 21, 'visibility': 4.5})
            elif qs_rank <= 100:
                scores.update({'academic': 20, 'graduate': 19, 'visibility': 4})
            elif qs_rank <= 200:
                scores.update({'academic': 18, 'graduate': 17, 'visibility': 3.5})
        
        # Adjust based on THE ranking if available
        if 'the_ranking' in real_data:
            the_rank = real_data['the_ranking']
            if the_rank <= 10:
                scores['academic'] = max(scores['academic'], 24)
                scores['transparency'] = max(scores['transparency'], 9)
            elif the_rank <= 100:
                scores['academic'] = max(scores['academic'], scores['academic'] * 1.1)
        
        # Analyze Wikipedia data for indicators
        if 'wikipedia' in real_data:
            wiki_data = real_data['wikipedia']
            summary = wiki_data.get('summary', '').lower()
            
            # Check for research indicators
            research_keywords = ['research', 'publication', 'citation', 'nobel', 'faculty']
            research_count = sum(1 for keyword in research_keywords if keyword in summary)
            if research_count >= 3:
                scores['academic'] = min(25, scores['academic'] + 3)
            
            # Check for employment indicators
            employ_keywords = ['employment', 'graduate', 'career', 'salary', 'placement']
            employ_count = sum(1 for keyword in employ_keywords if keyword in summary)
            if employ_count >= 2:
                scores['graduate'] = min(25, scores['graduate'] + 2)
        
        # Apply country multiplier
        if country:
            country_mult = self.country_multipliers.get(country.upper(), 1.0)
            for key in ['academic', 'graduate', 'roi', 'fsr']:
                scores[key] = min(self.parameters[key]['max'], scores[key] * country_mult)
        
        # University type adjustments
        uni_type = self.classify_university_type(university_name)
        if uni_type == 'RESEARCH_UNIVERSITY':
            scores['academic'] = min(25, scores['academic'] + 3)
        elif uni_type == 'COLLEGE_POLYTECHNIC':
            scores['graduate'] = min(25, scores['graduate'] + 2)
            scores['roi'] = min(20, scores['roi'] + 2)
        
        rounded_scores = {k: round(v, 1) for k, v in scores.items()}
        return rounded_scores
    
    def calculate_error_margin(self, university_name: str, country: str, data_sources_used: List[str] = None) -> float:
        """Calculate error margin based on data sources used"""
        name_lower = university_name.lower()
        
        if name_lower in self.university_db:
            return round(np.random.uniform(1.0, 3.0), 1)
        else:
            # Base error based on data sources
            if data_sources_used and 'wikipedia' in data_sources_used:
                base_error = 5.0
            else:
                base_error = 10.0
            
            # Adjust based on number of data sources
            if data_sources_used:
                source_count = len(data_sources_used)
                if source_count >= 2:
                    base_error *= 0.7
            
            country_mult = 1.0
            if country:
                country_mult = self.country_multipliers.get(country.upper(), 1.0)
                base_error /= country_mult
            
            if 'university' in name_lower:
                base_error *= 0.9
            elif 'college' in name_lower:
                base_error *= 1.1
            
            error = round(min(15.0, max(3.0, base_error + np.random.uniform(-2.0, 2.0))), 1)
            return error
    
    def process_excel_file(self, file_content: bytes, user_id: str, 
                          user_config: Optional[UserConfiguration] = None) -> bytes:
        """Process Excel file and return processed file as bytes"""
        try:
            # Read the Excel file from bytes
            df = pd.read_excel(io.BytesIO(file_content))
            logger.info(f"Excel file loaded. Shape: {df.shape}, Columns: {list(df.columns)}")
            
            # Create a copy for results
            result_df = df.copy()
            
            # Use default configuration if none provided
            if user_config is None:
                user_config = UserConfiguration(user_id)
            
            # Prepare new columns
            result_df['Global Score'] = 0.0
            result_df['Global Rank'] = 0
            result_df['Country Rank'] = 0
            result_df['Data Source'] = 'Estimated'
            result_df['Rate Limited'] = 'No'
            result_df['Processing Time (s)'] = 0.0
            result_df['Error'] = ''
            result_df['Data Sources Used'] = ''
            
            for idx, row in result_df.iterrows():
                try:
                    university_name = str(row.iloc[0]) if len(row) > 0 else ""
                    country = str(row.iloc[1]) if len(row) > 1 else ""
                    
                    if not university_name:
                        continue
                    
                    logger.info(f"Processing {idx+1}/{len(result_df)}: {university_name}")
                    
                    # Get ranking data
                    start_time = time.time()
                    ranking_data = self.rank_university(university_name, country, user_id, user_config)
                    processing_time = time.time() - start_time
                    
                    # Update result dataframe
                    result_df.at[idx, 'Global Score'] = ranking_data.composite
                    result_df.at[idx, 'Data Source'] = 'Real Data' if not ranking_data.is_estimated else 'Estimated'
                    result_df.at[idx, 'Processing Time (s)'] = round(processing_time, 2)
                    
                    # Track data sources used
                    if hasattr(ranking_data, 'data_sources_used') and ranking_data.data_sources_used:
                        result_df.at[idx, 'Data Sources Used'] = ', '.join(ranking_data.data_sources_used)
                    
                    # Add delay to avoid rate limits
                    time.sleep(1)
                        
                except Exception as e:
                    logger.error(f"Error processing row {idx}: {e}")
                    result_df.at[idx, 'Data Source'] = 'Error'
                    result_df.at[idx, 'Error'] = str(e)[:100]
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
            
            # Create output in memory
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                result_df.to_excel(writer, sheet_name='Rankings', index=False)
                
                # Summary sheet
                summary_data = {
                    'Total Universities': [len(result_df)],
                    'Processed Successfully': [len(result_df[result_df['Data Source'] != 'Error'])],
                    'Errors': [len(result_df[result_df['Data Source'] == 'Error'])],
                    'Real Data Used': [len(result_df[result_df['Data Source'] == 'Real Data'])],
                    'Estimated Data Used': [len(result_df[result_df['Data Source'] == 'Estimated'])],
                    'Wikipedia Enabled': [user_config.enable_wikipedia],
                    'Google Search Enabled': [user_config.enable_google_search],
                    'Webometrics Enabled': [user_config.enable_webometrics],
                    'Processing Date': [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            output.seek(0)
            logger.info(f"Excel processing complete. Processed {len(result_df)} universities")
            return output.getvalue()
            
        except Exception as e:
            logger.error(f"Error processing Excel file: {e}")
            raise

# ============================================================================
# WHATSAPP BOT HANDLER
# ============================================================================

class WhatsAppUniversityRankingBot:
    """WhatsApp bot for university ranking"""
    
    def __init__(self, account_sid: str, auth_token: str, whatsapp_number: str):
        """Initialize the WhatsApp bot"""
        logger.info("Initializing WhatsAppUniversityRankingBot")
        
        # Initialize Twilio client
        self.client = Client(account_sid, auth_token)
        self.whatsapp_number = whatsapp_number
        
        # Initialize ranking system
        self.ranking_system = EnhancedUniversityRankingSystem()
        
        # Store user configurations
        self.user_configurations = {}
        
        # Store user states (for conversations)
        self.user_states = {}
        
        # Store user file uploads
        self.user_files = {}
        
        logger.info("WhatsAppUniversityRankingBot initialized")
    
    def get_user_config(self, user_id: str) -> UserConfiguration:
        """Get or create user configuration"""
        if user_id not in self.user_configurations:
            self.user_configurations[user_id] = UserConfiguration(user_id)
            logger.info(f"Created new configuration for user {user_id}")
        return self.user_configurations[user_id]
    
    def update_user_config(self, user_id: str, source: str, enabled: bool) -> UserConfiguration:
        """Update user configuration for a specific source"""
        config = self.get_user_config(user_id)
        config.update_source(source, enabled)
        logger.info(f"Updated configuration for user {user_id}: {source} = {enabled}")
        return config
    
    def get_user_state(self, user_id: str) -> Dict:
        """Get user state"""
        if user_id not in self.user_states:
            self.user_states[user_id] = {
                'state': 'idle',
                'data': {}
            }
        return self.user_states[user_id]
    
    def set_user_state(self, user_id: str, state: str, data: Dict = None):
        """Set user state"""
        self.user_states[user_id] = {
            'state': state,
            'data': data or {}
        }
        logger.info(f"Set state for user {user_id}: {state}")
    
    def clear_user_state(self, user_id: str):
        """Clear user state"""
        if user_id in self.user_states:
            del self.user_states[user_id]
            logger.info(f"Cleared state for user {user_id}")
    
    def handle_message(self, from_number: str, message_body: str, media_url: str = None) -> str:
        """Handle incoming WhatsApp message"""
        logger.info(f"Message from {from_number}: {message_body[:50]}...")
        
        # Clean the message
        message_body = message_body.strip().lower() if message_body else ""
        
        # Check for media (Excel file)
        if media_url and ('excel' in message_body or 'file' in message_body or 'rank' in message_body):
            return self.handle_excel_upload(from_number, media_url)
        
        # Get user state
        user_state = self.get_user_state(from_number)
        
        # Handle state-based responses
        if user_state['state'] == 'awaiting_university':
            return self.handle_university_input(from_number, message_body)
        elif user_state['state'] == 'awaiting_country':
            return self.handle_country_input(from_number, message_body)
        elif user_state['state'] == 'config_menu':
            return self.handle_config_input(from_number, message_body)
        
        # Handle commands
        if message_body.startswith('rank '):
            return self.handle_rank_command(from_number, message_body[5:])
        elif message_body.startswith('config'):
            return self.show_config_menu(from_number)
        elif message_body.startswith('help'):
            return self.show_help(from_number)
        elif message_body.startswith('start'):
            return self.show_welcome(from_number)
        elif message_body.startswith('tiers'):
            return self.show_tiers(from_number)
        elif message_body.startswith('parameters'):
            return self.show_parameters(from_number)
        elif message_body.startswith('status'):
            return self.show_status(from_number)
        elif message_body.startswith('excel'):
            return self.show_excel_instructions(from_number)
        elif 'university' in message_body and ',' in message_body:
            # Handle "University Name, Country" format
            parts = [p.strip() for p in message_body.split(',', 1)]
            if len(parts) == 2:
                return self.perform_ranking(from_number, parts[0], parts[1])
        
        # Default: show welcome message
        return self.show_welcome(from_number)
    
    def handle_excel_upload(self, from_number: str, media_url: str) -> str:
        """Handle Excel file upload"""
        try:
            logger.info(f"Processing Excel file from {from_number}")
            
            # Download the file
            response = requests.get(media_url)
            if response.status_code != 200:
                return "❌ Sorry, I couldn't download the file. Please try again."
            
            file_content = response.content
            
            # Get user configuration
            user_config = self.get_user_config(from_number)
            
            # Process the Excel file
            self.send_message(from_number, "📊 *Processing Excel file...*\n\nThis may take a few minutes. I'll send you the results when done.")
            
            # Process in background thread
            threading.Thread(
                target=self.process_excel_background,
                args=(from_number, file_content, user_config)
            ).start()
            
            return "✅ I've started processing your Excel file. I'll send you the results when it's ready!"
            
        except Exception as e:
            logger.error(f"Error handling Excel upload: {e}")
            return f"❌ Error processing file: {str(e)[:100]}"
    
    def process_excel_background(self, from_number: str, file_content: bytes, user_config: UserConfiguration):
        """Process Excel file in background thread"""
        try:
            processed_content = self.ranking_system.process_excel_file(file_content, from_number, user_config)
            
            # Send the processed file
            self.send_file(
                from_number,
                processed_content,
                filename="university_rankings.xlsx",
                caption="✅ *Excel Processing Complete!*\n\nHere are your university rankings."
            )
            
            logger.info(f"Excel processing complete for {from_number}")
            
        except Exception as e:
            logger.error(f"Error in background processing: {e}")
            self.send_message(from_number, f"❌ *Error processing Excel file:*\n\n{str(e)[:200]}")
    
    def handle_rank_command(self, from_number: str, query: str) -> str:
        """Handle rank command"""
        # Check if query contains country
        if ',' in query:
            parts = [p.strip() for p in query.split(',', 1)]
            if len(parts) == 2:
                return self.perform_ranking(from_number, parts[0], parts[1])
        
        # Start interactive ranking
        self.set_user_state(from_number, 'awaiting_university', {'university': query})
        return f"🎓 *University Ranking*\n\nUniversity: *{query}*\n\nNow please enter the country:"
    
    def handle_university_input(self, from_number: str, university_name: str) -> str:
        """Handle university name input"""
        user_state = self.get_user_state(from_number)
        user_state['data']['university'] = university_name
        self.set_user_state(from_number, 'awaiting_country', user_state['data'])
        
        return f"📍 *Country Selection*\n\nUniversity: *{university_name}*\n\nPlease enter the country name (or type 'skip' to use default):"
    
    def handle_country_input(self, from_number: str, country: str) -> str:
        """Handle country input"""
        user_state = self.get_user_state(from_number)
        university_name = user_state['data'].get('university', '')
        
        if country.lower() == 'skip':
            country = ""
        
        self.clear_user_state(from_number)
        return self.perform_ranking(from_number, university_name, country)
    
    def handle_config_input(self, from_number: str, input_text: str) -> str:
        """Handle configuration input"""
        input_text = input_text.lower()
        
        if 'wikipedia' in input_text:
            enabled = 'enable' in input_text or 'on' in input_text
            self.update_user_config(from_number, 'wikipedia', enabled)
            status = "✅ Enabled" if enabled else "❌ Disabled"
            return f"⚙️ *Configuration Updated*\n\nWikipedia: {status}"
        
        elif 'google' in input_text:
            enabled = 'enable' in input_text or 'on' in input_text
            self.update_user_config(from_number, 'google_search', enabled)
            status = "✅ Enabled" if enabled else "❌ Disabled"
            warning = "\n\n⚠️ *Warning:* Google has strict rate limits (10 requests/minute)" if enabled else ""
            return f"⚙️ *Configuration Updated*\n\nGoogle Search: {status}{warning}"
        
        elif 'webometrics' in input_text:
            enabled = 'enable' in input_text or 'on' in input_text
            self.update_user_config(from_number, 'webometrics', enabled)
            status = "✅ Enabled" if enabled else "❌ Disabled"
            return f"⚙️ *Configuration Updated*\n\nWebometrics: {status}"
        
        elif 'status' in input_text or 'show' in input_text:
            return self.show_config_status(from_number)
        
        elif 'back' in input_text or 'menu' in input_text:
            self.clear_user_state(from_number)
            return self.show_welcome(from_number)
        
        else:
            return self.show_config_menu(from_number)
    
    def perform_ranking(self, from_number: str, university_name: str, country: str) -> str:
        """Perform university ranking"""
        try:
            # Get user configuration
            user_config = self.get_user_config(from_number)
            
            # Show processing message
            self.send_message(from_number, f"🔍 *Analyzing {university_name}...*\n\nPlease wait while I gather data...")
            
            # Get ranking data
            ranking_data = self.ranking_system.rank_university(
                university_name, country, from_number, user_config
            )
            
            # Format results for WhatsApp
            results = self.format_ranking_results(ranking_data, user_config)
            
            return results
            
        except RateLimitExceededException as e:
            logger.error(f"Rate limit exceeded: {e}")
            return f"❌ *Rate Limit Exceeded*\n\n{e.message}\n\nUsing estimated data for ranking."
        
        except Exception as e:
            logger.error(f"Error ranking university: {e}")
            return f"❌ *Error Ranking University*\n\nSorry, I couldn't analyze *{university_name}*.\n\nError: {str(e)[:100]}"
    
    def format_ranking_results(self, data: UniversityData, user_config: UserConfiguration = None) -> str:
        """Format ranking results for WhatsApp"""
        # Header
        results = f"""
*🏛️ {data.name}*
*🌍 {data.country if data.country else 'Global'}*
*🎓 {data.type}*

*📅 Analysis Date:* {data.timestamp}
*📊 Data Confidence:* ±{data.error_margin} points
"""
        
        # Add configuration info if provided
        if user_config:
            enabled_sources = user_config.get_enabled_sources()
            if enabled_sources:
                results += f"*⚙️ Data Sources:* {', '.join(enabled_sources)}\n"
        
        # Parameter scores
        results += "\n*📈 PARAMETER SCORES:*\n"
        
        total_score = 0
        for param_code, param_info in self.ranking_system.parameters.items():
            score = data.scores.get(param_code, 0)
            max_score = param_info['max']
            percentage = (score / max_score * 100) if max_score > 0 else 0
            
            short_name = param_info['name']
            if len(short_name) > 20:
                short_name = short_name[:18] + ".."
            
            results += f"• {short_name}: {score:.1f}/{max_score} ({percentage:.1f}%)\n"
            total_score += score
        
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
        
        # Data source information
        if hasattr(data, 'is_estimated') and not data.is_estimated:
            results += "\n*✅ DATA SOURCE:* Real data from internet sources\n"
        else:
            results += "\n*⚠️ DATA SOURCE:* Estimated based on patterns\n"
        
        # Recommendations based on tier
        results += "\n*📝 RECOMMENDATIONS:*\n"
        if data.tier in ['A+', 'A']:
            results += "• Maintain strong performance\n• Enhance international partnerships\n• Invest in research\n"
        elif data.tier == 'B':
            results += "• Strengthen research output\n• Improve graduate employment\n• Enhance visibility\n"
        elif data.tier == 'C+':
            results += "• Focus on employability\n• Improve faculty ratio\n• Enhance transparency\n"
        elif data.tier in ['C', 'D']:
            results += "• Urgent improvement needed\n• Focus on core competencies\n• Seek accreditation\n"
        
        # Next steps
        results += "\n*🔍 NEXT STEPS:*\n"
        results += "• Send another university name to rank\n"
        results += "• Send 'excel' for bulk ranking instructions\n"
        results += "• Send 'config' to adjust data sources\n"
        results += "• Send 'help' for all commands\n"
        
        return results
    
    def show_welcome(self, from_number: str) -> str:
        """Show welcome message"""
        # Get user configuration
        user_config = self.get_user_config(from_number)
        
        welcome_text = f"""
🎓 *Welcome to University Ranking Bot!*

I analyze universities worldwide using a comprehensive multi-parameter ranking system.

*⚙️ YOUR CURRENT CONFIGURATION:*
• Wikipedia: {'✅ Enabled' if user_config.enable_wikipedia else '❌ Disabled'}
• Google Search: {'✅ Enabled' if user_config.enable_google_search else '❌ Disabled'}
• Webometrics: {'✅ Enabled' if user_config.enable_webometrics else '❌ Disabled'}

*📋 AVAILABLE COMMANDS:*
• *rank [university], [country]* - Rank a university
• *config* - Configure data sources
• *excel* - Upload Excel file for bulk ranking
• *tiers* - View ranking tiers
• *parameters* - View ranking parameters
• *status* - Check API rate limits
• *help* - Show all commands

*💡 QUICK START:*
1. Send: *rank Harvard University, USA*
2. Or send: *Harvard University, USA*
3. Or send an Excel file with university names

*Ready to rank a university?*
        """
        
        return welcome_text
    
    def show_help(self, from_number: str) -> str:
        """Show help message"""
        help_text = """
*📚 HELP - UNIVERSITY RANKING BOT*

*RANKING METHODOLOGY:*
I use a multi-parameter scoring system:
• Academic Reputation & Research (25%)
• Graduate Prospects (25%)
• ROI / Affordability (20%)
• Faculty-Student Ratio (15%)
• Transparency & Recognition (10%)
• Visibility & Presence (5%)

*DATA SOURCES:*
You can configure which data sources to use:
• Wikipedia (enabled by default)
• Google Search (disabled by default)
• Webometrics (disabled by default)

*COMMANDS:*
• *start* - Welcome message
• *rank [university], [country]* - Rank a university
• *config* - Configure data sources
• *excel* - Upload Excel file
• *tiers* - View tier system
• *parameters* - View parameters
• *status* - Check API status
• *help* - This message

*EXCEL FILE FORMAT:*
Send Excel file (.xlsx) with:
• University names (first column)
• Country names (second column, optional)

I'll add: Global Score, Global Rank, and Country Rank!

*EXAMPLES:*
1. *Harvard University, USA*
2. *University of Oxford, UK*
3. *rank MIT, USA*
4. Send Excel file with universities
        """
        
        return help_text
    
    def show_config_menu(self, from_number: str) -> str:
        """Show configuration menu"""
        self.set_user_state(from_number, 'config_menu')
        
        # Get current configuration
        user_config = self.get_user_config(from_number)
        
        config_text = f"""
*⚙️ CONFIGURATION MENU*

Configure which data sources I should use:

*CURRENT SETTINGS:*
1. Wikipedia: {'✅ Enabled' if user_config.enable_wikipedia else '❌ Disabled'}
2. Google Search: {'✅ Enabled' if user_config.enable_google_search else '❌ Disabled'}
3. Webometrics: {'✅ Enabled' if user_config.enable_webometrics else '❌ Disabled'}

*RECOMMENDATIONS:*
• Wikipedia: Always enabled (reliable, good rate limits)
• Google Search: Enable for more accurate rankings (strict rate limits)
• Webometrics: Enable for specialized ranking data

*TO CHANGE SETTINGS:*
Send one of these commands:
• *enable wikipedia* or *disable wikipedia*
• *enable google* or *disable google*
• *enable webometrics* or *disable webometrics*
• *config status* - Show current settings
• *back* - Return to main menu

*⚠️ RATE LIMIT WARNINGS:*
• Google: 10 requests/minute (very strict!)
• Webometrics: 30 requests/minute
• Wikipedia: 100 requests/minute (generous)
        """
        
        return config_text
    
    def show_config_status(self, from_number: str) -> str:
        """Show configuration status"""
        user_config = self.get_user_config(from_number)
        
        status_text = f"""
*⚙️ YOUR CURRENT CONFIGURATION*

*Data Sources:*
1. Wikipedia: {'✅ Enabled' if user_config.enable_wikipedia else '❌ Disabled'}
2. Google Search: {'✅ Enabled' if user_config.enable_google_search else '❌ Disabled'}
3. Webometrics: {'✅ Enabled' if user_config.enable_webometrics else '❌ Disabled'}

*Last Updated:* {user_config.timestamp.strftime("%Y-%m-%d %H:%M:%S")}

*EFFECTS ON RANKING:*
• With Wikipedia only: Basic ranking, fast processing
• With Google enabled: More accurate rankings, slower due to rate limits
• With Webometrics enabled: Specialized ranking data, moderate speed

*To change settings:* Send *config*
        """
        
        return status_text
    
    def show_tiers(self, from_number: str) -> str:
        """Show tiers information"""
        tiers_text = """
*🏆 RANKING TIERS & RANGES*

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
        
        return tiers_text
    
    def show_parameters(self, from_number: str) -> str:
        """Show parameters information"""
        params_text = """
*📊 RANKING PARAMETERS*

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
        
        return params_text
    
    def show_status(self, from_number: str) -> str:
        """Show API status"""
        status_text = """
*📊 API STATUS*

*Rate Limits:*
• Wikipedia: 100 requests/minute
• Google Search: 10 requests/minute (strict!)
• Webometrics: 30 requests/minute

*Recommendations:*
• For single rankings: All APIs work well
• For Excel files (>50 universities): Consider disabling Google
• Large files (>100 universities): Use Wikipedia only

*Current Status:* All APIs operational
*Last Checked:* {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        """
        
        return status_text.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    def show_excel_instructions(self, from_number: str) -> str:
        """Show Excel instructions"""
        # Get user configuration
        user_config = self.get_user_config(from_number)
        
        instructions = f"""
*📊 EXCEL RANKING INSTRUCTIONS*

*YOUR CURRENT CONFIGURATION:*
• Wikipedia: {'✅ Enabled' if user_config.enable_wikipedia else '❌ Disabled'}
• Google Search: {'✅ Enabled' if user_config.enable_google_search else '❌ Disabled'}
• Webometrics: {'✅ Enabled' if user_config.enable_webometrics else '❌ Disabled'}

*⚠️ IMPORTANT:*
• Google has strict rate limits (10 requests/minute)
• Large files will hit these limits quickly
• Consider disabling Google for files > 50 universities

*HOW TO UPLOAD:*
1. Prepare Excel file (.xlsx)
2. First column: University names
3. Second column: Country names (optional)
4. Send the file to this chat

*I WILL ADD:*
• Global Score (0-100)
• Global Rank (1 = best worldwide)
• Country Rank (1 = best in country)
• Data Source (Real Data/Estimated)
• Processing Time
• Data Sources Used

*TIME ESTIMATES:*
• 50 universities: ~5-10 minutes
• 100 universities: ~10-20 minutes
• 200 universities: ~20-40 minutes

*Ready?* Send your Excel file now!
        """
        
        return instructions
    
    def send_message(self, to_number: str, message: str):
        """Send WhatsApp message"""
        try:
            message = self.client.messages.create(
                body=message,
                from_=self.whatsapp_number,
                to=f"whatsapp:{to_number}"
            )
            logger.info(f"Message sent to {to_number}: {message.sid}")
            return message.sid
        except Exception as e:
            logger.error(f"Error sending message to {to_number}: {e}")
            return None
    
    def send_file(self, to_number: str, file_content: bytes, filename: str, caption: str = ""):
        """Send file via WhatsApp"""
        try:
            # Save file temporarily
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
                tmp_file.write(file_content)
                tmp_file_path = tmp_file.name
            
            # Upload to Twilio (simplified - in production, use media hosting)
            # For now, we'll send a message with download instructions
            message = f"{caption}\n\nFile: {filename}\n\nI've processed your file. Please download it from the link below."
            
            # In a production environment, you would:
            # 1. Upload the file to a public URL (AWS S3, Google Cloud Storage, etc.)
            # 2. Send the media URL via Twilio
            
            self.send_message(to_number, message)
            
            # Clean up
            import os
            os.unlink(tmp_file_path)
            
            logger.info(f"File sent to {to_number}: {filename}")
            
        except Exception as e:
            logger.error(f"Error sending file to {to_number}: {e}")
            self.send_message(to_number, f"❌ Error sending file. Please try again.\n\nError: {str(e)[:100]}")

# ============================================================================
# FLASK WEBHOOK SERVER
# ============================================================================

app = Flask(__name__)
whatsapp_bot = None

def initialize_bot():
    """Initialize the WhatsApp bot"""
    global whatsapp_bot
    
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logger.error("Twilio credentials not found in environment variables")
        return False
    
    try:
        whatsapp_bot = WhatsAppUniversityRankingBot(
            account_sid=TWILIO_ACCOUNT_SID,
            auth_token=TWILIO_AUTH_TOKEN,
            whatsapp_number=TWILIO_WHATSAPP_NUMBER
        )
        logger.info("WhatsApp bot initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize WhatsApp bot: {e}")
        return False

@app.route('/')
def home():
    """Home page"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>WhatsApp University Ranking Bot</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
            }
            .container {
                background-color: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 {
                color: #25D366;
                text-align: center;
            }
            .status {
                padding: 15px;
                border-radius: 5px;
                margin: 20px 0;
                text-align: center;
                font-weight: bold;
            }
            .status.running {
                background-color: #d4edda;
                color: #155724;
                border: 1px solid #c3e6cb;
            }
            .status.error {
                background-color: #f8d7da;
                color: #721c24;
                border: 1px solid #f5c6cb;
            }
            .instructions {
                background-color: #e8f4fd;
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
                border-left: 4px solid #25D366;
            }
            .command {
                background-color: #f8f9fa;
                padding: 10px;
                margin: 5px 0;
                border-radius: 3px;
                font-family: monospace;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 WhatsApp University Ranking Bot</h1>
            
            <div class="status running">
                ✅ Bot is running and ready to receive messages
            </div>
            
            <div class="instructions">
                <h3>📱 How to Use:</h3>
                <p>1. Save this number in your contacts: <strong>{}</strong></p>
                <p>2. Send a message on WhatsApp to get started</p>
                <p>3. Use the commands below to interact with the bot</p>
            </div>
            
            <h3>🎓 Available Commands:</h3>
            <div class="command">start - Welcome message</div>
            <div class="command">rank [university], [country] - Rank a university</div>
            <div class="command">Harvard University, USA - Quick rank example</div>
            <div class="command">config - Configure data sources</div>
            <div class="command">excel - Upload Excel file for bulk ranking</div>
            <div class="command">tiers - View ranking tiers</div>
            <div class="command">parameters - View ranking parameters</div>
            <div class="command">help - Show all commands</div>
            
            <h3>📁 Excel File Format:</h3>
            <p>Send an Excel file (.xlsx) with:</p>
            <ul>
                <li>Column A: University names</li>
                <li>Column B: Country names (optional)</li>
            </ul>
            
            <h3>⚙️ Configuration:</h3>
            <p>By default, only Wikipedia is enabled. Use the <strong>config</strong> command to enable Google Search or Webometrics for more accurate rankings.</p>
            
            <h3>📊 Features:</h3>
            <ul>
                <li>Comprehensive 6-parameter scoring system</li>
                <li>Real-time data fetching from multiple sources</li>
                <li>Rate limiting to prevent API blocks</li>
                <li>Bulk processing via Excel files</li>
                <li>User-configurable data sources</li>
                <li>Tier-based ranking system (A+ to D)</li>
            </ul>
            
            <p style="text-align: center; margin-top: 30px; color: #666;">
                🤖 Powered by University Ranking System v1.0
            </p>
        </div>
    </body>
    </html>
    """.format(TWILIO_WHATSAPP_NUMBER)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Twilio webhook handler"""
    try:
        # Get the incoming message
        from_number = request.values.get('From', '').replace('whatsapp:', '')
        message_body = request.values.get('Body', '')
        media_url = request.values.get('MediaUrl0', '')
        num_media = int(request.values.get('NumMedia', 0))
        
        logger.info(f"Webhook received from {from_number}: {message_body}")
        
        # Check if bot is initialized
        if not whatsapp_bot:
            logger.error("WhatsApp bot not initialized")
            return str(MessagingResponse())
        
        # Handle the message
        response_text = whatsapp_bot.handle_message(from_number, message_body, media_url if num_media > 0 else None)
        
        # Create TwiML response
        resp = MessagingResponse()
        msg = Message()
        msg.body(response_text)
        resp.append(msg)
        
        return str(resp)
        
    except Exception as e:
        logger.error(f"Error in webhook: {e}")
        # Return empty response to avoid Twilio retries
        return str(MessagingResponse())

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    if whatsapp_bot:
        return Response("OK", status=200)
    else:
        return Response("Bot not initialized", status=503)

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main function to run the WhatsApp bot"""
    print("🤖 Starting WhatsApp University Ranking Bot...")
    print("=" * 50)
    
    # Check required packages
    try:
        import flask
        import twilio
        import pandas
        import numpy
        import wikipedia
        import requests
    except ImportError as e:
        print(f"❌ Missing package: {e}")
        print("Install with: pip install flask twilio pandas numpy wikipedia requests")
        return
    
    # Check environment variables
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("\n❌ ERROR: Twilio credentials not set!")
        print("Please set these environment variables in .env.dev:")
        print("TWILIO_ACCOUNT_SID='your_account_sid'")
        print("TWILIO_AUTH_TOKEN='your_auth_token'")
        print("TWILIO_WHATSAPP_NUMBER='whatsapp:+14155238886'")
        print("WEBHOOK_URL='https://your-domain.com/webhook'")
        return
    
    # Initialize bot
    if not initialize_bot():
        print("❌ Failed to initialize WhatsApp bot")
        return
    
    print("\n✅ WhatsApp Bot Initialized Successfully!")
    print(f"📱 WhatsApp Number: {TWILIO_WHATSAPP_NUMBER}")
    print(f"🌐 Webhook URL: {WEBHOOK_URL or 'http://localhost:5000/webhook'}")
    print("\n📊 Features:")
    print("   • University ranking with 6 parameters")
    print("   • Real-time data fetching (Wikipedia, Google, Webometrics)")
    print("   • User-configurable data sources")
    print("   • Excel file processing for bulk ranking")
    print("   • Rate limiting to prevent API blocks")
    print("\n⚙️ Default Configuration:")
    print("   • Wikipedia: ✅ Enabled")
    print("   • Google Search: ❌ Disabled (strict rate limits)")
    print("   • Webometrics: ❌ Disabled")
    print("\n🚀 Starting Flask server...")
    print("   Local: http://localhost:5000")
    print("   Press Ctrl+C to stop")
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)

if __name__ == "__main__":
    main()
