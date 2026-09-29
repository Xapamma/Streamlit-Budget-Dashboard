"""Local storage for reusable bank CSV import formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


def load_bank_profiles(profile_path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    path = Path(profile_path) if profile_path is not None else Path(__file__).with_name("bank_profiles.json")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as profile_file:
        profiles = json.load(profile_file)
    if not isinstance(profiles, dict):
        raise ValueError("Bank profiles must contain a JSON object.")
    return {
        str(name): profile
        for name, profile in profiles.items()
        if isinstance(name, str) and isinstance(profile, dict)
    }


def save_bank_profile(
    bank_name: str,
    profile: Mapping[str, Any],
    profile_path: str | Path | None = None,
) -> None:
    name = bank_name.strip()
    if not name:
        raise ValueError("A bank name is required to save an import format.")

    path = Path(profile_path) if profile_path is not None else Path(__file__).with_name("bank_profiles.json")
    profiles = load_bank_profiles(path)
    profiles[name] = dict(profile)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(profiles, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)