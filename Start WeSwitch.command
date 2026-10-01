#!/bin/bash
set -euo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SUPPORT="$HOME/Library/Application Support/WeSwitch"
VENV="$SUPPORT/venv"
MARKER="$VENV/.weswitch-source-venv"
STAMP="$VENV/.weswitch-requirements.txt"

fail() {
    printf '\n%s\n' "$1" >&2
    exit 1
}

consent() {
    local answer
    printf '\n%s\n' 'WeSwitch needs an isolated Python environment and its dependencies.'
    printf '%s\n' 'WeSwitch 需要创建独立 Python 环境并安装依赖。'
    printf 'Location / 位置: %s\n' "$VENV"
    printf '%s\n' 'With your permission, pip will download requirements.txt dependencies. No global packages will be installed.'
    printf '%s\n' '仅经你同意后，pip 才会下载 requirements.txt 中的依赖；不会全局安装。'
    printf '%s' 'Continue / 是否继续? [y/N]: '
    if ! IFS= read -r answer; then
        fail 'Setup cancelled; nothing installed. / 已取消安装。'
    fi
    case "$answer" in
        y|Y|yes|YES|Yes) ;;
        *) fail 'Setup cancelled; nothing installed. / 已取消安装。' ;;
    esac
}

valid_python() {
    [[ -x "$1" ]] && "$1" -I -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1
}

find_python() {
    local name candidate prefix version
    for name in python3.13 python3.12 python3.11 python3; do
        candidate="$(command -v "$name" 2>/dev/null || true)"
        if [[ -n "$candidate" ]] && valid_python "$candidate"; then
            PYTHON="$candidate"
            return 0
        fi
    done
    for prefix in /opt/homebrew /usr/local; do
        for name in python3.13 python3.12 python3.11 python3; do
            candidate="$prefix/bin/$name"
            if valid_python "$candidate"; then
                PYTHON="$candidate"
                return 0
            fi
        done
        for version in 3.13 3.12 3.11; do
            for candidate in "$prefix/opt/python@$version/bin/python$version" "$prefix/opt/python@$version/libexec/bin/python3"; do
                if valid_python "$candidate"; then
                    PYTHON="$candidate"
                    return 0
                fi
            done
        done
    done
    return 1
}

[[ "$(uname -s)" == Darwin ]] || fail 'WeSwitch is a macOS application. / WeSwitch 仅支持 macOS。'
[[ -f "$ROOT/requirements.txt" && -f "$ROOT/server.py" ]] || fail 'Source files are missing. Extract the complete source distribution. / 源文件缺失，请完整解压源码。'
[[ ! -L "$SUPPORT" && ! -L "$VENV" ]] || fail 'Refusing a symlinked application or environment directory. / 拒绝使用符号链接环境目录。'

approved=0
if [[ -e "$VENV" ]]; then
    [[ -d "$VENV" && -f "$MARKER" && ! -L "$MARKER" ]] || fail 'An unmanaged environment already exists at the WeSwitch venv location. Nothing was changed; inspect it before proceeding. / 目标位置已有其他环境，未作任何修改，请先检查。'
    [[ "$(< "$MARKER")" == 'WeSwitch source environment v1' ]] || fail 'Environment ownership could not be verified. Nothing was changed. / 无法确认环境归属，未作修改。'
else
    PYTHON=''
    if ! find_python; then
        fail 'Python 3.11+ was not found. Prefer the standalone macOS Apple Silicon app when a release is available at https://github.com/susunola/WeSwitch, or install Python 3.11+ yourself and retry. No installer was run. / 未找到 Python 3.11+。推荐在项目发布后使用 macOS Apple Silicon 独立应用，或自行安装 Python 3.11+ 后重试；未运行任何安装器。'
    fi
    consent
    approved=1
    if [[ ! -d "$SUPPORT" ]]; then
        mkdir -p "$SUPPORT"
    fi
    # An exclusive mkdir must succeed before claiming this environment.
    mkdir "$VENV"
    printf '%s\n' 'WeSwitch source environment v1' > "$MARKER"
    "$PYTHON" -I -m venv "$VENV" || fail 'Environment creation failed. Inspect the WeSwitch venv directory before retrying; no global packages were installed. / 独立环境创建失败，请检查 WeSwitch venv 目录；未全局安装。'
fi

if ! valid_python "$VENV/bin/python" || ! "$VENV/bin/python" -I -c 'import pathlib, sys; raise SystemExit(sys.prefix == sys.base_prefix or pathlib.Path(sys.prefix).resolve() != pathlib.Path(sys.argv[1]).resolve())' "$VENV"; then
    fail 'The managed environment is incomplete or its Python was removed. Inspect it and move it aside before retrying; it will not be overwritten. / 独立环境不完整或 Python 已移除，请检查并移走该环境后重试；不会覆盖。'
fi

if ! cmp -s "$ROOT/requirements.txt" "$STAMP" || ! "$VENV/bin/python" -I -c 'import tomlkit' >/dev/null 2>&1; then
    if [[ "$approved" != 1 ]]; then
        consent
    fi
    "$VENV/bin/python" -I -m pip --isolated install --disable-pip-version-check -r "$ROOT/requirements.txt"
    cp "$ROOT/requirements.txt" "$STAMP"
fi

# The server authenticates and reuses a running instance for the same CODEX_HOME.
exec "$VENV/bin/python" -E -B "$ROOT/server.py" --open --runtime "$SUPPORT/runtime.json" "$@"
