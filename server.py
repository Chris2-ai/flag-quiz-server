"""
Flag Quiz - Leaderboard Server
================================
Flask + PostgreSQL backend. Deploy anywhere, free.

Endpoints
---------
  GET  /ping                  -> {"status": "ok"}
  GET  /scores                -> top 200 scores sorted by % desc
  GET  /scores?mode=Flag+Quiz -> filtered by mode
  POST /score                 -> submit { player, mode, score, total }
  GET  /stats                 -> total games, unique players, top score
  DELETE /scores/clear        -> wipe all (requires X-API-Key header)

Deploy free on Render.com
--------------------------
  1. Create a free PostgreSQL database on Render
  2. Copy the Internal Database URL
  3. Add it as env var DATABASE_URL on your web service
  4. Render injects it automatically at runtime -- no hardcoding needed

Run locally
-----------
  pip install flask psycopg2-binary
  set DATABASE_URL=postgresql://user:pass@host/dbname
  python server.py
  -> http://localhost:5050
"""

import os, datetime
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, g

app     = Flask(__name__)
API_KEY = os.environ.get("API_KEY", "changeme")
DB_URL  = os.environ.get("DATABASE_URL", "")

# Render sometimes provides a postgres:// URL (legacy format)
# psycopg2 requires postgresql:// so we fix it here
if DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)


# ---------------------------------------------------------------------------
#  Database connection
#  One connection per request, closed when the request context tears down.
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
    """Create the scores table and indexes if they don't exist yet."""
    with app.app_context():
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id      SERIAL PRIMARY KEY,
                player  TEXT        NOT NULL,
                mode    TEXT        NOT NULL,
                score   INTEGER     NOT NULL,
                total   INTEGER     NOT NULL,
                pct     INTEGER     NOT NULL,
                date    TEXT        NOT NULL,
                created TIMESTAMP   NOT NULL DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_mode ON scores(mode)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pct  ON scores(pct DESC)")
        conn.commit()
        cur.close()


# ---------------------------------------------------------------------------
#  CORS  (let the desktop client reach the server from any origin)
# ---------------------------------------------------------------------------

@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>",             methods=["OPTIONS"])
def preflight(path):
    return "", 204


# ---------------------------------------------------------------------------
#  Routes
# ---------------------------------------------------------------------------

@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "version": 1})


@app.route("/scores", methods=["GET"])
def get_scores():
    mode  = request.args.get("mode")
    limit = min(int(request.args.get("limit", 200)), 500)
    conn  = get_db()
    cur   = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if mode:
        cur.execute("""
            SELECT id, player, mode, score, total, pct, date
            FROM scores
            WHERE mode = %s
            ORDER BY pct DESC, score DESC
            LIMIT %s
        """, (mode, limit))
    else:
        cur.execute("""
            SELECT id, player, mode, score, total, pct, date
            FROM scores
            ORDER BY pct DESC, score DESC
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

    player = str(data["player"]).strip()[:80] or "Anonymous"
    mode   = str(data["mode"]).strip()[:60]
    score  = int(data["score"])
    total  = int(data["total"])
    pct    = round(score / total * 100) if total else 0
    date   = str(data.get("date") or datetime.date.today().isoformat())

    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO scores (player, mode, score, total, pct, date)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (player, mode, score, total, pct, date))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/stats", methods=["GET"])
def get_stats():
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT COUNT(*) AS total FROM scores")
    total_games = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(DISTINCT player) AS total FROM scores")
    unique_players = cur.fetchone()["total"]

    cur.execute("""
        SELECT player, pct, score, total, mode
        FROM scores
        ORDER BY pct DESC, score DESC
        LIMIT 1
    """)
    top = cur.fetchone()
    cur.close()

    return jsonify({
        "total_games":    total_games,
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
# init the DB on startup -- works for both gunicorn and direct python runs
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"[Flag Quiz Server] http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
