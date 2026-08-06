import argparse
import json
import os
import sys
import time

import mysql.connector
from dotenv import load_dotenv

# Add parent directory to path so we can import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


def get_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
    )

def extract_explain_metrics(explain_json):
    """Recursively extract metrics from MySQL EXPLAIN JSON."""
    cost = 0.0
    rows_examined = 0

    if isinstance(explain_json, dict):
        if "query_cost" in explain_json:
            cost += float(explain_json["query_cost"])
        if "rows_examined_per_scan" in explain_json:
            rows_examined += int(explain_json["rows_examined_per_scan"])

        for k, v in explain_json.items():
            child_cost, child_rows = extract_explain_metrics(v)
            cost += child_cost
            rows_examined += child_rows

    elif isinstance(explain_json, list):
        for item in explain_json:
            child_cost, child_rows = extract_explain_metrics(item)
            cost += child_cost
            rows_examined += child_rows

    return cost, rows_examined

def run_benchmark(query: str, name: str = "Query"):
    print(f"\n{'='*60}")
    print(f"BENCHMARK: {name}")
    print(f"{'='*60}")

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Run EXPLAIN FORMAT=JSON
    explain_sql = f"EXPLAIN FORMAT=JSON {query}"
    try:
        cursor.execute(explain_sql)
        explain_result = cursor.fetchone()
        # The key is usually 'EXPLAIN'
        explain_json_str = list(explain_result.values())[0]
        explain_data = json.loads(explain_json_str)

        cost, rows_examined = extract_explain_metrics(explain_data["query_block"])
    except Exception as e:
        print(f"Warning: Could not extract EXPLAIN metrics: {e}")
        cost, rows_examined = 0.0, 0

    # 2. Flush buffers to get cold cache performance if possible
    # We can't force flush easily from a client connection without SUPER privileges,
    # but we can at least measure raw execution.

    # 3. Run Query
    start_time = time.time()
    cursor.execute(query)
    rows = cursor.fetchall()
    end_time = time.time()

    execution_time_ms = (end_time - start_time) * 1000
    rows_returned = len(rows)

    print(f"Query: {query.strip()[:80]}...")
    print("-" * 60)
    print(f"Rows Returned : {rows_returned}")
    print(f"Rows Examined : {rows_examined} (Estimated by Optimizer)")
    print(f"Query Cost    : {cost:.2f}")
    print(f"Execution Time: {execution_time_ms:.2f} ms")
    print(f"{'='*60}\n")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark SQL Queries.")
    args = parser.parse_args()

    load_dotenv()

    # Define baseline analytical queries to benchmark
    queries = {
        "Q1: Global Aggregation (Full Scan)": """
            SELECT SUM(actuals) as total_actuals, SUM(budget) as total_budget
            FROM budget_data
        """,
        "Q2: Grouped Aggregation (Scheme Level)": """
            SELECT s.scheme_name, SUM(b.budget) as total_budget
            FROM budget_data b
            JOIN sub_schemes ss ON b.sub_scheme_id = ss.sub_scheme_id
            JOIN schemes s ON ss.scheme_id = s.scheme_id
            WHERE b.fiscal_year_id = (SELECT MAX(fiscal_year_id) FROM fiscal_years)
            GROUP BY s.scheme_name
            ORDER BY total_budget DESC
            LIMIT 10
        """,
        "Q3: Specific Sub-Scheme Trend": """
            SELECT f.fiscal_year, b.budget, b.actuals
            FROM budget_data b
            JOIN fiscal_years f ON b.fiscal_year_id = f.fiscal_year_id
            WHERE b.sub_scheme_id = 50
            ORDER BY f.fiscal_year ASC
        """
    }

    for name, sql in queries.items():
        run_benchmark(sql, name)
