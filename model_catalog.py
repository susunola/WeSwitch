"""Merge custom provider models into a local model catalog.

The desktop picker renders `model_catalog_json`, not `model_providers`: a custom
model ID that has no catalog entry cannot be selected, and the picker falls back
to the default recommended set. This module builds those entries.

The entry shape is not a free choice. Codex parses this file with serde and
rejects the **whole file** when one entry omits a required field. Measured
against codex-cli 0.159.2:

    missing field `shell_type`
    missing field `support_verbosity`
    missing field `truncation_policy`
    missing field `experimental_supported_tools`
    model `x` is missing both `base_instructions` and `model_messages.instructions_template`

A rejection is worse than writing no catalog at all: every model disappears from
the picker, not just the custom one. So `plan()` writes each required field, and
`validate()` re-parses the candidate with the installed Codex before it is saved.

Nothing else is invented. Fields that describe a model's own capability — context
window, service tiers, speed tiers, tool mode — are deliberately *not* copied
from the source entry: they describe a different model, and an inflated context
window breaks real requests. Only the structural fields Codex requires are
carried over, from an entry in the user's own catalog, so their accepted values
track the installed build instead of assumptions baked into this tool.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config_core import REASONING_EFFORTS

CATALOG_NAME = "models.json"
# What the signed-in desktop app is rendering right now. A server-fetched cache,
# so it is the closest thing to "the models you already have" — but it is absent
# before the first sign-in and can go stale.
CACHE_NAME = "models_cache.json"
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_TEXT = 200
MAX_PREVIEW_CHARS = 4000
MAX_SEED = 500
# Structural fields Codex requires on every entry, with the value this tool uses
# when the user's own catalog cannot supply one. These describe the Codex harness
# rather than the upstream model, which is why defaulting them is safe.
STRUCTURAL_DEFAULTS = {
    "shell_type": "unified_exec",
    "support_verbosity": False,
    "truncation_policy": {"mode": "tokens", "limit": 10000},
    "experimental_supported_tools": [],
}
# Codex also insists on instructions. An empty string satisfies the parser and is
# preferred over copying a system prompt that was written for a different model.
INSTRUCTIONS_KEYS = ("base_instructions", "model_messages")
COMMAND_TIMEOUT = 30


def target_path(home):
    return Path(home) / CATALOG_NAME


def cache_path(home):
    return Path(home) / CACHE_NAME


def entries_from_body(body, limit=MAX_SEED):
    """Return the usable entries of a decoded catalog body, or [] when malformed."""
    if not isinstance(body, dict):
        return []
    models = body.get("models")
    if not isinstance(models, list):
        return []
    return [m for m in models[:limit]
            if isinstance(m, dict) and isinstance(m.get("slug"), str) and m["slug"]]


def load_catalog(path):
    """Return (entries, meta) from an existing catalog, or ([], {}) if unusable."""
    try:
        p = Path(path).expanduser()
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_SOURCE_BYTES:
            return [], {}
        body = json.loads(p.read_bytes())
    except (OSError, ValueError):
        return [], {}
    entries = entries_from_body(body)
    meta = {k: v for k, v in body.items() if k != "models"} if isinstance(body, dict) else {}
    return entries, meta


def template_entry(entries):
    """Pick the entry whose structural fields get copied, or {} when none qualifies.

    Prefers an entry Codex itself wrote: one carrying `shell_type` is a real
    catalog entry, whereas a hand-written minimal one cannot supply the values.
    """
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("shell_type"), str) and entry["shell_type"]:
            return entry
    return {}


def bundled_catalog(codex, timeout=COMMAND_TIMEOUT):
    """Ask the installed Codex for the catalog it ships with, or [] on any failure.

    `--bundled` skips the network refresh, so this is a local read of the same
    list the picker starts from. Runs against a throwaway CODEX_HOME.
    """
    if not codex:
        return []
    try:
        sandbox = Path(tempfile.mkdtemp(prefix="codex-ui-bundled-")).resolve()
    except OSError:
        return []
    try:
        result = subprocess.run(
            [codex, "debug", "models", "--bundled"],
            capture_output=True, timeout=timeout, check=False,
            env={**os.environ, "CODEX_HOME": str(sandbox)},
        )
        if result.returncode:
            return []
        return entries_from_body(json.loads(result.stdout.decode("utf-8", "replace")))
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def read_seed(home, codex=None):
    """Return (entries, origin) for the built-in models the picker already shows.

    Two sources, cheapest first:

    ``cache``     `models_cache.json`, what the signed-in picker renders now. A
                  plain file read, so it costs nothing and matches the user's
                  current list exactly.
    ``bundled``   `codex debug models --bundled`, the catalog inside the
                  installed build. Needs one CLI call, but exists before the
                  first sign-in and cannot go stale.

    ([], "") means neither is available — the only case where the caller must
    still refuse to write a catalog.
    """
    entries, _ = load_catalog(cache_path(home))
    if entries:
        return entries, "cache"
    entries = bundled_catalog(codex)
    if entries:
        return entries, "bundled"
    return [], ""


def validate(rendered, codex, timeout=COMMAND_TIMEOUT):
    """Re-parse a candidate catalog with the installed Codex, as it will on startup.

    Returns None when Codex accepts the file, otherwise a plain report of what
    went wrong. Codex's own text is passed through verbatim — it names the exact
    missing field, which is the whole reason this check exists — and stays in
    English like every other technical detail this tool surfaces.
    """
    if not codex:
        return None
    try:
        sandbox = Path(tempfile.mkdtemp(prefix="codex-ui-validate-")).resolve()
    except OSError as exc:
        return f"could not create a validation directory: {exc}"
    try:
        candidate = sandbox / "candidate.json"
        candidate.write_bytes(rendered)
        result = subprocess.run(
            [codex, "-c", f'model_catalog_json="{candidate}"', "debug", "models"],
            capture_output=True, timeout=timeout, check=False,
            env={**os.environ, "CODEX_HOME": str(sandbox)},
        )
        if result.returncode == 0:
            return None
        # Whitespace-collapsed so a multi-line report still arrives as one message,
        # the way every other parameter this tool surfaces does.
        report = " ".join((result.stderr or result.stdout).decode("utf-8", "replace").split())
        return report or "Codex rejected the catalog without reporting a reason"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run Codex to validate the catalog: {exc}"
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def level_descriptions(entries):
    """Collect effort -> description from existing entries; first occurrence wins."""
    mapping = {}
    for entry in entries:
        levels = entry.get("supported_reasoning_levels")
        if not isinstance(levels, list):
            continue
        for level in levels:
            if not isinstance(level, dict):
                continue
            effort = level.get("effort")
            if not isinstance(effort, str) or not effort or effort in mapping:
                continue
            description = level.get("description")
            # An unknown effort still needs text; reuse the effort name verbatim.
            mapping[effort] = description[:MAX_TEXT] if isinstance(description, str) else effort
    return mapping


def build_entry(model, provider_name, effort, levels, descriptions, template=None):
    """Build one catalog entry that Codex will parse, for a custom model.

    `template` is a real entry from the user's own catalog. Only its structural
    fields are carried over, so their accepted values match the installed Codex
    build. Everything that describes the template model's own capability is left
    behind on purpose: inheriting a 272k context window or a "priority" service
    tier would be a claim about a model this tool has never called, and a wrong
    context window fails real requests rather than merely looking wrong.
    """
    template = template if isinstance(template, dict) else {}
    entry = {
        "slug": model,
        "display_name": model,
        "description": (provider_name or model)[:MAX_TEXT],
        "default_reasoning_level": effort or ("medium" if "medium" in levels else levels[0]),
        "supported_reasoning_levels": [
            {"effort": level, "description": descriptions.get(level, level)} for level in levels
        ],
        "visibility": "list",
        "supported_in_api": True,
        # Lowest priority so built-in entries keep their ordering.
        "priority": 1,
    }
    for key, fallback in STRUCTURAL_DEFAULTS.items():
        value = template.get(key)
        entry[key] = copy.deepcopy(value) if type(value) is type(fallback) else copy.deepcopy(fallback)
    for key in INSTRUCTIONS_KEYS:
        if key in template:
            entry[key] = copy.deepcopy(template[key])
    if not any(key in entry for key in INSTRUCTIONS_KEYS):
        entry["base_instructions"] = ""
    return entry


def merge(source_entries, new_entries):
    """Append new entries by slug, never overwriting an existing one."""
    by_slug = {}
    order = []
    for entry in source_entries:
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug or slug in by_slug:
            continue
        by_slug[slug] = entry
        order.append(slug)
    added, skipped = [], []
    for entry in new_entries:
        slug = entry["slug"]
        if slug in by_slug:
            skipped.append(slug)
            continue
        by_slug[slug] = entry
        order.append(slug)
        added.append(slug)
    return {"models": [by_slug[slug] for slug in order]}, added, skipped


def cache_plan(home, new_entries, seed_entries=()):
    """Merge custom entries into models_cache.json, the signed-in picker cache.

    The desktop model picker renders this server-fetched file, not profiles and
    not only model_catalog_json. A custom model that never lands here stays in
    the default recommended set. fetched_at is refreshed so the cache stays
    inside its TTL and Codex does not immediately replace it with the remote
    list. Existing entries are kept; a missing cache is seeded from the catalog
    about to be written, which already contains the built-in models.
    """
    path = cache_path(home)
    existed = path.is_file() and not path.is_symlink()
    meta = {}
    source = []
    if existed:
        try:
            body = json.loads(path.read_bytes())
        except (OSError, ValueError):
            body = None
        if isinstance(body, dict):
            meta = {k: v for k, v in body.items() if k != "models"}
            models = body.get("models")
            if isinstance(models, list):
                source = [m for m in models
                          if isinstance(m, dict) and isinstance(m.get("slug"), str) and m["slug"]]
    if not source:
        source = list(seed_entries)
    catalog, added, skipped = merge(source, new_entries)
    body = dict(meta)
    body["models"] = catalog["models"]
    body["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "path": str(path),
        "existed": existed,
        "rendered": render(body),
        "added": added,
        "skipped": skipped,
        "count": len(catalog["models"]),
    }


def render(catalog):
    return (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def preview_text(entries):
    text = json.dumps(entries, ensure_ascii=False, indent=2)
    return text if len(text) <= MAX_PREVIEW_CHARS else text[:MAX_PREVIEW_CHARS] + "\n…"


def plan(home, source_entries, models, provider_name, effort, source_path="", seed_entries=()):
    """Build the merged catalog and the metadata the preview and apply need.

    `source_entries` is the catalog the merge starts from — either the file
    `model_catalog_json` already points at, or the built-in list when there is
    none. `seed_entries` is the built-in list, passed alongside so a hand-made
    or truncated source can still borrow the structural fields from a real one.
    """
    source_entries = list(source_entries)
    descriptions = level_descriptions(source_entries) or level_descriptions(seed_entries)
    template = template_entry(source_entries) or template_entry(seed_entries)
    # Offer only levels the local catalog already knows how to describe, so no
    # capability is claimed for a model this tool has never called.
    levels = [level for level in REASONING_EFFORTS if level in descriptions] or list(REASONING_EFFORTS)
    if effort and effort not in levels:
        # The effort the user selected must stay selectable in the picker.
        levels.append(effort)
    entries = [build_entry(m, provider_name, effort, levels, descriptions, template) for m in models]
    catalog, added, skipped = merge(source_entries, entries)
    return {
        "path": str(target_path(home)),
        "source_path": str(source_path) if source_path else "",
        "rendered": render(catalog),
        "entries": entries,
        "added": added,
        "skipped": skipped,
        "count": len(catalog["models"]),
        "source_count": len(source_entries),
    }


def remove_slugs(document, slugs):
    """Drop only the named slugs. Every other entry, including official GPT models, stays."""
    wanted = {slug for slug in slugs if isinstance(slug, str) and slug}
    models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(models, list):
        return document, []
    kept, removed = [], []
    for entry in models:
        slug = entry.get("slug") if isinstance(entry, dict) else None
        if slug in wanted:
            removed.append(slug)
        else:
            kept.append(entry)
    return {"models": kept}, removed
