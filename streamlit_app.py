# streamlit_app.py
# VoyageCraft – AI Travel Agent (Streamlit-only, OpenAI + OSM/Wikipedia)
# - Model: o4-mini (no temperature, no response_format). Token-cap auto-handled.
# - Per-day calls with retries to stay within Tier-1.
# - Multi-interest: pulls OSM POIs per interest + Wikipedia sights; enforces daily mix and global coverage.

import os, json, datetime as dt, asyncio, re
import streamlit as st
import httpx
from dotenv import load_dotenv

load_dotenv()

OPENAI_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "o4-mini")
USER_AGENT_EMAIL = os.getenv("USER_AGENT_EMAIL", "dev@example.com")

# ---------------- Utilities ----------------
def _ua():
    return {"User-Agent": f"voyagecraft/1.0 ({USER_AGENT_EMAIL})"}

def _token_cap_key(model: str):
    # o4* + gpt-5* => max_completion_tokens ; legacy gpt-4o* => max_tokens
    return "max_completion_tokens" if model.startswith(("o4","gpt-5")) else "max_tokens"

def date_list(start: dt.date, end: dt.date):
    out, d = [], start
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out

def _dedupe_by_name(items):
    seen, out = set(), []
    for it in items:
        name = (it.get("name") or "").strip()
        key = re.sub(r"\s+", " ", name).lower()
        if key and key not in seen:
            seen.add(key); out.append(it)
    return out

CHAIN_WORDS = {"starbucks","mcdonald","kfc","burger king","dunkin","subway","7-eleven","7 11","7-eleven","pizza hut"}
def _filter_chains(items, keep_at_least=8):
    # Drop global chains unless we don't have enough items
    non_chains = [i for i in items if all(w not in i["name"].lower() for w in CHAIN_WORDS)]
    return non_chains if len(non_chains) >= keep_at_least else items

def _split_by_category(items):
    by = {}
    for it in items:
        cat = (it.get("category") or "sight").lower()
        by.setdefault(cat, []).append(it)
    return by

# ---------------- Geocode + Wikipedia ----------------
async def geocode_city(city: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": city, "format": "json", "limit": 1}
    async with httpx.AsyncClient(headers=_ua(), timeout=20) as cx:
        r = await cx.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if not data: return None
        return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}

async def wiki_geosearch(lat: float, lon: float, radius_m=5000, limit=40):
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action":"query","list":"geosearch","gscoord":f"{lat}|{lon}",
        "gsradius":radius_m,"gslimit":limit,"format":"json"
    }
    async with httpx.AsyncClient(headers=_ua(), timeout=20) as cx:
        r = await cx.get(url, params=params)
        r.raise_for_status()
        items = r.json().get("query",{}).get("geosearch",[])
        return [{"name":i["title"],"lat":i["lat"],"lon":i["lon"],"category":"sight"} for i in items]

async def wiki_summary(title: str, chars: int = 220) -> str:
    # Try EN first; if empty, try Japanese (ja) and Turkish (tr) as common cases
    for lang in ("en", "ja", "tr"):
        try:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}"
            async with httpx.AsyncClient(headers=_ua(), timeout=20) as cx:
                r = await cx.get(url)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                txt = (data.get("extract") or "").strip()
                if txt:
                    return (txt[:chars] + "…") if len(txt) > chars else txt
        except Exception:
            continue
    # Category-aware fallback if no wiki entry
    tl = title.lower()
    return ("Casual local eatery." if any(k in tl for k in ["cafe","coffee","ramen","sushi","izakaya","yakitori","noodle","donburi","tonkatsu"])
            else "Notable local spot.")

# ---------------- Overpass (interest → POIs) ----------------
INTEREST_TAGS = {
    "food": [
        ('amenity', r'restaurant|cafe|fast_food|ice_cream|bakery|food_court')
    ],
    "history": [
        ('historic', r'.+'), ('memorial', r'.+'),
        ('tourism', r'museum'), ('tourism', r'gallery'), ('heritage', r'.+')
    ],
    "nature": [
        ('leisure', r'park|garden'), ('natural', r'wood|water|beach'),
        ('tourism', r'viewpoint')
    ],
    "art": [
        ('tourism', r'gallery|museum'), ('amenity', r'theatre|arts_centre')
    ],
    "shopping": [
        ('shop', r'department_store|mall|supermarket|marketplace|convenience|boutique')
    ],
    "religion": [
        ('amenity', r'place_of_worship'), ('religion', r'.+')
    ],
    "nightlife": [
        ('amenity', r'bar|pub|nightclub')
    ],
    # default will fall back to generic sights
}

