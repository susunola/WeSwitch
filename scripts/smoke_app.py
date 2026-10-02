#!/usr/bin/env python3
"""Smoke-test a built WeSwitch executable using only a disposable home and config."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="weswitch-package-smoke-") as tmp:
        home = Path(tmp)
        config = home / "config.toml"
        original = b'# Smoke test only\nmodel = "before"\n[mcp_servers.keep]\ncommand = "keep"\n'
        config.write_bytes(original)
        runtime = home / "runtime.json"
        env = dict(os.environ, HOME=str(home), PATH="/usr/bin:/bin")
        for name in ("PYTHONHOME", "PYTHONPATH", "CODEX_HOME"):
            env.pop(name, None)
        process = subprocess.Popen(
            [str(executable), "--no-browser", "--config-home", str(home),
             "--runtime", str(runtime), "--port", "0"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 20
            ready = threading.Event()
            while not runtime.is_file():
                if process.poll() is not None:
                    raise AssertionError("Packaged app exited before startup: " + process.stderr.read().decode(errors="replace"))
                if time.monotonic() >= deadline:
                    raise AssertionError("Packaged app startup timed out")
                ready.wait(0.05)
            metadata = json.loads(runtime.read_bytes())
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

            def request(path, body=None, authenticated=True, language="en"):
                headers = {"Content-Type": "application/json", "X-WeSwitch-Language": language}
                if authenticated:
                    headers["X-Codex-UI-Token"] = metadata["token"]
                req = urllib.request.Request(metadata["origin"] + path,
                    data=None if body is None else json.dumps(body).encode(), headers=headers)
                with opener.open(req, timeout=5) as response:
                    return response.read()

            assert b'lang="zh-CN"' in request("/")
            assert b"WeSwitchI18n" in request("/i18n.js")
            assert json.loads(request("/api/state"))["tool_version"] == "0.6.6"
            try:
                request("/api/state", authenticated=False)
                raise AssertionError("Unauthenticated request unexpectedly succeeded")
            except urllib.error.HTTPError as error:
                assert error.code == 401
            form = {"provider_id": "smoke-provider", "name": "Smoke only",
                    "base_url": "http://127.0.0.1:11434/v1", "model": "smoke-model",
                    "auth_mode": "none", "has_key": False, "set_default": True}
            preview = json.loads(request("/api/preview", form))
            assert config.read_bytes() == original
            assert all(not any("\u3400" <= char <= "\u9fff" for char in line) for line in preview["warnings"])
            result = json.loads(request("/api/apply", dict(form, plan_id=preview["plan_id"],
                revision=preview["revision"], api_key="", confirmed=True)))
            assert result["ok"] and not result["connection_tested"]
            parsed = tomllib.loads(config.read_text())
            assert parsed["model"] == "smoke-model"
            assert parsed["mcp_servers"]["keep"]["command"] == "keep"
            assert (Path(result["backup_path"]) / "config.toml").read_bytes() == original
            print("PASS: standalone startup with system-only PATH, bundled assets, locale, auth gate, preview, disposable apply and exact backup")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            if process.stderr:
                process.stderr.close()


if __name__ == "__main__":
    main()
