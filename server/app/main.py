# server/app/main.py

import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .tools import geocode_city, wiki_geosearch, date_range
from .llm import plan_with_llm
from .planner import enrich_with_scores

load_dotenv()
app = FastAPI(title="VoyageCraft Travel Agent", version="0.1.0")


# ---------- Schemas ----------
class PlanRequest(BaseModel):
    destination: str = Field(..., examples=["Istanbul"])
    start_date: str = Field(..., examples=["2025-08-20"])
    end_date: str = Field(..., examples=["2025-08-22"])
    budget: Optional[str] = Field(None, examples=["low", "medium", "high"])
    interests: Optional[List[str]] = Field(default_factory=list)

class PlanResponse(BaseModel):
    days: list
    totals: dict


# ---------- Health ----------
@app.get("/health")
async def health():
    return {"ok": True}


# ---------- Fallback (used if LLM is rate-limited or errors) ----------
def _fallback_plan(dates: List[str], pois: List[dict]) -> dict:
    items_per_day = 4
    out_days = []
    idx = 0
    for d in dates:
        day_items = []
        for _ in range(items_per_day):
            if idx >= len(pois):
                break
            p = {**pois[idx], "start": "09:00", "end": "11:00", "blurb": "(fallback) Popular spot"}
            day_items.append(p)
            idx += 1
        out_days.append({"date": d, "items": day_items})
    return {"days": out_days, "totals": {}}


# ---------- Main endpoint ----------
@app.post("/plan", response_model=PlanResponse)
async def plan_trip(req: PlanRequest):
    """
    Flow:
      1) Geocode destination (OSM Nominatim)
      2) Fetch nearby POIs (Wikipedia Geosearch)
      3) Ask LLM to pick/sequence + write blurbs (strict JSON)  -> fallback if it fails
      4) Enrich with scores and totals
    """
    email = os.getenv("USER_AGENT_EMAIL", "dev@example.com")

    # 1) Geocode
    loc = await geocode_city(req.destination, email)
    if not loc:
        raise HTTPException(status_code=404, detail="Destination not found")

    # 2) Nearby POIs
    pois = await wiki_geosearch(lat=loc["lat"], lon=loc["lon"], email=email)
    if not pois:
        raise HTTPException(status_code=404, detail="No points of interest found")

    # Dates
    dates = date_range(req.start_date, req.end_date)
    if not dates:
        raise HTTPException(status_code=400, detail="Invalid date range")

    # 3) Plan with LLM (fallback on any error, e.g., 429 rate limit)
    try:
        llm_plan = await plan_with_llm(req.destination, dates, req.interests or [], pois)
    except Exception:
        llm_plan = _fallback_plan(dates, pois[:16])

    # 4) Enrich and return
    final_plan = enrich_with_scores(llm_plan)
    return PlanResponse(**final_plan)