def _overpass_query(lat: float, lon: float, radius_m: int, tag_pairs):
    # Build a union of node/way queries for given (key, regex) pairs
    blocks = []
    for key, regex in tag_pairs:
        blocks.append(f'  node(around:{radius_m},{lat},{lon})["{key}"~"{regex}"];')
        blocks.append(f'  way(around:{radius_m},{lat},{lon})["{key}"~"{regex}"];')
    body = "\n".join(blocks)
    return f"""
[out:json][timeout:25];
(
{body}
);
out center 60;
""".strip()

async def overpass_for_interest(lat: float, lon: float, interest: str, radius_m=5000, limit=40):
    tags = INTEREST_TAGS.get(interest.lower())
    if not tags:
        # fallback to generic attractions
        tags = [('tourism', r'attraction|museum|gallery')]
    q = _overpass_query(lat, lon, radius_m, tags)
    items = []
    try:
        async with httpx.AsyncClient(headers=_ua(), timeout=40) as cx:
            r = await cx.post("https://overpass-api.de/api/interpreter", data=q)
            r.raise_for_status()
            data = r.json()
            for el in data.get("elements", []):
                tags = el.get("tags", {})
                name = tags.get("name") or tags.get("brand")
                if not name: continue
                lat0 = el.get("lat") or (el.get("center") or {}).get("lat")
                lon0 = el.get("lon") or (el.get("center") or {}).get("lon")
                if lat0 is None or lon0 is None: continue
                items.append({
                    "name": name, "lat": float(lat0), "lon": float(lon0),
                    "category": interest.lower()
                })
    except Exception:
        return []
    items = _dedupe_by_name(items)[:limit]
    items = _filter_chains(items, keep_at_least=12)
    return items

async def gather_interest_pois(lat: float, lon: float, interests, radius_m=5000):
    by_interest = {}
    for i in interests:
        by_interest[i] = await overpass_for_interest(lat, lon, i, radius_m=radius_m)
    return by_interest

# ---------------- OpenAI (per-day) ----------------
async def _retry_openai(json_body, max_retries=5):
    delay = 3
    async with httpx.AsyncClient(timeout=60) as cx:
        for attempt in range(1, max_retries + 1):
            r = await cx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                json=json_body
            )
            if r.status_code < 400:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                wait = max(int(ra) if ra and ra.isdigit() else 0, delay)
                if attempt == max_retries:
                    raise httpx.HTTPStatusError(
                        f"{r.status_code} {r.reason_phrase}: {r.text[:400]}",
                        request=r.request, response=r
                    )
                await asyncio.sleep(wait); delay *= 2; continue
            raise httpx.HTTPStatusError(
                f"{r.status_code} {r.reason_phrase}: {r.text[:400]}",
                request=r.request, response=r
            )

async def plan_one_day(city, day_iso, interests, candidates, show_debug=False):
    # Build a balanced candidate set: take a slice per interest + sights
    by_cat = _split_by_category(candidates)
    merged = []
    # Add up to 5 from each requested interest (diversity), then up to 8 sights
    for i in interests:
        merged += by_cat.get(i, [])[:5]
    merged += by_cat.get("sight", [])[:8]
    merged = _dedupe_by_name(merged)[:14]

    payload = {
        "city": city,
        "date": day_iso,
        "interests": interests or [],
        "time_slots": [["09:00","11:00"],["11:00","13:00"],["14:00","16:00"],["16:00","18:00"]],
        "rules": [
            "Pick 3–4 items total.",
            "Use only items from candidates; copy name/lat/lon/category.",
            "Prefer variety: include at least 2 distinct categories per day.",
            "If 'food' is present in interests, schedule the food item at 11:00–13:00 or 16:00–18:00.",
            "Each item: one brief, friendly blurb (1–2 sentences).",
            "Return ONLY compact JSON: {date, items[]}. No markdown or prose."
        ],
        "candidates": merged
    }
    json_body = {
        "model": MODEL,
        # o4-mini: do NOT send temperature; default=1 enforced
        "messages": [
            {"role":"system","content":
                "You are a precise travel planner. Output ONLY JSON with keys: date (string), items (array of {name,lat,lon,start,end,blurb,category}). No extra text."
            },
            {"role":"user","content": json.dumps(payload, separators=(',',':'))}
        ],
        "stop": ["```"]
    }
    json_body[_token_cap_key(MODEL)] = 220

    try:
        resp = await _retry_openai(json_body, max_retries=5)
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content), None
        except Exception:
            s = content; l = s.find("{"); r = s.rfind("}")
            if l >= 0 and r > l:
                return json.loads(s[l:r+1]), None
            raise ValueError("Model returned non-JSON content")
    except Exception as e:
        if show_debug:
            return None, f"OpenAI error for {day_iso}: {e}"
        return None, None

