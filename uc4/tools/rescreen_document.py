"""
Run Step 3 again on a portal upload already on file.

    python tools/rescreen_document.py DOC-0187 --reason "blur rule QR-13 added"

For when a quality rule changes after a file was screened. The file is screened
again by the same Step 3 run(); the old document row is kept, marked
superseded, and the re-screen is audited under the actor and reason given.
"""

import argparse
import sys
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("document_id")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--actor", default="ops.rescreen")
    args = ap.parse_args()
    conn = data.connect()
    r = data.rescreen_document(conn, args.document_id, args.actor, args.reason)
    print(f"{args.document_id} re-screened as {r.document_id}: {r.quality_status}; "
          f"case {r.case_id} is now {r.case_status}")


if __name__ == "__main__":
    main()
