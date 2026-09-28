"""
Customer portal access - which case a portal visitor may see.

An applicant reaches their case through a token: in production it would arrive
in an email link, in the demo the case selector issues one. The token is the
only key, so it is random, it is never derived from the case id (case ids are
sequential and anyone could guess the next one), and only its hash is stored.

    issue(conn, case_id, issued_by)   -> the raw token, shown once
    resolve(conn, token)              -> the case id, or None
    revoke(conn, token, revoked_by)

This module writes; the portal only calls it. Every issue and revoke is audited.
"""

import hashlib
import secrets

from . import db

ACTOR = "step.portal_access"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(conn, case_id: str, issued_by: str, kb=None) -> str:
    """A new token for this case. The raw value is returned once and not kept."""
    if not (issued_by or "").strip():
        raise ValueError("a portal token must say who issued it")
    if conn.execute("SELECT 1 FROM onboarding_case WHERE case_id = ?",
                    (case_id,)).fetchone() is None:
        raise KeyError(f"no such case {case_id}")
    token = secrets.token_urlsafe(24)
    conn.execute("INSERT INTO portal_token (token_hash, case_id, issued_by, issued_at)"
                 " VALUES (?,?,?,?)", (_hash(token), case_id, issued_by, db.now()))
    db.audit(conn, case_id, "system", ACTOR, "portal_access_issued",
             f"customer portal access issued by {issued_by}", getattr(kb, "version", None))
    return token


def resolve(conn, token: str | None) -> str | None:
    """The case this token opens, or None for an unknown or revoked token."""
    if not token:
        return None
    row = conn.execute("SELECT case_id FROM portal_token WHERE token_hash = ?"
                       " AND revoked_at IS NULL", (_hash(token),)).fetchone()
    return row["case_id"] if row else None


def revoke(conn, token: str, revoked_by: str, kb=None) -> bool:
    """Stop a token working. Returns False if it was not active."""
    row = conn.execute("SELECT case_id FROM portal_token WHERE token_hash = ?"
                       " AND revoked_at IS NULL", (_hash(token),)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE portal_token SET revoked_at = ? WHERE token_hash = ?",
                 (db.now(), _hash(token)))
    db.audit(conn, row["case_id"], "applicant", revoked_by, "portal_access_revoked",
             "customer portal session ended", getattr(kb, "version", None))
    return True
