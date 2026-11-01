"""Back up one SAHAAY database to a folder of compressed JSON-lines files (works on any MongoDB tier, needs no mongodump).

    python scripts/backup_db.py --out D:\\sahaay-backups            (backs up MONGODB_DATABASE from backend/.env)
    python scripts/backup_db.py --out D:\\sahaay-backups --database sahaay_test_abc

* READ-ONLY: it only reads the database. It never writes to, modifies or deletes anything in MongoDB.
* The backup contains PATIENT DATA and password hashes. Keep it OUTSIDE the project folder (the script refuses to write inside the git
  repository), encrypt the disk/folder, and restrict who can read it.
* Prints the database name and counts, never the connection string.
"""
import argparse
import gzip
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

from bson import json_util
from dotenv import dotenv_values
from pymongo import MongoClient
from pymongo.server_api import ServerApi

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="folder to create the backup in (must be outside the repository)")
    ap.add_argument("--database", help="default: MONGODB_DATABASE from backend/.env")
    a = ap.parse_args()
    env = dotenv_values(BACKEND / ".env")
    uri, name = env.get("MONGODB_URI"), a.database or env.get("MONGODB_DATABASE") or "sahaay"
    if not uri:
        sys.exit("MONGODB_URI is not set in backend/.env")
    out = pathlib.Path(a.out).resolve()
    if REPO in out.parents or out == REPO:
        sys.exit("Refusing: the backup holds patient data and must not be written inside the project/git folder.")
    dest = out / f"{name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    dest.mkdir(parents=True, exist_ok=False)
    client = MongoClient(uri, server_api=ServerApi("1"), serverSelectionTimeoutMS=15000)
    db = client[name]
    manifest = {"database": name, "created_at": datetime.now(timezone.utc).isoformat(), "collections": {}}
    for coll in sorted(db.list_collection_names()):
        path = dest / f"{coll}.jsonl.gz"
        n = 0
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            for doc in db[coll].find({}):
                fh.write(json_util.dumps(doc, sort_keys=True) + "\n")
                n += 1
        indexes = [{k: v for k, v in i.items() if k not in ("v", "ns")} for i in db[coll].list_indexes() if i["name"] != "_id_"]
        manifest["collections"][coll] = {"documents": n, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "indexes": json.loads(json_util.dumps(indexes))}
        print(f"  {coll}: {n} documents")
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    client.close()
    print(f"Backup of '{name}' written to {dest}")


if __name__ == "__main__":
    main()
