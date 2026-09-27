"""
Loads the raw UK Amazon sales CSV, cleans it, computes KPIs (same logic as
the PySpark ETL pipeline, re-implemented in pandas for a lightweight
container demo), and writes both the cleaned fact table and each KPI table
into Postgres. Runs once at container startup via entrypoint.sh.
"""
import os
import time
import pandas as pd
from sqlalchemy import create_engine, text

DB_USER = os.environ.get("POSTGRES_USER", "etl_user")
DB_PASS = os.environ.get("POSTGRES_PASSWORD", "etl_pass")
DB_HOST = os.environ.get("POSTGRES_HOST", "db")
DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_NAME = os.environ.get("POSTGRES_DB", "salesdb")
CSV_PATH = os.environ.get("CSV_PATH", "/app/data/uk_amazon_sales.csv")

DB_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def wait_for_db(engine, retries=30, delay=2):
    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print(f"[load_data] DB is ready (attempt {attempt}).")
            return
        except Exception as e:
            print(f"[load_data] DB not ready yet (attempt {attempt}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("Database never became ready.")


def clean_transform(df: pd.DataFrame) -> pd.DataFrame:
    df["Order_Date"] = pd.to_datetime(df["Order_Date"], format="%d/%m/%Y", errors="coerce")
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Unit_Price_GBP"] = pd.to_numeric(df["Unit_Price_GBP"], errors="coerce")
    df["Unit_Cost_GBP"] = pd.to_numeric(df["Unit_Cost_GBP"], errors="coerce")
    df["Discount_Percent"] = pd.to_numeric(df["Discount_Percent"], errors="coerce")

    before = len(df)
    df = df.drop_duplicates()
    print(f"[load_data] Duplicates removed: {before - len(df)}")

    df = df.dropna(subset=["Order_ID", "Order_Date", "City", "Region", "Unit_Cost_GBP"])
    df["Quantity"] = df["Quantity"].fillna(1)
    df["Unit_Price_GBP"] = df["Unit_Price_GBP"].fillna(df["Unit_Price_GBP"].median())

    for c in ["City", "Region", "Category", "Product_Name", "Payment_Mode", "Order_Status"]:
        df[c] = df[c].str.strip().str.title()

    df["Is_Valid_Sale"] = df["Order_Status"] != "Cancelled"
    df["Sale_Year"] = df["Order_Date"].dt.year
    df["Sale_Month"] = df["Order_Date"].dt.to_period("M").astype(str)
    df["Gross_Revenue"] = (df["Quantity"] * df["Unit_Price_GBP"]).round(2)
    df["Net_Revenue"] = (df["Quantity"] * df["Unit_Price_GBP"] * (1 - df["Discount_Percent"] / 100)).round(2)
    df["Total_Cost"] = (df["Quantity"] * df["Unit_Cost_GBP"]).round(2)
    df["Profit"] = (df["Net_Revenue"] - df["Total_Cost"]).round(2)

    print(f"[load_data] Final clean row count: {len(df)}")
    return df


def compute_kpis(df: pd.DataFrame) -> dict:
    sales = df[df["Is_Valid_Sale"]]

    monthly_revenue = (
        sales.groupby("Sale_Month")
        .agg(Monthly_Revenue=("Net_Revenue", "sum"), Num_Orders=("Order_ID", "nunique"))
        .reset_index().sort_values("Sale_Month")
    )
    monthly_revenue["Monthly_Revenue"] = monthly_revenue["Monthly_Revenue"].round(2)

    total_profit = sales["Profit"].sum()
    total_revenue = sales["Net_Revenue"].sum()
    profit_margin = pd.DataFrame([{
        "Total_Profit": round(total_profit, 2),
        "Total_Revenue": round(total_revenue, 2),
        "Profit_Margin_Percent": round(total_profit / total_revenue * 100, 2),
    }])

    region_sales = (
        sales.groupby("Region")
        .agg(Region_Revenue=("Net_Revenue", "sum"), Units_Sold=("Quantity", "sum"),
             Num_Orders=("Order_ID", "nunique"))
        .reset_index().sort_values("Region_Revenue", ascending=False)
    )
    region_sales["Region_Revenue"] = region_sales["Region_Revenue"].round(2)

    order_values = sales.groupby("Order_ID")["Net_Revenue"].sum()
    aov = pd.DataFrame([{"Average_Order_Value": round(order_values.mean(), 2)}])

    units_by_category = (
        sales.groupby("Category")["Quantity"].sum()
        .reset_index(name="Total_Units_Sold").sort_values("Total_Units_Sold", ascending=False)
    )

    top_products = (
        sales.groupby(["Product_Name", "Category"])
        .agg(Product_Revenue=("Net_Revenue", "sum"), Units_Sold=("Quantity", "sum"))
        .reset_index().sort_values("Product_Revenue", ascending=False).head(10)
    )
    top_products["Product_Revenue"] = top_products["Product_Revenue"].round(2)

    orders_by_status = (
        df.groupby("Order_Status")["Order_ID"].nunique()
        .reset_index(name="Order_Count").sort_values("Order_Count", ascending=False)
    )

    return {
        "monthly_revenue": monthly_revenue,
        "profit_margin": profit_margin,
        "region_sales": region_sales,
        "aov": aov,
        "units_by_category": units_by_category,
        "top_products": top_products,
        "orders_by_status": orders_by_status,
    }


def main():
    engine = create_engine(DB_URL)
    wait_for_db(engine)

    print(f"[load_data] Reading raw CSV from {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"[load_data] Raw rows loaded: {len(df)}")

    clean_df = clean_transform(df)
    kpis = compute_kpis(clean_df)

    with engine.begin() as conn:
        clean_cols = [
            "Order_ID", "Order_Date", "Customer_ID", "City", "Region", "Category",
            "Product_Name", "Quantity", "Unit_Price_GBP", "Unit_Cost_GBP",
            "Discount_Percent", "Payment_Mode", "Order_Status", "Sale_Year",
            "Sale_Month", "Net_Revenue", "Profit",
        ]
        clean_df[clean_cols].to_sql("cleaned_sales_fact", conn, if_exists="replace", index=False)
        print("[load_data] Loaded table: cleaned_sales_fact")

        for name, kpi_df in kpis.items():
            kpi_df.to_sql(name, conn, if_exists="replace", index=False)
            print(f"[load_data] Loaded table: {name} ({len(kpi_df)} rows)")

    print("[load_data] Done. All tables loaded into Postgres.")


if __name__ == "__main__":
    main()
