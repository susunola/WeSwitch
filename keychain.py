"""macOS Keychain access. Never put credential values in argv or files."""
from __future__ import annotations

import ctypes as C
import re
import subprocess
import sys

SERVICE = "local.codex-model-ui"


class KeychainError(Exception):
    pass


class Keychain:
    def __init__(self):
        self.available = sys.platform == "darwin"
        if not self.available:
            return
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = C.CDLL("/System/Library/Frameworks/Security.framework/Security")
        p = C.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [p, C.c_char_p, C.c_uint32]
        self.cf.CFStringCreateWithCString.restype = p
        self.cf.CFDataCreate.argtypes = [p, p, C.c_long]
        self.cf.CFDataCreate.restype = p
        self.cf.CFDictionaryCreate.argtypes = [p, C.POINTER(p), C.POINTER(p), C.c_long, p, p]
        self.cf.CFDictionaryCreate.restype = p
        self.cf.CFRelease.argtypes = [p]
        self.sec.SecItemAdd.argtypes = [p, C.POINTER(p)]
        self.sec.SecItemAdd.restype = C.c_int32
        self.sec.SecItemCopyMatching.argtypes = [p, C.POINTER(p)]
        self.sec.SecItemCopyMatching.restype = C.c_int32

    def const(self, name):
        return C.c_void_p.in_dll(self.sec, name).value

    def string(self, value):
        return self.cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)

    def query(self, account, secret=None):
        owned = []
        service, account_obj = self.string(SERVICE), self.string(account)
        owned.extend([service, account_obj])
        pairs = [
            (self.const("kSecClass"), self.const("kSecClassGenericPassword")),
            (self.const("kSecAttrService"), service),
            (self.const("kSecAttrAccount"), account_obj),
        ]
        if secret is not None:
            data = secret.encode("utf-8")
            buf = C.create_string_buffer(data)
            value = self.cf.CFDataCreate(None, C.cast(buf, C.c_void_p), len(data))
            label = self.string("Codex custom provider / " + account.split(".")[0])
            owned.extend([value, label])
            pairs.extend([(self.const("kSecValueData"), value), (self.const("kSecAttrLabel"), label)])
        keys = (C.c_void_p * len(pairs))(*(x[0] for x in pairs))
        values = (C.c_void_p * len(pairs))(*(x[1] for x in pairs))
        # Null callbacks are intentional: all referenced CF objects stay alive until release.
        dictionary = self.cf.CFDictionaryCreate(None, keys, values, len(pairs), None, None)
        return dictionary, owned

    def exists(self, account):
        if not self.available:
            return False
        query, owned = self.query(account)
        try:
            status = self.sec.SecItemCopyMatching(query, None)
            if status in (-25300, -25308, -25293):
                return False
            if status != 0:
                raise KeychainError(f"无法检查钥匙串（系统状态 {status}）。请先解锁登录钥匙串。")
            return True
        finally:
            self.cf.CFRelease(query)
            for obj in owned:
                self.cf.CFRelease(obj)

    def read(self, account):
        """Read only this tool's credential; never execute a configured external helper."""
        if not self.available or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.[a-f0-9]{24}", account):
            raise KeychainError("无法读取这个钥匙串条目，请在表单中重新填写 API Key。")
        try:
            result = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-s", SERVICE, "-a", account, "-w"],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=25, check=False,
            )
            if result.returncode != 0:
                raise KeychainError("钥匙串读取未获授权或凭据不存在。请解锁钥匙串，或在表单中重新填写 API Key。")
            value = result.stdout.decode("utf-8").rstrip("\r\n")
            if not value:
                raise KeychainError("钥匙串中的凭据为空，请重新填写 API Key。")
            return value
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
            raise KeychainError("钥匙串读取失败或等待授权超时；请重试，或直接在表单中填写 API Key。") from None

    def add(self, account, secret):
        if not self.available:
            raise KeychainError("此环境无法使用 macOS 钥匙串。")
        query, owned = self.query(account, secret)
        try:
            status = self.sec.SecItemAdd(query, None)
            if status != 0:
                raise KeychainError(f"钥匙串保存未成功（系统状态 {status}）。未写入 Codex 配置。")
        finally:
            self.cf.CFRelease(query)
            for obj in owned:
                self.cf.CFRelease(obj)
