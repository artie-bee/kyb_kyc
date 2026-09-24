"""
STEP 6 - Screening: sanctions, PEP and adverse media  (brief Section 5.7)

Runs once the Step 5 gate has passed, and runs even when verification left a
blocking finding: an analyst opening the case should see the registry, identity
and screening picture together rather than being asked the same question twice.

Subjects: the applicant entity, plus every director, every declared beneficial
owner and every authorised signatory. Missing one is the failure mode that
matters here, so the count is asserted in the tests.

Rules live in kb/screening_rules.csv and kb/adverse_media_categories.csv.
Adverse-media relevance - is this article about this person, and how serious -
is the AI part and sits behind orchestrator/media_relevance.py.

Four things this step will not do:

  1. No code path turns a sanctions possible_match or clear_match into
     no_match. The value the provider gave is written once and the database
     refuses to update it; only a human decision at Step 8 resolves one.
  2. A provider that did not answer is not a pass. SC-05 to SC-07 retry once
     and then record insufficient evidence.
  3. Any human_required outcome sets requires_human_signoff on the case, which
     blocks automatic approval.
  4. Screening findings are internal. They go to the finding table and the
     audit trail, never to any field that reaches the customer.
"""

from dataclasses import dataclass, field

from .. import db
from .. import holds
from ..kb import KnowledgeBase
from ..media_relevance import MediaRelevanceAssessor, MockMediaRelevance
from ..providers import MockScreeningProvider, ScreeningProvider, UNAVAILABLE

ACTOR = "step.screening"
SUBJECT_ROLES = ("director", "ubo", "authorised_signatory", "sole_trader")
MAX_ATTEMPTS = 2          # one call, one retry, then insufficient evidence

# Outcomes that mean a person must look at this before the case moves.
COMPLIANCE_OUTCOMES = ("escalate_compliance",)
ANALYST_OUTCOMES = ("analyst_review", "insufficient_evidence")


@dataclass
class ScreeningResult:
    case_id: str
    subjects_screened: int
    findings: list[dict] = field(default_factory=list)
    restricted_finding: bool = False
    requires_human_signoff: bool = False
    status: str = ""
    next_step: str | None = None
    problems: list[str] = field(default_factory=list)


def subjects_for(conn, case_id: str, application: dict) -> list[dict]:
    """The entity plus every person who has to be screened."""
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()
    ref_by_name = {p["full_name"]: p["ref"] for p in application.get("individuals", [])}

    out = [{"subject_type": "applicant", "applicant_id": case["applicant_id"],
            "individual_id": None, "dataset_ref": "", "full_name": None}]
    for person in conn.execute(
            "SELECT * FROM individual WHERE applicant_id = ? AND role IN "
            f"({','.join('?' * len(SUBJECT_ROLES))}) ORDER BY individual_id",
            (case["applicant_id"], *SUBJECT_ROLES)):
        out.append({"subject_type": "individual", "applicant_id": None,
                    "individual_id": person["individual_id"],
                    "dataset_ref": ref_by_name.get(person["full_name"], ""),
                    "full_name": person["full_name"], "role": person["role"]})
    return out


def run(conn, case_id: str, application: dict, kb: KnowledgeBase,
        screening_provider: ScreeningProvider | None = None,
        media_assessor: MediaRelevanceAssessor | None = None) -> ScreeningResult:
    provider = screening_provider or MockScreeningProvider()
    assessor = media_assessor or MockMediaRelevance()
    case = conn.execute("SELECT * FROM onboarding_case WHERE case_id = ?", (case_id,)).fetchone()

    findings, problems = [], []
    outcomes, human_required, restricted = [], False, False

    def add_finding(rule_id, summary, evidence, blocking):
        fid = db.next_id(conn, "finding")
        conn.execute("INSERT INTO finding VALUES (?,?,?,?,?,?,?,?)",
                     (fid, case_id, "screening", rule_id, summary, "|".join(evidence),
                      int(blocking), db.now()))
        record = {"finding_id": fid, "source": "screening", "rule_id": rule_id,
                  "summary": summary, "evidence_refs": evidence, "blocking": blocking}
        findings.append(record)
        return record

    subjects = subjects_for(conn, case_id, application)
    for subject in subjects:
        # SC-05..SC-07: one call, one retry if anything came back unanswered.
        attempts, response = 0, None
        while attempts < MAX_ATTEMPTS:
            attempts += 1
            response = provider.screen(subject, dict(case))
            unanswered = (not response.available
                          or UNAVAILABLE in (response.sanctions_result, response.pep_result))
            db.audit(conn, case_id, "external_provider", response.provider_name or "screening",
                     "screening_check_attempted",
                     f"attempt {attempts} of {MAX_ATTEMPTS} for "
                     f"{subject['full_name'] or 'the applicant entity'}; mode={provider.mode}; "
                     f"{'no answer' if unanswered else 'answered'}",
                     provider.version or kb.version)
            if not unanswered:
                break

        who = subject["full_name"] or "the applicant entity"
        media = assessor.assess(subject, response).validate()
        rules_fired = []

        check_id = db.next_id(conn, "screening_check")
        conn.execute(
            "INSERT INTO screening_check (check_id, case_id, subject_type, applicant_id,"
            " individual_id, provider_name, sanctions_result, pep_result, adverse_media_result,"
            " severity, evidence_refs, attempts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (check_id, case_id, subject["subject_type"], subject["applicant_id"],
             subject["individual_id"], response.provider_name or provider.name,
             response.sanctions_result, response.pep_result, response.adverse_media_result,
             response.severity, "|".join(response.evidence_refs), attempts))

        # ---- sanctions and PEP, straight from the provider's list result ----
        for check, value in (("sanctions", response.sanctions_result),
                             ("pep", response.pep_result)):
            rule = kb.screening_rule(check, value)
            if rule is None:
                continue
            rules_fired.append(rule["rule_id"])
            if rule["outcome"] == "pass":
                continue
            outcomes.append(rule["outcome"])
            if rule["human_required"].lower() == "true":
                human_required = True
            if value not in (UNAVAILABLE,):
                restricted = True
            add_finding(rule["rule_id"],
                        f"{check} screening on {who} returned '{value}': {rule['description']}",
                        [check_id, *response.evidence_refs],
                        blocking=rule["outcome"] in COMPLIANCE_OUTCOMES + ANALYST_OUTCOMES)

        # ---- adverse media, via the relevance assessment ---------------------
        category = kb.media_category(media.category)
        if category and category["outcome"] != "record_only":
            rules_fired.append(f"AM-{media.category}")
            outcomes.append(category["outcome"])
            if media.category != UNAVAILABLE:
                restricted = True
            if category["outcome"] in COMPLIANCE_OUTCOMES + ANALYST_OUTCOMES:
                human_required = True
            add_finding(f"AM-{media.category}",
                        f"adverse media on {who} assessed as '{media.category}' "
                        f"(relevant={media.relevant}, confidence {media.confidence:.2f}): "
                        f"{category['description']}",
                        [check_id, *media.evidence_refs],
                        blocking=category["outcome"] in COMPLIANCE_OUTCOMES)
        elif media.category in ("none", "low_relevance"):
            rules_fired.append(f"AM-{media.category}")

        db.audit(conn, case_id, "external_provider", response.provider_name or provider.name,
                 "screening_check_completed",
                 f"{check_id} for {who} ({subject['subject_type']}) -> "
                 f"sanctions {response.sanctions_result}, pep {response.pep_result}, "
                 f"media {response.adverse_media_result}, severity {response.severity}; "
                 f"mode={provider.mode}; media_assessor={assessor.mode}; "
                 f"rules={','.join(rules_fired) or 'none fired'}",
                 provider.version or kb.version)

    # ---- routing ----------------------------------------------------------
    # Ordered worst first: compliance escalation beats an analyst stop, which
    # beats enhanced due diligence, which still continues to the risk step.
    holds.release_own(conn, case_id, ACTOR, "screening re-evaluated", kb)

    if "escalate_compliance" in outcomes:
        summary = "escalated to compliance"
        holds.place(conn, case_id, ACTOR, "sanctions_escalation",
                    "a screening finding needs a compliance decision before the case moves",
                    "compliance", kb)
    elif "analyst_review" in outcomes:
        summary = "held for an analyst"
        holds.place(conn, case_id, ACTOR, "manual_review",
                    "a possible sanctions match needs an analyst; no automated step may "
                    "clear it", "analyst", kb)
    elif "insufficient_evidence" in outcomes:
        summary = "a provider did not answer; insufficient evidence"
        problems.append("A screening provider returned no result after a retry. "
                        "That is not a pass and the case cannot proceed on it.")
        holds.place(conn, case_id, ACTOR, "insufficient_evidence",
                    "a screening provider returned no result after a retry", "analyst", kb)
    else:
        summary = (f"{len(findings)} finding(s) carried to the risk step"
                   if findings else "no findings")

    # A clean screening result does not release a case another step is holding:
    # apply_status reads every open hold, not just this step's.
    status, owner = holds.apply_status(conn, case_id, kb, "verification_in_progress", "system")
    # The risk step runs either way: an analyst picking the case up needs the
    # band and the pack in front of them, not just a list of holds.
    next_step = "risk_assessment"

    db.update_case(conn, case_id, restricted_finding=int(restricted),
                   requires_human_signoff=int(human_required or case["requires_human_signoff"]))
    db.audit(conn, case_id, "system", ACTOR, "screening_completed",
             f"{len(subjects)} subject(s) screened; {len(findings)} finding(s); {summary}; "
             f"restricted_finding={restricted}; requires_human_signoff={human_required}; "
             f"case -> {status}", kb.version)

    return ScreeningResult(case_id, len(subjects), findings, restricted, human_required,
                           status, next_step, problems)
