"""Ladybug 接続の前処理（Windows は OpenSSL 3 の DLL 名を合わせ、pybind を使う）。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_OPENSSL_READY = False


def ensure_windows_openssl() -> None:
    """ladybug._lbug は libssl-3-x64.dll を探す。CPython 同梱は libssl-3.dll。"""
    global _OPENSSL_READY
    if _OPENSSL_READY or sys.platform != "win32":
        _OPENSSL_READY = True
        return
    dlls = Path(sys.base_prefix) / "DLLs"
    cache = Path.home() / ".lbdb" / "win-openssl"
    cache.mkdir(parents=True, exist_ok=True)
    pairs = (
        ("libssl-3.dll", "libssl-3-x64.dll"),
        ("libcrypto-3.dll", "libcrypto-3-x64.dll"),
    )
    for src_name, dst_name in pairs:
        src = dlls / src_name
        dst = cache / dst_name
        if not src.exists():
            continue
        if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
            shutil.copy2(src, dst)
    os.add_dll_directory(str(cache))
    if dlls.is_dir():
        os.add_dll_directory(str(dlls))
    _OPENSSL_READY = True


def import_ladybug():
    ensure_windows_openssl()
    try:
        import ladybug as lb
    except ImportError as exc:
        raise ImportError("ladybug が入っていない。pip install ladybug") from exc
    try:
        import ladybug._lbug  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "ladybug._lbug を読めない。Windows では OpenSSL 3 "
            f"(libssl-3.dll) が {Path(sys.base_prefix) / 'DLLs'} に必要。"
        ) from exc
    return lb


def connect(db_path: Path, *, read_only: bool = False):
    lb = import_ladybug()
    database = lb.Database(str(db_path), read_only=read_only, backend="pybind")
    connection = lb.Connection(database)
    return database, connection
