"""
STEP 5 - Verification: registry, identity and beneficial ownership
(brief Sections 5.5 and 5.6)

This is the first step that spends money, so it opens with a gate: it refuses to
run unless every REQUIRED checklist item is accepted. A case still waiting on a
document does not get billed for provider calls it will have to repeat.

Registry (kb/registry_rules.csv)
  The provider returns what the register HOLDS - legal name, number, address,
  directors. The match results are computed here, by comparing those values
  against the EXTRACTED fields, an analyst's correction included. A provider
  that does not answer is not a pass: RG-09 retries once and then stops the case.

Identity (per director, UBO and authorised signatory)
  fail or review                 -> analyst review (blocking)
  duplicate_individual_detected  -> analyst review (blocking)
  document_expired               -> that one ID goes back for resubmission
  Identity providers return verdicts rather than raw facts, so those verdicts are
  recorded as given; what this step owns is what each verdict means for the case.

Beneficial ownership (kb/ubo_policy.csv)
  Effective ownership is multiplied along the chain - 70% of a holding company
  that owns 45% of the applicant is 31.5% - and compared with what was declared.
  Owners at or above the threshold must be verified.

Blocking outcomes stop the case at analyst_review_required. Non-blocking findings
are written to the finding table with their evidence references and the case
carries on to screening, which must still run on every subject.
"""

import re
from dataclasses import dataclass, field

from .. import db
from .. import holds
from ..kb import KnowledgeBase
from ..providers import (IdentityProvider, MockIdentityProvider, MockRegistryProvider,
                         RegistryProvider, UNAVAILABLE)

ACTOR = "step.verification"
SUBJECT_ROLES = ("director", "ubo", "authorised_signatory", "sole_trader")
MAX_REGISTRY_ATTEMPTS = 2          # the RG-09 retry: one call, one retry, then stop


class VerificationGateError(RuntimeError):
    """Step 5 was asked to run before the checklist was complete."""


@dataclass
class VerificationResult:
    case_id: str
    registry_result: str | None
    identities_checked: int
    ubos_checked: int
    findings: list[dict] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    status: str = ""
    next_step: str | None = None
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Comparing what the register holds with what the documents say
# ---------------------------------------------------------------------------

def _norm(text: str | None) -> str:
    """Compare on meaning, not punctuation: case, spacing and commas are noise."""
    return re.sub(r"[\s,.]+", " ", (text or "").strip().lower()).strip()


def _match(registry_value: str | None, extracted_value: str | None) -> str:
    if not registry_value or not extracted_value:
        return UNAVAILABLE          # nothing to compare is not a match
    return "match" if _norm(registry_value) == _norm(extracted_value) else "mismatch"


def _names_match(registry_names: list[str], declared: list[str]) -> str:
    if not registry_names or not declared:
        return UNAVAILABLE
    return "match" if {_norm(n) for n in registry_names} == {_norm(n) for n in declared} else "mismatch"


def effective_ownership(chain_percentages: list[float]) -> float:
    """Multiply along the chain (UB-02). [70, 45] -> 31.5"""
    if not chain_percentages:
        return 0.0
    product = 1.0
    for pct in chain_percentages:
        product *= float(pct) / 100.0
    return round(product * 100.0, 4)


def extracted_values(conn, case_id: str) -> dict:
    """Every field extracted for this case, corrections winning over first readings.

    A corrected value replaces the value OCR read, which is the whole point of
    correcting it: this is what the register is compared against.
    """
    values = {}
    # A superseded upload is history: a customer's wrong file, replaced by the
    # right one, must not feed the comparison it was replaced to correct.
    for r in conn.execute(
            "SELECT f.name, f.value, f.corrected_by_analyst, d.document_type"
            " FROM extracted_field f JOIN document d USING (document_id)"
            " WHERE d.case_id = ? AND f.value IS NOT NULL AND d.quality_status != 'superseded'"
            " ORDER BY f.corrected_by_analyst", (case_id,)):
        values.setdefault(r["name"], []).append(r["value"])
        if r["corrected_by_analyst"]:
            values[r["name"]] = [r["value"]] + [v for v in values[r["name"]] if v != r["value"]]
    return values


def gate(conn, case_id: str) -> None:
    """The paid-check boundary. Nothing external is called before this passes."""
    open_items = conn.execute(
        "SELECT i.item_id, i.document_type, i.status FROM checklist_item i"
        " JOIN requirement_pack p USING (pack_id)"
        " WHERE p.case_id = ? AND i.level = 'required' AND i.status != 'accepted'",
        (case_id,)).fetchall()
    if open_items:
        raise VerificationGateError(
            f"{case_id}: {len(open_items)} required checklist item(s) are not accepted "
            f"({', '.join(sorted({r['document_type'] for r in open_items}))}); "
            f"verification is a paid step and does not run until the checklist is complete")


