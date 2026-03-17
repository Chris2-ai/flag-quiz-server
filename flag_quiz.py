"""
World Flag & Map Quiz  --  v8  (User Accounts)
================================================
Same 4 game modes, now with proper user accounts.

Account flow:
  - Connect to the server URL on the main menu
  - Register once, log in on return visits
  - Scores are linked to your account so your full history is tracked
  - Guest play is still available -- scores just stay local

Dependencies:
  pip install pycountry pillow requests
"""

import json, os, random, difflib, threading, urllib.request, urllib.parse
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from io import BytesIO


# ---------------------------------------------------------------------------
# HTTP layer -- prefers requests, falls back to stdlib urllib
# Both functions accept an optional headers dict for auth tokens etc.
# ---------------------------------------------------------------------------
try:
    import requests as _req

    def fetch_bytes(url, timeout=14, headers=None):
        r = _req.get(url, timeout=timeout, headers=headers or {})
        r.raise_for_status()
        return r.content

    def post_json(url, payload, timeout=8, headers=None):
        r = _req.post(url, json=payload, timeout=timeout, headers=headers or {})
        r.raise_for_status()
        return r.content

except ImportError:
    def fetch_bytes(url, timeout=14, headers=None):
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

    def post_json(url, payload, timeout=8, headers=None):
        body        = json.dumps(payload).encode()
        all_headers = {"Content-Type": "application/json"}
        if headers:
            all_headers.update(headers)
        req = urllib.request.Request(
            url, data=body, headers=all_headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()

try:
    from PIL import Image, ImageTk
except ImportError:
    raise SystemExit("Missing dependency -- run:  pip install pillow")

try:
    import pycountry
except ImportError:
    raise SystemExit("Missing dependency -- run:  pip install pycountry")


# ---------------------------------------------------------------------------
# RemoteClient -- talks to the Flask leaderboard server
# All calls are best-effort -- network errors never crash or block the quiz
# ---------------------------------------------------------------------------

class RemoteClient:

    def __init__(self, base_url: str):
        url = base_url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.base = url

    def _auth_header(self, token):
        return {"Authorization": f"Bearer {token}"} if token else {}

    def ping(self, timeout=6) -> bool:
        if not self.base:
            return False
        try:
            data = json.loads(fetch_bytes(f"{self.base}/ping", timeout=timeout))
            return data.get("status") == "ok"
        except Exception:
            return False

    def register(self, username, password):
        """Returns (token, error_msg). token is None on failure."""
        try:
            data = json.loads(post_json(
                f"{self.base}/register",
                {"username": username, "password": password}))
            if "token" in data:
                return data["token"], None
            return None, data.get("error", "Registration failed")
        except Exception as exc:
            msg = str(exc)
            # pull out the server's error message if we can
            if hasattr(exc, "read"):
                try:
                    msg = json.loads(exc.read()).get("error", msg)
                except Exception:
                    pass
            return None, msg

    def login(self, username, password):
        """Returns (token, error_msg). token is None on failure."""
        try:
            data = json.loads(post_json(
                f"{self.base}/login",
                {"username": username, "password": password}))
            if "token" in data:
                return data["token"], None
            return None, data.get("error", "Login failed")
        except Exception as exc:
            msg = str(exc)
            if hasattr(exc, "read"):
                try:
                    msg = json.loads(exc.read()).get("error", msg)
                except Exception:
                    pass
            return None, msg

    def verify_token(self, token):
        """Returns username if the token is still valid, None if expired/invalid."""
        if not self.base or not token:
            return None
        try:
            data = json.loads(fetch_bytes(
                f"{self.base}/me",
                headers=self._auth_header(token)))
            return data.get("username")
        except Exception:
            return None

    def post_score(self, player, mode, score, total, token=None):
        if not self.base:
            return
        pct   = round(score / total * 100) if total else 0
        entry = {
            "player": player, "mode": mode,
            "score":  score,  "total": total,
            "pct":    pct,
            "date":   datetime.date.today().isoformat(),
        }
        try:
            post_json(f"{self.base}/score", entry,
                      headers=self._auth_header(token))
        except Exception as exc:
            print(f"[RemoteClient] post_score failed: {exc}")

    def get_scores(self, mode=None):
        """Returns list[dict] or None on error."""
        if not self.base:
            return None
        url = f"{self.base}/scores"
        if mode:
            url += f"?mode={urllib.parse.quote(mode)}"
        try:
            return json.loads(fetch_bytes(url, timeout=8))
        except Exception:
            return None

    def get_profile(self, username):
        """Returns the full attempt history for a user, or None on error."""
        if not self.base:
            return None
        try:
            return json.loads(fetch_bytes(
                f"{self.base}/profile/{urllib.parse.quote(username)}", timeout=8))
        except Exception:
            return None

    def get_stats(self):
        if not self.base:
            return None
        try:
            return json.loads(fetch_bytes(f"{self.base}/stats", timeout=8))
        except Exception:
            return None


# ---------------------------------------------------------------------------
# ScoreDB -- local JSON backup + persisted settings
# Stores scores, last player name, server URL, and session token
# ---------------------------------------------------------------------------

_SCORE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "quiz_scores.json")


class ScoreDB:

    MODE_FLAG    = "Flag Quiz"
    MODE_ALL     = "All 197 Countries"
    MODE_MAP     = "Map Fill"
    MODE_MAPFLAG = "Map Fill + Flags"

    def __init__(self):
        self._data = self._load()
        self._lock = threading.Lock()

    def _load(self):
        if os.path.exists(_SCORE_FILE):
            try:
                with open(_SCORE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_player": "", "server_url": "", "token": "",
                "logged_in_user": "", "scores": []}

    def _save(self):
        try:
            with open(_SCORE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ScoreDB] could not save: {e}")

    # -- persisted settings ------------------------------------------------

    @property
    def last_player(self):
        return self._data.get("last_player", "")

    @last_player.setter
    def last_player(self, v):
        with self._lock:
            self._data["last_player"] = v.strip()
            self._save()

    @property
    def server_url(self):
        return self._data.get("server_url", "")

    @server_url.setter
    def server_url(self, v):
        with self._lock:
            self._data["server_url"] = v.strip()
            self._save()

    @property
    def token(self):
        return self._data.get("token", "")

    @token.setter
    def token(self, v):
        with self._lock:
            self._data["token"] = v
            self._save()

    @property
    def logged_in_user(self):
        return self._data.get("logged_in_user", "")

    @logged_in_user.setter
    def logged_in_user(self, v):
        with self._lock:
            self._data["logged_in_user"] = v
            self._save()

    # -- score operations --------------------------------------------------

    def add(self, player, mode, score, total):
        player = (player or "Anonymous").strip()
        pct    = round(score / total * 100) if total else 0
        entry  = {
            "player": player, "mode": mode,
            "score":  score,  "total": total,
            "pct":    pct,
            "date":   datetime.date.today().isoformat(),
        }
        with self._lock:
            self._data.setdefault("scores", []).append(entry)
            self._data["last_player"] = player
            self._save()
        return entry

    def scores(self, mode=None):
        rows = list(self._data.get("scores", []))
        if mode:
            rows = [r for r in rows if r["mode"] == mode]
        return list(reversed(rows))

    def top_scores(self, mode=None, n=200):
        rows = self.scores(mode)
        rows.sort(key=lambda r: (-r["pct"], -r["score"]))
        return rows[:n]

    def clear(self):
        with self._lock:
            self._data["scores"] = []
            self._save()


# ---------------------------------------------------------------------------
# Country data
# ---------------------------------------------------------------------------

SOVEREIGN = {
    "af","al","dz","ad","ao","ag","ar","am","au","at","az",
    "bs","bh","bd","bb","by","be","bz","bj","bt","bo","ba","bw","br","bn","bg","bf","bi",
    "cv","kh","cm","ca","cf","td","cl","cn","co","km","cg","cr","hr","cu","cy","cz",
    "cd","dk","dj","dm","do","ec","eg","sv","gq","er","ee","sz","et",
    "fj","fi","fr","ga","gm","ge","de","gh","gr","gd","gt","gn","gw","gy",
    "ht","hn","hu","is","in","id","ir","iq","ie","il","it",
    "jm","jp","jo","kz","ke","ki","kp","kr","kw","kg",
    "la","lv","lb","ls","lr","ly","li","lt","lu",
    "mg","mw","my","mv","ml","mt","mh","mr","mu","mx","fm","md","mc","mn","me","ma","mz","mm",
    "na","nr","np","nl","nz","ni","ne","ng","mk","no","om",
    "pk","pw","ps","pa","pg","py","pe","ph","pl","pt","qa",
    "ro","ru","rw","kn","lc","vc","ws","sm","st","sa","sn","rs","sc","sl","sg","sk","si",
    "sb","so","za","ss","es","lk","sd","sr","se","ch","sy",
    "tw","tj","tz","th","tl","tg","to","tt","tn","tr","tm","tv",
    "ug","ua","ae","gb","us","uy","uz","vu","va","ve","vn","ye","zm","zw","xk",
}

def _build_countries():
    seen, result = set(), []
    for c in pycountry.countries:
        code = c.alpha_2.lower()
        if code in SOVEREIGN and code not in seen:
            result.append((c.name, code))
            seen.add(code)
    if "xk" not in seen:
        result.append(("Kosovo", "xk"))
    return sorted(result, key=lambda x: x[0])

ALL_COUNTRIES = _build_countries()

DISPLAY = {
    "Iran, Islamic Republic of":               "Iran",
    "Korea, Democratic People's Republic of":  "North Korea",
    "Korea, Republic of":                      "South Korea",
    "Syrian Arab Republic":                    "Syria",
    "Taiwan, Province of China":               "Taiwan",
    "Bolivia, Plurinational State of":         "Bolivia",
    "Tanzania, United Republic of":            "Tanzania",
    "Venezuela, Bolivarian Republic of":       "Venezuela",
    "Moldova, Republic of":                    "Moldova",
    "Lao People's Democratic Republic":        "Laos",
    "Viet Nam":                                "Vietnam",
    "Russian Federation":                      "Russia",
    "Congo, The Democratic Republic of the":   "DR Congo",
    "Micronesia, Federated States of":         "Micronesia",
    "Palestine, State of":                     "Palestine",
    "Timor-Leste":                             "East Timor",
    "Brunei Darussalam":                       "Brunei",
    "Cabo Verde":                              "Cape Verde",
    "Cote d'Ivoire":                           "Ivory Coast",
    "Sao Tome and Principe":                   "Sao Tome & Principe",
    "Turkiye":                                 "Turkey",
}

