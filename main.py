from fastapi import FastAPI, Query, HTTPException
import requests, os, time
from collections import defaultdict
from datetime import datetime

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions with pagination, fuzzy name matching, state and date filters, and party breakdowns.",
    version="2.2.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

# -----------------------------
# CONFIG
# -----------------------------
FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_PAGES = 20  # limit total pages fetched
PAGE_SIZE = 100

VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC"
}

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def fetch_all_pages(name, state=None, start_date=None, end_date=None, per_page=PAGE_SIZE):
    """Fetch all paginated results for a given contributor."""
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
        if start_date:
            params["min_date"] = start_date
        if end_date:
            params["max_date"] = end_date
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

        pagination = data.get("pagination", {}).get("last_indexes", {})
        last_index = pagination.get("last_index")
        last_date = pagination.get("last_contribution_receipt_date")

        if not last_index or not last_date:
            break

        time.sleep(0.2)  # polite delay to respect API rate limits

    return all_results


def normalize_state(state: str | None) -> str | None:
    """Normalize full or lowercase state names to two-letter USPS codes."""
    if not state:
        return None
    state = state.strip().upper()
    full_to_code = {
        "MASSACHUSETTS": "MA", "NEW YORK": "NY", "CALIFORNIA": "CA", "TEXAS": "TX",
        "FLORIDA": "FL", "DISTRICT OF COLUMBIA": "DC"
    }
    if state not in VALID_STATES:
        if state in full_to_code:
            return full_to_code[state]
        raise HTTPException(status_code=400, detail=f"Invalid state '{state}'. Must be a 2-letter USPS code.")
    return state


def validate_date(date_str):
    """Ensure date format is YYYY-MM-DD."""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date format '{date_str}'. Use YYYY-MM-DD.")


# -----------------------------
# MAIN ENDPOINT
# -----------------------------
@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor full name"),
    state: str | None = Query(None, description="Two-letter state abbreviation (e.g., 'MA')"),
    start_date: str | None = Query(None, description="Filter contributions after this date (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="Filter contributions before this date (YYYY-MM-DD)"),
):
    # --- Validate inputs ---
    state = normalize_state(state)
    if start_date:
        validate_date(start_date)
    if end_date:
        validate_date(end_date)

    # --- Fuzzy name variants ---
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

    # --- Fetch data for each variant ---
    seen = set()
    combined = []
    total_count = 0

    for variant in name_variants:
        results = fetch_all_pages(
            name=variant,
            state=state,
            start_date=start_date,
            end_date=end_date
        )
        total_count += len(results)
        for r in results:
            uid = (r.get("committee_id"), r.get("transaction_id"))
            if uid not in seen:
                seen.add(uid)
                if not state or r.get("contributor_state", "").upper() == state:
                    combined.append(r)

    # --- Summaries ---
    committee_summary = defaultdict(lambda: {"count": 0, "total": 0.0, "party": None})
    party_summary = defaultdict(lambda: {"count": 0, "total": 0.0})

    for r in combined:
        committee = r.get("committee", {}).get("name", "Unknown Committee")
        amount = r.get("contribution_receipt_amount", 0.0)
        party = r.get("committee", {}).get("party_full", "Unknown")
        committee_summary[committee]["count"] += 1
        committee_summary[committee]["total"] += amount
        committee_summary[committee]["party"] = party

        party_summary[party]["count"] += 1
        party_summary[party]["total"] += amount

    committee_breakdown = [
        {
            "committee": c,
            "party": v["party"],
            "transactions": v["count"],
            "total_amount": round(v["total"], 2)
        }
        for c, v in committee_summary.items()
    ]
    committee_breakdown.sort(key=lambda x: x["total_amount"], reverse=True)

    party_breakdown = [
        {
            "party": p,
            "transactions": v["count"],
            "total_amount": round(v["total"], 2)
        }
        for p, v in party_summary.items() if v["total"] > 0
    ]
    party_breakdown.sort(key=lambda x: x["total_amount"], reverse=True)

    total_amount = round(sum(v["total_amount"] for v in party_breakdown), 2)

    # --- Response ---
    return {
        "query": {
            "contributor_name": contributor_name,
            "state": state,
            "start_date": start_date,
            "end_date": end_date,
            "variants_tested": name_variants,
        },
        "summary": {
            "total_records_fetched": len(combined),
            "total_estimated_records": total_count,
            "total_amount_donated": total_amount,
            "party_breakdown": party_breakdown,
            "committee_breakdown": committee_breakdown,
        },
        "sample_records": [
            {
                "date": r.get("contribution_receipt_date"),
                "amount": r.get("contribution_receipt_amount"),
                "committee": r.get("committee", {}).get("name"),
                "party": r.get("committee", {}).get("party_full"),
                "employer": r.get("contributor_employer"),
                "occupation": r.get("contributor_occupation"),
                "city": r.get("contributor_city"),
                "state": r.get("contributor_state"),
                "pdf_url": r.get("pdf_url")
            }
            for r in combined[:10]
        ],
    }
