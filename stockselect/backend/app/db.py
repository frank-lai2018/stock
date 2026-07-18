"""PostgreSQL 連線池 + 唯讀查詢輔助（psycopg2）。"""
from contextlib import contextmanager

from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from .config import DATABASE_URL

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL 未設定（請填 backend/.env）")
        _pool = ThreadedConnectionPool(1, 8, dsn=DATABASE_URL)
    return _pool


@contextmanager
def _cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.rollback()            # 只讀：不留交易狀態
    finally:
        pool.putconn(conn)


def query(sql, params=None):
    """執行唯讀查詢，回傳 list[dict]。params 用具名參數 %(key)s。"""
    with _cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


@contextmanager
def _wcursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def execute(sql, params=None, returning=False):
    """執行寫入（INSERT/UPDATE/DELETE），commit。returning=True 時回傳 RETURNING 結果。"""
    with _wcursor() as cur:
        cur.execute(sql, params or {})
        if returning:
            return cur.fetchall()
        return cur.rowcount
