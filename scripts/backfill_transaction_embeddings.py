"""
Backfill embeddings for transactions created before Ask Sanchay existed.

New transactions get embedded automatically at write time (see
app/services/transaction_service.py) -- this is a one-time catch-up for
whatever already existed in Sanchaydb before that wiring went in.
Re-running this is always safe: it only touches rows where
embedding IS NULL, so an interrupted run just picks up where it left
off next time rather than re-embedding everything.

Reuses the app's own embed_document()/transaction_to_text() (not a
separate copy of that logic) specifically so a transaction embedded by
this script and one embedded by the live API are guaranteed to use the
exact same rendering and model -- a second, slightly-different
implementation here would be exactly the kind of silent drift the RAG
playbook this was built against warns about (NON-NEGOTIABLE RULE: the
same model and encoding must produce both).

Run from the sanchay-api repo root:
    pip install -r requirements.txt --break-system-packages
    export SANCHAY_APP_DATABASE_URL="postgresql://..."   # Sanchaydb connection string
    python scripts/backfill_transaction_embeddings.py
"""
import json
import os
import sys

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.embedding_service import embed_document, transaction_to_text  # noqa: E402

BATCH_SIZE = 64  # matches the batching guidance in the RAG playbook this was built against


def main() -> None:
    database_url = os.environ.get("SANCHAY_APP_DATABASE_URL")
    if not database_url:
        print("SANCHAY_APP_DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    total_done = 0

    try:
        while True:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, description, amount, category, date
                    FROM transactions
                    WHERE embedding IS NULL
                    LIMIT %s
                    """,
                    (BATCH_SIZE,),
                )
                rows = cur.fetchall()

            if not rows:
                break

            texts = [
                transaction_to_text(
                    description=row["description"],
                    amount=float(row["amount"]),
                    category=row["category"],
                    date=row["date"].isoformat(),
                )
                for row in rows
            ]
            # One embed_document() call per row rather than a batched
            # model.encode(texts) here -- embed_document()'s signature
            # is intentionally single-text (matching the live API's
            # per-transaction call shape exactly), so this script stays
            # a real exercise of the same code path rather than a
            # faster but separately-implemented batch path that could
            # drift from what production actually does.
            vectors = [embed_document(text) for text in texts]

            with conn.cursor() as cur:
                for row, vector in zip(rows, vectors):
                    cur.execute(
                        "UPDATE transactions SET embedding = %s WHERE id = %s",
                        (json.dumps(vector), row["id"]),
                    )
            conn.commit()

            total_done += len(rows)
            print(f"Embedded {total_done} transactions so far...")

    finally:
        conn.close()

    print(f"Done. {total_done} transactions embedded.")


if __name__ == "__main__":
    main()
