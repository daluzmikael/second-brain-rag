from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector

from app.config import DATABASE_URL


@contextmanager
def get_connection():
    conn = psycopg.connect(DATABASE_URL, autocommit=True)
    try:
        register_vector(conn)
        yield conn
    finally:
        conn.close()
