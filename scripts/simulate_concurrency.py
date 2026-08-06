import os
import sys
import threading
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

def concurrent_insert_worker(worker_id: int):
    """
    Simulates a worker trying to insert overlapping data to demonstrate
    InnoDB row-level locking and gap locks.
    """
    print(f"[Worker {worker_id}] Starting transaction...")
    conn = get_connection()
    # Explicitly set isolation level if desired, or use MySQL default (REPEATABLE READ)
    conn.start_transaction(isolation_level="REPEATABLE READ")
    cursor = conn.cursor()

    try:
        # 1. Insert a shared sub_scheme (both workers will try to insert this)
        # Using ON DUPLICATE KEY UPDATE avoids crashing, but triggers locks!
        sql_ss = """
            INSERT INTO sub_schemes (sub_scheme_name, scheme_id)
            VALUES ('Concurrency Test Scheme', 1)
            ON DUPLICATE KEY UPDATE scheme_id = VALUES(scheme_id)
        """
        cursor.execute(sql_ss)
        print(f"[Worker {worker_id}] Executed sub_scheme UPSERT. Holding lock...")

        # 2. Artificial delay to ensure the other thread hits the lock
        time.sleep(2)

        # 3. Insert budget data
        # To demonstrate deadlocks, we would insert in opposite order.
        # But here we just demonstrate Lock Wait Timeout or successful serialization.
        sql_budget = """
            INSERT INTO budget_data (sub_scheme_id, fiscal_year_id, budget)
            VALUES (LAST_INSERT_ID(), 1, 1000.00)
            ON DUPLICATE KEY UPDATE budget = budget + VALUES(budget)
        """
        cursor.execute(sql_budget)
        print(f"[Worker {worker_id}] Executed budget UPSERT.")

        conn.commit()
        print(f"[Worker {worker_id}] Transaction COMMIT successful.")

    except mysql.connector.Error as err:
        print(f"[Worker {worker_id}] Transaction FAILED: {err}")
        conn.rollback()
        print(f"[Worker {worker_id}] Transaction ROLLBACK.")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    load_dotenv()
    print("Simulating Concurrent ETL Loads (Race Condition / Locking Test)\n")

    t1 = threading.Thread(target=concurrent_insert_worker, args=(1,))
    t2 = threading.Thread(target=concurrent_insert_worker, args=(2,))

    t1.start()
    # Stagger slightly so T1 gets the lock first
    time.sleep(0.5)
    t2.start()

    t1.join()
    t2.join()

    print("\nSimulation Complete. Notice how Worker 2 blocks waiting for Worker 1 to release the row lock.")
