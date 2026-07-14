#!/usr/bin/env python3
"""LOCOMO Dataset Server — serves industry-standard agent memory benchmark data."""
import json, os, sys
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "locomo_data", "locomo10.json")
DATA_PATH = os.path.abspath(DATA_PATH)

with open(DATA_PATH, "r", encoding="utf-8") as f:
    RAW = json.load(f)

def parse_session(session_data):
    """Parse a LOCOMO session into structured format."""
    conv = session_data.get("conversation", {})
    speaker_a = conv.get("speaker_a", "Speaker A")
    speaker_b = conv.get("speaker_b", "Speaker B")
    turns = []
    # Find session keys like session_1, session_2, etc.
    session_keys = sorted([k for k in conv.keys() if k.startswith("session_") and not k.endswith("_date_time")],
                          key=lambda x: int(x.split("_")[1]))
    for sk in session_keys:
        dt_key = sk + "_date_time"
        dt = conv.get(dt_key, "")
        msgs = conv.get(sk, [])
        if isinstance(msgs, list):
            for msg in msgs:
                if isinstance(msg, dict):
                    turns.append({
                        "session": sk,
                        "date": dt,
                        "speaker": msg.get("speaker", speaker_a if msg.get("turn", 0) % 2 == 0 else speaker_b),
                        "text": msg.get("text", msg.get("content", str(msg)[:200])),
                        "turn_id": msg.get("turn", len(turns)),
                    })
                elif isinstance(msg, str):
                    turns.append({"session": sk, "date": dt, "speaker": speaker_a, "text": msg, "turn_id": len(turns)})
    # Parse QA
    qas = []
    for qa in session_data.get("qa", []):
        qas.append({
            "question": qa.get("question", ""),
            "answer": qa.get("answer", ""),
            "evidence": qa.get("evidence", []),
            "category": qa.get("category", 0),
        })
    return {
        "sample_id": session_data.get("sample_id", ""),
        "speaker_a": speaker_a,
        "speaker_b": speaker_b,
        "turns": turns,
        "qa_pairs": qas,
        "session_summary": session_data.get("session_summary", ""),
        "event_summary": session_data.get("event_summary", ""),
    }

PARSED = [parse_session(s) for s in RAW]

@app.route("/api/locomo/sessions")
def list_sessions():
    """List all LOCOMO sessions with summary info."""
    return jsonify([{
        "sample_id": s["sample_id"],
        "speaker_a": s["speaker_a"],
        "speaker_b": s["speaker_b"],
        "turn_count": len(s["turns"]),
        "qa_count": len(s["qa_pairs"]),
        "summary_preview": str(s.get("session_summary",""))[:150],
    } for s in PARSED])

@app.route("/api/locomo/session/<sample_id>")
def get_session(sample_id):
    """Get full session detail including all conversation turns and QA pairs."""
    for s in PARSED:
        if s["sample_id"] == sample_id:
            return jsonify(s)
    return jsonify({"error": "session not found"}), 404

@app.route("/api/locomo/session/<sample_id>/turns")
def get_turns(sample_id):
    """Get conversation turns only (for memory ingestion)."""
    for s in PARSED:
        if s["sample_id"] == sample_id:
            return jsonify({"sample_id": sample_id, "turns": s["turns"]})
    return jsonify({"error": "session not found"}), 404

@app.route("/api/locomo/session/<sample_id>/qa")
def get_qa(sample_id):
    """Get QA pairs only (for evaluation)."""
    for s in PARSED:
        if s["sample_id"] == sample_id:
            return jsonify({"sample_id": sample_id, "qa_pairs": s["qa_pairs"]})
    return jsonify({"error": "session not found"}), 404

@app.route("/api/locomo/stats")
def stats():
    """Dataset statistics."""
    total_turns = sum(len(s["turns"]) for s in PARSED)
    total_qa = sum(len(s["qa_pairs"]) for s in PARSED)
    cats = {}
    for s in PARSED:
        for qa in s["qa_pairs"]:
            c = qa["category"]
            cats[c] = cats.get(c, 0) + 1
    return jsonify({
        "sessions": len(PARSED),
        "total_turns": total_turns,
        "total_qa": total_qa,
        "categories": cats,
        "dataset": "LOCOMO (Long Conversation Memory Benchmark)",
        "source": "https://github.com/snap-research/locomo",
    })

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"LOCOMO Dataset Server on http://127.0.0.1:{port}")
    print(f"  Sessions: {len(PARSED)} | Turns: {sum(len(s['turns']) for s in PARSED)} | QA: {sum(len(s['qa_pairs']) for s in PARSED)}")
    app.run(host="127.0.0.1", port=port, debug=False)
