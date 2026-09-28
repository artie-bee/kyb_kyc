"""
The demo scenario for a case made in the portal's demo application form.

An analyst chooses, in the console, what the simulated providers will say when
the case reaches Steps 5 and 6: clean, an address mismatch, a PEP match or a
possible sanctions match (providers.DEMO_SCENARIOS). The choice is a console
action only - the portal has no route, form or word for it - because it decides
what a case will be found to contain, which is never the customer's to see.

No table holds it. The choice is an audit event, and the current scenario is
read back from the audit trail, so the record of who chose what is the only
copy there is and cannot disagree with it.

It can be changed only while the case is still collecting documents. Once the
providers have answered, the answer stands; changing the scenario afterwards
would rewrite what a check found.
"""

from . import db
from .providers import DEMO_SCENARIOS
from .steps.document_quality import document_stage_open

ACTOR = "console.demo_scenario"
ACTION = "demo_scenario_selected"
DEFAULT = "clean"


class ScenarioRefused(ValueError):
    """The scenario cannot be set on this case, or not now."""


def current(conn, case_id: str) -> str:
    """The scenario the simulated providers will use: the last one chosen."""
    row = conn.execute(
        "SELECT payload_summary FROM audit_event WHERE case_id = ? AND action = ?"
        " ORDER BY event_id DESC LIMIT 1", (case_id, ACTION)).fetchone()
    if row is None:
        return DEFAULT
    chosen = row["payload_summary"].split(";", 1)[0].removeprefix("scenario=").strip()
    return chosen if chosen in DEMO_SCENARIOS else DEFAULT


def choose(conn, case_id: str, scenario: str, analyst_id: str, kb=None) -> str:
    if not db.is_demo_case(case_id):
        raise ScenarioRefused(f"{case_id} is not a demo case; its checks replay the dataset")
    if scenario not in DEMO_SCENARIOS:
        raise ScenarioRefused(f"unknown scenario {scenario!r}; choose from {sorted(DEMO_SCENARIOS)}")
    if not (analyst_id or "").strip():
        raise ScenarioRefused("the analyst choosing the scenario must be identified")
    if not document_stage_open(conn, case_id):
        raise ScenarioRefused(
            f"the providers have already answered on {case_id}; the scenario is fixed once "
            f"they have, or it would rewrite what a check found")
    db.audit(conn, case_id, "analyst", analyst_id, ACTION,
             f"scenario={scenario}; {DEMO_SCENARIOS[scenario]}; applies to the simulated "
             f"provider responses when Steps 5 and 6 run", getattr(kb, "version", None))
    return scenario
