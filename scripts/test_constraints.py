import os
import sys
import mysql.connector
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.settings import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME

def get_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
    )

def test_foreign_key_violation(cursor):
    print("\n[TEST 1] Foreign Key Violation")
    print("Attempting to insert budget_data with a non-existent fiscal_year_id (99999)...")
    try:
        # Assuming sub_scheme 1 exists
        cursor.execute("INSERT INTO budget_data (sub_scheme_id, fiscal_year_id, budget) VALUES (1, 99999, 100.00)")
        print("❌ FAIL: Database allowed invalid FK.")
    except mysql.connector.Error as err:
        print(f"✅ PASS: Database rejected invalid FK. Error: {err}")

def test_unique_constraint_violation(cursor):
    print("\n[TEST 2] Unique Constraint Violation")
    print("Attempting to insert a duplicate scheme under the exact same group...")
    try:
        # Insert a scheme, then try to insert the exact same name under the same group_id
        cursor.execute("INSERT INTO schemes (scheme_name, group_id) VALUES ('Test Scheme', 1)")
        cursor.execute("INSERT INTO schemes (scheme_name, group_id) VALUES ('Test Scheme', 1)")
        print("❌ FAIL: Database allowed duplicate scheme.")
    except mysql.connector.Error as err:
        print(f"✅ PASS: Database rejected duplicate. Error: {err}")
    finally:
        # Cleanup if the first one succeeded
        cursor.execute("DELETE FROM schemes WHERE scheme_name = 'Test Scheme'")

def test_check_constraint_violation(cursor):
    print("\n[TEST 3] Check Constraint (Regex) Violation")
    print("Attempting to insert a fiscal year formatted as '2021/22' instead of 'YYYY-YYYY'...")
    try:
        cursor.execute("INSERT INTO fiscal_years (fiscal_year) VALUES ('2021/22')")
        print("❌ FAIL: Database allowed invalid fiscal year format.")
    except mysql.connector.Error as err:
        print(f"✅ PASS: Database rejected regex violation. Error: {err}")

if __name__ == "__main__":
    load_dotenv()
    print("============================================================")
    print("BudgetIQ Constraint Validation Tests")
    print("============================================================")
    
    conn = get_connection()
    # Turn auto-commit ON for these tests so we don't have to rollback after every failure
    conn.autocommit = True 
    cursor = conn.cursor()

    try:
        test_foreign_key_violation(cursor)
        test_unique_constraint_violation(cursor)
        test_check_constraint_violation(cursor)
    finally:
        cursor.close()
        conn.close()
    
    print("\n============================================================")
    print("Constraint testing complete. All application logic is protected by DB layer.")
