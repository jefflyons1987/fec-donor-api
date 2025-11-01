from fastapi import FastAPI, Query, HTTPException
import requests, os, time
from collections import defaultdict
from datetime import datetime
from typing import Optional
from difflib import SequenceMatcher

app = FastAPI(
    title="FEC Donor Lookup",
    description="Query and summarize FEC contributions with fuzzy name matching, identity continuity tracking, and confidence scoring.",
    version="3.5.0",
    servers=[{"url": "https://fec-donor-api.onrender.com"}]
)

# -----------------------------
# CONFIG
# -----------------------------
FEC_API_KEY = os.getenv("FEC_API_KEY")
BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_PAGES = 20
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
def similarity(a, b):
    """Return fuzzy similarity ratio between two strings."""
    if not a or not b:
        return 0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def match_confidence(record, target_city=None, target_employer=None, target_occupation=None):
    """Compute a confidence score (0–1) that this record matches the intended donor."""
    weights = {"city": 0.4, "employer": 0.3, "occupation": 0.3}
    score = 0.0
    count = 0

    if target_city:
        score += weights["city"] * similarity(record.get("contributor_city", ""), target_city)
        count += weights["city"]
    if target_employer:
        score += weights["employer"] * similarity(record.get("contributor_employer", ""), target_employer)
        count += weights["employer"]
    if target_occupation:
        score += weights["occupation"] * similarity(record.get("contributor_occupation", ""), target_occupation)
        count += weights["occupation"]

    return score / count if count > 0 else 0


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

        time.sleep(0.2)

    return all_results


def normalize_state(state: Optional[str]) -> Optional[str]:
    """Normalize full or lowercase state names to two-letter USPS codes."""
    if not state:
        return None
    state = state.strip().upper()
    full_to_code = {
        "MASSACHUSETTS": "MA", "NEW YORK": "NY", "CALIFORNIA": "CA",
        "TEXAS": "TX", "FLORIDA": "FL", "DISTRICT OF COLUMBIA": "DC"
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
# CLUSTERING / CONTINUITY LOGIC
# -----------------------------
def cluster_records(records, target_city=None, target_employer=None, target_occupation=None):
    """Cluster records into identity-consistent timelines using city/employer/occupation continuity."""
    if not records:
        return [], []

    # Sort by date (oldest first)
    def safe_date(r):
        try:
            return datetime.strptime(r.get("contribution_receipt_date", ""), "%Y-%m-%d")
        except Exception:
            return datetime.min

    records = sorted(records, key=safe_date)
    clusters = []
    current_cluster = [records[0]]
    last = records[0]

    for r in records[1:]:
        city_sim = similarity(r.get("contributor_city", ""), last.get("contributor_city", ""))
        employer_sim = similarity(r.get("contributor_employer", ""), last.get("contributor_employer", ""))
        occupation_sim = similarity(r.get("contributor_occupation", ""), last.get("contributor_occupation", ""))

        # continuity: city OR employer/occupation are similar enough
        if city_sim > 0.6 or (employer_sim > 0.7 or occupation_sim > 0.7):
            current_cluster.append(r)
        else:
            clusters.append(current_cluster)
            current_cluster = [r]
        last = r

    clusters.append(current_cluster)

    # Compute confidence for each record within each cluster relative to target info
    for cluster in clusters:
        for r in cluster:
            confidence = match_confidence(
                r,
                target_city=target_city,
                target_employer=target_employer,
                target_occupation=target_occupation
            )
            r["match_confidence"] = round(confidence, 3)

    # Pick cluster with highest average confidence
    scored_clusters = []
    for cluster in clusters:
        avg_conf = sum(r.get("match_confidence", 0) for r in cluster) / len(cluster)
        scored_clusters.append((avg_conf, cluster))
    scored_clusters.sort(reverse=True, key=lambda x: x[0])

    # Highest-confidence cluster = likely correct identity
    if scored_clusters:
        best_cluster = scored_clusters[0][1]
        excluded = [r for _, c in scored_clusters[1:] for r in c]
        for r in best_cluster:
            r["likely_same_person"] = True
        for r in excluded:
            r["likely_same_person"] = False
        return best_cluster, excluded
    else:
        return [], records


# -----------------------------
# MAIN ENDPOINT
# -----------------------------
@app.get("/contributions")
def get_contributions(
    contributor_name: str = Query(..., description="Contributor full name"),
    state: Optional[str] = Query(None, description="Two-letter state abbreviation (e.g., 'MA')"),
    start_date: Optional[str] = Query(None, description="Filter contributions after this date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Filter contributions before this date (YYYY-MM-DD)"),
    target_city: Optional[str] = Query(None, description="Expected city for this donor"),
    target_employer: Optional[str] = Query(None, description="Expected employer or organization"),
    target_occupation: Optional[str] = Query(None, description="Expected occupation title"),
    strict: bool = Query(False, description="If true, only return likely_same_person records")
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

    # --- Fetch data ---
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

    # --- Cluster and filter ---
    matched, excluded = cluster_records(
        combined,
        target_city=target_city,
        target_employer=target_employer,
        target_occupation=target_occupation
    )

    # Apply strict mode
    relevant_records = matched if strict else combined

    # --- Summaries ---
    committee_summary = defaultdict(lambda: {"count": 0, "total": 0.0, "party": None})
    party_summary = defaultdict(lambda: {"count": 0, "total": 0.0})

    for r in relevant_records:
        committee = r.get("committee", {}).get("name", "Unknown Committee")
        amount = r.get("contribution_receipt_amount", 0.0)
        party = r.get("committee", {}).get("party_full", "Unknown")
        committee_summary[committee]["count"] += 1
        committee_summary[committee]["total"] += amount
        committee_summary[committee]["party"] = party
        party_summary[party]["count"] += 1
        party_summary[party]["total"] += amount

    committee_breakdown = [
        {"committee": c, "party": v["party"], "transactions": v["count"], "total_amount": round(v["total"], 2)}
        for c, v in committee_summary.items()
    ]
    committee_breakdown.sort(key=lambda x: x["total_amount"], reverse=True)

    party_breakdown = [
        {"party": p, "transactions": v["count"], "total_amount": round(v["total"], 2)}
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
            "target_city": target_city,
            "target_employer": target_employer,
            "target_occupation": target_occupation,
            "strict_mode": strict,
            "variants_tested": name_variants,
        },
        "summary": {
            "total_records_fetched": len(combined),
            "likely_same_person_records": len(matched),
            "excluded_records_count": len(excluded),
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
                "match_confidence": r.get("match_confidence"),
                "likely_same_person": r.get("likely_same_person"),
                "pdf_url": r.get("pdf_url")
            }
            for r in matched[:10]
        ],
        "excluded_examples": [
            {
                "date": r.get("contribution_receipt_date"),
                "city": r.get("contributor_city"),
                "employer": r.get("contributor_employer"),
                "occupation": r.get("contributor_occupation"),
                "match_confidence": r.get("match_confidence")
            }
            for r in excluded[:5]
        ]
    }
