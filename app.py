import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from signals import llm_classifier, stylometric_heuristics, combine_signals
from database import init_db, log_submission, get_log

load_dotenv()

app = Flask(__name__)

with app.app_context():
    init_db()


@app.post("/submit")
def submit():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    missing = [field for field in ("text", "creator_id") if field not in data]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    content_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    signal1_score = llm_classifier(data["text"])
    signal2_score = stylometric_heuristics(data["text"])
    confidence_score = combine_signals(signal1_score, signal2_score, data["text"])

    log_submission(
        content_id=content_id,
        creator_id=data["creator_id"],
        timestamp=timestamp,
        llm_score=signal1_score,
        stylometric_score=signal2_score,
        confidence=confidence_score,
    )

    return jsonify({
        "content_id": content_id,
        "signal1_score": signal1_score,
        "signal2_score": signal2_score,
        "confidence_score": confidence_score,
        "label": None,
    }), 200


@app.get("/log")
def log():
    return jsonify({"entries": get_log()}), 200


if __name__ == "__main__":
    app.run(debug=True)
