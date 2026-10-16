"""
Combined Tire Scraper — GitHub Actions Edition
===============================================

Scrapes:
    1. Giga Tires
    2. Priority Tire

Final output:
    tire_prices_YYYY-MM-DD.csv

Checkpoint outputs:
    giga_checkpoint.csv
    priority_checkpoint.csv

Designed for GitHub Actions.
"""

import asyncio
import datetime
import json
import re
import sys
from urllib.parse import urljoin

import nest_asyncio
import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeout,
)
from playwright_stealth import Stealth


nest_asyncio.apply()


# =============================================================================
# COMMON
# =============================================================================

COLUMN_ORDER = [
    "run_date",
    "source",
    "model",
    "size",
    "original_price",
    "easy_score",
    "reviews",
