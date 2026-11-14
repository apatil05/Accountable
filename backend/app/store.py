"""User-profile persistence, backed by the `profiles` collection in MongoDB Atlas.

Owner: Person A. One document per customer (`_id` = customer id); the profile's
nested weights and lists live inside that single document.
"""

from pymongo.database import Database

from .db import repos
from .models import UserProfile


def load_profile(db: Database, customer_id: str) -> UserProfile:
    doc = repos.get_profile(db, customer_id)
    if not doc:
        return UserProfile()
    return UserProfile(**{k: v for k, v in doc.items() if k in UserProfile.model_fields})


def save_profile(db: Database, customer_id: str, profile: UserProfile) -> UserProfile:
    repos.save_profile(db, customer_id, profile.model_dump())
    return profile
