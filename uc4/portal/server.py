"""
The customer portal, served. Port 8701, beside the console on 8700.

The same stack as the console's server - a plain http.server, no framework,
no template engine, no build step - and none of its routes. Nothing here loads
the console package, and any path the console answers is a plain 404 here.

The rules it keeps:

  - **it never writes SQL.** An upload goes through app/data.upload_document,
    which calls document_quality.receive_upload and nothing else; access tokens
    go through orchestrator/portal_access.py. tests/test_portal.py scans this
    package for SQL writes and fails if it finds any;
  - **every page passes through customer_view.leaks()** before it is sent. A page
    carrying restricted wording is refused, not shown;
  - **it reads the same database as the console**, one short connection per
    request, so the two can never disagree and the console's "Reset demo" can
    replace the file between requests;
  - **POST always redirects (303)**, so a refresh cannot resend a file, and the
    outcome message is held server-side under a random key - never taken from
    the query string, where anyone could write a link that puts their own words
    on the page;
  - **every page works with JavaScript off.** Uploads are plain
    multipart/form-data posts, parsed here with the standard library.

    python tools/serve_portal.py
"""

import secrets
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from app import data                                                  # noqa: E402
from app.customer_view import customer_checklist, customer_view, leaks  # noqa: E402
from orchestrator.steps.document_quality import MAX_UPLOAD_BYTES, UploadRefused  # noqa: E402
from portal import apply, render, render_form                         # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "wal_portal"
# The largest request body read at all: the file limit plus room for the form.
MAX_BODY = MAX_UPLOAD_BYTES + 64 * 1024

CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # An access link carries a token in its path; it must not travel to another
    # site as a referrer. same-origin, not no-referrer: under no-referrer a
    # browser sends "Origin: null" on every form post, even to this site, and
    # the origin check below would refuse the portal's own forms.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; form-action 'self'; frame-ancestors 'none'",
}

# One request at a time against the database, as in the console.
_lock = threading.RLock()


class PortalServer(ThreadingHTTPServer):
    """Carries its settings, so a test can point one at its own database."""

    def __init__(self, address, db_path: Path, demo: bool, uploads_dir: Path | None):
        super().__init__(address, Handler)
        self.db_path = db_path
        self.demo = demo
        self.uploads_dir = uploads_dir
        self.flashes: dict[str, tuple[str, str]] = {}


