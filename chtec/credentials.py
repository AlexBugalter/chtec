"""Per-user Windows DPAPI encryption, never a plaintext fallback."""

import ctypes
import hashlib
import os
import tempfile
from ctypes import wintypes
from pathlib import Path

from chtec.domain import AppError


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(value: bytes, decrypt=False):
    if os.name != "nt":
        raise AppError("Сохранение API-ключей поддерживается защищённым хранилищем Windows.")
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    method = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    method.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise AppError("Windows не смог прочитать или защитить API-ключ. Введите его заново.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class CredentialStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.unreadable = set()

    def _path(self, provider, endpoint):
        scope = hashlib.sha256(f"{provider}\n{endpoint}".encode()).hexdigest()
        return self.directory / f"{scope}.dpapi"

    def get(self, provider, endpoint):
        path = self._path(provider, endpoint)
        if not path.exists():
            return ""
        try:
            return _crypt(path.read_bytes(), decrypt=True).decode("utf-8")
        except (OSError, UnicodeError, AppError) as exc:
            self.unreadable.add(path)
            raise AppError("Не удалось прочитать защищённый API-ключ.") from exc

    def set(self, provider, endpoint, value):
        path = self._path(provider, endpoint)
        temporary = None
        try:
            if not value:
                if path in self.unreadable:
                    return  # Never destroy an unreadable secret during unrelated settings saves.
                path.unlink(missing_ok=True)
                return
            encrypted = _crypt(value.encode("utf-8"))
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(encrypted)
            os.replace(temporary, path)
            self.unreadable.discard(path)
        except OSError as exc:
            raise AppError("Не удалось сохранить защищённый API-ключ.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
