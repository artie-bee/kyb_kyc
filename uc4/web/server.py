"""
The console, served.

A plain http.server: no framework, no template engine, no build step. Routes map
to the render functions; POST actions map to app/data.py - the same functions the
Streamlit app calls, which is what keeps one set of rules behind two front ends.

**This module never writes SQL.** Every action goes through the orchestrator's
own function, so a refusal you see on screen - "cannot approve while holds are
open", "compliance role required" - is the real rule refusing, not a message this
server invented. tests/test_app.py scans web/ for SQL writes and fails if it
finds any.

POST always redirects (303) rather than rendering, so a refresh cannot replay an
action. The outcome travels back in the query string and is shown once.

    python tools/serve.py
    python tools/serve.py --port 8700 --no-browser
"""

import json
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from web import render                                                # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"

# One connection, guarded. SQLite is happy with this and it keeps the demo's
# state in one place; the lock matters because ThreadingHTTPServer will happily
# run two requests at once and an action must not interleave with a read.
#
# Reentrant, because a request that fails part-way through re-enters to build
# the error page - and a plain Lock deadlocks the thread against itself there,
# which shows up as a page that hangs rather than one that errors.
_lock = threading.RLock()
_conn = None


def connection():
    global _conn
    if _conn is None:
        if not data.DB_PATH.exists():
            data.reset_demo()
        _conn = data.connect()
    return _conn


