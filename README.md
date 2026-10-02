# WeSwitch

**Review and safely update Codex Mac model settings in your local browser.**

English · [简体中文](README.zh.md) · [MIT License](LICENSE)

WeSwitch is an independent community project, not affiliated with OpenAI. **Chinese is the default, with an English toggle**. Switching language preserves your form and never reapplies configuration.

[Download the app](https://github.com/susunola/WeSwitch/releases/latest) · [Browse the source](https://github.com/susunola/WeSwitch)

## Review first. Change deliberately.

- Edit providers, model IDs, and default model settings; preview changes before explicitly applying them.
- **Consent → byte-exact, verified backup → atomic configuration write.** If no original file exists, its absence is recorded instead of inventing a backup.
- Newly entered managed API keys live in macOS Keychain, not as secret values in configuration or process arguments.
- Optional model discovery sends `GET <base>/models` only after separate, explicit consent. No generation calls are made, and listing models is not a compatibility test.
- **Add several models at once:** one provider entry, one credential, many model IDs (for example Pro and Flash), each saved as a switchable profile.
- **Optional model catalog write:** the desktop picker renders only the entries of `model_catalog_json`. When enabled, your models are merged into a new catalog file and the existing one is backed up first; when disabled, no catalog file is touched.
- **Optional connection and protocol check:** after consent, **one minimal request** (at most 16 output tokens) is sent to confirm the endpoint accepts Codex's only wire protocol. It may cost a small amount of quota; it reports the result and saves no key or configuration.
- **Every apply is reversible:** each apply produces a verified backup, and any applied backup can be restored from the UI. The current configuration is backed up again before restoring, so a restore is itself reversible. The backup directory keeps the most recent 5 plus the original, clearing anything beyond that after an apply or a restore.
- **See what Codex already has first:** plan, account, subscription expiry, and login-token status are read **offline** from local sign-in data, and the installed model list from the local catalog. Models you already have are marked and **left unchecked by default**, so the same model cannot be added twice by accident.

## Get started

### Recommended: standalone Apple Silicon app

The initial binary release target is **macOS Apple Silicon (arm64)**. CI builds target macOS 14; the local build has been smoke-tested on macOS 27. Other OS versions require validation. The standalone app bundles Python and does not require Python, Homebrew, or WorkBuddy to be installed.

Once published, download `WeSwitch-v0.5.0-macos-arm64.zip` and its `.zip.sha256` companion. In the download directory, run:

```bash
shasum -a 256 -c WeSwitch-v0.5.0-macos-arm64.zip.sha256
```

Extract the archive and open `WeSwitch.app`. Your default browser opens the local UI. A checksum checks file integrity, not publisher identity.

**This release pipeline does not perform Developer ID signing or Apple notarization.** PyInstaller may apply an ad-hoc signature; that is not a verified publisher signature. Gatekeeper can still warn or block launch. Only after verifying the source and deciding to trust the app, use macOS's per-app open confirmation. Do not disable Gatekeeper globally. If you prefer not to run an unnotarized app, review and run the source instead.

### Source fallback: Python 3.11+

Available for Apple Silicon and the initial path for Intel Macs. Extract the complete source distribution, then double-click `Start WeSwitch.command`, or run this from the source directory:

```bash
bash "Start WeSwitch.command"
```

You can also pass the script's full path to `bash`; no particular working directory is required. Quote paths containing spaces.

The launcher searches PATH and known Homebrew locations for Python 3.13, 3.12, 3.11, or another `python3` meeting the 3.11+ minimum. It asks for bilingual consent before creating its dedicated venv and downloading pinned dependencies from `requirements.txt`. It never installs global packages or runs a Homebrew/curl installer, and refuses to overwrite an unrelated environment.

Subsequent launches authenticate and reuse a valid local server for the same configuration directory, then reopen the browser. Stop a source-launched server with `Ctrl+C` in its terminal; closing the browser does not stop it. If environment creation was interrupted, inspect and move aside the incomplete dedicated venv before retrying; the launcher will not overwrite it automatically.

## Add several models at once

One gateway and one credential need only one provider entry: enter **one model ID per line** in the model box, or fetch the list, select several models, and click Add to model list. The preview shows every model written, the default model, and the profile names for the rest.

- Codex configuration has **no** per-provider model list. Several models are saved as named profiles (`profiles.<name>`); see [PROFILES.md](PROFILES.md) for the source-level evidence.
- The default model is written to the root `model` / `model_provider`; the others are written only as `model`, `model_provider`, and `model_reasoning_effort`, leaving unrelated profile settings untouched.
- Names are derived from the provider ID and model ID, for example `deepseek-deepseek-chat`. An existing profile of the same name that holds settings this tool does not write is rejected instead of overwritten.
- Switching depends on the profile support of your Codex build (for example the `profile` setting or `--profile`). **This tool does not guarantee that these models appear in the desktop model picker.** Check your Codex version and enter a model ID manually if needed.

## See what Codex already has: plan, installed models, and usage

The **Codex status** panel has two halves and is **read-only and offline by default**:

**Plan and installed models (offline).** The plan type, account, subscription expiry, and login-token status are parsed from the local sign-in data in `~/.codex/auth.json`; the installed model list comes from the local catalog (`models_cache.json`, or the `model_catalog_json` you configured). Neither makes a network request — the page shows them as soon as it loads. This tool only reads this data; it never modifies the sign-in data and **never returns the token to the page**.

**Avoiding duplicate additions.** In the model picker, models you already have are marked "already added" (written by this tool) or "in the catalog" (already present in the catalog), and are **unchecked by default**. Adding one twice takes a deliberate click, so an accidental duplicate cannot happen in passing.

**Usage (one query, only when you click).** Usage is never requested on page load. Only after you click Query usage and accept the confirmation does the tool use the locally stored ChatGPT login token to send **one read-only request** to `chatgpt.com`. This is stated next to the button and again in the confirmation dialog: **the token is sent to chatgpt.com**. The query writes no file, uploads no configuration, and saves no token; cancelling sends nothing at all. The button is unavailable when the login is not a ChatGPT session (for example an API key only).

Results are grouped by window (5-hour and 7-day), showing the used percentage and reset time, with an expandable breakdown by dimension (model, trigger, conversation source, surface). The figures come from the server and are **indicative only**: the window rolls, coverage may be incomplete, and rounding and counting conventions follow what Codex itself displays.

## One wire protocol

Codex has exactly one wire protocol: the **Responses API**. The legacy `wire_api = "chat"` value was removed from codex-cli (Chat Completions is no longer supported), and the configuration exposes no protocol switch. The UI therefore offers no protocol dropdown — the only open question is whether the endpoint accepts a minimal Responses request.

The connection test sends that request and inspects the reply: a `response` object or an `output` array counts as compatible, while a `chat.completion` shape is reported as incompatible and **cannot be worked around by switching protocols**. A successful listing does not prove protocol compatibility, and a saved configuration does not prove the model works; tool calling, long context, and streaming still need to be verified in a fresh Codex conversation.

## Model catalog and rollback

**Catalog:** when "write model catalog" is enabled, WeSwitch **merges** the existing entries with your new models into `~/.codex/models.json` (the original is always backed up first) and points `model_catalog_json` at it. Without both of those, the picker falls back to its default recommended set. Catalog entries only decide the desktop display name and reasoning options; requests still use the model ID and base URL you entered. After the write, Codex's remote catalog updates no longer apply — point `model_catalog_json` back at the original path to restore that. The option is not offered when no existing catalog can be found: a catalog holding only custom models would hide the built-in models in the picker.

**Rollback:** every applied backup under `~/.codex/model-ui-backups/` can be restored from the UI, and the current configuration is saved as another backup first. A restore only overwrites files this tool wrote (the configuration, and the catalog file written by that apply); it never deletes Keychain items.

**Backup retention:** the directory keeps the most recent **5** backups plus the **oldest one** — the configuration as it was before this tool ever changed it. That original is pinned so a long run of applies stays reversible, and it is never removed. The set therefore settles at 6, and anything beyond that is cleared under the write lock after a successful **apply or restore**; the UI reports how many were cleared. Opening the UI, switching language, previewing, or reading state never deletes a backup — only a real configuration write does. A backup that cannot be removed is left in place and never fails the apply that triggered it.

## Scope and limitations

1. Enter the provider base URL and model ID. `https://api.example.com/v1` and `model-id` are **illustrative only**, not service recommendations or claims of availability.
2. To discover models, review the destination and credentials, then approve that request separately. Authentication information may be sent to the selected provider; cancellation makes no request. The result is metadata: it **does not establish Responses API compatibility, model availability, account access, or visibility in Codex's model picker**.
3. Preview and explicitly apply the configuration changes. Writing the model catalog is your choice; when it is disabled the catalog file and `model_catalog_json` are left untouched and the model will not appear in the desktop picker.
4. Save your work, fully quit and reopen Codex, and check a new conversation. Project settings, profiles, launch arguments, and current-thread selections may override global defaults; WeSwitch does not force those overrides to change.

**Environment-variable authentication:** configuration stores the variable name only. WeSwitch does not set system environment variables, and Finder-launched apps may not inherit your shell environment. Model discovery can read only a process variable matching the **same pre-saved provider, base URL, and variable name**; it cannot read arbitrary environment variables. For a new provider, enter a model ID manually or use a temporary key in the form for an explicitly approved list request. Discovery itself does not save that key. A reused server retains its original environment; stop it before relaunching with changed variables.

## Security and local data

| Item | Default location / boundary |
| --- | --- |
| Codex configuration | `~/.codex/config.toml`; `CODEX_HOME` is supported and must match the directory actually used by Codex |
| Configuration backups | `~/.codex/model-ui-backups/`, or under `CODEX_HOME`; legacy directory name retained for existing backups |
| Backup retention | Most recent 5 plus the oldest, capping at 6; cleared after a successful apply or restore, never by merely opening the UI |
| Managed credentials | macOS Keychain service `local.codex-model-ui`; legacy service name retained for existing entries |
| Source environment | `~/Library/Application Support/WeSwitch/venv/` |
| Runtime state | `~/Library/Application Support/WeSwitch/runtime.json`; contains a session token and must not be shared |
| Browser server | Binds only to `127.0.0.1`; APIs require a session token and enforce Host / Origin checks |
| ChatGPT sign-in data | `~/.codex/auth.json`; parsed read-only for plan and token status, and the token is never returned to the page |

Keychain writes use native security APIs. Reads use the `/usr/bin/security` helper with service/account identifiers, not secret values, in its arguments. Configuration references the helper rather than embedding the key. Existing configuration and byte-exact backups may still contain pre-existing secrets: protect them accordingly.

WeSwitch makes only two kinds of outbound request, each confirmed by you individually: the model list request (`GET <base>/models`, sent to **the provider you entered**) and the usage query (sent to **chatgpt.com** with the local ChatGPT login token). The usage query is the only operation that sends that token to an external address, and it happens once, only after you click and accept the confirmation dialog. When it fails, the error body is not read, so the token cannot leak through a log or the UI.

Do not forward the port or publish the local server as a remote site. Never commit real configuration, keys, runtime state, backups, personal screenshots, or unredacted logs. `.gitignore` is only a safeguard: review an explicit publish whitelist and inspect its contents. The legacy launcher is excluded from public distribution. Before restoring a backup, stop Codex and WeSwitch, inspect the backup, and preserve the current file. Existing Keychain entries are not automatically deleted.

## Development and builds

Run these commands from the source directory after checking that `python3` is 3.11+. Use a new dedicated development environment, not another project's venv.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s . -p 'test_*.py'
bash -n "Start WeSwitch.command"
```

CI uses Python 3.11 and 3.13 on Ubuntu. Tests should use temporary configuration, test-only credentials, and mocked Keychain access. `test_browser.py` is an optional, separately invoked browser test; unittest import must not launch a browser.

Build for your current architecture on macOS:

```bash
.venv/bin/python scripts/build_macos.py --version v0.5.0
# Optional: choose a fresh distribution directory.
.venv/bin/python scripts/build_macos.py --version v0.5.0 --output "$HOME/WeSwitch release"
```

The build script does not install dependencies. It packages `launch_desktop.py`, `index.html`, `i18n.js`, and `assets/WeSwitch.icns` when present. Work files and the spec stay in `build/`; output defaults to `dist/`. Existing apps and version archives are never overwritten; use a fresh output directory for another build. Python and executable architectures are checked, then `ditto` preserves bundle metadata in a zip with a SHA-256 companion. The initial GitHub Release workflow publishes arm64 only.

The Release workflow accepts a `v*` tag push or a manual dispatch from the default branch referencing an **existing** stable version tag. PRs never publish. Only the release job has `contents: write`, and no long-lived credentials are used. Review and test the code before creating and pushing a version tag. To dispatch manually:

```bash
gh workflow run release.yml --repo susunola/WeSwitch -f version=v0.5.0
```

## License

[MIT](LICENSE) · Copyright (c) 2026 susunola
