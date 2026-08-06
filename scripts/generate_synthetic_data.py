import argparse
import os
import random
import sys

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

def generate_data(target_rows: int):
    print(f"Starting synthetic data generation for target: {target_rows} rows.")
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    # 1. Fetch existing Schemes and Major Heads
    cursor.execute("SELECT scheme_id, scheme_name FROM schemes")
    schemes = cursor.fetchall()
    if not schemes:
        print("Error: No schemes found. Run ETL pipeline first.")
        return

    cursor.execute("SELECT major_head_id FROM major_heads")
    major_heads = [r['major_head_id'] for r in cursor.fetchall()]
    major_heads.append(None) # Allow some NULL major heads

    # 2. Define synthetic fiscal years (10 years)
    synthetic_years = [f"{2000+i}-{2001+i}" for i in range(10)]

    # Insert fiscal years if not exist
    for fy in synthetic_years:
        cursor.execute("INSERT IGNORE INTO fiscal_years (fiscal_year) VALUES (%s)", (fy,))
    conn.commit()

    # Fetch fiscal year IDs
    cursor.execute(
        "SELECT fiscal_year_id, fiscal_year FROM fiscal_years "
        "WHERE fiscal_year IN (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        tuple(synthetic_years),
    )
    fy_map = {row['fiscal_year']: row['fiscal_year_id'] for row in cursor.fetchall()}

    # 3. Calculate how many sub_schemes we need
    # rows = sub_schemes * 10 (years)
    target_sub_schemes = max(1, target_rows // 10)
    sub_schemes_per_scheme = max(1, target_sub_schemes // len(schemes))

    print(
        f"Generating ~{sub_schemes_per_scheme} sub-schemes for each of the "
        f"{len(schemes)} schemes across 10 fiscal years."
    )

    total_inserted = 0
    batch_size = 5000

    for scheme in schemes:
        scheme_id = scheme['scheme_id']
        print(f"Processing Scheme: {scheme['scheme_name']} (ID: {scheme_id})")

        # We will batch inserts to sub_schemes
        sub_scheme_records = []
        for i in range(sub_schemes_per_scheme):
            ss_name = f"Synth_{scheme_id}_{i:06d}_{random.randint(1000, 9999)}"
            mh_id = random.choice(major_heads)
            sub_scheme_records.append((ss_name, scheme_id, mh_id))

        # Insert Sub Schemes in batches
        insert_ss_sql = "INSERT IGNORE INTO sub_schemes (sub_scheme_name, scheme_id, major_head_id) VALUES (%s, %s, %s)"

        for idx in range(0, len(sub_scheme_records), batch_size):
            batch = sub_scheme_records[idx:idx+batch_size]
            cursor.executemany(insert_ss_sql, batch)
        conn.commit()

        # Fetch the newly inserted sub_scheme_ids
        cursor.execute(
            "SELECT sub_scheme_id FROM sub_schemes "
            "WHERE scheme_id = %s AND sub_scheme_name LIKE 'Synth_%'",
            (scheme_id,),
        )
        new_ss_ids = [r['sub_scheme_id'] for r in cursor.fetchall()]

        # Generate budget data
        budget_records = []
        for ss_id in new_ss_ids:
            for fy in synthetic_years:
                fy_id = fy_map[fy]
                actuals = round(random.uniform(10.0, 5000.0), 2) if random.random() > 0.1 else None
                budget = round(random.uniform(10.0, 6000.0), 2)
                revised = round(random.uniform(10.0, 6000.0), 2) if random.random() > 0.2 else None
                budget_records.append((ss_id, fy_id, actuals, budget, revised))

        insert_budget_sql = """
            INSERT IGNORE INTO budget_data (sub_scheme_id, fiscal_year_id, actuals, budget, revised)
            VALUES (%s, %s, %s, %s, %s)
        """
        for idx in range(0, len(budget_records), batch_size):
            batch = budget_records[idx:idx+batch_size]
            cursor.executemany(insert_budget_sql, batch)
            total_inserted += len(batch)

        conn.commit()
        print(f"  -> Inserted {len(budget_records)} budget rows.")

        if total_inserted >= target_rows:
            break

    print(f"Successfully generated {total_inserted} synthetic budget rows.")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic budget data.")
    parser.add_argument("--rows", type=int, default=100000, help="Target number of rows to generate")
    args = parser.parse_args()

    load_dotenv()
    generate_data(args.rows)
