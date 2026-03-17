"""
Flag Quiz - Leaderboard Server  (v2 -- with user accounts)
============================================================
Flask + PostgreSQL backend.

Endpoints
---------
  GET  /ping                  -> {"status": "ok"}

  -- Auth --
  POST /register              -> {username, password} -> {token, username}
  POST /login                 -> {username, password} -> {token, username}
  GET  /me                    -> Authorization: Bearer <token> -> {username, user_id}

  -- Scores --
  POST /score                 -> submit score (token optional -- links to account if present)
  GET  /scores                -> full leaderboard, all attempts, sorted by % desc
  GET  /scores?mode=Flag+Quiz -> filtered by mode
  GET  /profile/<username>    -> full attempt history for a specific user
  GET  /stats                 -> total games, unique players, top score
  DELETE /scores/clear        -> wipe all scores (requires X-API-Key header)

Deploy on Render
-----------------
  1. Create a free PostgreSQL DB on Render, copy the Internal Database URL
  2. Add env var DATABASE_URL on your web service
  3. Add env var API_KEY (for the clear endpoint)
  4. Push -- Render redeploys automatically

Run locally
-----------
  pip install flask psycopg2-binary
  set DATABASE_URL=postgresql://user:pass@host/dbname
  python server.py
"""

import os, datetime, secrets
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

app     = Flask(__name__)
API_KEY = os.environ.get("API_KEY", "changeme")
DB_URL  = os.environ.get("DATABASE_URL", "")

# Render sometimes gives a legacy postgres:// URL -- psycopg2 needs postgresql://
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

# Session tokens expire after 30 days
SESSION_DAYS = 30


# ---------------------------------------------------------------------------
#  Database connection -- one per request, closed on teardown
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DB_URL)
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    """
    Create all tables if they don't exist.
    Safe to run on every startup -- IF NOT EXISTS means it's idempotent.
    Also handles the migration from the old scores schema (no user_id column).
    """
    with app.app_context():
        conn = get_db()
        cur  = conn.cursor()

        # users -- stores credentials
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created       TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)

        # sessions -- one row per active login token
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token    TEXT PRIMARY KEY,
                user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created  TIMESTAMP NOT NULL DEFAULT NOW(),
                expires  TIMESTAMP NOT NULL
            )
        """)

        # scores -- user_id is nullable so guest scores are still accepted
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id      SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                player  TEXT    NOT NULL,
                mode    TEXT    NOT NULL,
                score   INTEGER NOT NULL,
                total   INTEGER NOT NULL,
                pct     INTEGER NOT NULL,
                date    TEXT    NOT NULL,
                created TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)

        # migration: add user_id column if it was created without it
        cur.execute("""
            ALTER TABLE scores
            ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        """)

        cur.execute("CREATE INDEX IF NOT EXISTS idx_mode    ON scores(mode)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pct     ON scores(pct DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON scores(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sess_expires ON sessions(expires)")

        conn.commit()
        cur.close()


# ---------------------------------------------------------------------------
#  Auth helpers
# ---------------------------------------------------------------------------

def get_token_from_request():
    """Pull the Bearer token out of the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None

def get_user_from_token(token):
    """
    Look up a session token and return {id, username} if it's valid and not expired.
    Returns None if the token is missing, invalid, or expired.
    """
    if not token:
        return None
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT u.id, u.username
        FROM users u
        JOIN sessions s ON s.user_id = u.id
        WHERE s.token = %s AND s.expires > NOW()
    """, (token,))
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else None

def create_session(user_id):
    """Generate a new session token for a user and store it in the DB."""
    token   = secrets.token_urlsafe(32)
    expires = datetime.datetime.utcnow() + datetime.timedelta(days=SESSION_DAYS)
    conn    = get_db()
    cur     = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (token, user_id, expires)
        VALUES (%s, %s, %s)
    """, (token, user_id, expires))
    conn.commit()
    cur.close()
    return token


# ---------------------------------------------------------------------------
#  CORS
# ---------------------------------------------------------------------------

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>",             methods=["OPTIONS"])
def preflight(path):
    return "", 204


# ---------------------------------------------------------------------------
#  Auth endpoints
# ---------------------------------------------------------------------------

