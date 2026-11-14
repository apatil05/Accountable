"""Pull a customer from Nessie (or the mock data) into MongoDB Atlas.

Usage (from backend/):
    python -m scripts.sync                  # the default customer
    python -m scripts.sync --customer ID    # a specific Nessie customer

Safe to run repeatedly: documents are upserted by id.
"""

import argparse
import json
import sys

from pymongo.errors import PyMongoError

from app import config
from app.db.client import get_db, ping
from app.db.indexes import ensure_indexes
from app.services import ingest, nessie


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customer", help="Nessie customer id (default: the configured default customer)")
    args = parser.parse_args()

    try:
        config.validate()
        customer_id = nessie.validate_id(args.customer or nessie.default_customer_id(), "customer id")
        db = get_db()
        ping(db)
        ensure_indexes(db)
        result = ingest.sync_customer(db, customer_id)
    except (config.ConfigError, nessie.NessieError, nessie.InvalidIdError) as exc:
        print(f"Sync failed: {exc}", file=sys.stderr)
        return 1
    except PyMongoError as exc:
        print(f"Sync failed: MongoDB error ({type(exc).__name__}). Check MONGODB_URI and Atlas Network Access.", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
