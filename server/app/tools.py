import httpx, datetime as dt
from typing import List, Dict, Any, Optional

def _headers(email: str):
    # Required by OpenStreetMap Nominatim usage policy
    return {"User-Agent": f"voyagecraft/1.0 ({email})"}

async def geocode_city(city: str, email: str) -> Optional[Dict[str, float]]:
    """
    Given 'Istanbul', return {'lat': 41.0082, 'lon': 28.9784} (example).
    Uses OpenStreetMap Nominatim (no key required, but valid email is required in UA).
    """
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": city, "format": "json", "limit": 1}
    async with httpx.AsyncClient(headers=_headers(email), timeout=20) as cx:
        r = await cx.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}

async def wiki_geosearch(lat: float, lon: float, email: str,
                         radius_m: int = 5000, limit: int = 25) -> List[Dict[str, Any]]:
    """
    Find nearby points of interest from Wikipedia around a coordinate.
    Returns a list of {name, lat, lon, category}.
    """
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "list": "geosearch",
        "gscoord": f"{lat}|{lon}",
        "gsradius": radius_m,
        "gslimit": limit,
        "format": "json",
    }
    async with httpx.AsyncClient(headers=_headers(email), timeout=20) as cx:
        r = await cx.get(url, params=params)
        r.raise_for_status()
        items = r.json().get("query", {}).get("geosearch", [])
        return [
            {"name": i["title"], "lat": i["lat"], "lon": i["lon"], "category": "sight"}
            for i in items
        ]

def date_range(start: str, end: str) -> List[str]:
    """
    Inclusive ISO date list.
    '2025-08-20'..'2025-08-22' -> ['2025-08-20','2025-08-21','2025-08-22']
    """
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    days: List[str] = []
    while s <= e:
        days.append(s.isoformat())
        s += dt.timedelta(days=1)
    return days
