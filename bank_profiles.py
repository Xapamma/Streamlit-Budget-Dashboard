"""Read-only seed of reusable bank CSV import formats.

Bank format profiles are seed data provided with the app and committed to the
repository as ``bank_profiles.json``, so every deployment — including the hosted
Streamlit app — lists the same banks. They are loaded each session and never
written from the Streamlit UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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