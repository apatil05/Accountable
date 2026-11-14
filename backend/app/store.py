"""Tiny persistence layer. JSON file for now so the shell runs with zero setup.

Owner: Person A. Swap for Postgres (or Mongo; profiles are already JSON docs)
once the schema settles. Keep the function signatures the same.
"""

import json

from . import config
from .models import UserProfile

_PROFILE_FILE = config.DATA_DIR / "profile.local.json"  # gitignored


def load_profile() -> UserProfile:
    if _PROFILE_FILE.exists():
        return UserProfile(**json.loads(_PROFILE_FILE.read_text()))
    return UserProfile()


def save_profile(profile: UserProfile) -> UserProfile:
    _PROFILE_FILE.write_text(profile.model_dump_json(indent=2))
    return profile
