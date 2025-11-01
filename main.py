from fastapi import FastAPI, Query
import requests, os

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query the Federal Election Commission (FEC) API for contributions.",
    version="1.0.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"

@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor name"),
    state: str | None = Query(None, description="Two-letter state abbreviation, e.g. 'MA'"),
    limit: int = Query(50, ge=1, le=100, description="Max number of records (default 50, max 100)")
):
    # Build query params
    params = {
        "api_key": FEC_API_KEY,
        "contributor_name": contributor_name,
        "per_page": limit,
        "sort": "contribution_receipt_date",
    }
    if state:
        params["contributor_state"] = state.upper()

    # Call the FEC API
    res = requests.get(BASE_URL, params=params)
    res.raise_for_status()
    data = res.json()

    # Count and truncate if necessary
    total = data.get("pagination", {}).get("count", 0)
    results = data.get("results", [])

    if len(results) > limit:
        results = results[:limit]

    return {
        "summary": f"Found {total} results. Returning the first {len(results)} for review.",
        "results": results,
    }
