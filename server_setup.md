# Flag Quiz – Leaderboard Server

Backend server for the World Flag & Map Quiz app.  
Built with Flask + SQLite. Free to host on Render.

---

## Connect to the live server

Paste this URL into the quiz's **Server URL** field and click **Connect**:

```
https://flag-quiz-server.onrender.com
```

> **Note:** The free server sleeps after 15 minutes of inactivity.
> The first connection after a long gap may take 30–60 seconds to wake up.

---

## Run the quiz

1. Download `flag_quiz.py`
2. Install dependencies:
   ```
   pip install pycountry pillow requests
   ```
3. Run it:
   ```
   python flag_quiz.py
   ```
4. Paste the server URL above into the **Server URL** bar and click **Connect**

---

## Deploy your own server (Render – free)

Follow these steps if you want to host your own instance.

### 1. Fork or clone this repo

Click **Fork** at the top right of this GitHub page, or clone it:
```
git clone https://github.com/Chris2-ai/flag-quiz-server.git
```

### 2. Create a Render account

Go to [render.com](https://render.com) and sign up (free).  
You can sign up directly with your GitHub account.

### 3. Create a new Web Service

1. Click **New +** → **Web Service**
2. Click **Connect GitHub** and authorize Render
3. Select this repository from the list
4. Fill in the configuration:

| Field | Value |
|---|---|
| Language | Python 3 |
| Branch | main |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn server:app` |
| Instance Type | Free |

### 4. Add an environment variable

Scroll to **Environment Variables** and add:

| Key | Value |
|---|---|
| `API_KEY` | anything secret, e.g. `flagquiz2024` |

### 5. Deploy

Click **Deploy Web Service**.  
Wait ~2 minutes for the build to finish.  
Your URL will appear at the top of the page:
```
https://your-service-name.onrender.com
```

Share that URL with anyone who has `flag_quiz.py` and they can connect.

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/ping` | Health check |
| GET | `/scores` | Full leaderboard |
| GET | `/scores?mode=Flag+Quiz` | Filtered by mode |
| POST | `/score` | Submit a score |
| GET | `/stats` | Total games, players, top score |
| DELETE | `/scores/clear` | Wipe all scores (requires API key) |