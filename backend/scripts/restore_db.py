"""Restore a backup made by backup_db.py into an EMPTY database.

    python scripts/restore_db.py --backup D:\\sahaay-backups\\sahaay-20261101T120000Z --into sahaay_restore_check

Safety rules (enforced):
* The target database must be named explicitly and every collection in it must be empty/non-existent; nothing is ever overwritten.
* Restoring into the live database name ('sahaay' or MONGODB_DATABASE) is refused. Restore into a new name, check it, then point
  MONGODB_DATABASE at it (see documentation/DEPLOYMENT_GUIDE.md).
* File checksums are verified before anything is written; document counts are verified afterwards. Indexes are re-created.
"""
import argparse
import gzip
import hashlib
import json
import pathlib
import sys

from bson import json_util
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.server_api import ServerApi

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--into", required=True, help="name of the NEW database to restore into")
    a = ap.parse_args()
    env = dotenv_values(BACKEND / ".env")
    live = {"sahaay", env.get("MONGODB_DATABASE") or "sahaay"}
    if a.into in live:
        sys.exit(f"Refusing to restore into '{a.into}': that is the live database. Use a new name.")
    src = pathlib.Path(a.backup)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    for coll, meta in manifest["collections"].items():
        if hashlib.sha256((src / f"{coll}.jsonl.gz").read_bytes()).hexdigest() != meta["sha256"]:
            sys.exit(f"Checksum mismatch for {coll}: the backup file is damaged or was changed. Nothing was restored.")
    client = MongoClient(env["MONGODB_URI"], server_api=ServerApi("1"), serverSelectionTimeoutMS=15000)
    target = client[a.into]
    nonempty = [c for c in target.list_collection_names() if target[c].estimated_document_count() > 0]
    if nonempty:
        sys.exit(f"Refusing: '{a.into}' already holds data in {nonempty}. Restore only into an empty database.")
    for coll, meta in manifest["collections"].items():
        batch = []
        with gzip.open(src / f"{coll}.jsonl.gz", "rt", encoding="utf-8") as fh:
            for line in fh:
                batch.append(json_util.loads(line))
                if len(batch) >= 500:
                    target[coll].insert_many(batch); batch = []
        if batch:
            target[coll].insert_many(batch)
        for ix in meta["indexes"]:
            keys = [(k, v) for k, v in ix.pop("key").items()]
            ix.pop("ns", None)
            target[coll].create_index(keys, **ix)
        got = target[coll].count_documents({})
        if got != meta["documents"]:
            sys.exit(f"Count mismatch in {coll}: backup has {meta['documents']}, restored {got}.")
        print(f"  {coll}: {got} documents restored")
    client.close()
    print(f"Restore into '{a.into}' complete and verified. Review it, then switch MONGODB_DATABASE if this is a real recovery.")


if __name__ == "__main__":
    main()
