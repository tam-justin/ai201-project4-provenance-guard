import uuid
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

from signals import llm_classifier, stylometric_heuristics, combine_signals, generate_label
from database import init_db, log_submission, get_log, get_submission, process_appeal

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri="memory://",
    default_limits=[],
)

with app.app_context():
    init_db()


@app.post("/submit")
@limiter.limit("5 per minute;100 per day")
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
    attribution, label_text = generate_label(confidence_score)

    log_submission(
        content_id=content_id,
        creator_id=data["creator_id"],
        text=data["text"],
        timestamp=timestamp,
        attribution=attribution,
        label_text=label_text,
        llm_score=signal1_score,
        stylometric_score=signal2_score,
        confidence=confidence_score,
    )

    return jsonify({
        "content_id": content_id,
        "creator_id": data["creator_id"],
        "text": data["text"],
        "timestamp": timestamp,
        "attribution": attribution,
        "label_text": label_text,
        "confidence": confidence_score,
        "llm_score": signal1_score,
        "stylometric_score": signal2_score,
        "status": "classified",
    }), 200


@app.post("/appeal")
def appeal():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    missing = [field for field in ("content_id", "creator_reasoning") if field not in data]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    submission = get_submission(data["content_id"])
    if not submission:
        return jsonify({"error": "No submission found with that content_id."}), 404

    if submission["attribution"] != "High-Confidence AI":
        return jsonify({"error": "Appeals are only accepted for submissions flagged as High-Confidence AI."}), 403

    if submission["status"] == "Under Review":
        return jsonify({"error": "An appeal is already under review for this submission."}), 409

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

    process_appeal(
        content_id=data["content_id"],
        appeal_reason=data["creator_reasoning"],
        timestamp=timestamp,
        creator_id=submission["creator_id"],
        attribution=submission["attribution"],
        label_text=submission["label_text"],
        llm_score=submission["llm_score"],
        stylometric_score=submission["stylometric_score"],
        confidence=submission["confidence"],
    )

    return jsonify({
        "message": "Your appeal has been received and is now under review.",
        "content_id": data["content_id"],
        "status": "Under Review",
        "original_confidence_score": submission["confidence"],
        "original_attribution": submission["attribution"],
        "original_label": submission["label_text"],
    }), 200


@app.get("/log")
def log():
    return jsonify({"entries": get_log()}), 200


if __name__ == "__main__":
    app.run(debug=True)
