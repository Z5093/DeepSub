from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import zlib

from app.common.paths import get_data_path

_CACHE_DB_PATH = get_data_path("cache", "asr_cache.db")


def _ensure_cache_dir() -> None:
    cache_dir = os.path.dirname(_CACHE_DB_PATH)
    os.makedirs(cache_dir, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    _ensure_cache_dir()
    conn = sqlite3.connect(_CACHE_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS asr_cache ("
        "  cache_key TEXT PRIMARY KEY,"
        "  result_json TEXT NOT NULL,"
        "  audio_path TEXT NOT NULL,"
        "  created_at REAL NOT NULL"
        ")"
    )
    conn.commit()
    return conn


def compute_audio_crc32(file_path: str, chunk_size: int = 65536) -> int:
    crc = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def compute_args_md5(args: list[str]) -> str:
    args_str = " ".join(args)
    return hashlib.md5(args_str.encode("utf-8")).hexdigest()


def generate_asr_cache_key(audio_path: str, cmd_args: list[str]) -> str:
    crc = compute_audio_crc32(audio_path)
    args_hash = compute_args_md5(cmd_args)
    return f"{crc:08x}_{args_hash}"


def get_cached_asr_result(cache_key: str) -> dict | None:
    try:
        conn = _get_db()
        cursor = conn.execute(
            "SELECT result_json FROM asr_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return None
    except Exception:
        return None


def save_asr_cache(cache_key: str, result: dict, audio_path: str) -> bool:
    try:
        import time
        conn = _get_db()
        conn.execute(
            "INSERT OR REPLACE INTO asr_cache (cache_key, result_json, audio_path, created_at) "
            "VALUES (?, ?, ?, ?)",
            (cache_key, json.dumps(result, ensure_ascii=False), audio_path, time.time()),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def clear_asr_cache() -> bool:
    try:
        conn = _get_db()
        conn.execute("DELETE FROM asr_cache")
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def prune_asr_cache(max_age_days: int = 30) -> int:
    try:
        import time
        cutoff = time.time() - max_age_days * 86400
        conn = _get_db()
        cursor = conn.execute(
            "DELETE FROM asr_cache WHERE created_at < ?", (cutoff,)
        )
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        return deleted
    except Exception:
        return 0
