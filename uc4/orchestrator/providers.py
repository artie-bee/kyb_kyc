"""
External verification providers  (brief Sections 5.5 and 5.6)

Same shape as quality_checker.py and extractor.py, with one difference that
matters: these calls cost money. Nothing here is called until Step 5's gate has
confirmed the checklist is complete, and every call is audited with the provider
name and the mode it ran in.

    MockRegistryProvider / MockIdentityProvider   replay the scripted dataset
    LiveRegistryProvider / LiveIdentityProvider   STUBS - see below

A registry provider returns what the register HOLDS - the legal name, number,
address and directors - not a verdict. Step 5 computes the match results itself
by comparing those values against what was extracted from the documents,
including anything an analyst corrected. That way the comparison is inspectable,
and a corrected value is demonstrably the one that was used.

Anything a provider could not answer comes back as unavailable. Unavailable is
never a pass: RG-09 makes it insufficient evidence, retried once and then sent
to an analyst.
"""

from dataclasses import dataclass, field

UNAVAILABLE = "unavailable"


@dataclass
class RegistryResponse:
    """What the register holds. `available` false means the call did not answer."""

    available: bool = True
    provider_name: str = ""
    company_status: str = UNAVAILABLE
    legal_name: str | None = None
    number: str | None = None
    address: str | None = None
    directors: list[str] = field(default_factory=list)
    ubo_supported_by_registry: bool | None = None
    high_risk_jurisdiction_or_industry: bool | None = None
    confidence: float | None = None


@dataclass
class IdentityResponse:
    available: bool = True
    provider_name: str = ""
    individual_id: str = ""
    document_result: str = UNAVAILABLE
    liveness_result: str = UNAVAILABLE
    biometric_result: str = UNAVAILABLE
    address_result: str = UNAVAILABLE
    name_dob_match: str = UNAVAILABLE
    document_expired: bool = False
    duplicate_individual_detected: bool = False
    result: str = UNAVAILABLE


@dataclass
class ScreeningResponse:
    """Sanctions, PEP and adverse-media results for one subject.

    A result the provider could not give comes back as UNAVAILABLE. Nothing in
    the pipeline may turn an unavailable into a clear result, and nothing may
    turn a sanctions match into a no_match: only a human decision resolves one.
    """

    available: bool = True
    provider_name: str = ""
    subject_type: str = "individual"
    individual_id: str | None = None
    applicant_id: str | None = None
    sanctions_result: str = UNAVAILABLE
    pep_result: str = UNAVAILABLE
    adverse_media_result: str = UNAVAILABLE
    severity: str = "none"
    evidence_refs: list[str] = field(default_factory=list)


class ScreeningProvider:
    mode = "base"
    name = "base"
    version: str | None = None

    def screen(self, subject: dict, case: dict) -> ScreeningResponse:
        raise NotImplementedError


class MockScreeningProvider(ScreeningProvider):
    """Replays the screening row scripted for each subject."""

    mode = "mock"
    name = "ScreenMock Global"

    def __init__(self, scripted: dict | None = None):
        self.scripted = scripted or {}

    def screen(self, subject: dict, case: dict) -> ScreeningResponse:
        key = (case["case_id"], subject.get("dataset_ref") or "")
        row = self.scripted.get(key)
        if row is None:
            return ScreeningResponse(available=False, provider_name=self.name,
                                     subject_type=subject["subject_type"],
                                     individual_id=subject.get("individual_id"),
                                     applicant_id=subject.get("applicant_id"))
        return ScreeningResponse(
            available=True, provider_name=self.name,
            subject_type=subject["subject_type"],
            individual_id=subject.get("individual_id"),
            applicant_id=subject.get("applicant_id"),
            sanctions_result=row["sanctions_result"], pep_result=row["pep_result"],
            adverse_media_result=row["adverse_media_result"], severity=row["severity"],
            evidence_refs=[e for e in (row.get("evidence_refs") or "").split("|") if e])


class LiveScreeningProvider(ScreeningProvider):
    """Call a real sanctions, PEP and adverse-media provider.

    TODO: not wired up. No API call is made yet - calling screen() raises.

    When implemented it must:
      - be called once per subject after the Step 5 gate, never speculatively;
      - return UNAVAILABLE rather than an optimistic no_match on any non-answer,
        because SC-05 to SC-07 turn silence into insufficient evidence and a
        false no_match would clear a case that was never actually screened;
      - never be re-run to try for a cleaner answer on a subject that already
        returned a match - that is shopping, and the match stands until a human
        resolves it;
      - carry the provider's own reference for every finding into evidence_refs,
        so an analyst can look the hit up rather than take our word for it;
      - set `version` to the provider's list version and date for the audit row.
    """

    mode = "live"
    name = "TODO-screening"

    def screen(self, subject: dict, case: dict) -> ScreeningResponse:
        raise NotImplementedError(
            "LiveScreeningProvider is a stub: no API call is wired up yet. "
            "Run with the mock provider (the default) until it is.")


class RegistryProvider:
    mode = "base"
    name = "base"
    version: str | None = None

    def lookup(self, applicant: dict, case: dict) -> RegistryResponse:
        raise NotImplementedError


class IdentityProvider:
    mode = "base"
    name = "base"
    version: str | None = None

    def verify(self, individual: dict, case: dict) -> IdentityResponse:
        raise NotImplementedError


