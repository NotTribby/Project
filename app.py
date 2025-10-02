
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

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

@app.route("/", methods=["GET"])
def index():
    return render_template(
        "index.html",
        base_prices=BASE_PRICES,
        addon_prices=ADDON_PRICES
    )

@app.route("/estimate", methods=["POST"])
def estimate():
    data = request.get_json(force=True) or {}

    category = data.get("category", "Hot Brew")
    style = data.get("style", "Latte")
    size = data.get("size", "Medium")
    shopper_type = data.get("shopper_type", "Personal Curiosity")

    def to_float(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    # quantities
    try:
        per_week = max(0, int(float(data.get("per_week", 0) or 0)))
    except ValueError:
        per_week = 0

    sugar_tsp = to_float(data.get("sugar_tsp", 0), 0.0)
    cream_oz = to_float(data.get("cream_oz", 0), 0.0)
    milk_oz = to_float(data.get("milk_oz", 0), 0.0)

    wraw = data.get("whipped", False)
    if isinstance(wraw, bool):
        whipped = wraw
    else:
        whipped = str(wraw).strip().lower() in {"1", "true", "yes", "on"}

    try:
        shots = int(float(data.get("shots", 0) or 0))
    except ValueError:
        shots = 0

    # bulk handling
    try:
        bulk_qty = max(0, int(float(data.get("bulk_qty", 24) or 24)))
    except ValueError:
        bulk_qty = 24

    price_per_cup = calc_price(category, style, size, sugar_tsp, cream_oz, milk_oz, whipped, shots)

    # Base timeline costs
    weekly_cost = round(price_per_cup * per_week, 2)
    monthly_cost = round(weekly_cost * 4.33, 2)  # average weeks per month
    yearly_cost = round(weekly_cost * 52, 2)

    # Bulk mode calculations
    bulk_cost = None
    breakdown_bulk = None
    if shopper_type == "Bulk Shopper":
        bulk_cost = round(price_per_cup * bulk_qty, 2)
        breakdown_bulk = {
            "Base drink x{}".format(bulk_qty): round(BASE_PRICES[category][style][size] * bulk_qty, 2),
            "Sugar x{}".format(bulk_qty): round(sugar_tsp * ADDON_PRICES["Sugar (per teaspoon)"] * bulk_qty, 2),
            "Cream x{}".format(bulk_qty): round(cream_oz * ADDON_PRICES["Cream (per oz)"] * bulk_qty, 2),
            "Milk x{}".format(bulk_qty): round(milk_oz * ADDON_PRICES["Milk (per oz)"] * bulk_qty, 2),
            "Whipped Cream x{}".format(bulk_qty): (ADDON_PRICES["Whipped Cream (flat)"] * bulk_qty if whipped else 0.0),
            "Espresso Shots x{}".format(bulk_qty): round(shots * ADDON_PRICES["Espresso Shot (each)"] * bulk_qty, 2),
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
        "shopper_type": shopper_type
    }
    if bulk_cost is not None:
        resp["bulk_cost"] = bulk_cost
        resp["bulk_qty"] = bulk_qty
        resp["breakdown_bulk"] = breakdown_bulk

    return jsonify(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
    #This is a Test comment for Github
