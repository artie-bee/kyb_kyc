"""
Run the console.

    python tools/serve.py
    python tools/serve.py --port 8700 --no-browser

Plain HTML and CSS over the orchestrator - no Streamlit, no framework, no build
step. The database is rebuilt to the demo start state on first run if it is not
there; "Reset demo" in the sidebar does it again on demand.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from web import server                                                # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="rebuild the demo database before starting")
    args = ap.parse_args()

    if args.reset or not data.DB_PATH.exists():
        print("building the demo database ...")
        data.reset_demo()

    url = f"http://{args.host}:{args.port}/"
    httpd = server.serve(args.port, args.host)
    print(f"Wallester UC4 console on {url}")
    print(f"  mode: {data.mode_badge()}    knowledge base: {data.kb().version}")
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