# ---------------------------------------------------------------------------

def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        registry_provider: RegistryProvider | None = None,
        identity_provider: IdentityProvider | None = None) -> VerificationResult:
    gate(conn, case_id)

    registry_provider = registry_provider or MockRegistryProvider()
    identity_provider = identity_provider or MockIdentityProvider()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    applicant = conn.execute("SELECT * FROM applicant WHERE applicant_id = ?",
                             (case["applicant_id"],)).fetchone()
    values = extracted_values(conn, case_id)

    findings, blocking, problems = [], [], []

    def add_finding(source, rule_id, summary, evidence, is_blocking=False):
        fid = db.next_id(conn, "finding")
        conn.execute("INSERT INTO finding VALUES (?,?,?,?,?,?,?,?)",
                     (fid, case_id, source, rule_id, summary, "|".join(evidence),
                      int(is_blocking), db.now()))
        record = {"finding_id": fid, "source": source, "rule_id": rule_id,
                  "summary": summary, "evidence_refs": evidence, "blocking": is_blocking}
        findings.append(record)
        if is_blocking:
            blocking.append(f"{rule_id}: {summary}")
        return record

    # ---- registry ---------------------------------------------------------
    response, attempts = None, 0
    while attempts < MAX_REGISTRY_ATTEMPTS:
        attempts += 1
        response = registry_provider.lookup(dict(applicant), dict(case))
        db.audit(conn, case_id, "external_provider", response.provider_name or "registry",
                 "registry_check_attempted",
                 f"attempt {attempts} of {MAX_REGISTRY_ATTEMPTS}; mode={registry_provider.mode}; "
                 f"{'answered' if response.available else 'no answer'}",
                 registry_provider.version or kb.version)
        if response.available:
            break

    check_id = db.next_id(conn, "registry_check")
    if not response.available:
        # RG-09: an unanswered provider call is insufficient evidence, never a pass.
        rule = kb.registry_rule("provider_status", "unavailable")
        conn.execute(
            "INSERT INTO registry_check (check_id, case_id, applicant_id, provider_name,"
            " company_status, result, attempts) VALUES (?,?,?,?,?,?,?)",
            (check_id, case_id, applicant["applicant_id"], response.provider_name or "unknown",
             UNAVAILABLE, UNAVAILABLE, attempts))
        add_finding("registry", rule["rule_id"],
                    f"Registry provider did not answer after {attempts} attempt(s); "
                    f"insufficient evidence, not a pass", [check_id], is_blocking=True)
        registry_result, fired = UNAVAILABLE, [rule["rule_id"]]
    else:
        # Match results are COMPUTED from what the register holds vs what was extracted.
        # Who the DOCUMENTS say runs the business - never who the application
        # says. A company's register of directors names them; a sole trader has
        # none, and is named by their own identity document. If no document
        # names anyone, there is nothing to compare, and "unavailable" is not a
        # match.
        declared_directors = [v for name in ("director_name", "director_name_2")
                              for v in values.get(name, [])]
        if not declared_directors:
            declared_directors = [r["value"] for r in conn.execute(
                "SELECT f.value FROM extracted_field f JOIN document d USING (document_id)"
                " JOIN individual i ON i.individual_id = d.subject_individual_id"
                " WHERE d.case_id = ? AND d.document_type = 'id_document'"
                " AND d.quality_status != 'superseded' AND i.role = 'sole_trader'"
                " AND f.name = 'full_name' AND f.value IS NOT NULL", (case_id,))]
        matches = {
            "name_match": _match(response.legal_name, (values.get("company_name")
                                                       or values.get("legal_name") or [None])[0]),
            "number_match": _match(response.number, (values.get("registration_number")
                                                     or [None])[0]),
            "address_match": _match(response.address, (values.get("registered_address")
                                                       or [None])[0]),
            "director_match": _names_match(response.directors, declared_directors),
        }

        fired = []
        rule = kb.registry_rule("company_status", response.company_status)
        if rule:
            fired.append(rule["rule_id"])
            if rule["blocking"].lower() == "true":
                add_finding("registry", rule["rule_id"],
                            f"Company status on the register is '{response.company_status}': "
                            f"{rule['description']}", [check_id], is_blocking=True)
        for check, outcome in matches.items():
            if outcome != "mismatch":
                continue
            rule = kb.registry_rule(check, "mismatch")
            if not rule:
                continue
            fired.append(rule["rule_id"])
            is_blocking = rule["blocking"].lower() == "true"
            add_finding("registry", rule["rule_id"],
                        f"{check.replace('_', ' ')} is a mismatch: register holds "
                        f"{getattr(response, check.split('_')[0], None)!r}",
                        [check_id], is_blocking=is_blocking)

        # The check's own result covers everything the register said, including a
        # failure to corroborate the beneficial ownership - that is a registry
        # outcome even though the rule that acts on it lives in the UBO policy.
        registry_findings = [f for f in findings if f["source"] == "registry"]
        if any(f["blocking"] for f in registry_findings):
            registry_result = "fail"           # dissolved, struck off, name/number mismatch
        elif registry_findings or response.ubo_supported_by_registry is False:
            registry_result = "review"
        else:
            registry_result = "pass"
        conn.execute(
            "INSERT INTO registry_check (check_id, case_id, applicant_id, provider_name,"
            " company_status, registry_legal_name, registry_number, registry_address,"
            " registry_directors, name_match, number_match, address_match, director_match,"
            " ubo_supported_by_registry, high_risk_jurisdiction_or_industry, confidence,"
            " result, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (check_id, case_id, applicant["applicant_id"], response.provider_name,
             response.company_status, response.legal_name, response.number, response.address,
             "|".join(response.directors), matches["name_match"], matches["number_match"],
             matches["address_match"], matches["director_match"],
             str(response.ubo_supported_by_registry).lower(),
             str(response.high_risk_jurisdiction_or_industry).lower(),
             response.confidence, registry_result, attempts))

    db.audit(conn, case_id, "external_provider", response.provider_name or "registry",
             "registry_check_completed",
             f"{check_id} -> {registry_result}; provider={response.provider_name}; "
             f"mode={registry_provider.mode}; rules={','.join(fired) or 'none fired'}",
             registry_provider.version or kb.version)

    # ---- identity, one call per subject ------------------------------------
    subjects = conn.execute(
        "SELECT * FROM individual WHERE applicant_id = ? AND role IN "
        f"({','.join('?' * len(SUBJECT_ROLES))}) ORDER BY individual_id",
        (case["applicant_id"], *SUBJECT_ROLES)).fetchall()
    ref_by_name = {p["full_name"]: p["ref"] for p in application.get("individuals", [])}

    for person in subjects:
        payload = {"individual_id": person["individual_id"],
                   "dataset_ref": ref_by_name.get(person["full_name"]),
                   "full_name": person["full_name"], "role": person["role"]}
        idc = identity_provider.verify(payload, dict(case))
        idc_id = db.next_id(conn, "identity_check")
        conn.execute(
            "INSERT INTO identity_check VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (idc_id, case_id, person["individual_id"], idc.provider_name, idc.document_result,
             idc.liveness_result, idc.biometric_result, idc.address_result, idc.name_dob_match,
             str(idc.document_expired).lower(), str(idc.duplicate_individual_detected).lower(),
             idc.result))

        rules_fired = []
        if not idc.available or idc.result == UNAVAILABLE:
            rules_fired.append("RG-09")
            add_finding("identity", "RG-09",
                        f"Identity provider did not answer for {person['full_name']}; "
                        f"insufficient evidence, not a pass", [idc_id], is_blocking=True)
        if idc.result in ("fail", "review"):
            rules_fired.append("ID-RESULT")
            add_finding("identity", "ID-RESULT",
                        f"Identity check for {person['full_name']} returned '{idc.result}'",
                        [idc_id], is_blocking=True)
        if idc.duplicate_individual_detected:
            rules_fired.append("ID-DUPLICATE")
            add_finding("identity", "ID-DUPLICATE",
                        f"{person['full_name']} appears to duplicate an individual already known",
                        [idc_id], is_blocking=True)
        if idc.document_expired:
            # Not an analyst question: the customer simply has to send a current ID.
            rules_fired.append("ID-EXPIRED")
            item = conn.execute(
                "SELECT i.item_id FROM checklist_item i JOIN requirement_pack p USING (pack_id)"
                " WHERE p.case_id = ? AND i.document_type = 'id_document'"
                " AND i.subject_individual_id = ?", (case_id, person["individual_id"])).fetchone()
            if item:
                conn.execute("UPDATE checklist_item SET status = 'resubmission_requested',"
                             " resubmission_attempts = resubmission_attempts + 1"
                             " WHERE item_id = ?", (item["item_id"],))
            msg = (f"The ID document for {person['full_name']} has expired; a current one is "
                   f"needed before the case can be decided")
            problems.append(msg)
            add_finding("identity", "ID-EXPIRED", msg, [idc_id], is_blocking=False)

        db.audit(conn, case_id, "external_provider", idc.provider_name, "identity_check_completed",
                 f"{idc_id} for {person['individual_id']} ({person['full_name']}) -> {idc.result}; "
                 f"mode={identity_provider.mode}; rules={','.join(rules_fired) or 'none fired'}",
                 identity_provider.version or kb.version)

    # ---- beneficial ownership ---------------------------------------------
    ubos = conn.execute(
        "SELECT u.*, i.full_name FROM ubo u JOIN individual i USING (individual_id)"
        " WHERE u.applicant_id = ? ORDER BY u.ubo_id", (case["applicant_id"],)).fetchall()
    declared_chains = {u["individual_ref"]: u.get("ownership_chain_percentages")
                       for u in application.get("ubos", [])}
    name_by_ref = {p["ref"]: p["full_name"] for p in application.get("individuals", [])}
    chain_by_name = {name_by_ref.get(ref): chain for ref, chain in declared_chains.items()}

    threshold = kb.ubo_threshold
    total_declared = 0.0
    for u in ubos:
        chain = chain_by_name.get(u["full_name"]) or [u["ownership_percentage"]]
        effective = effective_ownership([float(c) for c in chain])
        total_declared += effective

        if abs(effective - float(u["ownership_percentage"])) > 0.01:
            add_finding("ubo", "UB-06",
                        f"{u['full_name']}: declared {u['ownership_percentage']}% but the chain "
                        f"{chain} multiplies out to {effective}%", [u["ubo_id"]])

        # An owner counts as verified when the register corroborates the holding
        # and their identity check passed. ubo_supported_by_registry speaks to the
        # ownership CHAIN, so it governs indirect holdings; a direct shareholding
        # stands on the shareholder register instead. Case 4 shows the difference:
        # its indirect owner is unsupported and stays unverified, while its direct
        # 24.5% holder is unaffected.
        indirect = (u["control_type"] or "") == "indirect_shareholding"
        registry_supports = (not indirect
                             or (response.available
                                 and response.ubo_supported_by_registry is not False))
        id_result = conn.execute(
            "SELECT result FROM identity_check WHERE case_id = ? AND individual_id = ?",
            (case_id, u["individual_id"])).fetchone()
        identity_ok = id_result is not None and id_result["result"] == "pass"
        status = "verified" if (registry_supports and identity_ok) else "unverified"

        if effective >= threshold and status != "verified":
            add_finding("ubo", "UB-03",
                        f"{u['full_name']} holds {effective}% effective, at or above the "
                        f"{threshold}% threshold, and is not verified", [u["ubo_id"]])
        rule_id = "UB-03" if effective >= threshold else "UB-07"

        conn.execute("UPDATE ubo SET verification_status = ? WHERE ubo_id = ?",
                     (status, u["ubo_id"]))
        db.audit(conn, case_id, "system", ACTOR, "ubo_verified",
                 f"{u['ubo_id']} {u['full_name']}: chain {chain} -> {effective}% effective "
                 f"(threshold {threshold}%, {rule_id}); registry_supports={registry_supports}, "
                 f"identity_passed={identity_ok} -> {status}", kb.version)

    if response.available and response.ubo_supported_by_registry is False:
        add_finding("ubo", "UB-04",
                    "The register does not corroborate the declared beneficial ownership; "
                    "flagged for enhanced due diligence at the risk step", [check_id])

    if ubos and total_declared > 100.01:
        add_finding("ubo", "UB-06",
                    f"Declared effective ownership totals {round(total_declared, 2)}%, "
                    f"which is more than the whole", [u["ubo_id"] for u in ubos])

    # ---- routing ----------------------------------------------------------
    # Screening runs either way. A case held here still gets screened, so the
    # analyst opens one queue item with the registry, identity and screening
    # picture together rather than being asked the same question twice.
    next_step = "screening"
    holds.release_own(conn, case_id, ACTOR, "verification re-evaluated", kb)
    if blocking:
        summary = f"{len(blocking)} blocking outcome(s): " + "; ".join(blocking[:3])
        # A provider that did not answer is insufficient evidence. An identity
        # check that came back fail or review is a result, not an absence of one,
        # so it gets its own hold rather than being filed under "could not tell".
        code = ("insufficient_evidence" if any("RG-09" in b for b in blocking)
                else "eligibility" if registry_result == "fail"
                else "identity_failed" if any("ID-RESULT" in b or "ID-DUPLICATE" in b
                                              for b in blocking)
                else "manual_review")
        holds.place(conn, case_id, ACTOR, code,
                    f"{len(blocking)} verification outcome(s) need an analyst: "
                    + "; ".join(blocking[:2]), "analyst", kb)
    else:
        summary = (f"{len(findings)} non-blocking finding(s) carried forward"
                   if findings else "no findings")

    status, owner = holds.apply_status(conn, case_id, kb, "verification_in_progress", "system")
    db.audit(conn, case_id, "system", ACTOR, "verification_completed",
             f"registry {registry_result}, {len(subjects)} identity check(s), "
             f"{len(ubos)} beneficial owner(s); {summary}; case -> {status}", kb.version)

    return VerificationResult(case_id, registry_result, len(subjects), len(ubos),
                              findings, blocking, status, next_step, problems)
