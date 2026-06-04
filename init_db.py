"""
Run once at startup: copies boards_commissions.db to the persistent disk
if the database hasn't been initialized yet.
"""
import os, sqlite3, shutil

DATA_DB = os.environ.get('DB_PATH', '/data/boards_commissions.db')
SRC_DB  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'boards_commissions.db')

needs_init = True
if os.path.exists(DATA_DB):
    try:
        conn = sqlite3.connect(DATA_DB)
        conn.execute("SELECT 1 FROM persons LIMIT 1")
        conn.close()
        needs_init = False
        print(f"Database already initialized at {DATA_DB}, skipping copy.")
    except Exception:
        pass

if needs_init:
    print(f"Initializing database: copying {SRC_DB} → {DATA_DB}")
    os.makedirs(os.path.dirname(DATA_DB), exist_ok=True)
    shutil.copy2(SRC_DB, DATA_DB)
    print("Database initialized successfully.")