def disp(name):
    return DISPLAY.get(name, name)

def _build_aliases():
    d = {
        "uae":"United Arab Emirates","uk":"United Kingdom","usa":"United States",
        "drc":"Congo, The Democratic Republic of the","car":"Central African Republic",
        "roc":"Taiwan, Province of China","prc":"China","png":"Papua New Guinea",
        "ksa":"Saudi Arabia","rok":"Korea, Republic of",
        "dprk":"Korea, Democratic People's Republic of","rsa":"South Africa",
        "north korea":"Korea, Democratic People's Republic of",
        "south korea":"Korea, Republic of","north macedonia":"North Macedonia",
        "south africa":"South Africa","turkey":"Turkiye","turkiye":"Turkiye",
        "ivory coast":"Cote d'Ivoire","cote divoire":"Cote d'Ivoire",
        "czech republic":"Czechia","czechia":"Czechia",
        "russia":"Russian Federation","iran":"Iran, Islamic Republic of",
        "syria":"Syrian Arab Republic","taiwan":"Taiwan, Province of China",
        "bolivia":"Bolivia, Plurinational State of",
        "tanzania":"Tanzania, United Republic of",
        "venezuela":"Venezuela, Bolivarian Republic of",
        "moldova":"Moldova, Republic of","laos":"Lao People's Democratic Republic",
        "vietnam":"Viet Nam","east timor":"Timor-Leste","cape verde":"Cabo Verde",
        "swaziland":"Eswatini","burma":"Myanmar","trinidad":"Trinidad and Tobago",
        "micronesia":"Micronesia, Federated States of","brunei":"Brunei Darussalam",
        "palestine":"Palestine, State of","kosovo":"Kosovo",
        "macedonia":"North Macedonia","netherlands":"Netherlands",
        "holland":"Netherlands","south sudan":"South Sudan",
        "antigua":"Antigua and Barbuda","dr congo":"Congo, The Democratic Republic of the",
        "democratic republic of the congo":"Congo, The Democratic Republic of the",
    }
    for official, friendly in DISPLAY.items():
        d[friendly.lower()] = official
    return d

ALIASES = _build_aliases()

def resolve(raw_input):
    key = raw_input.strip().lower()
    if key in ALIASES:
        return ALIASES[key]
    for name, _ in ALL_COUNTRIES:
        if name.lower() == key:
            return name
    return raw_input

def diff_hint(guess, correct):
    similarity = difflib.SequenceMatcher(None, guess.lower(), correct.lower()).ratio()
    prefix = "Almost! " if similarity > 0.75 else ""
    return f'{prefix}You wrote "{guess}"  ->  correct: "{correct}"'


# ---------------------------------------------------------------------------
# GeoJSON world map
# ---------------------------------------------------------------------------

GEO_URL = ("https://raw.githubusercontent.com/datasets/geo-countries"
           "/master/data/countries.geojson")

def _build_alpha3_lookup():
    lookup = {}
    for c in pycountry.countries:
        if hasattr(c, "alpha_3"):
            lookup[c.alpha_3.upper()] = c.alpha_2.lower()
    lookup.update({"XKX": "xk", "PSE": "ps", "TWN": "tw"})
    return lookup

A3_TO_A2 = _build_alpha3_lookup()

ADMIN_MAP = {
    "france":"fr","norway":"no","somaliland":None,"northern cyprus":None,
    "kosovo":"xk","united states of america":"us","united kingdom":"gb",
    "russia":"ru","south korea":"kr","north korea":"kp","taiwan":"tw",
    "iran":"ir","syria":"sy","laos":"la","vietnam":"vn","bolivia":"bo",
    "moldova":"md","venezuela":"ve","tanzania":"tz",
    "democratic republic of the congo":"cd","republic of the congo":"cg",
    "czech republic":"cz","ivory coast":"ci","east timor":"tl",
    "swaziland":"sz","burma":"mm","cape verde":"cv","palestine":"ps",
    "western sahara":None,"falkland islands":None,
}
for _c in pycountry.countries:
    _a2 = _c.alpha_2.lower()
    if _a2 in SOVEREIGN:
        ADMIN_MAP[_c.name.lower()] = _a2
        if hasattr(_c, "common_name"):
            ADMIN_MAP[_c.common_name.lower()] = _a2

_geo_cache = None
_geo_lock  = threading.Lock()

def load_geojson(on_progress=None):
    global _geo_cache
    with _geo_lock:
        if _geo_cache is not None:
            return _geo_cache
        if on_progress:
            on_progress("Downloading map data...")
        raw = fetch_bytes(GEO_URL, timeout=40)
        if on_progress:
            on_progress("Parsing shapes...")
        gj  = json.loads(raw)
        out = {}
        for feat in gj["features"]:
            props = feat.get("properties", {})
            a3 = (props.get("ISO_A3") or props.get("iso_a3") or "").strip().upper()
            a2 = A3_TO_A2.get(a3, "") if a3 and a3 != "-99" else ""
            if not a2 or a2 not in SOVEREIGN:
                admin = (props.get("ADMIN") or props.get("NAME") or "").strip().lower()
                a2 = ADMIN_MAP.get(admin, "")
            if not a2 or a2 not in SOVEREIGN:
                a2 = (props.get("ISO_A2") or props.get("iso_a2") or "").strip().lower()
            if not a2 or a2 not in SOVEREIGN:
                continue
            geom   = feat.get("geometry", {})
            gtype  = geom.get("type", "")
            coords = geom.get("coordinates", [])
            rings  = []
            if gtype == "Polygon" and coords:
                rings = [coords[0]]
            elif gtype == "MultiPolygon":
                rings = [p[0] for p in coords if p]
            if rings:
                out.setdefault(a2, []).extend(rings)
        _geo_cache = out
        if on_progress:
            on_progress(f"Map ready -- {len(out)} countries loaded")
        return out

def project_ring(ring, canvas_w, canvas_h, pad=8):
    pts = []
    for lon, lat in ring:
        pts.extend([
            pad + (lon + 180) / 360 * (canvas_w - 2 * pad),
            pad + (90 - lat)  / 180 * (canvas_h - 2 * pad),
        ])
    return pts


# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
BG         = "#0f172a"
CARD       = "#1e293b"
ACCENT     = "#1e3a5f"
HIGHLIGHT  = "#e94560"
TEXT       = "#f1f5f9"
MUTED      = "#64748b"
SUCCESS    = "#22c55e"
ERROR      = "#ef4444"
WARNING    = "#f59e0b"
INFO       = "#3b82f6"
GOLD       = "#fbbf24"
SILVER     = "#94a3b8"
BRONZE     = "#cd7f32"
MAP_SEA    = "#0d2137"
MAP_LAND   = "#2d4a6e"
MAP_GRID   = "#1a3050"
MAP_BORDER = "#1e3a5f"
MAP_FOUND  = "#15803d"
MAP_FOUND2 = "#166534"
MAP_MISS   = "#991b1b"
MAP_MISS2  = "#7f1d1d"


def apply_styles():
    s = ttk.Style()
    try:
        s.theme_use("clam")
    except Exception:
        pass
    s.configure("Treeview",
                background=CARD, foreground=TEXT, fieldbackground=CARD,
                rowheight=26, borderwidth=0, relief="flat")
    s.configure("Treeview.Heading",
                background=ACCENT, foreground=TEXT,
                font=("Helvetica Neue", 11, "bold"),
                relief="flat", borderwidth=0)
    s.map("Treeview",
          background=[("selected", HIGHLIGHT)],
          foreground=[("selected", "#ffffff")])
    s.map("Treeview.Heading",
          background=[("active", HIGHLIGHT)])


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("World Flag & Map Quiz")
        self.root.configure(bg=BG)
        self.root.geometry("720x900")
        self.root.resizable(True, True)
        apply_styles()
        self._fonts()

        self._geo = None
        self.db   = ScoreDB()

        # auth state
        self._token          = self.db.token or None
        self._logged_in_user = self.db.logged_in_user or None
        self._player         = self._logged_in_user or self.db.last_player or ""

        # remote client -- restored from saved URL
        saved_url    = self.db.server_url
        self._remote = RemoteClient(saved_url) if saved_url else None

        # leaderboard polling
        self._lb_poll_id = None
        self._lb_win     = None

        # if we have a saved token, verify it silently before showing the menu
        if self._remote and self._token:
            self._verify_saved_token()
        else:
            self._menu()

    def _fonts(self):
        self.fT = tkfont.Font(family="Helvetica Neue", size=22, weight="bold")
        self.fS = tkfont.Font(family="Helvetica Neue", size=14)
        self.fB = tkfont.Font(family="Helvetica Neue", size=13)
        self.fX = tkfont.Font(family="Helvetica Neue", size=11)
        self.fK = tkfont.Font(family="Helvetica Neue", size=11, weight="bold")

    def _clear(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.root.unbind("<Return>")
        self._lb_stop_poll()

    # -----------------------------------------------------------------------
    # Startup token verification
    # -----------------------------------------------------------------------

    def _verify_saved_token(self):
        """
        Check our saved token in the background on startup.
        If it's still valid we go straight to the menu as logged in.
        If it expired we clear it and go to menu as guest.
        """
        def _check():
            username = self._remote.verify_token(self._token) if self._remote else None
            def _done():
                if username:
                    self._logged_in_user = username
                    self._player         = username
                else:
                    # token expired -- clear it so the login prompt shows next time
                    self._token          = None
                    self._logged_in_user = None
                    self.db.token        = ""
                    self.db.logged_in_user = ""
                self._menu()
            self.root.after(0, _done)
        threading.Thread(target=_check, daemon=True).start()

    # -----------------------------------------------------------------------
    # Login / Register dialog
    # -----------------------------------------------------------------------

    def _show_auth_dialog(self, initial_tab="login"):
        """
        Modal dialog for login and registration.
        Opens over the main menu so the user doesn't lose their context.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Account")
        dlg.configure(bg=BG)
        dlg.geometry("440x400")
        dlg.resizable(False, False)
        dlg.grab_set()

        tk.Label(dlg, text="Account", font=self.fT, bg=BG, fg=TEXT).pack(pady=(24, 4))

        # tab toggle row
        tab_frame = tk.Frame(dlg, bg=CARD,
                             highlightbackground=ACCENT, highlightthickness=1)
        tab_frame.pack(padx=50, pady=(8, 0), fill="x")

        self._auth_tab = tk.StringVar(value=initial_tab)

        btn_login = tk.Button(tab_frame, text="Log In", font=self.fB,
                              relief="flat", cursor="hand2", padx=20, pady=8)
        btn_reg   = tk.Button(tab_frame, text="Register", font=self.fB,
                              relief="flat", cursor="hand2", padx=20, pady=8)
        btn_login.pack(side="left", fill="x", expand=True)
        btn_reg.pack(side="left",   fill="x", expand=True)

        # form
        form = tk.Frame(dlg, bg=CARD,
                        highlightbackground=ACCENT, highlightthickness=1)
        form.pack(padx=50, pady=0, fill="x")
        form_inner = tk.Frame(form, bg=CARD)
        form_inner.pack(padx=16, pady=16, fill="x")

        tk.Label(form_inner, text="Username", font=self.fX, bg=CARD, fg=MUTED,
                 anchor="w").pack(fill="x")
        username_entry = tk.Entry(form_inner, font=self.fB, bg=ACCENT, fg=TEXT,
                                  insertbackground=TEXT, relief="flat")
        username_entry.pack(fill="x", ipady=6, pady=(2, 10))

        tk.Label(form_inner, text="Password", font=self.fX, bg=CARD, fg=MUTED,
                 anchor="w").pack(fill="x")
        password_entry = tk.Entry(form_inner, font=self.fB, bg=ACCENT, fg=TEXT,
                                  insertbackground=TEXT, relief="flat", show="*")
        password_entry.pack(fill="x", ipady=6, pady=(2, 0))

        # error label
        error_var = tk.StringVar()
        error_lbl = tk.Label(dlg, textvariable=error_var, font=self.fX,
                             bg=BG, fg=ERROR, wraplength=340)
        error_lbl.pack(pady=(8, 0))

        # submit button
        submit_btn = tk.Button(dlg, font=self.fS,
                               bg=HIGHLIGHT, fg="white", relief="flat",
                               cursor="hand2", padx=20, pady=10)
        submit_btn.pack(pady=(8, 4))

        tk.Button(dlg, text="Play as Guest instead",
                  font=self.fX, bg=BG, fg=MUTED, relief="flat",
                  cursor="hand2",
                  command=dlg.destroy).pack()

        # -- tab switching -------------------------------------------------

        def set_tab(tab):
            self._auth_tab.set(tab)
            if tab == "login":
                btn_login.config(bg=HIGHLIGHT, fg="white")
                btn_reg.config(bg=CARD, fg=MUTED)
                submit_btn.config(text="Log In")
            else:
                btn_reg.config(bg=HIGHLIGHT, fg="white")
                btn_login.config(bg=CARD, fg=MUTED)
                submit_btn.config(text="Register")
            error_var.set("")

        btn_login.config(command=lambda: set_tab("login"))
        btn_reg.config(command=lambda: set_tab("register"))
        set_tab(initial_tab)

        # -- submit logic --------------------------------------------------

        def do_submit():
            username = username_entry.get().strip()
            password = password_entry.get().strip()

            if not username or not password:
                error_var.set("Please fill in both fields.")
                return

            submit_btn.config(state="disabled", text="Please wait...")
            error_var.set("")

            def _try():
                if self._auth_tab.get() == "login":
                    token, err = self._remote.login(username, password)
                else:
                    token, err = self._remote.register(username, password)

                def _done():
                    submit_btn.config(state="normal")
                    set_tab(self._auth_tab.get())   # restore button text
                    if token:
                        # success -- save credentials and refresh menu
                        self._token          = token
                        self._logged_in_user = username
                        self._player         = username
                        self.db.token        = token
                        self.db.logged_in_user = username
                        self.db.last_player    = username
                        dlg.destroy()
                        self._menu()
                    else:
                        error_var.set(err or "Something went wrong -- try again.")
                self.root.after(0, _done)
            threading.Thread(target=_try, daemon=True).start()

        submit_btn.config(command=do_submit)
        dlg.bind("<Return>", lambda e: do_submit())
        username_entry.focus_set()

    def _logout(self):
        self._token          = None
        self._logged_in_user = None
        self._player         = self.db.last_player or ""
        self.db.token        = ""
        self.db.logged_in_user = ""
        self._menu()

    # -----------------------------------------------------------------------
    # MENU
    # -----------------------------------------------------------------------

    def _menu(self):
        self._clear()
        self.root.geometry("720x900")

        tk.Label(self.root, text="World Flag & Map Quiz",
                 font=self.fT, bg=BG, fg=TEXT).pack(pady=(24, 4))
        tk.Label(self.root, text="Choose a game mode",
                 font=self.fS, bg=BG, fg=MUTED).pack(pady=(0, 8))

        # server + account bar
        self._build_account_bar()

        # game mode cards
        modes = [
            ("Flag Quiz",
             "Identify flags. Choose region & question count.",
             lambda: self._flag_setup(False)),
            ("All 197 Countries",
             "Every sovereign nation once -- the ultimate challenge.",
             lambda: self._flag_setup(True)),
            ("Map Fill -- Type Countries",
             "Type country names to fill their outlines on the world map.",
             lambda: self._launch_map(False)),
            ("Map Fill -- Flag Hints",
             "A flag appears -- type the country name to fill its shape.",
             lambda: self._launch_map(True)),
        ]
        for title, desc, cmd in modes:
            card = tk.Frame(self.root, bg=CARD, cursor="hand2",
                            highlightbackground=ACCENT, highlightthickness=1)
            card.pack(padx=50, pady=4, fill="x")
            card.bind("<Enter>", lambda e, f=card: f.config(highlightbackground=HIGHLIGHT))
            card.bind("<Leave>", lambda e, f=card: f.config(highlightbackground=ACCENT))
            inner = tk.Frame(card, bg=CARD)
            inner.pack(padx=14, pady=9, fill="x")
            tk.Label(inner, text=title, font=self.fS, bg=CARD, fg=TEXT, anchor="w").pack(anchor="w")
            tk.Label(inner, text=desc,  font=self.fX, bg=CARD, fg=MUTED,
                     anchor="w").pack(anchor="w", pady=(2, 0))
            for w in [card, inner] + list(inner.winfo_children()):
                w.bind("<Button-1>", lambda e, c=cmd: c())

        # leaderboard button
        has_remote = self._remote and bool(self._remote.base)
        lb_card = tk.Frame(self.root, bg=CARD, cursor="hand2",
                           highlightbackground=GOLD, highlightthickness=1)
        lb_card.pack(padx=50, pady=4, fill="x")
        lb_card.bind("<Enter>", lambda e: lb_card.config(highlightbackground=WARNING))
        lb_card.bind("<Leave>", lambda e: lb_card.config(highlightbackground=GOLD))
        lb_inner = tk.Frame(lb_card, bg=CARD)
        lb_inner.pack(padx=14, pady=9, fill="x")
        n_local  = len(self.db.scores())
        src_lbl  = ("Live -- " + self._remote.base) if has_remote \
                   else f"Local  ({n_local} scores)"
        tk.Label(lb_inner, text="Leaderboard", font=self.fS,
                 bg=CARD, fg=GOLD, anchor="w").pack(anchor="w")
        tk.Label(lb_inner, text=src_lbl, font=self.fX, bg=CARD,
                 fg=INFO if has_remote else MUTED, anchor="w").pack(anchor="w", pady=(2, 0))
        for w in [lb_card, lb_inner] + list(lb_inner.winfo_children()):
            w.bind("<Button-1>", lambda e: self._leaderboard())

        tk.Label(self.root,
                 text=f"{len(ALL_COUNTRIES)} sovereign nations  |  local backup: quiz_scores.json",
                 font=self.fX, bg=BG, fg=MUTED).pack(pady=(8, 0))

    # -----------------------------------------------------------------------
    # Account bar -- shows server URL or logged-in user info
    # -----------------------------------------------------------------------

    def _build_account_bar(self):
        bar = tk.Frame(self.root, bg=CARD,
                       highlightbackground=ACCENT, highlightthickness=1)
        bar.pack(padx=50, pady=(0, 4), fill="x")
        inner = tk.Frame(bar, bg=CARD)
        inner.pack(padx=12, pady=8, fill="x")

        has_remote = self._remote and bool(self._remote.base)
        logged_in  = bool(self._logged_in_user)

        if not has_remote:
            # no server -- show URL entry
            tk.Label(inner, text="No server -- local scores only",
                     font=self.fX, bg=CARD, fg=MUTED).grid(
                     row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
            tk.Label(inner, text="Server URL:", font=self.fX, bg=CARD,
                     fg=MUTED).grid(row=1, column=0, sticky="w", padx=(0, 6))
            self._srv_url_var = tk.StringVar(value="")
            tk.Entry(inner, textvariable=self._srv_url_var, font=self.fX,
                     bg=ACCENT, fg=TEXT, insertbackground=TEXT,
                     relief="flat", width=34).grid(row=1, column=1, ipady=4, padx=(0, 8))
            tk.Button(inner, text="Connect", font=self.fX,
                      bg=INFO, fg="white", relief="flat", cursor="hand2",
                      padx=10, pady=3,
                      command=self._connect_server).grid(row=1, column=2)

        elif not logged_in:
            # server connected but not logged in
            tk.Label(inner,
                     text=f"Connected  --  {self._remote.base}",
                     font=self.fX, bg=CARD, fg=SUCCESS).grid(
                     row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
            tk.Label(inner, text="Not logged in  --  scores won't be linked to an account",
                     font=self.fX, bg=CARD, fg=WARNING).grid(
                     row=1, column=0, columnspan=2, sticky="w")
            tk.Button(inner, text="Log In", font=self.fX,
                      bg=INFO, fg="white", relief="flat", cursor="hand2",
                      padx=10, pady=3,
                      command=lambda: self._show_auth_dialog("login")).grid(
                      row=1, column=2, padx=(8, 4))
            tk.Button(inner, text="Register", font=self.fX,
                      bg=SUCCESS, fg="#071a0b", relief="flat", cursor="hand2",
                      padx=10, pady=3,
                      command=lambda: self._show_auth_dialog("register")).grid(
                      row=1, column=3)

        else:
            # logged in -- show user info
            tk.Label(inner,
                     text=f"Logged in as  {self._logged_in_user}  --  {self._remote.base}",
                     font=self.fX, bg=CARD, fg=SUCCESS).grid(
                     row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
            tk.Button(inner, text="My History", font=self.fX,
                      bg=ACCENT, fg=TEXT, relief="flat", cursor="hand2",
                      padx=10, pady=3,
                      command=self._show_profile).grid(row=1, column=0, padx=(0, 8))
            tk.Label(inner, text="", bg=CARD).grid(row=1, column=1,
                     sticky="ew")   # spacer
            inner.columnconfigure(1, weight=1)
            tk.Button(inner, text="Log Out", font=self.fX,
                      bg=ERROR, fg="white", relief="flat", cursor="hand2",
                      padx=10, pady=3,
                      command=self._logout).grid(row=1, column=2)

    def _connect_server(self):
        url = self._srv_url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter the server URL.")
            return

        def _try():
            client = RemoteClient(url)
            ok     = client.ping()
            def _done():
                if ok:
                    self._remote       = client
                    self.db.server_url = client.base
                    self._menu()
                else:
                    messagebox.showerror(
                        "Cannot Connect",
                        f"Could not reach:\n{client.base}\n\n"
                        "Make sure the server is running and the URL is correct.")
            self.root.after(0, _done)
        threading.Thread(target=_try, daemon=True).start()

    # -----------------------------------------------------------------------
    # Score submission -- local always, server when logged in
    # -----------------------------------------------------------------------

    def _submit_score(self, player, mode, score, total):
        self.db.add(player, mode, score, total)
        if self._remote and self._remote.base:
            token = self._token  # may be None for guests -- server handles both
            threading.Thread(
                target=self._remote.post_score,
                args=(player, mode, score, total, token),
                daemon=True).start()

    def _update_player(self):
        """Keep _player in sync. For logged in users the name is locked to the username."""
        if self._logged_in_user:
            self._player = self._logged_in_user
        elif hasattr(self, "_player_var"):
            name = self._player_var.get().strip()
            self._player = name
            if name:
                self.db.last_player = name

    # -----------------------------------------------------------------------
    # Profile screen -- personal attempt history
    # -----------------------------------------------------------------------

    def _show_profile(self):
        if not self._logged_in_user or not self._remote:
            return

        win = tk.Toplevel(self.root)
        win.title(f"{self._logged_in_user}'s History")
        win.configure(bg=BG)
        win.geometry("860x600")
        win.grab_set()

        tk.Label(win, text=f"{self._logged_in_user}'s History",
                 font=self.fT, bg=BG, fg=TEXT).pack(pady=(18, 2))
        tk.Label(win, text="Every quiz you've completed on this server.",
                 font=self.fX, bg=BG, fg=MUTED).pack(pady=(0, 8))

        # tabs per mode
        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        tab_defs = [
            ("All",         None),
            ("Flag Quiz",   ScoreDB.MODE_FLAG),
            ("All 197",     ScoreDB.MODE_ALL),
            ("Map Fill",    ScoreDB.MODE_MAP),
            ("Map + Flags", ScoreDB.MODE_MAPFLAG),
        ]
        trees = {}
        for label, mode_filter in tab_defs:
            tab  = tk.Frame(tabs, bg=BG)
            tabs.add(tab, text=f"  {label}  ")
            trees[mode_filter] = self._lb_make_tree(tab, show_player=False)

        status_var = tk.StringVar(value="Loading...")
        tk.Label(win, textvariable=status_var, font=self.fX,
                 bg=BG, fg=MUTED).pack(pady=(0, 4))

        def _load():
            rows = self._remote.get_profile(self._logged_in_user)
            def _done():
                if rows is None:
                    status_var.set("Could not load history -- check your connection.")
                    return
                status_var.set(f"{len(rows)} attempts total")
                for mode_filter, tree in trees.items():
                    filtered = [r for r in rows if mode_filter is None
                                or r.get("mode") == mode_filter]
                    # newest first for personal history
                    self._lb_fill_tree(tree, filtered, sort_by_date=True)
            self.root.after(0, _done)
        threading.Thread(target=_load, daemon=True).start()

        tk.Button(win, text="Close", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=16, pady=8,
                  command=win.destroy).pack(pady=(0, 12))

    # -----------------------------------------------------------------------
    # LEADERBOARD -- Treeview with live polling
    # -----------------------------------------------------------------------

    def _leaderboard(self):
        if self._lb_win and self._lb_win.winfo_exists():
            self._lb_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Leaderboard")
        win.configure(bg=BG)
        win.geometry("900x640")
        win.grab_set()
        self._lb_win = win
        win.protocol("WM_DELETE_WINDOW",
                     lambda: (self._lb_stop_poll(), win.destroy()))

        tk.Label(win, text="Leaderboard", font=self.fT, bg=BG, fg=GOLD).pack(pady=(18, 2))

        has_remote = self._remote and bool(self._remote.base)
        status_txt = (f"Live  --  {self._remote.base}  (auto-refreshing every 5 s)"
                      if has_remote else "Local scores only")
        self._lb_status_var = tk.StringVar(value=status_txt)
        tk.Label(win, textvariable=self._lb_status_var, font=self.fX, bg=BG,
                 fg=INFO if has_remote else MUTED).pack(pady=(0, 4))

        self._lb_stats_var = tk.StringVar(value="")
        if has_remote:
            tk.Label(win, textvariable=self._lb_stats_var,
                     font=self.fX, bg=BG, fg=MUTED).pack(pady=(0, 4))

        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        tab_defs = [
            ("All",         None),
            ("Flag Quiz",   ScoreDB.MODE_FLAG),
            ("All 197",     ScoreDB.MODE_ALL),
            ("Map Fill",    ScoreDB.MODE_MAP),
            ("Map + Flags", ScoreDB.MODE_MAPFLAG),
        ]
        self._lb_trees = {}
        for label, mode_filter in tab_defs:
            tab  = tk.Frame(tabs, bg=BG)
            tabs.add(tab, text=f"  {label}  ")
            self._lb_trees[mode_filter] = self._lb_make_tree(tab)

        self._lb_populate_all()

        if has_remote:
            self._lb_start_poll(win)
            threading.Thread(target=self._lb_fetch_stats, daemon=True).start()

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(pady=(0, 12))
        tk.Button(btn_row, text="Clear Local Scores", font=self.fX, bg=ERROR, fg="white",
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=lambda: self._lb_clear(win)).pack(side="left", padx=8)
        tk.Button(btn_row, text="Close", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=lambda: (self._lb_stop_poll(), win.destroy())).pack(side="left", padx=8)

    def _lb_make_tree(self, parent, show_player=True):
        """
        Treeview with fixed columns.
        show_player=False is used for the personal history view
        where the player column is redundant.
        """
        frame = tk.Frame(parent, bg=BG)
        frame.pack(fill="both", expand=True, padx=6, pady=6)

        if show_player:
            cols = ("rank", "player", "mode", "score", "pct", "date")
            col_config = {
                "rank":   ("#",      46,  "center"),
                "player": ("Player", 190, "w"),
                "mode":   ("Mode",   160, "w"),
                "score":  ("Score",  90,  "center"),
                "pct":    ("%",      60,  "center"),
                "date":   ("Date",   100, "center"),
            }
        else:
            cols = ("rank", "mode", "score", "pct", "date")
            col_config = {
                "rank":  ("#",     46,  "center"),
                "mode":  ("Mode",  200, "w"),
                "score": ("Score", 100, "center"),
                "pct":   ("%",     70,  "center"),
                "date":  ("Date",  120, "center"),
            }

        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="none")
        for col, (heading, width, anchor) in col_config.items():
            tree.heading(col, text=heading, anchor=anchor)
            tree.column(col,  width=width,  anchor=anchor,
                        minwidth=width, stretch=False)

        tree.tag_configure("even", background=CARD,      foreground=TEXT)
        tree.tag_configure("odd",  background="#16243a", foreground=TEXT)
        tree.tag_configure("gold", background=CARD,      foreground=GOLD)
        tree.tag_configure("silv", background=CARD,      foreground=SILVER)
        tree.tag_configure("brnz", background=CARD,      foreground=BRONZE)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    def _lb_fetch_rows(self, mode_filter):
        if self._remote and self._remote.base:
            rows = self._remote.get_scores(mode=mode_filter)
            if rows is not None:
                return rows
        return self.db.top_scores(mode=mode_filter, n=200)

    def _lb_populate_all(self):
        for mode_filter, tree in self._lb_trees.items():
            rows = self._lb_fetch_rows(mode_filter)
            self._lb_fill_tree(tree, rows)

    def _lb_fill_tree(self, tree, rows, sort_by_date=False):
        """
        Fill a Treeview with score rows.
        sort_by_date=True is used for the profile history view (newest first).
        Default is rank order (by % desc).
        """
        tree.delete(*tree.get_children())
        place_labels = {1: "1st", 2: "2nd", 3: "3rd"}

        # detect whether this tree has a player column
        has_player = "player" in tree["columns"]

        for rank, row in enumerate(rows, 1):
            pct  = row.get("pct", 0)
            s, t = row.get("score", 0), row.get("total", 0)
            tag  = {1: "gold", 2: "silv", 3: "brnz"}.get(rank,
                   "even" if rank % 2 == 0 else "odd")

            if has_player:
                values = (
                    place_labels.get(rank, str(rank)),
                    row.get("player", "--"),
                    row.get("mode",   "--"),
                    f"{s} / {t}",
                    f"{pct}%",
                    row.get("date",  "--"),
                )
            else:
                values = (
                    str(rank),
                    row.get("mode",  "--"),
                    f"{s} / {t}",
                    f"{pct}%",
                    row.get("date", "--"),
                )
            tree.insert("", "end", tags=(tag,), values=values)

    def _lb_fetch_stats(self):
        if not (self._remote and self._remote.base):
            return
        stats = self._remote.get_stats()
        if not stats:
            return
        total   = stats.get("total_games", 0)
        users   = stats.get("total_users", 0)
        top     = stats.get("top")
        txt = f"{total} games played  |  {users} registered players"
        if top:
            txt += f"  |  Top: {top['player']} ({top['pct']}%)"
        def _update():
            try:
                self._lb_stats_var.set(txt)
            except Exception:
                pass
        self.root.after(0, _update)

    def _lb_start_poll(self, win, interval_ms=5000):
        def poll():
            if not (win.winfo_exists() and self._lb_win == win):
                return
            self._lb_populate_all()
            threading.Thread(target=self._lb_fetch_stats, daemon=True).start()
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                self._lb_status_var.set(
                    f"Live  --  {self._remote.base}  |  refreshed {ts}")
            except tk.TclError:
                return
            self._lb_poll_id = self.root.after(interval_ms, poll)
        self._lb_poll_id = self.root.after(interval_ms, poll)

    def _lb_stop_poll(self):
        if self._lb_poll_id:
            try:
                self.root.after_cancel(self._lb_poll_id)
            except Exception:
                pass
            self._lb_poll_id = None

    def _lb_clear(self, parent_win):
        if messagebox.askyesno("Clear Local Scores",
                               "Delete all local scores?\nThis does not affect the server.",
                               parent=parent_win):
            self.db.clear()
            self._lb_populate_all()

    # -----------------------------------------------------------------------
    # FLAG QUIZ SETUP
    # -----------------------------------------------------------------------

    REGIONS = {
        "All Regions": None,
        "Africa":   {"dz","ao","bj","bw","bf","bi","cv","cm","cf","td","km","cg","cd",
                     "dj","eg","gq","er","et","ga","gm","gh","gn","gw","ke","ls","lr",
                     "ly","mg","mw","ml","mr","mu","ma","mz","na","ne","ng","rw","st",
                     "sn","sc","sl","so","za","ss","sd","sz","tz","tg","tn","ug","zm","zw"},
        "Americas": {"ag","ar","bs","bb","bz","bo","br","ca","cl","co","cr","cu","dm",
                     "do","ec","sv","gd","gt","gy","ht","hn","jm","mx","ni","pa","py",
                     "pe","kn","lc","vc","sr","tt","us","uy","ve","ws"},
        "Asia":     {"af","am","az","bh","bd","bt","bn","kh","cn","ge","in","id","ir",
                     "iq","il","jp","jo","kz","kp","kr","kw","kg","la","lb","my","mv",
                     "mn","mm","np","om","pk","ps","ph","qa","sa","sg","lk","sy","tw",
                     "tj","th","tl","tr","tm","ae","uz","vn","ye"},
        "Europe":   {"al","ad","at","by","be","ba","bg","hr","cy","cz","dk","ee","fi",
                     "fr","ge","de","gr","hu","is","ie","it","xk","lv","li","lt","lu",
                     "mk","mt","md","mc","me","nl","no","pl","pt","ro","ru","sm","rs",
                     "sk","si","es","se","ch","ua","gb","va"},
        "Oceania":  {"au","fj","ki","mh","fm","nr","nz","pw","pg","ws","sb","to","tv","vu"},
    }

    def _flag_setup(self, full_mode):
        self._update_player()
        self._clear()
        self.root.geometry("520x450")
        tk.Label(self.root, text="Flag Quiz Setup",
                 font=self.fT, bg=BG, fg=TEXT).pack(pady=(30, 8))

        if full_mode:
            tk.Label(self.root,
                     text=f"All {len(ALL_COUNTRIES)} countries, shown once each.",
                     font=self.fB, bg=BG, fg=MUTED, wraplength=440).pack(pady=(0, 18))
        else:
            tk.Label(self.root, text="How many questions?",
                     font=self.fB, bg=BG, fg=TEXT).pack(pady=(8, 6))
            self._q_count_var = tk.IntVar(value=10)
            btn_row = tk.Frame(self.root, bg=BG)
            btn_row.pack()
            for n in [5, 10, 20, 50, len(ALL_COUNTRIES)]:
                lbl = str(n) if n != len(ALL_COUNTRIES) else f"All {n}"
                tk.Radiobutton(btn_row, text=lbl, variable=self._q_count_var, value=n,
                               font=self.fB, bg=BG, fg=TEXT,
                               selectcolor=ACCENT, activebackground=BG).pack(
                               side="left", padx=8)
            tk.Label(self.root, text="-- or type a custom number --",
                     font=self.fX, bg=BG, fg=MUTED).pack(pady=(10, 2))
            self._q_custom = tk.Entry(self.root, font=self.fB, bg=ACCENT, fg=TEXT,
                                      insertbackground=TEXT, width=6,
                                      justify="center", relief="flat")
            self._q_custom.pack(ipady=6)

        tk.Label(self.root, text="Region filter:",
                 font=self.fB, bg=BG, fg=TEXT).pack(pady=(16, 4))
        self._region_var = tk.StringVar(value="All Regions")
        ttk.Combobox(self.root, textvariable=self._region_var,
                     values=list(self.REGIONS.keys()),
                     state="readonly", font=self.fB, width=18).pack()

        def start_quiz():
            if full_mode:
                count, mode = len(ALL_COUNTRIES), ScoreDB.MODE_ALL
            else:
                custom = self._q_custom.get().strip()
                count  = int(custom) if custom.isdigit() and \
                         1 <= int(custom) <= len(ALL_COUNTRIES) \
                         else self._q_count_var.get()
                mode   = ScoreDB.MODE_FLAG
            pool         = ALL_COUNTRIES
            region_codes = self.REGIONS.get(self._region_var.get())
            if region_codes:
                pool = [(nm, co) for nm, co in ALL_COUNTRIES
                        if co in region_codes] or pool
            self._run_flag_quiz(random.sample(pool, min(count, len(pool))), mode=mode)

        btn_row = tk.Frame(self.root, bg=BG)
        btn_row.pack(pady=22)
        tk.Button(btn_row, text="Start", font=self.fS, bg=SUCCESS, fg="#071a0b",
                  relief="flat", cursor="hand2", padx=20, pady=10,
                  command=start_quiz).pack(side="left", padx=10)
        tk.Button(btn_row, text="Back", font=self.fS, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=16, pady=10,
                  command=self._menu).pack(side="left")

    # -----------------------------------------------------------------------
    # FLAG QUIZ -- game loop
    # -----------------------------------------------------------------------

    def _run_flag_quiz(self, question_pool, recycled=False, mode=ScoreDB.MODE_FLAG):
        from collections import deque
        self._clear()
        self.root.geometry("600x780")

        self._fq_queue    = deque(question_pool)
        self._fq_total    = len(question_pool)
        self._fq_answered = 0
        self._fq_score    = 0
        self._fq_wrong    = []
        self._fq_done     = False
        self._fq_recycled = recycled
        self._fq_mode     = mode

        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(pady=(18, 4))
        tk.Label(hdr, text="Recycled Flags" if recycled else "Flag Quiz",
                 font=self.fT, bg=BG, fg=TEXT).pack()
        self._fq_remaining_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._fq_remaining_var,
                 font=self.fX, bg=BG, fg=MUTED).pack(pady=(2, 0))

        pb_bg = tk.Frame(self.root, bg=ACCENT, height=5, width=540)
        pb_bg.pack(pady=(0, 8))
        self._fq_progress = tk.Frame(pb_bg, bg=HIGHLIGHT, height=5, width=0)
        self._fq_progress.place(x=0, y=0)

        flag_card = tk.Frame(self.root, bg=CARD,
                             highlightbackground=ACCENT, highlightthickness=2)
        flag_card.pack(padx=28, pady=4, fill="x")
        tk.Label(flag_card, text="Which country does this flag belong to?",
                 font=self.fS, bg=CARD, fg=TEXT,
                 wraplength=520, justify="center").pack(pady=(12, 8))
        self._fq_flag_img = tk.Label(flag_card, bg=CARD)
        self._fq_flag_img.pack(pady=(0, 12))

        input_row = tk.Frame(self.root, bg=BG)
        input_row.pack(pady=8, padx=28, fill="x")
        self._fq_input = tk.Entry(input_row, font=self.fB, bg=ACCENT, fg=TEXT,
                                  insertbackground=TEXT, relief="flat",
                                  highlightbackground=MUTED, highlightthickness=1)
        self._fq_input.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        self._fq_submit_btn = tk.Button(input_row, text="Submit", font=self.fK,
                                        bg=HIGHLIGHT, fg="white", relief="flat",
                                        activebackground="#c73652", cursor="hand2",
                                        padx=12, pady=6, command=self._fq_check)
        self._fq_submit_btn.pack(side="right")

        self._fq_feedback = tk.Label(self.root, text="", font=self.fB, bg=BG,
                                     fg=TEXT, wraplength=540, justify="center")
        self._fq_feedback.pack(pady=6, padx=20)

        score_bar = tk.Frame(self.root, bg=CARD,
                             highlightbackground=ACCENT, highlightthickness=1)
        score_bar.pack(padx=28, fill="x")
        self._fq_score_lbl = tk.Label(score_bar, text="Score: 0 / 0",
                                      font=self.fK, bg=CARD, fg=TEXT, pady=8)
        self._fq_score_lbl.pack()

        ctrl_row = tk.Frame(self.root, bg=BG)
        ctrl_row.pack(pady=8)
        tk.Button(ctrl_row, text="Skip", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._fq_skip).pack(side="left", padx=8)
        tk.Button(ctrl_row, text="Menu", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=12, pady=6,
                  command=self._menu).pack(side="left")

        self.root.bind("<Return>", lambda e: self._fq_check())
        self._fq_load()

    def _fq_widgets_alive(self):
        try:
            return (self._fq_progress.winfo_exists() and
                    self._fq_score_lbl.winfo_exists() and
                    self._fq_input.winfo_exists())
        except Exception:
            return False

    def _fq_load(self):
        if not self._fq_widgets_alive():
            return
        if not self._fq_queue:
            self._fq_results()
            return
        self._fq_done = False
        name, code = self._fq_queue[0]
        self._fq_name = name
        self._fq_code = code
        self._fq_img  = None
        try:
            remaining = len(self._fq_queue)
            self._fq_remaining_var.set(
                f"{remaining} flag{'s' if remaining != 1 else ''} remaining")
            self._fq_progress.config(
                width=int(540 * min(self._fq_answered, self._fq_total)
                          / max(self._fq_total, 1)))
            self._fq_score_lbl.config(
                text=f"Score: {self._fq_score} / {self._fq_answered}")
            self._fq_feedback.config(text="", fg=TEXT)
            self._fq_input.config(state="normal")
            self._fq_input.delete(0, tk.END)
            self._fq_submit_btn.config(state="normal")
            self._fq_flag_img.config(image="", text="Loading...", fg=MUTED, font=self.fX)
        except tk.TclError:
            return
        threading.Thread(target=self._fq_fetch, args=(code,), daemon=True).start()
        self._fq_input.focus_set()

    def _fq_fetch(self, code):
        try:
            data  = fetch_bytes(f"https://flagcdn.com/w320/{code}.png")
            img   = Image.open(BytesIO(data)).resize((400, 240))
            photo = ImageTk.PhotoImage(img)
            self._fq_img = data
            def _show(p=photo):
                try:
                    if self._fq_flag_img.winfo_exists():
                        self._fq_flag_img.config(image=p, text="")
                        self._fq_flag_img.image = p
                except tk.TclError:
                    pass
            self.root.after(0, _show)
        except Exception:
            def _err():
                try:
                    if self._fq_flag_img.winfo_exists():
                        self._fq_flag_img.config(
                            text=f"Flag unavailable ({self._fq_code})",
                            fg=ERROR, font=self.fX)
                except tk.TclError:
                    pass
            self.root.after(0, _err)

    def _fq_check(self):
        if self._fq_done:
            return
        raw = self._fq_input.get().strip()
        if not raw:
            return
        self._fq_done = True
        self._fq_input.config(state="disabled")
        self._fq_submit_btn.config(state="disabled")
        resolved = resolve(raw)
        correct  = self._fq_name
        self._fq_queue.popleft()
        self._fq_answered += 1
        if resolved.lower() == correct.lower():
            self._fq_score += 1
            self._fq_feedback.config(
                text=f"Correct!  It's {disp(correct)}.", fg=SUCCESS)
        else:
            self._fq_feedback.config(
                text=f"  {diff_hint(raw, disp(correct))}", fg=ERROR)
            self._fq_wrong.append(
                (self._fq_img, raw, disp(correct), self._fq_code))
        self._fq_score_lbl.config(
            text=f"Score: {self._fq_score} / {self._fq_answered}")
        self.root.after(2400, self._fq_load)

    def _fq_skip(self):
        if self._fq_done:
            return
        self._fq_done = True
        self._fq_input.config(state="disabled")
        self._fq_submit_btn.config(state="disabled")
        correct = self._fq_name
        self._fq_feedback.config(
            text=f"Skipped -- it was: {disp(correct)}", fg=WARNING)
        self._fq_queue.popleft()
        self._fq_answered += 1
        self._fq_wrong.append(
            (self._fq_img, "(skipped)", disp(correct), self._fq_code))
        self._fq_score_lbl.config(
            text=f"Score: {self._fq_score} / {self._fq_answered}")
        self.root.after(2000, self._fq_load)

    def _fq_results(self):
        self.root.unbind("<Return>")
        if not self._fq_recycled and self._fq_answered:
            self._submit_score(
                self._player or "Anonymous",
                self._fq_mode, self._fq_score, self._fq_answered)

        answered = self._fq_answered or 1
        pct      = int(self._fq_score / answered * 100)
        grade, grade_colour = (
            ("Perfect score!",   SUCCESS) if pct == 100 else
            ("Great job!",       INFO)    if pct >= 80  else
            ("Good effort!",     WARNING) if pct >= 60  else
            ("Keep practising!", MUTED)
        )
        win = tk.Toplevel(self.root)
        win.title("Results")
        win.configure(bg=BG)
        win.geometry("660x700")
        win.grab_set()

        tk.Label(win, text="Quiz Complete!", font=self.fT, bg=BG, fg=TEXT).pack(pady=(22, 2))
        tk.Label(win, text=grade, font=self.fS, bg=BG, fg=grade_colour).pack()
        tk.Label(win,
                 text=f"You scored  {self._fq_score} / {self._fq_answered}  ({pct}%)",
                 font=self.fB, bg=BG, fg=TEXT).pack(pady=(4, 2))
        if not self._fq_recycled and self._fq_answered:
            if self._logged_in_user:
                saved_note = f"Saved to your account  ({self._logged_in_user})"
            elif self._remote and self._remote.base:
                saved_note = "Saved locally (not logged in -- score not on server)"
            else:
                saved_note = "Saved locally only"
            tk.Label(win, text=saved_note, font=self.fX, bg=BG, fg=INFO).pack(pady=(0, 8))

        missed = self._fq_wrong
        if not missed:
            tk.Label(win, text="Flawless -- no mistakes!",
                     font=self.fB, bg=BG, fg=SUCCESS).pack(pady=12)
        else:
            tk.Label(win, text=f"Mistakes / Skips  ({len(missed)})",
                     font=self.fS, bg=BG, fg=ERROR).pack()
            self._scrollable_wrong_list(win, missed)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(pady=10)
        if missed:
            recycle_pool = [(w[2], w[3]) for w in missed]
            disp_to_py   = {v: k for k, v in DISPLAY.items()}
            recycle_pool = [(disp_to_py.get(nm, nm), co) for nm, co in recycle_pool]
            def do_recycle():
                win.destroy()
                random.shuffle(recycle_pool)
                self._run_flag_quiz(recycle_pool, recycled=True)
            tk.Button(btn_row,
                      text=f"Retry {len(missed)} missed flag{'s' if len(missed) != 1 else ''}",
                      font=self.fS, bg=WARNING, fg="#1a0f00",
                      relief="flat", cursor="hand2", padx=16, pady=10,
                      command=do_recycle).pack(side="left", padx=8)
        tk.Button(btn_row, text="Leaderboard", font=self.fS, bg=GOLD, fg="#1a0f00",
                  relief="flat", cursor="hand2", padx=16, pady=10,
                  command=lambda: (win.destroy(), self._leaderboard())).pack(
                  side="left", padx=8)
        tk.Button(btn_row, text="Main Menu", font=self.fS, bg=HIGHLIGHT, fg="white",
                  relief="flat", cursor="hand2", padx=16, pady=10,
                  command=lambda: (win.destroy(), self._menu())).pack(side="left", padx=8)

    def _scrollable_wrong_list(self, parent, missed_list):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True, padx=20, pady=8)
        cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        sf = tk.Frame(cv, bg=BG)
        sf.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=sf, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for img_bytes, guess, correct, code in missed_list:
            row = tk.Frame(sf, bg=CARD,
                           highlightbackground=ACCENT, highlightthickness=1)
            row.pack(fill="x", pady=5, padx=4)
            if img_bytes:
                try:
                    thumb = ImageTk.PhotoImage(
                        Image.open(BytesIO(img_bytes)).resize((110, 68)))
                    fl = tk.Label(row, image=thumb, bg=CARD)
                    fl.image = thumb
                    fl.pack(side="left", padx=10, pady=8)
                except Exception:
                    pass
            info = tk.Frame(row, bg=CARD)
            info.pack(side="left", fill="x", expand=True, pady=10)
            tk.Label(info, text=f'You wrote:  "{guess}"',
                     font=self.fX, bg=CARD, fg=WARNING, anchor="w").pack(anchor="w")
            tk.Label(info, text=f"Correct:  {correct}",
                     font=self.fX, bg=CARD, fg=SUCCESS, anchor="w").pack(
                     anchor="w", pady=(3, 0))

    # -----------------------------------------------------------------------
    # MAP -- loading
    # -----------------------------------------------------------------------

    def _launch_map(self, use_flags):
        self._update_player()
        self._clear()
        self.root.geometry("500x300")
        tk.Label(self.root, text="Loading Map...",
                 font=self.fT, bg=BG, fg=TEXT).pack(pady=(60, 16))
        self._map_status = tk.StringVar(value="Connecting...")
        tk.Label(self.root, textvariable=self._map_status,
                 font=self.fB, bg=BG, fg=MUTED).pack()
        pb_bg = tk.Frame(self.root, bg=ACCENT, height=6, width=380)
        pb_bg.pack(pady=20)
        self._load_pb = tk.Frame(pb_bg, bg=INFO, height=6, width=0)
        self._load_pb.place(x=0, y=0)

        def do_load():
            try:
                stop_pulse = [False]
                def pulse(n=0):
                    if stop_pulse[0]:
                        return
                    try:
                        self._load_pb.config(width=int(380 * abs(math.sin(n * 0.15))))
                    except Exception:
                        pass
                    self.root.after(60, lambda: pulse(n + 1))
                import math
                self.root.after(0, pulse)
                self._geo = load_geojson(
                    on_progress=lambda s: self.root.after(
                        0, lambda: self._map_status.set(s)))
                stop_pulse[0] = True
                self.root.after(0, lambda: self._map_mode(use_flags))
            except Exception as e:
                stop_pulse[0] = True
                self.root.after(0, lambda: (
                    messagebox.showerror("Network Error",
                        f"Could not download map data:\n{e}"),
                    self._menu()))
        threading.Thread(target=do_load, daemon=True).start()

    # -----------------------------------------------------------------------
    # MAP -- game screen
    # -----------------------------------------------------------------------

    def _map_mode(self, use_flags):
        self._clear()
        self.root.geometry("1280x740")

        self._mp_flags       = use_flags
        self._mp_mode        = ScoreDB.MODE_MAPFLAG if use_flags else ScoreDB.MODE_MAP
        self._mp_found       = {}
        self._mp_total       = len(ALL_COUNTRIES)
        self._mp_remain      = {code: name for name, code in ALL_COUNTRIES}
        self._mp_cur_code    = None
        self._mp_finished    = False
        self._mp_tooltip_win = None
        self._mp_results_win = None

        left = tk.Frame(self.root, bg=BG, width=220)
        left.pack(side="left", fill="y", padx=(8, 0), pady=8)
        left.pack_propagate(False)

        mid = tk.Frame(self.root, bg=BG)
        mid.pack(side="left", fill="both", expand=True, padx=6, pady=8)

        right = tk.Frame(self.root, bg=BG, width=220)
        right.pack(side="right", fill="y", padx=(0, 8), pady=8)
        right.pack_propagate(False)

        tk.Label(left, text="Map + Flags" if use_flags else "Map Fill",
                 font=self.fS, bg=BG, fg=TEXT, wraplength=210).pack(pady=(8, 2))
        tk.Label(left, text="Type country names to fill shapes.",
                 font=self.fX, bg=BG, fg=MUTED, wraplength=210,
                 justify="left").pack(pady=(0, 6))

        if use_flags:
            self._mp_flag_display = tk.Label(left, bg=BG, text="", fg=MUTED, font=self.fX)
            self._mp_flag_display.pack(pady=4)
            self._mp_flag_counter = tk.Label(left, text="", font=self.fX, bg=BG, fg=MUTED)
            self._mp_flag_counter.pack()

        input_frame = tk.Frame(left, bg=BG)
        input_frame.pack(fill="x", pady=(8, 4), padx=4)
        self._mp_input = tk.Entry(input_frame, font=self.fB, bg=ACCENT, fg=TEXT,
                                  insertbackground=TEXT, relief="flat",
                                  highlightbackground=MUTED, highlightthickness=1)
        self._mp_input.pack(fill="x", ipady=7)
        self._mp_input.bind("<Return>", lambda e: self._mp_submit())
        self._mp_input.focus_set()

        btn_row = tk.Frame(left, bg=BG)
        btn_row.pack(fill="x", padx=4, pady=(0, 4))
        tk.Button(btn_row, text="Submit", font=self.fK, bg=HIGHLIGHT, fg="white",
                  relief="flat", cursor="hand2", pady=6,
                  command=self._mp_submit).pack(
                  side="left", fill="x", expand=True, padx=(0, 4))
        if use_flags:
            tk.Button(btn_row, text="Skip", font=self.fX, bg=ACCENT, fg=TEXT,
                      relief="flat", cursor="hand2", pady=6, padx=6,
                      command=self._mp_skip_flag).pack(side="left")

        self._mp_feedback = tk.Label(left, text="", font=self.fX, bg=BG, fg=TEXT,
                                     wraplength=210, justify="left")
        self._mp_feedback.pack(pady=4, padx=4)
        self._mp_score_lbl = tk.Label(left, text=f"Found: 0 / {self._mp_total}",
                                      font=self.fK, bg=BG, fg=TEXT)
        self._mp_score_lbl.pack(pady=4)

        pb_bg = tk.Frame(left, bg=ACCENT, height=5, width=210)
        pb_bg.pack(pady=(0, 4))
        self._mp_progress = tk.Frame(pb_bg, bg=SUCCESS, height=5, width=0)
        self._mp_progress.place(x=0, y=0)

        legend = tk.Frame(left, bg=BG)
        legend.pack(pady=4, padx=4, fill="x")
        for colour, label in [(MAP_FOUND, "Guessed"), (MAP_LAND, "Not yet"),
                              (MAP_MISS,  "Missed")]:
            row = tk.Frame(legend, bg=BG)
            row.pack(anchor="w", pady=1)
            tk.Frame(row, bg=colour, width=13, height=13).pack(side="left", padx=(0, 5))
            tk.Label(row, text=label, font=self.fX, bg=BG, fg=MUTED).pack(side="left")

        tk.Frame(left, bg=BG).pack(expand=True, fill="both")

        bottom_btns = tk.Frame(left, bg=BG)
        bottom_btns.pack(fill="x", padx=4, pady=(0, 4), side="bottom")
        self._mp_finish_btn = tk.Button(
            bottom_btns, text="Finish & Results", font=self.fX,
            bg=SUCCESS, fg="#071a0b", relief="flat", cursor="hand2", pady=6,
            command=self._mp_finish)
        self._mp_finish_btn.pack(fill="x", pady=(0, 4))
        tk.Button(bottom_btns, text="Menu", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", pady=5,
                  command=self._menu).pack(fill="x")

        self._mp_canvas = tk.Canvas(mid, bg=MAP_SEA, highlightthickness=0)
        self._mp_canvas.pack(fill="both", expand=True)
        self._mp_canvas.bind("<Configure>", lambda e: self._mp_redraw())
        self._mp_canvas.bind("<Motion>",    self._mp_hover)
        self._mp_canvas.bind("<Leave>",     lambda e: self._mp_hide_tooltip())

        tk.Label(right, text="Guessed", font=self.fS,
                 bg=BG, fg=SUCCESS).pack(pady=(8, 4))
        self._mp_guessed_count = tk.Label(right, text="0 countries",
                                          font=self.fX, bg=BG, fg=MUTED)
        self._mp_guessed_count.pack()

        gl_outer = tk.Frame(right, bg=BG)
        gl_outer.pack(fill="both", expand=True, pady=4)
        gl_sb = ttk.Scrollbar(gl_outer, orient="vertical")
        gl_sb.pack(side="right", fill="y")
        gl_cv = tk.Canvas(gl_outer, bg=BG, highlightthickness=0,
                          yscrollcommand=gl_sb.set)
        gl_cv.pack(side="left", fill="both", expand=True)
        gl_sb.config(command=gl_cv.yview)
        self._mp_guessed_frame = tk.Frame(gl_cv, bg=BG)
        win_id = gl_cv.create_window((0, 0), window=self._mp_guessed_frame, anchor="nw")
        gl_cv.bind("<Configure>",
                   lambda e, cv=gl_cv, w=win_id: cv.itemconfig(w, width=e.width))
        self._mp_guessed_frame.bind(
            "<Configure>",
            lambda e, cv=gl_cv: cv.configure(scrollregion=cv.bbox("all")))
        def _scroll(e, cv=gl_cv):
            cv.yview_scroll(-1 if (e.num == 4 or e.delta > 0) else 1, "units")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            gl_cv.bind(seq, _scroll)
            self._mp_guessed_frame.bind(seq, _scroll)
            right.bind(seq, _scroll)
        self._mp_guessed_canvas = gl_cv

        if use_flags:
            self._mp_next_flag()

    def _mp_add_guessed_row(self, code, name, flag_img):
        row = tk.Frame(self._mp_guessed_frame, bg=CARD,
                       highlightbackground=MAP_FOUND2, highlightthickness=1)
        row.pack(fill="x", pady=2, padx=3, side="top", anchor="n")
        if flag_img:
            try:
                thumb = ImageTk.PhotoImage(flag_img.resize((36, 22)))
                fl = tk.Label(row, image=thumb, bg=CARD)
                fl.image = thumb
                fl.pack(side="left", padx=4, pady=3)
            except Exception:
                pass
        tk.Label(row, text=disp(name), font=self.fX, bg=CARD, fg=TEXT,
                 anchor="w", wraplength=148).pack(
                 side="left", padx=4, pady=3, fill="x", expand=True)
        n = len(self._mp_found)
        self._mp_guessed_count.config(
            text=f"{n} countr{'y' if n == 1 else 'ies'}")
        self.root.after(10, lambda: self._mp_guessed_canvas.yview_moveto(1.0))

    def _mp_submit(self):
        if self._mp_finished:
            return
        raw = self._mp_input.get().strip()
        if not raw:
            return
        self._mp_input.delete(0, tk.END)
        resolved = resolve(raw)

        matched_code = matched_name = None
        for code, name in list(self._mp_remain.items()):
            if (resolved.lower() == name.lower() or
                    raw.strip().lower() == disp(name).lower()):
                matched_code, matched_name = code, name
                break

        if matched_code:
            if self._mp_flags and self._mp_cur_code:
                if matched_code != self._mp_cur_code:
                    self._mp_feedback.config(
                        text="Not this one -- try again!", fg=ERROR)
                    return
            del self._mp_remain[matched_code]
            self._mp_feedback.config(text=f"{disp(matched_name)}!", fg=SUCCESS)
            n_found = self._mp_total - len(self._mp_remain)
            self._mp_score_lbl.config(text=f"Found: {n_found} / {self._mp_total}")
            self._mp_progress.config(width=int(210 * n_found / self._mp_total))

            def _fetch_and_add(c=matched_code, nm=matched_name):
                try:
                    data = fetch_bytes(f"https://flagcdn.com/w40/{c}.png")
                    img  = Image.open(BytesIO(data)).resize((36, 22))
                except Exception:
                    img = None
                self._mp_found[c] = img
                self.root.after(0, lambda: self._mp_add_guessed_row(c, nm, img))
                self.root.after(0, self._mp_redraw)
            threading.Thread(target=_fetch_and_add, daemon=True).start()

            if self._mp_flags:
                self.root.after(700, self._mp_next_flag)
            if not self._mp_remain:
                self.root.after(1200, self._mp_finish)
        else:
            self._mp_feedback.config(
                text="Not recognised -- try again!" if (self._mp_flags and self._mp_cur_code)
                else "Not found or already placed.", fg=ERROR)

    def _mp_skip_flag(self):
        if not self._mp_cur_code:
            return
        self._mp_input.delete(0, tk.END)
        remaining = [(c, n) for c, n in self._mp_remain.items()
                     if c != self._mp_cur_code]
        if not remaining:
            self._mp_feedback.config(text="Only one left!", fg=WARNING)
            return
        code, _ = random.choice(remaining)
        self._mp_cur_code = code
        self._mp_feedback.config(text="New flag...", fg=MUTED)
        n_found = self._mp_total - len(self._mp_remain)
        self._mp_flag_counter.config(
            text=f"{n_found} placed / {len(self._mp_remain)} remaining")
        self._mp_flag_display.config(image="", text="...", fg=MUTED, font=self.fX)
        threading.Thread(target=self._mp_load_flag, args=(code,), daemon=True).start()

    def _mp_next_flag(self):
        if not self._mp_remain:
            try:
                self._mp_flag_display.config(
                    image="", text="All done!", fg=SUCCESS, font=self.fS)
            except Exception:
                pass
            return
        code, _ = random.choice(list(self._mp_remain.items()))
        self._mp_cur_code = code
        n_found = self._mp_total - len(self._mp_remain)
        try:
            self._mp_flag_counter.config(
                text=f"{n_found} placed / {len(self._mp_remain)} remaining")
            self._mp_flag_display.config(image="", text="...", fg=MUTED, font=self.fX)
        except Exception:
            pass
        threading.Thread(target=self._mp_load_flag, args=(code,), daemon=True).start()

    def _mp_load_flag(self, code):
        try:
            data  = fetch_bytes(f"https://flagcdn.com/w160/{code}.png")
            img   = Image.open(BytesIO(data)).resize((200, 124))
            photo = ImageTk.PhotoImage(img)
            def _set(lbl=self._mp_flag_display, p=photo):
                try:
                    if lbl.winfo_exists():
                        lbl.config(image=p, text="")
                        lbl.image = p
                except Exception:
                    pass
            self.root.after(0, _set)
        except Exception:
            def _err(lbl=self._mp_flag_display):
                try:
                    if lbl.winfo_exists():
                        lbl.config(text="Flag unavailable", fg=ERROR, font=self.fX)
                except Exception:
                    pass
            self.root.after(0, _err)

    def _mp_redraw(self):
        c = self._mp_canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 10 or h < 10:
            return
        c.delete("all")
        geo        = self._geo or {}
        missed_set = set(self._mp_remain.keys()) if self._mp_finished else set()

        for _, code in ALL_COUNTRIES:
            if code in self._mp_found:
                fill, outline = MAP_FOUND, MAP_FOUND2
            elif code in missed_set:
                fill, outline = MAP_MISS,  MAP_MISS2
            else:
                fill, outline = MAP_LAND,  MAP_BORDER
            for ring in geo.get(code, []):
                pts = project_ring(ring, w, h)
                if len(pts) >= 6:
                    c.create_polygon(pts, fill=fill, outline=outline, width=1)

        for lon in range(-180, 181, 30):
            x = int((lon + 180) / 360 * w)
            c.create_line(x, 0, x, h, fill=MAP_GRID, width=1, dash=(2, 6))
        for lat in range(-90, 91, 30):
            y = int((90 - lat) / 180 * h)
            c.create_line(0, y, w, y, fill=MAP_GRID, width=1, dash=(2, 6))

        n_found  = len(self._mp_found)
        n_missed = len(self._mp_remain) if self._mp_finished else 0
        legend_txt = (f"{n_found} found  |  {n_missed} missed -- hover for names"
                      if self._mp_finished else f"{n_found} / {self._mp_total} placed")
        c.create_rectangle(4, 4, len(legend_txt) * 6 + 14, 22, fill="#0a111f", outline="")
        c.create_text(10, 13, anchor="w", text=legend_txt, fill=TEXT,
                      font=("Helvetica Neue", 10))

    def _mp_hover(self, event):
        if not self._mp_finished:
            return
        c = self._mp_canvas
        w, h = c.winfo_width(), c.winfo_height()
        geo  = self._geo or {}
        mx, my = event.x, event.y
        hit_code = hit_name = hit_found = None

        for search_found in (False, True):
            pool = self._mp_remain if not search_found else self._mp_found
            for code in pool:
                name = self._mp_remain.get(code) or next(
                    (n for n, c2 in ALL_COUNTRIES if c2 == code), code)
                for ring in geo.get(code, []):
                    pts  = project_ring(ring, w, h)
                    if len(pts) < 6:
                        continue
                    poly = [(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
                    if self._point_in_poly(mx, my, poly):
                        hit_code, hit_name, hit_found = code, name, search_found
                        break
                if hit_code:
                    break
            if hit_code:
                break

        if hit_code:
            self._mp_show_tooltip(event, hit_code, hit_name, hit_found)
        else:
            self._mp_hide_tooltip()

    @staticmethod
    def _point_in_poly(x, y, poly):
        inside = False
        n = len(poly)
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > y) != (yj > y)) and \
               (x < (xj - xi) * (y - yi) / (yj - yi + 1e-10) + xi):
                inside = not inside
            j = i
        return inside

    def _mp_show_tooltip(self, event, code, name, found=False):
        if self._mp_tooltip_win and \
                getattr(self._mp_tooltip_win, "_code", None) == code:
            rx = self.root.winfo_rootx() + event.x + 16
            ry = self.root.winfo_rooty() + event.y - 10
            self._mp_tooltip_win.geometry(f"+{rx}+{ry}")
            return
        self._mp_hide_tooltip()
        border_col = MAP_FOUND2 if found else MAP_MISS
        name_col   = SUCCESS    if found else ERROR

        tw = tk.Toplevel(self.root)
        tw._code = code
        tw.overrideredirect(True)
        tw.configure(bg=CARD, highlightbackground=border_col, highlightthickness=2)
        tw.attributes("-topmost", True)
        flag_lbl = tk.Label(tw, bg=CARD, text="...", fg=MUTED, font=self.fX)
        flag_lbl.pack(padx=10, pady=(8, 2))
        tk.Label(tw, text=("Found: " if found else "Missed: ") + disp(name),
                 font=self.fK, bg=CARD, fg=name_col, padx=10, pady=6).pack()
        rx = self.root.winfo_rootx() + event.x + 16
        ry = self.root.winfo_rooty() + event.y - 10
        tw.geometry(f"+{rx}+{ry}")
        self._mp_tooltip_win = tw

        def _load_thumb(c2=code, lbl=flag_lbl):
            try:
                data = fetch_bytes(f"https://flagcdn.com/w80/{c2}.png")
                img  = Image.open(BytesIO(data)).resize((80, 50))
                ph   = ImageTk.PhotoImage(img)
                def _set(l=lbl, p=ph):
                    try:
                        if l.winfo_exists():
                            l.config(image=p, text="")
                            l.image = p
                    except Exception:
                        pass
                self.root.after(0, _set)
            except Exception:
                pass
        threading.Thread(target=_load_thumb, daemon=True).start()

    def _mp_hide_tooltip(self):
        if self._mp_tooltip_win:
            try:
                self._mp_tooltip_win.destroy()
            except Exception:
                pass
            self._mp_tooltip_win = None

    def _mp_finish(self):
        self._mp_finished = True
        self._mp_hide_tooltip()
        n_found = len(self._mp_found)
        self._submit_score(self._player or "Anonymous",
                           self._mp_mode, n_found, self._mp_total)
        try:
            self._mp_input.config(state="disabled")
        except Exception:
            pass
        try:
            self._mp_finish_btn.config(
                text="View Results", bg=INFO, fg="white",
                command=self._mp_results_popup)
        except Exception:
            pass
        self._mp_redraw()
        self._mp_results_popup()

    def _mp_results_popup(self):
        if self._mp_results_win and self._mp_results_win.winfo_exists():
            self._mp_results_win.lift()
            self._mp_results_win.focus_set()
            return

        missed  = list(self._mp_remain.items())
        n_found = len(self._mp_found)
        pct     = int(n_found / self._mp_total * 100)
        grade, grade_colour = (
            ("Perfect -- every country!", SUCCESS) if pct == 100 else
            ("Excellent geographer!",     INFO)    if pct >= 80  else
            ("Good effort!",              WARNING) if pct >= 60  else
            ("Keep exploring!",           MUTED)
        )
        win = tk.Toplevel(self.root)
        win.title("Map Results")
        win.configure(bg=BG)
        win.geometry("780x700")
        self._mp_results_win = win

        tk.Label(win, text="Map Complete!", font=self.fT, bg=BG, fg=TEXT).pack(pady=(18, 2))
        tk.Label(win, text=grade, font=self.fS, bg=BG, fg=grade_colour).pack()
        tk.Label(win, text=f"You placed  {n_found} / {self._mp_total}  ({pct}%)",
                 font=self.fB, bg=BG, fg=TEXT).pack(pady=(4, 2))

        if self._logged_in_user:
            saved_note = f"Saved to your account  ({self._logged_in_user})"
        elif self._remote and self._remote.base:
            saved_note = "Saved locally (not logged in -- score not on server)"
        else:
            saved_note = "Saved locally only"
        tk.Label(win, text=saved_note, font=self.fX, bg=BG, fg=INFO).pack(pady=(0, 4))
        tk.Label(win, text="Hover over the map to see any country's name.",
                 font=self.fX, bg=BG, fg=MUTED).pack(pady=(0, 6))

        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        tab_missed  = tk.Frame(tabs, bg=BG)
        tab_guessed = tk.Frame(tabs, bg=BG)
        tabs.add(tab_missed,  text=f"  Missed ({len(missed)})  ")
        tabs.add(tab_guessed, text=f"  Guessed ({n_found})  ")

        def _make_grid(parent, items, border_col, name_col):
            outer = tk.Frame(parent, bg=BG)
            outer.pack(fill="both", expand=True, padx=8, pady=8)
            sb  = ttk.Scrollbar(outer, orient="vertical")
            sb.pack(side="right", fill="y")
            cv  = tk.Canvas(outer, bg=BG, highlightthickness=0, yscrollcommand=sb.set)
            cv.pack(side="left", fill="both", expand=True)
            sb.config(command=cv.yview)
            sf  = tk.Frame(cv, bg=BG)
            w   = cv.create_window((0, 0), window=sf, anchor="nw")
            sf.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
            cv.bind("<Configure>", lambda e: cv.itemconfig(w, width=e.width))
            def _wheel(e):
                cv.yview_scroll(-1 if (e.num == 4 or e.delta > 0) else 1, "units")
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                cv.bind(seq, _wheel)
                sf.bind(seq, _wheel)
            if not items:
                tk.Label(sf, text="None!", font=self.fB,
                         bg=BG, fg=SUCCESS).pack(pady=30)
                return
            for i, (code, name) in enumerate(
                    sorted(items, key=lambda x: disp(x[1]))):
                row_idx, col_idx = divmod(i, 3)
                cell = tk.Frame(sf, bg=CARD,
                                highlightbackground=border_col, highlightthickness=1)
                cell.grid(row=row_idx, column=col_idx, padx=4, pady=4, sticky="nsew")
                sf.columnconfigure(col_idx, weight=1)
                flag_lbl = tk.Label(cell, text="...", bg=CARD, fg=MUTED, font=self.fX)
                flag_lbl.pack(pady=(6, 2))
                tk.Label(cell, text=disp(name), font=self.fX, bg=CARD,
                         fg=name_col, wraplength=170, justify="center").pack(pady=(0, 6))
                def _fetch_thumb(c3=code, lbl=flag_lbl):
                    try:
                        data = fetch_bytes(f"https://flagcdn.com/w80/{c3}.png")
                        img  = Image.open(BytesIO(data)).resize((72, 44))
                        ph   = ImageTk.PhotoImage(img)
                        def _set(l=lbl, p=ph):
                            try:
                                if l.winfo_exists():
                                    l.config(image=p, text="")
                                    l.image = p
                            except Exception:
                                pass
                        self.root.after(0, _set)
                    except Exception:
                        pass
                threading.Thread(target=_fetch_thumb, daemon=True).start()

        _make_grid(tab_missed,  missed,
                   border_col=MAP_MISS,   name_col=ERROR)
        _make_grid(tab_guessed,
                   [(code, next(n for n, c2 in ALL_COUNTRIES if c2 == code))
                    for code in self._mp_found],
                   border_col=MAP_FOUND2, name_col=SUCCESS)

        btn_row = tk.Frame(win, bg=BG)
        btn_row.pack(pady=(0, 12))
        tk.Button(btn_row, text="Back to Map", font=self.fX, bg=ACCENT, fg=TEXT,
                  relief="flat", cursor="hand2", padx=16, pady=8,
                  command=win.destroy).pack(side="left", padx=8)
        tk.Button(btn_row, text="Leaderboard", font=self.fX, bg=GOLD, fg="#1a0f00",
                  relief="flat", cursor="hand2", padx=16, pady=8,
                  command=lambda: (win.destroy(), self._leaderboard())).pack(
                  side="left", padx=8)
        tk.Button(btn_row, text="Main Menu", font=self.fS, bg=HIGHLIGHT, fg="white",
                  relief="flat", cursor="hand2", padx=16, pady=10,
                  command=lambda: (win.destroy(), self._menu())).pack(side="left", padx=8)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math
    root = tk.Tk()
    App(root)
    root.mainloop()