# --------- Post-processing: enforce daily mix + global coverage ----------
async def _fill_blurb(item):
    if not item.get("blurb"):
        item["blurb"] = await wiki_summary(item["name"])
    return item

async def enforce_daily_mix(day_obj, interests, pools_by_interest, sights_pool):
    # Ensure at least 2 distinct categories per day, and place food at lunch/dinner
    items = _dedupe_by_name(day_obj.get("items", []))
    cats = { (it.get("category") or "sight").lower() for it in items }
    # add from pools until we have 2 distinct categories
    wanted = 2
    if len(cats) < wanted:
        for i in interests:
            pool = pools_by_interest.get(i, [])
            if pool:
                cand = dict(pool.pop(0))
                cand["start"], cand["end"] = "11:00","13:00" if i=="food" else "09:00","11:00"
                cand["blurb"] = await wiki_summary(cand["name"])
                items.append(cand)
                cats.add(i)
            if len(cats) >= wanted:
                break
        if len(cats) < wanted and sights_pool:
            s = dict(sights_pool.pop(0))
            s["start"], s["end"] = "14:00","16:00"
            s["blurb"] = await wiki_summary(s["name"])
            items.append(s)
            cats.add("sight")

    # If food requested, ensure exactly one food in good slot
    if "food" in [i.lower() for i in interests]:
        foods = [it for it in items if (it.get("category") or "") == "food"]
        if len(foods) == 0 and pools_by_interest.get("food"):
            f = dict(pools_by_interest["food"].pop(0))
            f["start"], f["end"] = "11:00","13:00"
            f["blurb"] = await wiki_summary(f["name"])
            items.append(f)
        elif len(foods) > 1:
            # keep the first; demote extras
            items = [it for it in items if (it.get("category") or "") != "food"] + foods[:1]
        for it in items:
            if it.get("category") == "food":
                it["start"], it["end"] = ("11:00","13:00") if it.get("start")=="09:00" else (it.get("start","11:00"), it.get("end","13:00"))

    # Cap 4 and ensure blurbs
    items = items[:4]
    items = [await _fill_blurb(it) for it in items]
    day_obj["items"] = items
    return day_obj

async def enforce_global_coverage(days, interests, pools_by_interest, sights_pool):
    # Ensure each requested interest appears at least once across the itinerary
    have = set()
    for d in days:
        have |= { (it.get("category") or "sight").lower() for it in d.get("items",[]) }

    missing = [i for i in interests if i not in have]
    if not missing:
        return days

    # Inject one item for each missing interest into earliest day that can accept it (<=4 items)
    for miss in missing:
        pool = pools_by_interest.get(miss, [])
        if not pool:
            continue
        for d in days:
            if len(d.get("items",[])) < 4:
                it = dict(pool.pop(0))
                it["start"], it["end"] = ("11:00","13:00") if miss=="food" else ("14:00","16:00")
                it["blurb"] = await wiki_summary(it["name"])
                d["items"].append(it)
                break
    return days