def drop_connection():
    """After a reset the file underneath has been replaced."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
    _conn = None


CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".pdf": "application/pdf"}


class Handler(BaseHTTPRequestHandler):
    server_version = "WallesterUC4"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):            # one tidy line, not two
        sys.stderr.write("  %s %s\n" % (self.command, self.path))

    def _send(self, body: bytes, status=200, ctype="text/html; charset=utf-8",
              extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, text: str, status=200, extra=None):
        self._send(text.encode("utf-8"), status, extra=extra)

    def _redirect(self, where: str, cookies=None):
        extra = {"Location": where}
        self.send_response(303)
        self.send_header("Location", where)
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookies(self) -> dict:
        raw = self.headers.get("Cookie") or ""
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _identity(self):
        c = self._cookies()
        role = c.get("role", "analyst")
        if role not in ("analyst", "compliance"):
            role = "analyst"
        reviewer = c.get("reviewer") or (role + ".demo")
        return role, reviewer.replace("%40", "@")

    def _form(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    # -- GET --------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        query = parse_qs(url.query)
        flash = None
        if "ok" in query:
            flash = ("ok", query["ok"][0])
        elif "err" in query:
            flash = ("err", query["err"][0])

        try:
            if path.startswith("/static/"):
                return self._static(path)
            if path.startswith("/doc/"):
                return self._document(path.rsplit("/", 1)[-1])
            if path.startswith("/export/") and path.endswith(".json"):
                return self._bundle(path[len("/export/"):-len(".json")])

            role, reviewer = self._identity()
            with _lock:
                conn = connection()

                # A bare case id - what a browser offers from history, or what
                # you get typing the id you were just looking at. It is
                # unambiguous, so redirect rather than refuse.
                bare = path.strip("/")
                if bare and "/" not in bare and _case_exists(conn, bare):
                    return self._redirect("/case/" + bare)

                # A screen path with no case on it. The sidebar generates these
                # whenever it has no case to hand, so they have to go somewhere.
                if path in ("/case/", "/customer/", "/export/"):
                    first = next((r["case_id"] for r in data.dashboard(conn)), None)
                    if first:
                        return self._redirect(path + first)

                if path == "/":
                    body = render.dashboard(conn)
                    title, active, case_id = "Operations dashboard", "Operations dashboard", None
                elif path == "/reuse":
                    body = render.reuse(conn)
                    title, active, case_id = "Agent reuse", "Agent reuse", None
                elif path.startswith("/case/"):
                    case_id = path[len("/case/"):]
                    if not _case_exists(conn, case_id):
                        return self._html(self._no_case(case_id), 404)
                    tab = query.get("tab", ["Timeline"])[0]
                    if tab not in render.TABS:
                        tab = "Timeline"
                    body = render.case_detail(conn, case_id, tab=tab, role=role,
                                              reviewer=reviewer)
                    title, active = case_id, "Case detail"
                elif path.startswith("/customer/"):
                    case_id = path[len("/customer/"):]
                    if not _case_exists(conn, case_id):
                        return self._html(self._no_case(case_id), 404)
                    body = render.customer(conn, case_id)
                    title, active = "Customer view", "Customer view"
                elif path.startswith("/export/"):
                    case_id = path[len("/export/"):]
                    if not _case_exists(conn, case_id):
                        return self._html(self._no_case(case_id), 404)
                    body = render.export(conn, case_id)
                    title, active = "Audit export", "Audit export"
                else:
                    return self._html(self._not_found(path), 404)

                html = render.page(title, body, active, conn=conn, case_id=case_id,
                                   role=role, reviewer=reviewer, flash=flash)
            return self._html(html)
        except KeyError as err:
            return self._html(self._not_found(str(err)), 404)
        except Exception:
            return self._html(self._crash(), 500)

    def _static(self, path):
        name = path[len("/static/"):]
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.exists():
            return self._html(self._not_found(path), 404)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        return self._send(target.read_bytes(), ctype=ctype)

    def _document(self, document_id):
        """Serve a sample document. Read-only, and only ever one this case owns."""
        with _lock:
            conn = connection()
            row = conn.execute(
                "SELECT case_id, file_name FROM document WHERE document_id = ?",
                (document_id,)).fetchone()
        if row is None:
            return self._html(self._not_found(document_id), 404)
        path = (data.SAMPLE_DOCS / row["case_id"] / row["file_name"]).resolve()
        root = data.SAMPLE_DOCS.resolve()
        if not str(path).startswith(str(root)) or not path.exists():
            return self._html(self._not_found(row["file_name"]), 404)
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return self._send(path.read_bytes(), ctype=ctype)

    def _bundle(self, case_id):
        with _lock:
            bundle = data.export_bundle(connection(), case_id)
        body = json.dumps(bundle, indent=2, ensure_ascii=False).encode("utf-8")
        return self._send(body, ctype="application/json; charset=utf-8",
                          extra={"Content-Disposition":
                                 'attachment; filename="' + case_id + '.json"'})

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        form = self._form()
        back = form.get("back") or "/"
        role, reviewer = self._identity()

        if path == "/action/settings":
            new_role = form.get("role", "analyst")
            new_reviewer = (form.get("reviewer") or "").strip() or (new_role + ".demo")
            return self._redirect(back, cookies=[
                "role=" + quote(new_role) + "; Path=/; SameSite=Lax",
                "reviewer=" + quote(new_reviewer) + "; Path=/; SameSite=Lax"])

        try:
            with _lock:
                message = self._run(path, form, role, reviewer)
            return self._redirect(_with(back, "ok", message))
        except KeyError:
            return self._html(self._not_found(path), 404)
        except Exception as err:
            # The backend's refusal, verbatim. It is the real rule speaking and
            # the screen must not soften or reword it.
            text = type(err).__name__ + ": " + str(err)
            return self._redirect(_with(back, "err", text))

    def _run(self, path, form, role, reviewer):
        conn = connection()

        if path == "/action/reset":
            drop_connection()
            data.reset_demo()
            connection()
            return "Reset. Every case is back at its first human action."

        if path == "/action/release-document":
            result = data.release_document(
                conn, form["document_id"], reviewer,
                form.get("choice", "accept"), form.get("reason", ""))
            return "Document " + result.document_id + ": " + result.decision

        if path == "/action/field":
            how = form.get("how", "accept")
            if how == "correct":
                data.correct_field(conn, form["field_id"], reviewer,
                                   form.get("value", ""), form.get("reason", ""))
                return "Field " + form["field_id"] + " corrected."
            data.accept_field_as_read(conn, form["field_id"], reviewer,
                                      form.get("reason", ""))
            return "Field " + form["field_id"] + " accepted as read."

        if path == "/action/record-decision":
            result = data.record_decision(
                conn, form["case_id"], reviewer, role, form["choice"],
                form.get("reason_code", "demo_decision"), form.get("rationale", ""),
                override_reason=(form.get("override_reason") or None),
                escalation_target=(form.get("escalation_target") or None))
            extra = ""
            if result.override_flag:
                extra = " (recorded as a " + str(result.override_direction) + " override)"
            return "Recorded " + result.decision + " as " + role + extra

        if path == "/action/send-message":
            result = data.send_message(conn, form["case_id"],
                                       form["situation"], reviewer)
            return ("Message " + result.communication_id + " ("
                    + result.template_id + ") " + result.sent_status)

        raise KeyError(path)

    # -- error pages -------------------------------------------------------
    def _shell(self, body):
        """An error page with a working sidebar.

        Built with the connection rather than without it: a 404 whose own
        navigation is broken leaves you clicking in circles, which is worse
        than no navigation at all.
        """
        role, reviewer = self._identity()
        try:
            with _lock:
                conn = connection()
                first = next((r["case_id"] for r in data.dashboard(conn)), None)
                return render.page("Not found", body, "", conn=conn, case_id=first,
                                   role=role, reviewer=reviewer)
        except Exception:
            return render.page("Not found", body, "", conn=None)

    def _no_case(self, case_id):
        return self._shell(render.note(
            "There is no case <code>" + render.e(case_id) + "</code> in this "
            "database. It may have been renamed, or the demo may need resetting.",
            "warn", "No such case"))

    def _not_found(self, what):
        return self._shell(render.note(
            "No screen or file at <code>" + render.e(what) + "</code>.", "warn",
            "Not found"))

    def _crash(self):
        detail = render.e(traceback.format_exc())
        return self._shell(render.note(
            "<p>Something in the server failed while rendering this page. The trace "
            "is below and in the terminal.</p><pre class=\"code\">" + detail + "</pre>",
            "bad", "This page did not render"))


def _case_exists(conn, case_id: str) -> bool:
    """Checked before rendering, so an unknown id is a plain 404 rather than a
    stack trace from three layers down."""
    return conn.execute("SELECT 1 FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone() is not None


def _with(url: str, key: str, value: str) -> str:
    joiner = "&" if "?" in url else "?"
    return url + joiner + key + "=" + quote(value[:400])


def serve(port: int = 8700, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd
