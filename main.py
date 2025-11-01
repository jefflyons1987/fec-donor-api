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
    limit: int = 20
):
    params = {
        "api_key": FEC_API_KEY,
        "contributor_name": contributor_name,
        "per_page": limit,
        "sort": "contribution_receipt_date"
    }
    res = requests.get(BASE_URL, params=params)
    res.raise_for_status()
    return res.json()

