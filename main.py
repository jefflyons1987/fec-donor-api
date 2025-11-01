from fastapi import FastAPI, Query, HTTPException
import requests, os
from collections import defaultdict

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions with fuzzy name matching and state filtering.",
    version="1.3.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

# -----------------------------
# CONFIG
# -----------------------------
FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"

# USPS-valid two-letter state codes
VALID_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS",
    "KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
    "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV",
    "WI","WY","DC"
}

# -----------------------------
# Helper: Fetch results from FEC
# -----------------------------
def fetch_fec_results(name, state, limit):
    params = {
        "api_key": FEC_API_KEY,
        "contributor_name": name,
        "per_page": limit,
        "sort": "contribution_receipt_date",
    }
    if state:
        params["contributor_state"] = state

    res = requests.get(BASE_URL, params=params)
    res.raise_for_status()
    data = res.json()
    return data.get("results", []), data.get("pagination", {}).get("count", 0)

# -----------------------------
# Main route
# -----------------------------
@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor full name"),
    state: str | None = Query(None, description="Two-letter state abbreviation (e.g., 'MA')"),
    limit: int = Query(50, ge=1, le=100, description="Max number of records per variant (default 50)")
):
    # --- Validate & normalize state ---
    if state:
        state = state.strip().upper()
        # Handle full state names like "Massachusetts"
        full_to_code = {
            "MASSACHUSETTS": "MA", "NEW YORK": "NY", "CALIFORNIA": "CA", "TEXAS": "TX",
            "FLORIDA": "FL", "DISTRICT OF COLUMBIA": "DC"
        }
        if state not in VALID_STATES:
            if state.upper() in full_to_code:
                state = full_to_code[state.upper()]
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid state '{state}'. Must be a 2-letter USPS code."
                )

    # --- Build name variants for fuzzy matching ---
    name_variants = [
        contributor_name,
        contributor_name.upper(),
        contributor_name.lower(),
        contributor_name.title()
    ]

    # Add swapped and comma-separated forms ("Jeff Lyons" -> "LYONS, JEFF")
    if " " in contributor_name:
        parts = contributor_name.split()
        if len(parts) == 2:
            name_variants.append(f"{parts[1]}, {parts[0]}")
            name_variants.append(f"{parts[1].upper()}, {parts[0].upper()}")
            name_variants.append(f"{parts[1].capitalize()}, {parts[0].capitalize()}")

    # --- Fetch and combine results ---
    seen = set()
    combined = []
    total_count = 0

    for n in name_variants:
        results, count = fetch_fec_results(n, state, limit)
        total_count += count
        for r in results:
            uid = (r.get("committee_id"), r.get("transaction_id"))
            if uid not in seen:
                seen.add(uid)
                # Extra safety: filter by state client-side
                if not state or r.get("contributor_state", "").upper() == state:
                    combined.append(r)
        if len(combined) >= limit:
            break

    # --- Summarize results ---
    summary = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in combined:
        committee = r.get("committee", {}).get("name", "Unknown Committee")
        summary[committee]["count"] += 1
        summary[committee]["total"] += r.get("contribution_receipt_amount", 0.0)

    committee_summary = [
        {"committee": c, "transactions": v["count"], "total_amount": round(v["total"], 2)}
        for c, v in summary.items()
    ]
    committee_summary.sort(key=lambda x: x["total_amount"], reverse=True)
    total_amount = round(sum(v["total_amount"] for v in committee_summary), 2)

    # --- Construct response ---
    return {
        "query": {
            "contributor_name": contributor_name,
            "state": state,
            "variants_tested": name_variants,
        },
        "summary": {
            "total_estimated_records": total_count,
            "records_returned": len(combined),
            "total_amount_donated": total_amount,
            "committee_breakdown": committee_summary,
        },
        "sample_records": [
            {
                "date": r.get("contribution_receipt_date"),
                "amount": r.get("contribution_receipt_amount"),
                "committee": r.get("committee", {}).get("name"),
                "employer": r.get("contributor_employer"),
                "occupation": r.get("contributor_occupation"),
                "city": r.get("contributor_city"),
                "state": r.get("contributor_state"),
                "pdf_url": r.get("pdf_url")
            }
            for r in combined[:10]
        ],
    }
