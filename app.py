import os
import json
import uuid
import hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

APP_ROOT = os.path.dirname(__file__)
AUDIT_LOG = os.path.join(APP_ROOT, "audit.log")
CONTENT_STORE = os.path.join(APP_ROOT, "content_store.json")

app = Flask(__name__)

limiter = Limiter(
    key_func=lambda: request.get_json(silent=True).get("creator_id") if request.is_json and request.get_json(silent=True) else get_remote_address(),
    app=app,
    storage_uri="memory://",
    default_limits=[],
)


def deterministic_score_from_text(text: str) -> float:
    """Deterministic pseudo-score from text for a reproducible mock signal.
    Uses SHA256 of the text to produce a float in [0,1]."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    # take first 15 hex chars to a large int
    val = int(h[:15], 16)
    return (val % 10000) / 10000.0


def signal2_stylometric(text: str) -> dict:
    """Compute simple stylometric heuristics and return a heuristic_score in [0,1].
    Features: type-token ratio, sentence length variance, punctuation density.
    Heuristic score maps higher -> more likely AI.
    """
    # tokens and types
    toks = [t for t in text.split() if t]
    tok_count = len(toks)
    types = len(set(toks)) if tok_count > 0 else 0
    ttr = types / tok_count if tok_count > 0 else 0.0

    # sentences
    sents = [s.strip() for s in text.replace('!', '.').replace('?', '.').split('.') if s.strip()]
    sent_lens = [len(s.split()) for s in sents] if sents else [tok_count]
    import statistics
    sent_var = statistics.pvariance(sent_lens) if len(sent_lens) > 0 else 0.0

    # punctuation density (punctuation chars per token)
    punct_count = sum(1 for ch in text if ch in '.,;:!?"\'"()-')
    punct_density = punct_count / tok_count if tok_count > 0 else 0.0

    # Map features to partial signals in [0,1] where higher implies AI-like
    # type-token ratio: AI tends to have lower TTR -> partial = 1 - ttr (clamped)
    part_ttr = max(0.0, min(1.0, 1.0 - ttr))

    # sentence variance: AI tends to have lower variance -> partial = 1 - sigmoid(norm_var)
    # normalize variance by an empirical cap
    var_cap = 20.0
    norm_var = min(sent_var / var_cap, 1.0)
    part_var = 1.0 - norm_var

    # punctuation density: assume AI uses slightly less punctuation -> more AI if low density
    # normalize by typical density ~0.2
    norm_punct = min(punct_density / 0.25, 1.0)
    part_punct = 1.0 - norm_punct

    # combine with weights
    w_ttr = 0.5
    w_var = 0.3
    w_punct = 0.2
    heuristic_score = w_ttr * part_ttr + w_var * part_var + w_punct * part_punct
    heuristic_score = max(0.0, min(1.0, heuristic_score))

    features = {
        "tok_count": tok_count,
        "type_token_ratio": round(ttr, 4),
        "sent_len_var": round(sent_var, 4),
        "punct_density": round(punct_density, 4),
    }
    return {"heuristic_score": round(heuristic_score, 4), "features": features}


def combine_signals(model_score: float, heuristic_score: float, k_model: float = 30.0, k_heuristic: float = 10.0):
    """Combine two calibrated scores using Beta pseudo-count fusion and return mean and approx 90% interval.
    Uses normal approximation for the Beta credible interval.
    """
    alpha_prior = 1.0
    beta_prior = 1.0

    alpha_m = model_score * k_model
    beta_m = (1.0 - model_score) * k_model
    alpha_h = heuristic_score * k_heuristic
    beta_h = (1.0 - heuristic_score) * k_heuristic

    alpha = alpha_prior + alpha_m + alpha_h
    beta = beta_prior + beta_m + beta_h
    mean = alpha / (alpha + beta)

    # approximate variance and 90% interval using normal approx
    import math

    var = mean * (1 - mean) / (alpha + beta + 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    z = 1.645
    lower = max(0.0, mean - z * sd)
    upper = min(1.0, mean + z * sd)
    width = upper - lower
    return {"mean": round(mean, 4), "lower90": round(lower, 4), "upper90": round(upper, 4), "width": round(width, 4)}




def signal1_groq_mock(text: str) -> dict:
    """Mocked Groq response matching our planning.md signature.
    Returns model_score, explanation, prompt_hash."""
    score = deterministic_score_from_text(text)
    explanation = "mocked deterministic score from text hash"
    prompt_hash = hashlib.sha1(b"groq-prompt-v1").hexdigest()[:10]
    return {"model_score": score, "explanation": explanation, "prompt_hash": prompt_hash}


def write_audit_entry(entry: dict):
    entry_json = json.dumps(entry, ensure_ascii=False)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(entry_json + "\n")


def save_content_record(content_id: str, record: dict):
    store = {}
    if os.path.exists(CONTENT_STORE):
        with open(CONTENT_STORE, "r", encoding="utf-8") as f:
            try:
                store = json.load(f)
            except json.JSONDecodeError:
                store = {}
    store[content_id] = record
    with open(CONTENT_STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def load_content_record(content_id: str):
    if not os.path.exists(CONTENT_STORE):
        return None
    with open(CONTENT_STORE, "r", encoding="utf-8") as f:
        try:
            store = json.load(f)
        except json.JSONDecodeError:
            return None
    return store.get(content_id)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
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
    # Run second detection signal (stylometric)
    s2 = signal2_stylometric(text)
    heuristic_score = s2["heuristic_score"]

    # Combine signals into posterior and interval
    combined = combine_signals(model_score, heuristic_score)

    # Map to label per planning thresholds
    width = combined["width"]
    mean = combined["mean"]
    if mean >= 0.85 and width <= 0.20:
        label = "High-confidence AI"
    elif mean <= 0.15 and width <= 0.20:
        label = "High-confidence human"
    else:
        label = "Uncertain"

    # Build audit entry including both signals and combined result
    audit = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_1": s1,
        "signal_2": s2,
        "combined": combined,
        "label": label,
        "status": "classified",
    }
    write_audit_entry(audit)

    content_record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "label": label,
        "status": "classified",
        "combined": combined,
        "signal_1": s1,
        "signal_2": s2,
        "created_at": audit["timestamp"],
    }
    save_content_record(content_id, content_record)

    resp = {
        "content_id": content_id,
        "signals": {"signal_1": s1, "signal_2": s2},
        "combined": combined,
        "label": label,
    }
    return jsonify(resp)


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(force=True)
    content_id = data.get("content_id")
    creator_id = data.get("creator_id")
    reason = data.get("reason", "")

    if not content_id:
        return jsonify({"error": "missing 'content_id' field"}), 400
    if not creator_id:
        return jsonify({"error": "missing 'creator_id' field"}), 400

    record = load_content_record(content_id)
    if not record:
        return jsonify({"error": "content_id not found"}), 404
    if record.get("creator_id") != creator_id:
        return jsonify({"error": "creator_id does not match original submitter"}), 403

    appeal_id = uuid.uuid4().hex
    appeal_timestamp = datetime.now(timezone.utc).isoformat()

    record["status"] = "under_review"
    record["appeal_id"] = appeal_id
    record["appeal_reason"] = reason
    record["appeal_requested_at"] = appeal_timestamp
    save_content_record(content_id, record)

    audit = {
        "content_id": content_id,
        "creator_id": creator_id,
        "appeal_id": appeal_id,
        "timestamp": appeal_timestamp,
        "event": "appeal_requested",
        "reason": reason,
        "status": "under_review",
    }
    write_audit_entry(audit)

    return jsonify({
        "content_id": content_id,
        "appeal_id": appeal_id,
        "status": "under_review",
        "message": "Appeal received and content is under review.",
    })


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
