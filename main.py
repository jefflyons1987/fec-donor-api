from fastapi import FastAPI, Query, HTTPException
import requests, os, time
from collections import defaultdict
from datetime import datetime

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions with pagination, fuzzy name matching, state filtering, and date range support.",
    version="2.1.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

# -----------------------------
# CONFIG
# -----------------------------
FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_PAGES = 20  # safety cap
PAGE_SIZE = 100

VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC"
}

# -----------------------------
# Helper: Fetch all pages with pagination
# -----------------------------
def fetch_all_pages(name, state=None, start_date=None, end_date=None, per_page=PAGE_SIZE):
    all_results = []
    last_index = None
    last_date = None
    pages_fetched = 0

    while pages_fetched < MAX_PAGES:
        params = {
            "api_key": FEC_API_KEY,
            "contributor_name": name,
            "per_page": per_page,
            "sort": "contribution_receipt_date",
        }
        if state:
