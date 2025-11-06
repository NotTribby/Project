from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, os, json
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

DB_PATH = os.path.join(os.path.dirname(__file__), "app.db")

# --- Pricing Tables (Average Prices) ---
BASE_PRICES = {
    "Hot Brew": {
        "Cappuccino": {"Small": 3.75, "Medium": 4.25, "Large": 4.75},
        "Latte": {"Small": 3.95, "Medium": 4.45, "Large": 4.95},
        "Espresso": {"Small": 2.25, "Medium": 2.75, "Large": 3.25},
    },
    "Cold Brew": {
        "Frappuccino": {"Small": 4.75, "Medium": 5.25, "Large": 5.95},
        "Iced Coffee": {"Small": 3.25, "Medium": 3.75, "Large": 4.25},
    }
}

ADDON_PRICES = {
    "Sugar (per teaspoon)": 0.10,
    "Cream (per oz)": 0.25,
    "Milk (per oz)": 0.20,
    "Whipped Cream (flat)": 0.50,
    "Espresso Shot (each)": 1.00,
}

# ---------- DB Helpers ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_columns(cur, table, want_cols):
    """Add missing columns to an existing SQLite table, safely skipping duplicates."""
    cur.execute(f"PRAGMA table_info({table})")
    rows = cur.fetchall()
    existing = set()
    for row in rows:
        # row can be tuple or Row; index 1 is the column name
        existing.add(row[1] if isinstance(row, tuple) else row["name"])
    for name, ddl in want_cols.items():
        if name not in existing:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                print(f"Added missing column '{name}' to '{table}'.")
            except Exception as e:
                msg = str(e).lower()
                if "duplicate column name" in msg or "already exists" in msg:
                    print(f"Skipped duplicate column '{name}' in '{table}'.")
                else:
                    raise

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        profile_type TEXT NOT NULL CHECK (profile_type IN ('Daily Shopper','Bulk Purchaser')),
        created_at TEXT NOT NULL
      )
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
      )
    """)
    # Migrate: add name and updated_at if missing
    ensure_columns(cur, "estimates", {
        "name": "TEXT",
        "updated_at": "TEXT"
    })
    conn.commit()
    conn.close()

# ---------- Auth Utilities ----------


from flask import g

@app.before_request
def _ensure_db():
    # Idempotent: guarantees tables exist even if app started without running init_db()
    try:
        init_db()
    except Exception:
        pass
def current_user():
    if "user_id" in session:
        return {
            "id": session["user_id"],
            "username": session["username"],
            "email": session["email"],
            "profile_type": session["profile_type"],
        }
    return None

def login_user(row):
    session["user_id"] = row["id"]
    session["username"] = row["username"]
    session["email"] = row["email"]
    session["profile_type"] = row["profile_type"]

def logout_user():
    session.clear()

# ---------- Pricing ----------
def calc_price(category, style, size, sugar_tsp, cream_oz, milk_oz, whipped, shots):
    base = BASE_PRICES[category][style][size]
    addons = (
        sugar_tsp * ADDON_PRICES["Sugar (per teaspoon)"]
        + cream_oz * ADDON_PRICES["Cream (per oz)"]
        + milk_oz * ADDON_PRICES["Milk (per oz)"]
        + (ADDON_PRICES["Whipped Cream (flat)"] if whipped else 0.0)
        + shots * ADDON_PRICES["Espresso Shot (each)"]
    )
    return round(base + addons, 2)

# ---------- Main / Auth ----------
@app.route("/")
def home():
    return render_template("main.html", user=current_user())

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")
    # POST
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    profile_type = request.form.get("profile_type", "Daily Shopper")

    if not username or not email or not password or not confirm:
        flash("All fields are required.", "error")
        return redirect(url_for("register"))
    if password != confirm:
        flash("Passwords do not match.", "error")
        return redirect(url_for("register"))
    if profile_type not in ("Daily Shopper", "Bulk Purchaser"):
        flash("Invalid profile type.", "error")
        return redirect(url_for("register"))

    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, email, password_hash, profile_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (username, email, generate_password_hash(password), profile_type, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        flash("Username or email already exists.", "error")
        return redirect(url_for("register"))

    # Auto-login after registration
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email=?", (email,))
    row = cur.fetchone()
    conn.close()
    login_user(row)
    return redirect(url_for("estimator"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    # POST
    ident = request.form.get("identifier", "").strip()
    password = request.form.get("password", "")

    conn = db()
    cur = conn.cursor()
    # Allow email OR username
    cur.execute("SELECT * FROM users WHERE email=? OR username=?", (ident.lower(), ident))
    row = cur.fetchone()
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        flash("Invalid credentials.", "error")
        return redirect(url_for("login"))

    login_user(row)
    return redirect(url_for("estimator"))

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("home"))

@app.route("/guest")
def guest():
    # Guest gets no exclusive features (no save, no bulk)
    logout_user()
    session["guest"] = True
    return redirect(url_for("estimator"))

# ---------- Estimator Pages ----------
@app.route("/estimator")
def estimator():
    user = current_user()
    profile_type = user["profile_type"] if user else ("Guest" if session.get("guest") else "Guest")
    allow_bulk = (profile_type == "Bulk Purchaser")
    allow_save = (profile_type in ("Daily Shopper", "Bulk Purchaser"))
    # optional estimate_id param to preload an estimate
    estimate_id = request.args.get("estimate_id")
    return render_template(
        "estimator.html",
        base_prices=BASE_PRICES,
        addon_prices=ADDON_PRICES,
        allow_bulk=allow_bulk,
        allow_save=allow_save,
        user=user,
        profile_type=profile_type,
        estimate_id=estimate_id or ""
    )

@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.get_json(force=True) or {}
    user = current_user()
    profile_type = user["profile_type"] if user else ("Guest" if session.get("guest") else "Guest")
    allow_bulk = (profile_type == "Bulk Purchaser")

    category = data.get("category", "Hot Brew")
    style = data.get("style", "Latte")
    size = data.get("size", "Medium")

    def to_float(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    def to_int(x, default=0):
        try:
            return int(float(x or default))
        except (TypeError, ValueError):
            return default

    per_week = max(0, to_int(data.get("per_week", 0)))
    sugar_tsp = to_float(data.get("sugar_tsp", 0), 0.0)
    cream_oz = to_float(data.get("cream_oz", 0), 0.0)
    milk_oz = to_float(data.get("milk_oz", 0), 0.0)

    wraw = data.get("whipped", False)
    whipped = (wraw if isinstance(wraw, bool) else str(wraw).strip().lower() in {"1","true","yes","on"})

    shots = to_int(data.get("shots", 0))
    price_per_cup = calc_price(category, style, size, sugar_tsp, cream_oz, milk_oz, whipped, shots)

    weekly_cost = round(price_per_cup * per_week, 2)
    monthly_cost = round(weekly_cost * 4.33, 2)
    yearly_cost = round(weekly_cost * 52, 2)

    bulk_cost = None
    breakdown_bulk = None
    bulk_qty = None
    if allow_bulk and (str(data.get("shopper_type")) == "Bulk Shopper"):
        bulk_qty = max(1, to_int(data.get("bulk_qty", 24), 24))
        bulk_cost = round(price_per_cup * bulk_qty, 2)
        breakdown_bulk = {
            f"Base drink x{bulk_qty}": round(BASE_PRICES[category][style][size] * bulk_qty, 2),
            f"Sugar x{bulk_qty}": round(sugar_tsp * ADDON_PRICES["Sugar (per teaspoon)"] * bulk_qty, 2),
            f"Cream x{bulk_qty}": round(cream_oz * ADDON_PRICES["Cream (per oz)"] * bulk_qty, 2),
            f"Milk x{bulk_qty}": round(milk_oz * ADDON_PRICES["Milk (per oz)"] * bulk_qty, 2),
            f"Whipped Cream x{bulk_qty}": (ADDON_PRICES["Whipped Cream (flat)"] * bulk_qty if whipped else 0.0),
            f"Espresso Shots x{bulk_qty}": round(shots * ADDON_PRICES["Espresso Shot (each)"] * bulk_qty, 2),
        }

    breakdown = {
        "Base drink": BASE_PRICES[category][style][size],
        "Sugar": round(sugar_tsp * ADDON_PRICES["Sugar (per teaspoon)"], 2),
        "Cream": round(cream_oz * ADDON_PRICES["Cream (per oz)"], 2),
        "Milk": round(milk_oz * ADDON_PRICES["Milk (per oz)"], 2),
        "Whipped Cream": ADDON_PRICES["Whipped Cream (flat)"] if whipped else 0.0,
        "Espresso Shots": round(shots * ADDON_PRICES["Espresso Shot (each)"], 2),
    }

    resp = {
        "price_per_cup": price_per_cup,
        "weekly_cost": weekly_cost,
        "monthly_cost": monthly_cost,
        "yearly_cost": yearly_cost,
        "breakdown": breakdown,
        "allow_bulk": allow_bulk
    }
    if bulk_cost is not None:
        resp["bulk_cost"] = bulk_cost
        resp["bulk_qty"] = bulk_qty
        resp["breakdown_bulk"] = breakdown_bulk

    return jsonify(resp)

# ---------- Save & CRUD for Estimates (Daily Shopper & Bulk Purchaser only) ----------
def require_user_can_save():
    user = current_user()
    if not user:
        return None, (jsonify({"ok": False, "error": "Login required."}), 401)
    if user["profile_type"] not in ("Daily Shopper", "Bulk Purchaser"):
        return None, (jsonify({"ok": False, "error": "Your profile cannot save."}), 403)
    return user, None

@app.route("/save_estimate", methods=["POST"])
def save_estimate():
    user, error = require_user_can_save()
    if error: return error
    payload = request.get_json(force=True) or {}
    name = payload.pop("name", None) or f"Estimate {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
    conn = db()
    cur = conn.cursor();
    cur.execute(
        "INSERT INTO estimates (user_id, name, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], name, json.dumps(payload), datetime.utcnow().isoformat(), datetime.utcnow().isoformat()),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return jsonify({"ok": True, "id": new_id, "name": name})

@app.route("/rename_estimate", methods=["POST"])
def rename_estimate():
    user, error = require_user_can_save()
    if error: return error
    data = request.get_json(force=True) or {}
    est_id = int(data.get("id", 0))
    new_name = (data.get("name") or "").strip()
    if not est_id or not new_name:
        return jsonify({"ok": False, "error": "Missing id or name."}), 400
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE estimates SET name=?, updated_at=? WHERE id=? AND user_id=?", (new_name, datetime.utcnow().isoformat(), est_id, user["id"]))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({"ok": True})

@app.route("/update_estimate", methods=["POST"])
def update_estimate():
    user, error = require_user_can_save()
    if error: return error
    data = request.get_json(force=True) or {}
    est_id = int(data.get("id", 0))
    payload = data.get("payload", {})
    if not est_id:
        return jsonify({"ok": False, "error": "Missing id."}), 400
    conn = db()
    cur = conn.cursor()
    cur.execute("UPDATE estimates SET payload=?, updated_at=? WHERE id=? AND user_id=?", (json.dumps(payload), datetime.utcnow().isoformat(), est_id, user["id"]))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({"ok": True})

@app.route("/delete_estimate", methods=["POST"])
def delete_estimate():
    user, error = require_user_can_save()
    if error: return error
    data = request.get_json(force=True) or {}
    est_id = int(data.get("id", 0))
    if not est_id:
        return jsonify({"ok": False, "error": "Missing id."}), 400
    conn = db()
    cur = conn.cursor()
    cur.execute("DELETE FROM estimates WHERE id=? AND user_id=?", (est_id, user["id"]))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    if not changed:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({"ok": True})

@app.route("/get_estimate/<int:est_id>")
def get_estimate(est_id):
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "Login required."}), 401
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, payload, created_at, updated_at FROM estimates WHERE id=? AND user_id=?", (est_id, user["id"]))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"ok": False, "error": "Not found."}), 404
    return jsonify({
        "ok": True,
        "id": row["id"],
        "name": row["name"],
        "payload": json.loads(row["payload"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    })

@app.route("/my_estimates")
def my_estimates():
    user = current_user()
    if not user:
        flash("Login required.", "error")
        return redirect(url_for("login"))
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT id, name, payload, created_at, updated_at FROM estimates WHERE user_id=? ORDER BY id DESC", (user["id"],))
    rows = cur.fetchall()
    conn.close()
    items = [(r["id"], r["name"], json.loads(r["payload"]), r["created_at"], r["updated_at"]) for r in rows]
    return render_template("estimates.html", items=items, user=user)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