class Handler(BaseHTTPRequestHandler):
    server_version = "WallesterPortal"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):
        # Never log the path of an access link: it carries the token.
        path = "/access/..." if self.path.startswith("/access/") else self.path
        sys.stderr.write("  portal %s %s\n" % (self.command, path))

    def _send(self, body: bytes, status=200, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _page(self, html: str, status=200):
        """Every page goes out through here, and through leaks() first."""
        found = leaks(html)
        if found:
            sys.stderr.write(f"  portal REFUSED a page carrying {found}\n")
            html = render.page("Not available", render.refused(), demo=self.server.demo)
            status = 500
        self._send(html.encode("utf-8"), status)

    def _redirect(self, where: str, cookies=None):
        self.send_response(303)
        self.send_header("Location", where)
        for c in cookies or []:
            self.send_header("Set-Cookie", c)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _token(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and v:
                return v
        return None

    def _flash_put(self, kind: str, text: str) -> str:
        key = secrets.token_urlsafe(8)
        flashes = self.server.flashes
        if len(flashes) > 500:              # a demo; keep it from growing without end
            flashes.clear()
        flashes[key] = (kind, text)
        return key

    def _connect(self):
        return data.connect(self.server.db_path)

    # -- GET --------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        query = parse_qs(url.query)
        flash = self.server.flashes.pop(query.get("m", [""])[0], None)
        demo = self.server.demo
        try:
            if path.startswith("/static/"):
                return self._static(path)
            if path.startswith("/access/"):
                return self._access(path[len("/access/"):])
            with _lock:
                conn = self._connect()
                try:
                    if path == "/demo":
                        if not demo:
                            return self._page(render.page("Not found", render.not_found()), 404)
                        customers = [{"case_id": c["case_id"],
                                      "applicant_name": customer_view(conn, c["case_id"])
                                      ["applicant_name"]}
                                     for c in conn.execute(
                                         "SELECT case_id FROM onboarding_case ORDER BY case_id")]
                        return self._page(render.page("Choose a demo customer",
                                                      render.demo_selector(customers),
                                                      flash=flash, demo=demo))
                    if path == "/apply":
                        if not demo:
                            return self._page(render.page("Not found", render.not_found()), 404)
                        return self._page(render.page(
                            "Apply", render_form.application_form(apply.BUSINESS, {}),
                            flash=flash, demo=demo))
                    if path not in ("/", "/checklist", "/messages"):
                        return self._page(render.page("Not found", render.not_found(),
                                                      demo=demo), 404)
                    case_id = data.portal_case(conn, self._token())
                    if case_id is None:
                        if demo and path == "/":
                            return self._redirect("/demo")
                        return self._page(render.page("Sign in", render.signed_out(demo),
                                                      demo=demo), 401)
                    view = customer_view(conn, case_id)
                    if path == "/":
                        body, title, active = render.application(view), "My application", \
                            "My application"
                    elif path == "/checklist":
                        body = render.checklist(view, customer_checklist(conn, case_id))
                        title = active = "Documents needed"
                    else:
                        body, title, active = render.messages(view), "Messages", "Messages"
                    return self._page(render.page(title, body, view, active, flash, demo))
                finally:
                    conn.close()
        except Exception:
            traceback.print_exc()
            return self._page(render.page("Not available", render.refused(), demo=demo), 500)

    def _static(self, path):
        target = (STATIC / path[len("/static/"):]).resolve()
        if (not str(target).startswith(str(STATIC.resolve())) or not target.is_file()
                or target.suffix not in CONTENT_TYPES):
            return self._page(render.page("Not found", render.not_found()), 404)
        return self._send(target.read_bytes(), ctype=CONTENT_TYPES[target.suffix])

    def _access(self, token):
        """The link from the email: swap the token in the URL for a cookie."""
        with _lock:
            conn = self._connect()
            try:
                case_id = data.portal_case(conn, token)
            finally:
                conn.close()
        if case_id is None:
            return self._page(render.page("Sign in", render.signed_out(self.server.demo),
                                          demo=self.server.demo), 401)
        return self._redirect("/", cookies=[_session_cookie(token)])

    # -- POST -------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            # Refused before reading: the body is never pulled into memory.
            self.close_connection = True
            key = self._flash_put("err", f"The file is larger than "
                                         f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                                         "Please upload a smaller copy.")
            return self._redirect("/checklist?m=" + key)
        if not _same_origin(self.headers):
            return self._send(b"cross-site request refused", 403, "text/plain; charset=utf-8")
        body = self.rfile.read(length) if length else b""

        try:
            with _lock:
                conn = self._connect()
                try:
                    if path == "/demo/open" and self.server.demo:
                        return self._demo_open(conn, _urlencoded(body))
                    if path == "/apply" and self.server.demo:
                        return self._apply(conn, _urlencoded(body))
                    if path == "/signout":
                        token = self._token()
                        if token:
                            data.end_portal_session(conn, token)
                        return self._redirect("/demo" if self.server.demo else "/",
                                              cookies=[COOKIE + "=; Path=/; Max-Age=0; "
                                                                "HttpOnly; SameSite=Lax"])
                    if path == "/upload":
                        return self._upload(conn, body)
                finally:
                    conn.close()
        except Exception:
            traceback.print_exc()
            key = self._flash_put("err", "Something went wrong on our side. Please try again.")
            return self._redirect("/checklist?m=" + key)
        return self._page(render.page("Not found", render.not_found(),
                                      demo=self.server.demo), 404)

    def _demo_open(self, conn, form):
        case_id = form.get("case_id", "")
        if conn.execute("SELECT 1 FROM onboarding_case WHERE case_id = ?",
                        (case_id,)).fetchone() is None:
            return self._redirect("/demo")
        old = self._token()
        if old:
            data.end_portal_session(conn, old)
        token = data.issue_portal_access(conn, case_id, "demo.case_selector")
        return self._redirect("/", cookies=[_session_cookie(token)])

    def _apply(self, conn, form):
        """One step of the demo application form.

        A step that moves forward or back renders the next page straight from
        the POST: nothing has been stored yet, so a refresh repeats nothing.
        Only sending the finished application changes anything, and that one
        redirects (303), as every other action here does.
        """
        answers = apply.known_answers(form)
        try:
            step = min(max(int(form.get("step") or apply.BUSINESS), apply.BUSINESS),
                       apply.REVIEW)
        except ValueError:
            step = apply.BUSINESS
        demo = self.server.demo

        def show(n, errors=None):
            return self._page(render.page("Apply", render_form.application_form(
                n, answers, errors), demo=demo))

        if form.get("nav") == "back":
            return show(apply.previous_step(step, answers))
        errors = apply.validate(step, answers)
        if errors:
            return show(step, errors)
        if form.get("nav") != "submit" or step != apply.REVIEW:
            return show(apply.next_step(step, answers))

        application = apply.build_application(answers, "PORTAL-" + secrets.token_hex(4))
        case_id = data.submit_application(conn, application)
        old = self._token()
        if old:
            data.end_portal_session(conn, old)
        token = data.issue_portal_access(conn, case_id, "portal.demo_application")
        key = self._flash_put("ok", "We have your application. Your checklist shows the "
                                    "documents we need.")
        return self._redirect("/?m=" + key, cookies=[_session_cookie(token)])

    def _upload(self, conn, body):
        case_id = data.portal_case(conn, self._token())
        if case_id is None:
            return self._redirect("/")
        fields, files = parse_multipart(self.headers.get("Content-Type") or "", body)
        item_id = fields.get("item_id", "")
        name, content = files.get("file", ("", b""))
        anchor = "#item-" + item_id if item_id.replace("-", "").isalnum() else ""
        try:
            data.upload_document(conn, case_id, item_id, name, content,
                                 uploads_dir=self.server.uploads_dir)
        except UploadRefused as refusal:
            # The orchestrator's own wording, which is written for the customer.
            key = self._flash_put("err", str(refusal))
            return self._redirect("/checklist?m=" + key + anchor)
        # What happened to it, re-read from the records rather than assumed.
        item = next((i for i in customer_checklist(conn, case_id)["items"]
                     if i["checklist_item_id"] == item_id), None)
        status = item["status"] if item else ""
        if status == "Accepted":
            key = self._flash_put("ok", "Thank you. Your file has been accepted.")
        elif status == "Resubmission needed":
            key = self._flash_put("err", "We could not accept this file. " + item["reason"])
        else:
            key = self._flash_put("ok", "Thank you. We have your file and it is now under "
                                        "review. We will let you know if we need anything "
                                        "else.")
        return self._redirect("/checklist?m=" + key + anchor)


# ---------------------------------------------------------------------------

def _session_cookie(token: str) -> str:
    return COOKIE + "=" + token + "; Path=/; HttpOnly; SameSite=Lax"


def _same_origin(headers) -> bool:
    """A browser names the page a POST came from. If it names another site,
    refuse. (The session cookie is SameSite=Lax as well; this is the second lock.)"""
    origin = headers.get("Origin")
    if not origin or origin == "null":
        return origin is None
    return urlparse(origin).netloc == (headers.get("Host") or "")


def _urlencoded(body: bytes) -> dict:
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace"),
                                         keep_blank_values=True).items()}


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
    """(fields, files) from a multipart/form-data body. Standard library only.

    Byte-exact: the file content is sliced out of the body, never decoded, so a
    PDF or an image arrives as it was sent. files maps a field name to
    (file name, bytes); only the last component of a file name is kept.
    """
    fields, files = {}, {}
    params = dict(p.strip().split("=", 1) for p in content_type.split(";")[1:] if "=" in p)
    boundary = params.get("boundary", "").strip('"')
    if not content_type.lower().startswith("multipart/form-data") or not boundary:
        return fields, files
    delimiter = b"--" + boundary.encode("latin-1")
    for part in body.split(delimiter)[1:]:
        if part.startswith(b"--"):
            break
        part = part[2:] if part.startswith(b"\r\n") else part
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        disposition = {}
        for line in head.decode("utf-8", "replace").split("\r\n"):
            key, _, value = line.partition(":")
            if key.strip().lower() == "content-disposition":
                for item in value.split(";")[1:]:
                    k, _, v = item.strip().partition("=")
                    disposition[k.lower()] = v.strip('"')
        name = disposition.get("name")
        if not name:
            continue
        if "filename" in disposition:
            files[name] = (disposition["filename"].replace("\\", "/").rsplit("/", 1)[-1],
                           content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


def serve(port: int = 8701, host: str = "127.0.0.1", db_path: Path | None = None,
          demo: bool = True, uploads_dir: Path | None = None) -> PortalServer:
    return PortalServer((host, port), db_path or data.DB_PATH, demo, uploads_dir)
