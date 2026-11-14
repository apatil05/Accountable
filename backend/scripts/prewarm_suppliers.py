"""Look up and store real supplier reports for every brand the demo customer buys from.

Run once (it uses only the free ImportYeti search endpoints):  python -m scripts.prewarm_suppliers
Stored in the shared database, so the app and teammates never wait on a first lookup.
"""

from app import config
from app.db import repos
from app.db.client import get_db
from app.services import suppliers


def main() -> None:
    db = get_db()
    customer = config.NESSIE_CUSTOMER_ID or "cust_demo"
    ids = sorted(repos.distinct_brand_ids(db, customer))
    for report in suppliers.get_reports(db, ids):
        where = f"{report.importer.name} ({report.importer.address})" if report.importer else "no import records"
        print(f"{report.merchant_id:12} {len(report.suppliers)} suppliers  <- {where}")


if __name__ == "__main__":
    main()
