# 🌍 World Flag & Map Quiz

A geography quiz app I built with some friends to test our knowledge of world flags and maps.
Supports 4 game modes and a live multiplayer leaderboard backed by a server on Render.

---

## Game Modes

| Mode | Description |
|---|---|
| **Flag Quiz** | A flag appears -- type the country name. Pick your region and question count. |
| **All 197 Countries** | Every sovereign nation shown once. No repeats, no mercy. |
| **Map Fill -- Type Countries** | Type country names to fill their outlines on the world map. |
| **Map Fill -- Flag Hints** | Same as above but a flag is shown as a hint for each country. |

---

## Getting Started

**1. Install dependencies**
```
pip install pycountry pillow requests
```

**2. Run the quiz**
```
python flag_quiz.py
```

**3. Connect to the leaderboard server**

Paste this URL into the **Server URL** field on the main menu and click **Connect**:
```
https://flag-quiz-server-hafq.onrender.com
```

Once connected, scores post automatically after every game and the leaderboard
updates live for everyone.

> **Note:** The server is on Render's free tier so it sleeps after 15 minutes of
> inactivity. The first connection after a long gap may take 30–60 seconds to wake up.
> Just wait and it'll come back on its own.

---

## Sharing With Friends

1. Send them `flag_quiz.py`
2. They install the dependencies (`pip install pycountry pillow requests`)
3. They run the file and paste the server URL above
4. That's it -- everyone's scores show up on the same leaderboard in real time

No accounts, no setup, no LAN required. Works from anywhere.

---

## Project Structure

```
flag_quiz.py       -- the quiz app (runs locally on each player's machine)
server.py          -- Flask + SQLite leaderboard backend (runs on Render)
requirements.txt   -- server dependencies (flask, gunicorn)
quiz_scores.json   -- local score backup (auto-created next to flag_quiz.py)
```

---

## Leaderboard Server

The server is a lightweight Flask + SQLite app deployed on Render.

### API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/ping` | Health check -- returns `{"status": "ok"}` |
| GET | `/scores` | Full leaderboard, sorted by % desc |
| GET | `/scores?mode=Flag+Quiz` | Filtered by game mode |
| POST | `/score` | Submit a score |
| GET | `/stats` | Total games, unique players, top scorer |
| DELETE | `/scores/clear` | Wipe all scores (requires `X-API-Key` header) |

### Deploy your own instance

If you want to host your own server:

1. Fork this repo
2. Go to [render.com](https://render.com) → **New Web Service** → connect the repo
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn server:app`
5. Add environment variable: `API_KEY = something_secret`
6. Deploy -- your URL will be `https://<your-service>.onrender.com`
7. Paste that URL into the quiz and connect

### Running locally

```
pip install flask
python server.py
```
Server runs at `http://localhost:5050`

---

## Notes

- Scores are always saved locally to `quiz_scores.json` as a backup, even when connected to the server
- Recycled/retry runs are not saved to the leaderboard
- The leaderboard auto-refreshes every 5 seconds while open
- 197 sovereign nations are included -- no territories or dependencies
