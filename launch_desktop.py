"""WeSwitch macOS app entry point. Browser UI, bundled Python, no source installation."""
from __future__ import annotations

import subprocess
import sys


def main():
    # --no-browser is for isolated package smoke tests; production opens the UI.
    headless = "--no-browser" in sys.argv
    if headless:
        sys.argv.remove("--no-browser")
    elif "--open" not in sys.argv:
        sys.argv.append("--open")
    try:
        from server import main as serve
        serve()
    except SystemExit:
        raise
    except Exception:
        # Never include exception details: config paths or credentials may be present.
        message = (
            'display alert "WeSwitch" message '
            '"Unable to start the local service. Check the runtime folder permissions or use the source launcher. '
            'No configuration change was requested. / 无法启动本地服务，请检查运行目录权限或使用源码启动器。" as critical'
        )
        if not headless and sys.platform == "darwin":
            subprocess.run(["/usr/bin/osascript", "-e", message], check=False, timeout=30)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
