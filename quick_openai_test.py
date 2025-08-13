import os, httpx, json
from dotenv import load_dotenv

# Load .env so your API key is available
load_dotenv()

# Make a simple test request to OpenAI API
r = httpx.post(
    "https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
    json={
        "model": os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Say ok."}
        ],
        "max_completion_tokens": 5  # ✅ correct param for GPT-5
    },
    timeout=20
)

print("Status:", r.status_code)
print("Body:", r.text[:200])

