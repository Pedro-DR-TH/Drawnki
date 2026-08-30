import os
import secrets
import string
import time

from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

pairings = {}

PAIRING_EXPIRY_SECONDS = 600


def generate_pairing_id() -> str:
    return secrets.token_hex(16)


def generate_pairing_code() -> str:
    alphabet = string.ascii_uppercase + string.digits

    def block(n):
        return "".join(secrets.choice(alphabet) for _ in range(n))

    return f"ANKI-{block(4)}-{block(4)}"


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


@app.route("/pair")
def pair_page():
    from flask import render_template
    return render_template("pair.html")


@app.route("/draw")
def draw_page():
    from flask import render_template
    return render_template("draw.html")


@app.route("/api/pair/init", methods=["POST"])
def api_pair_init():
    body = request.get_json(silent=True) or {}
    deck_name = body.get("deck_name", "Unnamed Deck")

    pairing_id = generate_pairing_id()
    pairing_code = generate_pairing_code()

    pairings[pairing_id] = {
        "pairing_code": pairing_code,
        "deck_name": deck_name,
        "status": "pending",
        "device_token": None,
        "created_at": time.time(),
    }

    return jsonify({
        "pairing_id": pairing_id,
        "pairing_code": pairing_code,
    })


@app.route("/api/pair/status")
def api_pair_status():
    pairing_id = request.args.get("pairing_id")

    if not pairing_id or pairing_id not in pairings:
        return jsonify({"error": "Unknown pairing_id"}), 404

    record = pairings[pairing_id]

    if record["status"] == "pending" and time.time() - record["created_at"] > PAIRING_EXPIRY_SECONDS:
        return jsonify({"status": "expired"}), 410

    if record["status"] == "paired":
        return jsonify({
            "status": "paired",
            "device_token": record["device_token"],
        })

    return jsonify({"status": "pending"})


@app.route("/api/pair/confirm", methods=["POST"])
def api_pair_confirm():
    body = request.get_json(silent=True) or {}
    pairing_code = body.get("pairing_code")

    match = None
    for pid, record in pairings.items():
        if record["pairing_code"] == pairing_code and record["status"] == "pending":
            match = (pid, record)
            break

    if not match:
        return jsonify({"error": "Invalid or already-used pairing code"}), 404

    pairing_id, record = match
    record["status"] = "paired"
    record["device_token"] = generate_device_token()

    return jsonify({"status": "paired", "pairing_id": pairing_id})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
