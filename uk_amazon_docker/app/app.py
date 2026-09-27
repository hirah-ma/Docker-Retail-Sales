"""
Flask app that serves the UK Amazon Sales KPI dashboard, reading pre-computed
KPI tables from Postgres (loaded at container startup by load_data.py).
"""
import os
from flask import Flask, jsonify, render_template
from sqlalchemy import create_engine, text
import pandas as pd

DB_USER = os.environ.get("POSTGRES_USER", "etl_user")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "etl_pass")
DB_HOST = os.environ.get("POSTGRES_HOST", "db")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "salesdb")
DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

app = Flask(__name__)
engine = create_engine(DB_URL, pool_pre_ping=True)

KPI_TABLES = [
    "monthly_revenue", "profit_margin", "region_sales", "aov",
    "units_by_category", "top_products", "orders_by_status",
]


def fetch_df(table):
    with engine.connect() as conn:
        return pd.read_sql(f"SELECT * FROM {table}", conn)


@app.route("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return jsonify(status="ok", db="connected"), 200
    except Exception as e:
        return jsonify(status="error", detail=str(e)), 500


@app.route("/")
def dashboard():
    data = {}
    for table in KPI_TABLES:
        try:
            data[table] = fetch_df(table).to_dict(orient="records")
        except Exception as e:
            data[table] = []
    return render_template("index.html", data=data)


@app.route("/api/kpis/<table>")
def kpi_api(table):
    if table not in KPI_TABLES:
        return jsonify(error="unknown KPI table"), 404
    return jsonify(fetch_df(table).to_dict(orient="records"))


@app.route("/api/kpis")
def kpi_list():
    return jsonify(available=KPI_TABLES)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
