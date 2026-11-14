"""MongoDB Atlas connection. One cached client per process; PyMongo clients are
thread-safe and manage their own connection pool, so share a single instance."""

from functools import lru_cache

import certifi
from pymongo import MongoClient
from pymongo.database import Database

from .. import config

SERVER_SELECTION_TIMEOUT_MS = 10_000


def new_client() -> MongoClient:
    """A fresh client. tz_aware=True so datetimes read back from Mongo carry UTC tzinfo.

    tlsCAFile: Python installed from python.org on macOS does not use the system
    certificate store, so Atlas's TLS certificate fails verification without this.
    """
    return MongoClient(
        config.MONGODB_URI,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        tz_aware=True,
        tlsCAFile=certifi.where(),
    )


@lru_cache(maxsize=1)
def get_client() -> MongoClient:
    return new_client()


def get_db() -> Database:
    return get_client()[config.MONGODB_DB]


def ping(db: Database) -> None:
    """Raises pymongo.errors.PyMongoError if Atlas is unreachable or credentials are wrong."""
    db.client.admin.command("ping")
