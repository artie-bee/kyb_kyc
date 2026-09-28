"""
Run the customer portal.

    python tools/serve_portal.py
    python tools/serve_portal.py --port 8701 --no-browser
    python tools/serve_portal.py --no-demo      # no case selector: access links only

Plain HTML and CSS over the orchestrator, the same stack as the console
(tools/serve.py, port 8700) and none of its routes. Both read and act on the
same database, so run them side by side and a file uploaded here is waiting in
the console for an analyst.

The demo database is built on first run if it is not there. The portal never
resets it; "Reset demo" in the console does that.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from portal import server                                             # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8701)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-demo", action="store_true",
                    help="turn off the demo case selector; customers need an access link")
    args = ap.parse_args()

    if not data.DB_PATH.exists():
        print("building the demo database ...")
        data.reset_demo()

    demo = not args.no_demo
    url = f"http://{args.host}:{args.port}/" + ("demo" if demo else "")
    httpd = server.serve(args.port, args.host, demo=demo)
    print(f"Wallester UC4 customer portal on {url}")
    print(f"  demo case selector: {'on' if demo else 'off'}    mode: {data.mode_badge()}")
    print("  ctrl-c to stop")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
        httpd.server_close()


if __name__ == "__main__":
    main()
