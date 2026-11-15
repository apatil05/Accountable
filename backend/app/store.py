"""User-profile persistence, backed by the `profiles` collection in MongoDB Atlas.

Owner: Person A. One document per customer (`_id` = customer id); the profile's
nested weights and lists live inside that single document.
"""

from pymongo.database import Database

from .db import repos
from .models import CATEGORIES, UserProfile


def load_profile(db: Database, customer_id: str) -> UserProfile:
    doc = repos.get_profile(db, customer_id)
    if not doc:
        return UserProfile()
    fields = {k: v for k, v in doc.items() if k in UserProfile.model_fields}
    if isinstance(fields.get("weights"), dict):
        # Categories get retired (e.g. "political"); a profile saved before that must still load.
        fields = {**fields, "weights": {c: w for c, w in fields["weights"].items() if c in CATEGORIES}}
    return UserProfile(**fields)


def save_profile(db: Database, customer_id: str, profile: UserProfile) -> UserProfile:
    repos.save_profile(db, customer_id, profile.model_dump())
    return profile
