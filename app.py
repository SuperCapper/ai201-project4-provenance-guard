import os
import json
import uuid
import hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

APP_ROOT = os.path.dirname(__file__)
AUDIT_LOG = os.path.join(APP_ROOT, "audit.log")

app = Flask(__name__)


def deterministic_score_from_text(text: str) -> float:
    """Deterministic pseudo-score from text for a reproducible mock signal.
    Uses SHA256 of the text to produce a float in [0,1]."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # take first 15 hex chars to a large int
    val = int(h[:15], 16)
    return (val % 10000) / 10000.0


def signal1_groq_mock(text: str) -> dict:
    """Mocked Groq response matching our planning.md signature.
    Returns model_score, explanation, prompt_hash."""
    score = deterministic_score_from_text(text)
    explanation = "mocked deterministic score from text hash"
    prompt_hash = hashlib.sha1(b"groq-prompt-v1").hexdigest()[:10]
    return {"model_score": score, "explanation": explanation, "prompt_hash": prompt_hash}


def write_audit_entry(entry: dict):
    entry_json = json.dumps(entry, ensure_ascii=False)
    # ensure audit log directory exists
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(entry_json + "\n")


@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json(force=True)
    text = data.get("text")
    if not text:
        return jsonify({"error": "missing 'text' field"}), 400
    creator_id = data.get("creator_id", "anonymous")

    content_id = uuid.uuid4().hex

    # Run first detection signal (mocked Groq)
    s1 = signal1_groq_mock(text)
    model_score = s1["model_score"]

    # Simple placeholder attribution (will be replaced by combiner later)
    attribution = "likely_ai" if model_score >= 0.5 else "likely_human"

    # Build audit entry
    audit = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attribution": attribution,
        "confidence": model_score,
        "signal_1": s1,
        "status": "classified",
    }
    write_audit_entry(audit)

    resp = {
        "content_id": content_id,
        "attribution": attribution,
        "confidence": model_score,
        "label_text": attribution,
    }
    return jsonify(resp)


@app.route("/log", methods=["GET"])
def get_log():
    entries = []
    if os.path.exists(AUDIT_LOG):
        with open(AUDIT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    # skip malformed lines
                    continue
    # return most recent first
    entries = list(reversed(entries))
    return jsonify({"entries": entries})


if __name__ == "__main__":
    # Run development server on port 5000
    app.run(host="0.0.0.0", port=5000)