class MockRegistryProvider(RegistryProvider):
    """Replays the registry row scripted for this case.

    The scripted row carries the raw values the register holds, so Step 5 still
    computes the match results rather than being handed them.
    """

    mode = "mock"
    name = "MockRegistryHub"

    def __init__(self, scripted: dict | None = None):
        self.scripted = scripted or {}

    def lookup(self, applicant: dict, case: dict) -> RegistryResponse:
        row = self.scripted.get(case["case_id"])
        if row is None:
            return RegistryResponse(available=False, provider_name=self.name)
        self.name = row.get("provider_name") or self.name
        return RegistryResponse(
            available=True,
            provider_name=self.name,
            company_status=row["company_status"],
            legal_name=row.get("registry_legal_name") or None,
            number=row.get("registry_number") or None,
            address=row.get("registry_address") or None,
            directors=[d for d in (row.get("registry_directors") or "").split("|") if d],
            ubo_supported_by_registry=(row.get("ubo_supported_by_registry", "").lower() == "true"),
            high_risk_jurisdiction_or_industry=(
                row.get("high_risk_jurisdiction_or_industry", "").lower() == "true"),
            confidence=float(row["confidence"]) if row.get("confidence") else None)


class MockIdentityProvider(IdentityProvider):
    """Replays the identity row scripted for each individual."""

    mode = "mock"
    name = "VerifyMock ID"

    def __init__(self, scripted: dict | None = None):
        self.scripted = scripted or {}

    def verify(self, individual: dict, case: dict) -> IdentityResponse:
        row = self.scripted.get((case["case_id"], individual["dataset_ref"]))
        if row is None:
            return IdentityResponse(available=False, provider_name=self.name,
                                    individual_id=individual["individual_id"])
        return IdentityResponse(
            available=True,
            provider_name=row.get("provider_name") or self.name,
            individual_id=individual["individual_id"],
            document_result=row["document_result"], liveness_result=row["liveness_result"],
            biometric_result=row["biometric_result"], address_result=row["address_result"],
            name_dob_match=row["name_dob_match"],
            document_expired=row["document_expired"].lower() == "true",
            duplicate_individual_detected=row["duplicate_individual_detected"].lower() == "true",
            result=row["result"])


class LiveRegistryProvider(RegistryProvider):
    """Call a real company register.

    TODO: not wired up. No API call is made yet - calling lookup() raises.

    When implemented it must:
      - return what the register HOLDS, never a match verdict: Step 5 owns the
        comparison so that it stays inspectable and testable;
      - set available=False on a timeout, quota error or any non-answer rather
        than returning an optimistic default - RG-09 turns that into
        insufficient evidence, not a pass;
      - be called at most twice per case (the RG-09 retry) so a flapping
        provider cannot be billed in a loop;
      - set `version` to the provider's API version for the audit row.
    """

    mode = "live"
    name = "TODO-registry"

    def lookup(self, applicant: dict, case: dict) -> RegistryResponse:
        raise NotImplementedError(
            "LiveRegistryProvider is a stub: no API call is wired up yet. "
            "Run with the mock provider (the default) until it is.")


class LiveIdentityProvider(IdentityProvider):
    """Call a real identity-verification provider.

    TODO: not wired up. No API call is made yet - calling verify() raises.

    When implemented it must:
      - be called once per individual, only after Step 5's checklist gate has
        passed, and only for individuals whose ID document was accepted;
      - set available=False rather than inventing a result on a non-answer;
      - never be retried automatically on a 'fail' or 'review' verdict - those
        are answers, and re-running them until one passes is shopping;
      - set `version` to the provider's API version for the audit row.
    """

    mode = "live"
    name = "TODO-identity"

    def verify(self, individual: dict, case: dict) -> IdentityResponse:
        raise NotImplementedError(
            "LiveIdentityProvider is a stub: no API call is wired up yet. "
            "Run with the mock provider (the default) until it is.")


REGISTRY_PROVIDERS = {"mock": MockRegistryProvider, "live": LiveRegistryProvider}
SCREENING_PROVIDERS = {"mock": MockScreeningProvider, "live": LiveScreeningProvider}
IDENTITY_PROVIDERS = {"mock": MockIdentityProvider, "live": LiveIdentityProvider}


def get_providers(mode: str = "mock", application: dict | None = None):
    """Build the pair of providers for a run. Default is mock; no API by accident."""
    if mode not in REGISTRY_PROVIDERS:
        raise ValueError(f"unknown provider mode '{mode}'; choose from {sorted(REGISTRY_PROVIDERS)}")
    application = application or {}
    if mode == "mock":
        registry = {r["case_id"]: r for r in application.get("scripted_registry", [])}
        identity = {(r["case_id"], r["individual_id"]): r
                    for r in application.get("scripted_identity", [])}
        return MockRegistryProvider(registry), MockIdentityProvider(identity)
    return REGISTRY_PROVIDERS[mode](), IDENTITY_PROVIDERS[mode]()


def get_screening_provider(mode: str = "mock", application: dict | None = None):
    """Screening provider for a run. Default is mock; no API by accident."""
    if mode not in SCREENING_PROVIDERS:
        raise ValueError(f"unknown screening mode '{mode}'; "
                         f"choose from {sorted(SCREENING_PROVIDERS)}")
    if mode != "mock":
        return SCREENING_PROVIDERS[mode]()
    rows = (application or {}).get("scripted_screening", [])
    scripted = {(r["case_id"], r["individual_id"] or ""): r for r in rows}
    return MockScreeningProvider(scripted)
