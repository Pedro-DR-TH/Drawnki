import os
import secrets
import string
import time
from functools import wraps

from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True)

app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "changeme")

pairings = {}
cards_by_token = {}

PAIRING_EXPIRY_SECONDS = 600


def find_pairing_by_token(device_token):
    for record in pairings.values():
        if record.get("device_token") == device_token and record["status"] == "paired":
            return record
    return None


def generate_pairing_id() -> str:
    return secrets.token_hex(16)


def generate_pairing_code() -> str:
    alphabet = string.ascii_uppercase + string.digits

    def block(n):
        return "".join(secrets.choice(alphabet) for _ in range(n))

    return f"ANKI-{block(4)}-{block(4)}"


def generate_device_token() -> str:
    return secrets.token_urlsafe(32)


def is_authorized():
    if session.get("authorized"):
        return True
    header_password = request.headers.get("X-Site-Password")
    if header_password and header_password == SITE_PASSWORD:
        return True
    return False


def require_auth(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_authorized():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    if request.method == "POST":
        submitted = request.form.get("password", "")
        if submitted == SITE_PASSWORD:
            session["authorized"] = True
            next_url = request.args.get("next") or "/pair"
            return redirect(next_url)
        error = "Wrong password."

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Drawnki</title>
      <style>
        body {{ font-family: -apple-system, sans-serif; background: #fafaf8; display: flex;
                align-items: center; justify-content: center; height: 100vh; margin: 0; }}
        form {{ text-align: center; }}
        input {{ font-size: 18px; padding: 12px; border-radius: 8px; border: 1px solid #ddd;
                 text-align: center; }}
        button {{ display: block; margin: 12px auto 0; padding: 12px 24px; border-radius: 8px;
                  border: none; background: #2f6fed; color: white; font-weight: 600; font-size: 15px; }}
        p.error {{ color: #c0392b; }}
      </style>
    </head>
    <body>
      <form method="POST">
        <h2>Drawnki</h2>
        <input type="password" name="password" placeholder="Password" autofocus>
        <button type="submit">Enter</button>
        {'<p class="error">' + error + '</p>' if error else ''}
      </form>
    </body>
    </html>
    """


@app.route("/pair")
@require_auth
def pair_page():
    return render_template("pair.html")


@app.route("/debug")
@require_auth
def debug_page():
    return render_template("debug.html")


@app.route("/draw")
@require_auth
def draw_page():
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
@require_auth
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


@app.route("/api/cards/create", methods=["POST"])
@require_auth
def api_cards_create():
    body = request.get_json(silent=True) or {}
    device_token = body.get("device_token")
    front_image = body.get("front_image")
    back_image = body.get("back_image")

    if not device_token:
        return jsonify({"error": "Missing device_token"}), 400

    pairing = find_pairing_by_token(device_token)
    if not pairing:
        return jsonify({"error": "Invalid or unpaired device_token"}), 401

    if not front_image or not back_image:
        return jsonify({"error": "Missing front_image or back_image"}), 400

    card = {
        "card_id": secrets.token_hex(12),
        "front_image": front_image,
        "back_image": back_image,
        "created_at": time.time(),
    }

    cards_by_token.setdefault(device_token, []).append(card)

    return jsonify({"status": "queued", "card_id": card["card_id"]})


@app.route("/api/cards/poll")
def api_cards_poll():
    device_token = request.args.get("device_token")

    if not device_token:
        return jsonify({"error": "Missing device_token"}), 400

    pairing = find_pairing_by_token(device_token)
    if not pairing:
        return jsonify({"error": "Invalid or unpaired device_token"}), 401

    pending = cards_by_token.pop(device_token, [])
    return jsonify({"cards": pending})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
