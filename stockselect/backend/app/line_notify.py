"""LINE 單人推播：本機環境變數、額度檢查、持久化防重與相同請求重試。

不保存 Token／User ID，不接受群發；HTTP 200 只表示 LINE 接受請求。
SQLite 保留原訊息與 retry key，跨行程／重跑都不重新組裝失敗的請求。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCAL_DIR = Path(__file__).resolve().parents[1] / ".local"
API = "https://api.line.me/v2/bot"
LEASE_SECONDS = 120
RETRY_WINDOW_SECONDS = 23 * 3600  # LINE retry key 有效 24 小時，留安全餘裕


class NotificationError(RuntimeError):
    """只包含可安全輸出的錯誤，不回傳 HTTP 本文、URL 或憑證。"""


def environment_value(name):
    """Windows 長駐 app 尚未繼承新變數時，補讀使用者／系統的已儲存設定。"""
    value = os.environ.get(name, "").strip()
    if value or os.name != "nt":
        return value
    import winreg
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value = str(winreg.QueryValueEx(handle, name)[0]).strip()
                if value:
                    return value
        except OSError:
            continue
    return ""


def credentials():
    token, user_id = environment_value("LINE_TOKEN"), environment_value("LINE_USER_ID")
    if not token and not user_id:
        return None
    missing = [name for name, value in (("LINE_TOKEN", token), ("LINE_USER_ID", user_id)) if not value]
    if missing:
        raise NotificationError("缺少環境變數：" + "、".join(missing))
    if not re.fullmatch(r"U[0-9a-fA-F]{32}", user_id):
        raise NotificationError("LINE_USER_ID 格式不符：需為 U 開頭、後接 32 個十六進位字元")
    if any(ch.isspace() for ch in token):
        raise NotificationError("LINE_TOKEN 含空白，請確認已完整複製 Channel access token")
    return token, user_id


def _http(token, path, payload=None, retry_key=None, opener=urlopen):
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if retry_key:
        headers["X-Line-Retry-Key"] = retry_key
    request = Request(API + path, headers=headers,
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None,
                      method="POST" if payload else "GET")
    try:
        with opener(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}, dict(response.headers)
    except HTTPError as exc:
        # 不把 LINE 回覆本文印出，避免對端錯誤回顯輸入資料。
        return exc.code, {}, dict(exc.headers or {})
    except (URLError, TimeoutError, OSError):
        return 0, {}, {}
    except (ValueError, UnicodeError):
        raise NotificationError("LINE 回應格式異常") from None


def check_quota(token, opener=urlopen):
    status, quota, _ = _http(token, "/message/quota", opener=opener)
    if status != 200:
        raise NotificationError(f"LINE 額度查詢失敗（HTTP {status or '網路連線失敗'}）")
    status, used, _ = _http(token, "/message/quota/consumption", opener=opener)
    if status != 200:
        raise NotificationError(f"LINE 用量查詢失敗（HTTP {status or '網路連線失敗'}）")
    try:
        usage = int(used["totalUsage"])
        limit = None if quota.get("type") == "none" else int(quota["value"])
    except (KeyError, TypeError, ValueError):
        raise NotificationError("LINE 額度資料不足") from None
    if limit is not None and usage >= limit:
        raise NotificationError("LINE 本月推播額度已用完；保留通知紀錄，未發送")
    return {"limit": limit, "used": usage, "remaining": limit - usage if limit is not None else None}


def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS notifications ("
                 "recipient_hash TEXT NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL,"
                 "retry_key TEXT NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL,"
                 "lease_until REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,"
                 "last_error TEXT, accepted_at REAL, PRIMARY KEY(recipient_hash,event_key))")
    conn.commit()
    return conn


def send_once(event_key, text, *, state_path=None, auth=None, opener=urlopen,
              sleep=time.sleep, now=time.time):
    auth = auth if auth is not None else credentials()
    if auth is None:
        return {"status": "disabled", "message": "未設定 LINE_TOKEN／LINE_USER_ID，略過通知"}
    if not text or len(text.encode("utf-16-le")) // 2 > 5000:
        raise NotificationError("LINE 訊息為空或超過 5000 字限制")
    token, user_id = auth
    recipient = hashlib.sha256(user_id.encode()).hexdigest()
    moment = now()
    with closing(_connect(state_path or LOCAL_DIR / "line_notifications.sqlite3")) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM notifications WHERE recipient_hash=? AND event_key=?",
                           (recipient, event_key)).fetchone()
        if row and row["status"] == "accepted":
            return {"status": "duplicate", "message": "本事件已由 LINE 接受，略過重複推播"}
        if row and row["lease_until"] > moment:
            return {"status": "busy", "message": "另一個行程正在推播，略過重複工作"}
        if row and moment - row["started_at"] >= RETRY_WINDOW_SECONDS:
            raise NotificationError("舊通知已超過安全重試期限，不重新發送以避免重複")
        if row is None:
            conn.execute("INSERT INTO notifications "
                         "(recipient_hash,event_key,payload,retry_key,status,started_at,lease_until) "
                         "VALUES (?,?,?,?,?,?,?)", (recipient, event_key, text, str(uuid.uuid4()),
                                                  "sending", moment, moment + LEASE_SECONDS))
        else:
            conn.execute("UPDATE notifications SET status='sending', lease_until=? "
                         "WHERE recipient_hash=? AND event_key=?", (moment + LEASE_SECONDS, recipient, event_key))
        row = conn.execute("SELECT * FROM notifications WHERE recipient_hash=? AND event_key=?",
                           (recipient, event_key)).fetchone()
        conn.commit()
        try:
            quota = check_quota(token, opener)
            payload = {"to": user_id, "messages": [{"type": "text", "text": row["payload"]}]}
            for attempt in range(3):
                conn.execute("UPDATE notifications SET attempts=attempts+1 WHERE recipient_hash=? AND event_key=?",
                             (recipient, event_key))
                conn.commit()
                status, _, headers = _http(token, "/message/push", payload, row["retry_key"], opener)
                accepted_retry = status == 409 and any(k.lower() == "x-line-accepted-request-id" for k in headers)
                if status == 200 or accepted_retry:
                    conn.execute("UPDATE notifications SET status='accepted', accepted_at=?, lease_until=0,"
                                 "last_error=NULL WHERE recipient_hash=? AND event_key=?",
                                 (now(), recipient, event_key))
                    conn.commit()
                    return {"status": "accepted", "message": "LINE 已接受推播請求", "quota": quota}
                if status != 0 and status < 500:
                    break
                if attempt < 2:
                    sleep(2 ** attempt)
            raise NotificationError(f"LINE 推播失敗（HTTP {status or '網路連線失敗'}）")
        except NotificationError as exc:
            conn.execute("UPDATE notifications SET status='failed', last_error=?, lease_until=0 "
                         "WHERE recipient_hash=? AND event_key=?", (str(exc), recipient, event_key))
            conn.commit()
            raise


def fit_text(text, max_units=4500):
    """LINE 按 UTF-16 計算字數，emoji 可能占兩個單位。"""
    if len(text.encode("utf-16-le")) // 2 <= max_units:
        return text
    suffix = "\n…其餘內容請至決策中心與交易帳查看。"
    budget = max_units - len(suffix.encode("utf-16-le")) // 2
    return text.encode("utf-16-le")[:budget * 2].decode("utf-16-le", errors="ignore") + suffix