# ---------------- Build plan ----------------
async def build_plan(city, dates, interests, wiki_sights, by_interest, show_debug=False):
    # Pools we can pull from for enforcement/fallback
    pools_by_interest = {k: _dedupe_by_name(v)[:] for k, v in by_interest.items()}
    for k in pools_by_interest:
        pools_by_interest[k] = _filter_chains(pools_by_interest[k], keep_at_least=10)
    sights_pool = _dedupe_by_name([p for p in wiki_sights if (p.get("category") or "sight")!="food"])[:60]

    days_out, debug_msgs = [], []

    for i, d in enumerate(dates):
        if i > 0:
            await asyncio.sleep(1.8)  # gentle on RPM
        # Candidates = a few from each interest + some sights
        cand = []
        for it in interests:
            cand += pools_by_interest.get(it, [])[:6]
        cand += sights_pool[:10]
        cand = _dedupe_by_name(cand)[:18]

        obj, err = await plan_one_day(city, d, interests, cand, show_debug=show_debug)
        if obj:
            obj = await enforce_daily_mix(obj, interests, pools_by_interest, sights_pool)
            days_out.append({"date": obj["date"], "items": obj["items"]})
        else:
            # Fallback: assemble mix (2 categories min)
            items = []
            # add one per first 2 interests if possible
            for it in interests[:2]:
                pool = pools_by_interest.get(it, [])
                if pool:
                    x = dict(pool.pop(0))
                    x["start"], x["end"] = ("11:00","13:00") if it=="food" else ("09:00","11:00")
                    x["blurb"] = await wiki_summary(x["name"])
                    items.append(x)
            # top up with sights
            for slot in [("14:00","16:00"), ("16:00","18:00")]:
                if len(items) >= 4: break
                if sights_pool:
                    s = dict(sights_pool.pop(0))
                    s["start"], s["end"] = slot
                    s["blurb"] = await wiki_summary(s["name"])
                    items.append(s)
            days_out.append({"date": d, "items": items})
            if err: debug_msgs.append(err)

    # Ensure each requested interest appears at least once
    days_out = await enforce_global_coverage(days_out, interests, pools_by_interest, sights_pool)
    totals = {"cost_low": 50*len(days_out), "cost_high": 120*len(days_out)}
    return {"days": days_out, "totals": totals}, debug_msgs

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="VoyageCraft – AI Travel Agent", page_icon="🗺️", layout="centered")
st.title("🗺️ VoyageCraft – AI Travel Agent (Streamlit)")

if not OPENAI_KEY:
    st.error("Missing OPENAI_API_KEY in .env"); st.stop()

dest = st.text_input("Destination", "Tokyo")
c1, c2 = st.columns(2)
start = c1.date_input("Start date", dt.date(2025, 9, 1))
end = c2.date_input("End date", dt.date(2025, 9, 5))
interests_csv = st.text_input("Interests (comma-separated)", "history, food")
show_debug = st.toggle("Show OpenAI debug", value=False)

if st.button("Plan my trip"):
    try:
        dates = date_list(start, end)
        interests = [s.strip().lower() for s in interests_csv.split(",") if s.strip()]
        with st.status("Finding places…", expanded=False):
            loc = asyncio.run(geocode_city(dest))
            if not loc:
                st.error("Destination not found"); st.stop()
            wiki_sights = asyncio.run(wiki_geosearch(loc["lat"], loc["lon"]))
            wiki_sights = _dedupe_by_name(wiki_sights)[:60]

            # interest-aware POIs for each requested interest
            by_interest = {}
            for it in interests:
                by_interest[it] = asyncio.run(overpass_for_interest(loc["lat"], loc["lon"], it))
            # if none requested, default to sights
            if not by_interest and wiki_sights:
                by_interest = {"sight": wiki_sights[:20]}

        with st.status("Planning with OpenAI (per day)…", expanded=show_debug):
            plan, dbg = asyncio.run(build_plan(dest, dates, interests, wiki_sights, by_interest, show_debug=show_debug))
            if show_debug and dbg:
                for line in dbg:
                    st.write(line)

        st.success("Itinerary ready!")
        for day in plan["days"]:
            st.subheader(day["date"])
            for item in day["items"]:
                st.markdown(f"**• {item['name']}**  `{item['start']}-{item['end']}`  _({item.get('category','sight')})_")
                st.write(item.get("blurb",""))
                st.markdown(f"[Open in Google Maps](https://www.google.com/maps?q={item['lat']},{item['lon']})")

        t = plan.get("totals", {})
        if t:
            st.caption(f"Budget: ${t.get('cost_low','?')}–${t.get('cost_high','?')}")
        st.download_button("Download JSON", data=json.dumps(plan, indent=2),
                           file_name="itinerary.json", mime="application/json")

    except Exception as e:
        st.error(f"Unexpected error: {e}")
