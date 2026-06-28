import json
from app import app

CREATOR_ID = "rate_limit_tester"
LIMIT = 10
TEXT = "This is a test submission for rate limit demonstration."

with app.test_client() as client:
    print(f"Sending {LIMIT + 1} requests as creator_id='{CREATOR_ID}' (limit: {LIMIT}/minute)\n")

    for i in range(1, LIMIT + 2):
        res = client.post("/submit", json={"text": TEXT, "creator_id": CREATOR_ID})
        status = res.status_code
        if status == 429:
            print(f"Request {i:2d}: {status} TOO MANY REQUESTS — {res.get_data(as_text=True).strip()}")
        else:
            data = res.get_json()
            print(f"Request {i:2d}: {status} OK  — label: {data.get('label')}, content_id: {data.get('content_id', '')[:8]}...")
