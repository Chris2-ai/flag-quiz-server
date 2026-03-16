"""
Flag Quiz - Leaderboard Server
================================
Flask + SQLite backend. Deploy anywhere, free.

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
  1. Put server.py + requirements.txt in a GitHub repo
  2. render.com -> New Web Service -> connect repo
  3. Build command:  pip install -r requirements.txt
  4. Start command:  gunicorn server:app
  5. Add env var:    API_KEY = something_secret
  6. Your URL:       https://<your-service>.onrender.com

Run locally
-----------
  pip install flask
  python server.py
  -> http://localhost:5050
"""

import os, sqlite3, datetime
from flask import Flask, request, jsonify, g

app     = Flask(__name__)
DB_PATH = os.environ.get("DB_PATH", "quiz_scores.db")
API_KEY = os.environ.get("API_KEY", "changeme")


# ---------------------------------------------------------------------------
#  Database helpers
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                player  TEXT    NOT NULL,
                mode    TEXT    NOT NULL,
                score   INTEGER NOT NULL,
                total   INTEGER NOT NULL,
                pct     INTEGER NOT NULL,
                date    TEXT    NOT NULL,
                created TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_mode ON scores(mode)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_pct  ON scores(pct DESC)")
        db.commit()


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
    db    = get_db()
    if mode:
        rows = db.execute(
            "SELECT * FROM scores WHERE mode=? ORDER BY pct DESC, score DESC LIMIT ?",
            (mode, limit)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM scores ORDER BY pct DESC, score DESC LIMIT ?",
            (limit,)).fetchall()
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

    db  = get_db()
    cur = db.execute(
        "INSERT INTO scores (player,mode,score,total,pct,date) VALUES (?,?,?,?,?,?)",
        (player, mode, score, total, pct, date))
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid}), 201


@app.route("/stats", methods=["GET"])
def get_stats():
    db  = get_db()
    top = db.execute(
        "SELECT player,pct,score,total,mode FROM scores ORDER BY pct DESC, score DESC LIMIT 1"
    ).fetchone()
    return jsonify({
        "total_games":    db.execute("SELECT COUNT(*) FROM scores").fetchone()[0],
        "unique_players": db.execute("SELECT COUNT(DISTINCT player) FROM scores").fetchone()[0],
        "top":            dict(top) if top else None,
    })


@app.route("/scores/clear", methods=["DELETE"])
def clear_scores():
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    db = get_db()
    db.execute("DELETE FROM scores")
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------

# init the DB whether we're running via gunicorn or directly
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"[Flag Quiz Server] http://0.0.0.0:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)