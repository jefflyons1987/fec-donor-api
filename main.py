from fastapi import FastAPI, Query
import requests, os
from collections import defaultdict

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions.",
    version="1.1.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"

@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor name"),
    state: str | None = Query(None, description="Two-letter state abbreviation"),
    limit: int = Query(50, ge=1, le=100, description="Max number of records to return")
):
    params = {
        "api_key": FEC_API_KEY,
        "contributor_name": contributor_name,
        "per_page": limit,
        "sort": "contribution_receipt_date",
    }
    if state:
        params["contributor_state"] = state.upper()

    res = requests.get(BASE_URL, params=params)
    res.raise_for_status()
    data = res.json()
    results = data.get("results", [])
    total = data.get("pagination", {}).get("count", 0)

    # Summarize by committee
    summary = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in results:
        committee = r.get("committee", {}).get("name", "Unknown Committee")
        amount = r.get("contribution_receipt_amount", 0.0)
        summary[committee]["count"] += 1
        summary[committee]["total"] += amount

    committee_summary = [
        {"committee": c, "transactions": v["count"], "total_amount": round(v["total"], 2)}
        for c, v in summary.items()
    ]
    committee_summary.sort(key=lambda x: x["total_amount"], reverse=True)

    total_amount = round(sum(v["total_amount"] for v in committee_summary), 2)

    return {
        "query": {
            "contributor_name": contributor_name,
            "state": state,
            "limit": limit,
        },
        "summary": {
            "total_records_found": total,
            "records_returned": len(results),
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
                "pdf_url": r.get("pdf_url")
            } for r in results[:10]
        ],
    }
