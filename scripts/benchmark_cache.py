import os
import sys
import time

import pandas as pd
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Mock streamlit's cache_data for CLI testing if not running in streamlit
import streamlit as st

from utils.db import get_connection


@st.cache_data
def fetch_data_cached(query: str):
    conn = get_connection()
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def fetch_data_uncached(query: str):
    conn = get_connection()
    df = pd.read_sql(query, conn)
    conn.close()
    return df

if __name__ == "__main__":
    load_dotenv()
    print("Benchmarking Dashboard Data Loading...")

    query = """
        SELECT s.scheme_name, SUM(b.budget) as total_budget
        FROM budget_data b
        JOIN sub_schemes ss ON b.sub_scheme_id = ss.sub_scheme_id
        JOIN schemes s ON ss.scheme_id = s.scheme_id
        GROUP BY s.scheme_name
        ORDER BY total_budget DESC
        LIMIT 10
    """

    # Uncached
    start = time.time()
    fetch_data_uncached(query)
    uncached_time = (time.time() - start) * 1000

    # Cached (Cold Start)
    start = time.time()
    fetch_data_cached(query)
    cold_cache_time = (time.time() - start) * 1000

    # Cached (Warm Start)
    start = time.time()
    fetch_data_cached(query)
    warm_cache_time = (time.time() - start) * 1000

    print(f"Without Cache: {uncached_time:.2f} ms")
    print(f"With Cache: {warm_cache_time:.2f} ms")
