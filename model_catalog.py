"""Merge custom provider models into a local model catalog.

The desktop picker renders `model_catalog_json`, not `model_providers`: a custom
model ID that has no catalog entry cannot be selected, and the picker falls back
to the default recommended set. This module builds those entries.

Nothing is invented. Repository prompts, capability flags, and context-window
numbers are copied from an entry in the user's own existing catalog, or omitted
when the source has none. The generated file contains only `models`, matching the
shape vendors ship for their own Codex catalogs.
"""
from __future__ import annotations

import json
from pathlib import Path

from config_core import REASONING_EFFORTS

CATALOG_NAME = "models.json"
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_TEXT = 200
MAX_PREVIEW_CHARS = 4000


def target_path(home):
    return Path(home) / CATALOG_NAME


def load_catalog(path):
    """Return (entries, meta) from an existing catalog, or ([], {}) if unusable."""
    try:
        p = Path(path).expanduser()
        if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_SOURCE_BYTES:
            return [], {}
        body = json.loads(p.read_bytes())
    except (OSError, ValueError):
        return [], {}
    if not isinstance(body, dict):
        return [], {}
    models = body.get("models")
    if not isinstance(models, list):
        return [], {}
    entries = [m for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str) and m["slug"]]
    meta = {k: v for k, v in body.items() if k != "models"}
    return entries, meta


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


def shared_shell_type(entries):
    for entry in entries:
        value = entry.get("shell_type")
        if isinstance(value, str) and value:
            return value
    return None


def build_entry(model, provider_name, effort, levels, descriptions, shell=None):
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
    if shell:
        entry["shell_type"] = shell
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


def render(catalog):
    return (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def preview_text(entries):
    text = json.dumps(entries, ensure_ascii=False, indent=2)
    return text if len(text) <= MAX_PREVIEW_CHARS else text[:MAX_PREVIEW_CHARS] + "\n…"


def plan(home, source_path, models, provider_name, effort):
    """Build the merged catalog and the metadata the preview and apply need."""
    source_entries, _ = load_catalog(source_path) if source_path else ([], {})
    descriptions = level_descriptions(source_entries)
    shell = shared_shell_type(source_entries)
    # Offer only levels the local catalog already knows how to describe, so no
    # capability is claimed for a model this tool has never called.
    levels = [level for level in REASONING_EFFORTS if level in descriptions] or list(REASONING_EFFORTS)
    if effort and effort not in levels:
        # The effort the user selected must stay selectable in the picker.
        levels.append(effort)
    entries = [build_entry(m, provider_name, effort, levels, descriptions, shell) for m in models]
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
