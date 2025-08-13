# 🗺️ VoyageCraft – AI Travel Agent (Streamlit)

Plan multi-day itineraries with **OpenAI** + **OpenStreetMap**. Honors **multiple interests** (e.g., `history, food, art`) and builds a balanced daily mix. Includes Google Maps links and JSON export.

---

## Features

* Geocode → POIs (Wikipedia + Overpass/OSM) → **per-day OpenAI planning**
* **Multi-interest aware** (variety per day, coverage across the trip)
* Retries/backoff; compact per-day prompts (cheap + reliable)
* Fallback keeps a sensible mix if OpenAI blips
* “Open in Google Maps” links and **Download JSON** button

---

## Quickstart (Streamlit)

**1) Create & activate venv**

```bash
python -m venv .venv
# Windows (Git Bash):
source .venv/Scripts/activate
```

**2) Install deps**

```bash
pip install -r requirements.txt
```

**3) Configure environment**

```bash
cp .env.example .env
# then edit .env and set your real key (never commit .env)
```

`.env`

```
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
OPENAI_MODEL=o4-mini
USER_AGENT_EMAIL=you@example.com
```

**4) (Optional) Run headless without Streamlit sign-in**

```bash
mkdir -p ~/.streamlit
printf "[server]\nheadless = true\n" > ~/.streamlit/config.toml
```

**5) Launch**

```bash
streamlit run streamlit_app.py --server.headless true
```

**Example input**

* Destination: `Tokyo`
* Dates: `2025-09-01` → `2025-09-03`
* Interests: `history, food, art`

---

## Configuration

* **Model**: default `o4-mini`. If your account only shows `gpt-4o-mini`, set:

  ```
  OPENAI_MODEL=gpt-4o-mini
  ```
* **No `temperature` sent**: `o4-mini` enforces the default; sending it causes 400 errors (handled in code).
* **Token cap key auto-select**: the app picks `max_completion_tokens` for `o4-*` / `gpt-5-*`, and `max_tokens` for legacy `gpt-4o-*`.

---

## Files

* `streamlit_app.py` — Streamlit app (UI + logic)
* `requirements.txt`

  ```
  streamlit
  httpx
  python-dotenv
  ```
* `.env.example` — placeholders only (no secrets)
* `.gitignore`

  ```
  .env
  .venv/
  __pycache__/
  *.pyc
  ```

> The `server/` (FastAPI) folder is present but **not required** for Streamlit. You can ignore it.

---

## Troubleshooting

* **400 “Unsupported `temperature`”** → ensure the request doesn’t send `temperature` (already removed).
* **400 “Unsupported parameter: `max_*tokens`”** → make sure `OPENAI_MODEL` matches your dashboard (`o4-mini` vs `gpt-4o-mini`); the app auto-uses the right key.
* **401/403** → check `OPENAI_API_KEY` in `.env`. Never commit real keys.
* **429 (rate limit)** → try fewer days; the app backs off between per-day calls.
* **Sparse blurbs for local venues** → the app falls back to JP/EN/TR Wikipedia; some small places may have short summaries.

---

## Safety

* Keep **real keys only in `.env`**.
* `.env.example` must use placeholders.
* GitHub Push Protection will block commits containing secrets — that’s good.

---

## Export

* Use the **Download JSON** button after generating a plan.
* Each item includes an **Open in Google Maps** link.

---

## License

MIT License.
