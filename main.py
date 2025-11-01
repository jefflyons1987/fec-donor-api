from fastapi import FastAPI, Query, HTTPException
import requests, os, time
from collections import defaultdict

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions with pagination, fuzzy name matching, and state filtering.",
    version="2.0.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

# -----------------------------
# CONFIG
# -----------------------------
FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_PAGES = 20  # cap to prevent infinite loops (each page = 100 results)
PAGE_SIZE = 100

VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC"
}

# -----------------------------
# Helper: Fetch all pages from FEC API
# -----------------------------
def fetch_all_pages(name, state=None, per_page=PAGE_SIZE):
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
            params["contributor_state"] = state
        if last_index and last_date:
            params["last_index"] = last_index
            params["last_contribution_receipt_date"] = last_date

        res = requests.get(BASE_URL, params=params)
        res.raise_for_status()
        data = res.json()
        results = data.get("results", [])
        if not results:
            break

        all_results.extend(results)
        pages_fetched += 1

        # Get pagination cursor
        pagination = data.get("pagination", {}).get("last_indexes", {})
        last_index = pagination.get("last_index")
        last_date = pagination.get("last_contribution_receipt_date")

        # Stop if no more pages
        if not last_index or not last_date:
            break

        time.sleep(0.2)  # be kind to the API

    return all_results


# -----------------------------
# Main Route
# -----------------------------
@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor full name"),
    state: str | None = Query(None, description="Two-letter state abbreviation (e.g., 'MA')"),
    limit: int = Query(50, ge=1, le=100, description="Max number of records per variant before deduplication")
):
    # --- Normalize and validate state ---
    if state:
        state = state.strip().upper()
        full_to_code = {
            "MASSACHUSETTS": "MA", "NEW YORK": "NY", "CALIFORNIA": "CA", "TEXAS": "TX",
            "FLORIDA": "FL", "DISTRICT OF COLUMBIA": "DC"
        }
        if state not in VALID_STATES:
            if state.upper() in full_to_code:
                state = full_to_code[state.upper()]
            else:
                raise HTTPException(status_code=400, detail=f"Invalid state '{state}'. Must be a 2-letter USPS code.")

    # --- Build fuzzy name variants ---
    name_variants = [
        contributor_name,
        contributor_name.upper(),
        contributor_name.lower(),
        contributor_name.title()
    ]

    if " " in contributor_name:
        parts = contributor_name.split()
        if len(parts) == 2:
            name_variants.extend([
                f"{parts[1]}, {parts[0]}",
                f"{parts[1].upper()}, {parts[0].upper()}",
                f"{parts[1].capitalize()}, {parts[0].capitalize()}"
            ])

    # --- Fetch and merge all pages for each variant ---
    seen = set()
    combined = []
    total_count = 0

    for variant in name_variants:
        results = fetch_all_pages(variant, state=state, per_page=PAGE_SIZE)
        total_count += len(results)
        for r in results:
            uid = (r.get("committee_id"), r.get("transaction_id"))
            if uid not in seen:
                seen.add(uid)
                if not state or r.get("contributor_state", "").upper() == state:
                    combined.append(r)

    # --- Summarize results ---
    summary = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in combined:
        committee = r.get(