@app.route("/register", methods=["POST"])
def register():
    data     = request.get_json(force=True, silent=True) or {}
    username = str(data.get("username", "")).strip()[:40]
    password = str(data.get("password", "")).strip()

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400

    conn = get_db()
    cur  = conn.cursor()

    # check if username is already taken
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    if cur.fetchone():
        cur.close()
        return jsonify({"error": "Username already taken"}), 409

    pw_hash = generate_password_hash(password)
    cur.execute("""
        INSERT INTO users (username, password_hash)
        VALUES (%s, %s)
        RETURNING id
    """, (username, pw_hash))
    user_id = cur.fetchone()[0]
    conn.commit()
    cur.close()

    token = create_session(user_id)
    return jsonify({"token": token, "username": username}), 201


@app.route("/login", methods=["POST"])
def login():
    data     = request.get_json(force=True, silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", "")).strip()

    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
    user = cur.fetchone()
    cur.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Invalid username or password"}), 401

    token = create_session(user["id"])
    return jsonify({"token": token, "username": username})


@app.route("/me")
def get_me():
    token = get_token_from_request()
    user  = get_user_from_token(token)
    if not user:
        return jsonify({"error": "Invalid or expired token -- please log in again"}), 401
    return jsonify({"username": user["username"], "user_id": user["id"]})


# ---------------------------------------------------------------------------
#  Score endpoints
# ---------------------------------------------------------------------------

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": 2})


@app.route("/scores", methods=["GET"])
def get_scores():
    mode  = request.args.get("mode")
    limit = min(int(request.args.get("limit", 200)), 500)
    conn  = get_db()
    cur   = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if mode:
        cur.execute("""
            SELECT s.id, s.player, s.mode, s.score, s.total, s.pct, s.date,
                   u.username
            FROM scores s
            LEFT JOIN users u ON u.id = s.user_id
            WHERE s.mode = %s
            ORDER BY s.pct DESC, s.score DESC
            LIMIT %s
        """, (mode, limit))
    else:
        cur.execute("""
            SELECT s.id, s.player, s.mode, s.score, s.total, s.pct, s.date,
                   u.username
            FROM scores s
            LEFT JOIN users u ON u.id = s.user_id
            ORDER BY s.pct DESC, s.score DESC
            LIMIT %s
        """, (limit,))

    rows = cur.fetchall()
    cur.close()
    return jsonify([dict(r) for r in rows])


@app.route("/score", methods=["POST"])
def post_score():
    data    = request.get_json(force=True, silent=True) or {}
    missing = [k for k in ("player", "mode", "score", "total") if k not in data]
    if missing:
        return jsonify({"error": f"Missing: {missing}"}), 400

    player  = str(data["player"]).strip()[:80] or "Anonymous"
    mode    = str(data["mode"]).strip()[:60]
    score   = int(data["score"])
    total   = int(data["total"])
    pct     = round(score / total * 100) if total else 0
    date    = str(data.get("date") or datetime.date.today().isoformat())

    # link to user account if a valid token was provided
    token   = get_token_from_request()
    user    = get_user_from_token(token)
    user_id = user["id"] if user else None

    # if logged in, always use the account username so the leaderboard is consistent
    if user:
        player = user["username"]

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO scores (user_id, player, mode, score, total, pct, date)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (user_id, player, mode, score, total, pct, date))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/profile/<username>", methods=["GET"])
def get_profile(username):
    """Full attempt history for a specific user, newest first."""
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT s.id, s.player, s.mode, s.score, s.total, s.pct, s.date, s.created
        FROM scores s
        JOIN users u ON u.id = s.user_id
        WHERE u.username = %s
        ORDER BY s.created DESC
    """, (username,))
    rows = cur.fetchall()
    cur.close()

    # also pull a quick summary per mode (best score per mode)
    result = [dict(r) for r in rows]
    return jsonify(result)


@app.route("/stats", methods=["GET"])
def get_stats():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS total FROM scores")
    total_games = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS total FROM users")
    total_users = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(DISTINCT player) AS total FROM scores")
    unique_players = cur.fetchone()["total"]

    cur.execute("""
        SELECT s.player, s.pct, s.score, s.total, s.mode
        FROM scores s
        ORDER BY s.pct DESC, s.score DESC
        LIMIT 1
    """)
    top = cur.fetchone()
    cur.close()

    return jsonify({
        "total_games":    total_games,
        "total_users":    total_users,
        "unique_players": unique_players,
        "top":            dict(top) if top else None,
    })


@app.route("/scores/clear", methods=["DELETE"])
def clear_scores():
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("DELETE FROM scores")
    conn.commit()
    cur.close()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# init on startup -- works for both gunicorn and direct python runs
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"[Flag Quiz Server] http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
