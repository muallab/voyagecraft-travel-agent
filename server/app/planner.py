from typing import List, Dict, Any
from math import radians, sin, cos, asin, sqrt

def _haversine_km(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Great-circle distance in km between two lat/lon points."""
    lat1, lon1 = a.get("lat"), a.get("lon")
    lat2, lon2 = b.get("lat"), b.get("lon")
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    h = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon/2)**2
    return 2 * R * asin(sqrt(h))

def score_day(items: List[Dict[str, Any]]) -> float:
    """
    Score combines:
      - Less walking distance (higher score)
      - More category diversity (slight boost)
    """
    if not items:
        return 0.0
    walk = sum(_haversine_km(items[i], items[i+1]) for i in range(len(items)-1))
    cats = len({x.get("category", "other") for x in items})
    score = max(0.0, 10.0 - walk) + 0.5 * cats
    return round(score, 2)

def enrich_with_scores(plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds `score` per day and ensures `totals` has cost bands.
    Safe to call on any LLM output that follows our schema.
    """
    days = plan.get("days", [])
    for d in days:
        d["score"] = score_day(d.get("items", []))

    totals = plan.get("totals") or {}
    num_days = len(days)
    totals.setdefault("cost_low", 50 * num_days)
    totals.setdefault("cost_high", 120 * num_days)
    plan["totals"] = totals
    return plan
