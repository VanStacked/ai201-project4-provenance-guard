import json
import uuid
from datetime import datetime
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

LOG_FILE = "audit_log.jsonl"


# ── Helpers ──────────────────────────────────────────────────────────────────

def write_log(entry: dict):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def read_log():
    if not os.path.exists(LOG_FILE):
        return []
    entries = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ── Signal 1: LLM-based classification ───────────────────────────────────────

def llm_signal(text: str) -> float:
    prompt = f"""You are an AI content detection expert. Analyze the following text and determine the probability that it was AI-generated (not written by a human).

Consider:
- Uniformity of sentence structure
- Overly formal or generic phrasing
- Lack of personal voice, typos, or natural irregularities
- Use of filler phrases like "it is important to note" or "furthermore"

Respond with ONLY a number between 0.0 and 1.0 where:
0.0 = definitely human-written
1.0 = definitely AI-generated

Text:
{text}

Probability:"""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    try:
        score = float(raw.split()[0].strip(".,"))
        return max(0.0, min(1.0, score))
    except:
        return 0.5


# ── Signal 2: Stylometric heuristics ─────────────────────────────────────────

def stylometric_signal(text: str) -> float:
    import re
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if len(sentences) < 2:
        return 0.5

    # Sentence length variance (AI text is more uniform)
    lengths = [len(s.split()) for s in sentences]
    mean_len = sum(lengths) / len(lengths)
    variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
    # Low variance = more AI-like
    variance_score = max(0.0, 1.0 - (variance / 50.0))

    # Type-token ratio (vocabulary diversity; AI tends to be more repetitive)
    words = re.findall(r'\b\w+\b', text.lower())
    if len(words) == 0:
        return 0.5
    ttr = len(set(words)) / len(words)
    # Low TTR = more AI-like
    ttr_score = max(0.0, 1.0 - ttr)

    # Punctuation density (humans use more varied punctuation)
    punct_count = len(re.findall(r'[,;:\-—\'\"()\[\]]', text))
    punct_density = punct_count / max(len(words), 1)
    # Low punctuation = more AI-like
    punct_score = max(0.0, 1.0 - (punct_density * 10))

    return round((variance_score + ttr_score + punct_score) / 3, 3)


# ── Confidence scoring ────────────────────────────────────────────────────────

def combine_signals(llm_score: float, stylo_score: float) -> float:
    # LLM signal weighted more heavily as it captures semantics
    return round((llm_score * 0.65) + (stylo_score * 0.35), 3)


# ── Transparency label ────────────────────────────────────────────────────────

def get_label(confidence: float) -> dict:
    if confidence >= 0.75:
        return {
            "verdict": "Likely AI-Generated",
            "text": "Our system found strong indicators that this content was AI-generated. This does not mean the work has no value, but readers should be aware it may not reflect the creator's personal voice.",
            "level": "high_ai"
        }
    elif confidence <= 0.35:
        return {
            "verdict": "Likely Human-Written",
            "text": "Our system found strong indicators that this content was written by a human. Natural variation in style, voice, and structure suggest authentic human authorship.",
            "level": "high_human"
        }
    else:
        return {
            "verdict": "Uncertain",
            "text": "Our system could not confidently determine whether this content was AI-generated or human-written. The creator may appeal this classification if they believe it is inaccurate.",
            "level": "uncertain"
        }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json()
    if not data or "text" not in data or "creator_id" not in data:
        return jsonify({"error": "Request must include 'text' and 'creator_id'"}), 400

    text = data["text"]
    creator_id = data["creator_id"]
    content_id = str(uuid.uuid4())

    llm_score = llm_signal(text)
    stylo_score = stylometric_signal(text)
    confidence = combine_signals(llm_score, stylo_score)
    label = get_label(confidence)
    attribution = "likely_ai" if confidence >= 0.75 else ("likely_human" if confidence <= 0.35 else "uncertain")

    entry = {
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": datetime.utcnow().isoformat(),
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "status": "classified",
        "appeal_reasoning": None,
    }
    write_log(entry)

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
        "label": label,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json()
    if not data or "content_id" not in data or "creator_reasoning" not in data:
        return jsonify({"error": "Request must include 'content_id' and 'creator_reasoning'"}), 400

    content_id = data["content_id"]
    reasoning = data["creator_reasoning"]

    entries = read_log()
    found = False
    updated_entries = []
    for entry in entries:
        if entry["content_id"] == content_id:
            entry["status"] = "under_review"
            entry["appeal_reasoning"] = reasoning
            found = True
        updated_entries.append(entry)

    if not found:
        return jsonify({"error": "content_id not found"}), 404

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        for entry in updated_entries:
            f.write(json.dumps(entry) + "\n")

    return jsonify({
        "message": "Appeal received. Your content has been marked as under review.",
        "content_id": content_id,
        "status": "under_review",
    })


@app.route("/log", methods=["GET"])
def get_log():
    entries = read_log()
    return jsonify({"entries": entries[-20:]})


if __name__ == "__main__":
    app.run(debug=True)