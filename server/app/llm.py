import os
import json
import httpx
import asyncio
from typing import List, Dict, Any

OPENAI_API = "https://api.openai.com/v1/chat/completions"

def _headers() -> Dict[str, str]:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")
    hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    org = os.getenv("OPENAI_ORG_ID")  # optional
    if org:
        hdr["OpenAI-Organization"] = org
    return hdr

def _model() -> str:
    # Default to GPT-5 Mini; override via .env -> OPENAI_MODEL=gpt-5 or gpt-5-mini
    return os.getenv("OPENAI_MODEL", "gpt-5-mini")

def _retry_after_seconds(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("Retry-After", "0"))
    except Exception:
        return 0

async def _post_with_retries(json_payload: Dict[str, Any], max_retries: int = 5) -> httpx.Response:
    """
    Retries on 429/5xx with exponential backoff (3s, 6s, 12s, 24s, 48s),
    honoring Retry-After if present.
    """
    delay = 3
    async with httpx.AsyncClient(timeout=60) as cx:
        for attempt in range(1, max_retries + 1):
            resp = await cx.post(OPENAI_API, headers=_headers(), json=json_payload)
            if resp.status_code < 400:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = max(_retry_after_seconds(resp), delay)
                if attempt == max_retries:
                    resp.raise_for_status()
                await asyncio.sleep(wait)
                delay *= 2
                continue
            resp.raise_for_status()
    raise RuntimeError("Unexpected failure in _post_with_retries")

def _itinerary_schema() -> Dict[str, Any]:
    """Strict JSON schema the model must follow."""
    return {
        "name": "Itinerary",
        "schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string"},
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "lat": {"type": "number"},
                                        "lon": {"type": "number"},
                                        "start": {"type": "string"},
                                        "end": {"type": "string"},
                                        "blurb": {"type": "string"},
                                        "category": {"type": "string"}
                                    },
                                    "required": ["name","lat","lon","start","end","blurb","category"],
                                    "additionalProperties": False
                                }
                            }
                        },
                        "required": ["date","items"],
                        "additionalProperties": False
                    }
                },
                "totals": {
                    "type": "object",
                    "properties": {
                        "cost_low": {"type": "number"},
                        "cost_high": {"type": "number"}
                    },
                    "required": ["cost_low","cost_high"],
                    "additionalProperties": True
                }
            },
            "required": ["days","totals"],
            "additionalProperties": False
        },
        "strict": True
    }

async def plan_with_llm(
    city: str,
    dates: List[str],
    interests: List[str],
    pois: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Ask the LLM to:
      1) choose ~4 POIs/day that match interests,
      2) sequence into slots (09–11, 11–13, 14–16, 16–18),
      3) add short blurbs,
      4) return STRICT JSON (days[], totals{}).
    """
    system_msg = (
        "You are a precise travel planner. "
        "Return ONLY valid JSON that matches the provided JSON Schema exactly. "
        "No extra text."
    )

    # Keep prompt lean (tier-1 friendly)
    user_payload = {
        "city": city,
        "dates": dates,
        "interests": interests or [],
        "time_slots": [["09:00","11:00"], ["11:00","13:00"], ["14:00","16:00"], ["16:00","18:00"]],
        "rules": [
            "Prefer items that match interests; keep travel time reasonable.",
            "Up to 4 items per day; fewer is fine if quality is better.",
            "Each item: one brief, friendly blurb (1–2 sentences)."
        ],
        "candidates": pois[: min(len(pois), 6)]  # reduce token pressure
    }

    json_payload = {
        "model": _model(),
        "temperature": 0.4,
        "response_format": {
            "type": "json_schema",
            "json_schema": _itinerary_schema()
        },
        "max_completion_tokens": 400,  # GPT-5 param (not max_tokens)
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": json.dumps(user_payload)}
        ]
    }

    resp = await _post_with_retries(json_payload, max_retries=5)
    data = resp.json()

    # With response_format=json_schema, content is guaranteed to be valid JSON per schema
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
