"""
pytest configuration for MacroPulse Sentinel tests.
"""
import os
import sys

# Ensure src/ is on the Python path when running tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Set test environment defaults so Settings doesn't require real API keys
os.environ.setdefault("GROQ_API_KEY", "test_groq_key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "1234:ABCDEF")
os.environ.setdefault("TELEGRAM_CHAT_ID", "-1001234567890")
os.environ.setdefault("PRICE_FEED_PROVIDER", "mock")
os.environ.setdefault("TWITTER_MODE", "disabled")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("DEDUP_DB_PATH", "/tmp/macropulse_test_dedup.db")
