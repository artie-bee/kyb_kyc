"""
Sample documents for the demo cases.

Generates files into sample_documents/<case_id>/ whose names match document.csv
exactly and whose printed values match extracted_field.csv, so a demo can open
the actual file behind any extracted value.

Everything here is invented. The companies, people and addresses do not exist.

Identity documents use a plain design of my own: a bordered card with a photo
box, a few labelled lines and a large SPECIMEN watermark. It deliberately
resembles no real government identity document - no emblem, no coat of arms, no
MRZ, no security pattern, nothing that would make a convincing forgery of
anything. The watermark is drawn across every ID at low opacity and cannot be
cropped out without removing the data with it.

Case 2's director ID is genuinely blurred - the image is downsampled and box
blurred - so the quality screen has something real to fail on rather than a
label saying "pretend this is blurry". The card is drawn with the director's
full details first and blurred afterwards, so the data is there on the page and
simply cannot be read. A blank card would be a different defect.

    python tools/make_sample_documents.py
    python tools/make_sample_documents.py --cases WAL-ONB-0001 --out sample_documents/
"""

import argparse
import csv
import sys
import zlib
from pathlib import Path

UC4 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UC4))

from tools.dataset_to_applications import DEFAULT_DATASET        # noqa: E402

DEFAULT_OUT = UC4 / "sample_documents"
# The demo cases: clean, blurred ID, stale address, vague ownership, clean-but-flagged.
DEMO_CASES = ("WAL-ONB-0001", "WAL-ONB-0002", "WAL-ONB-0003", "WAL-ONB-0004", "WAL-ONB-0006")

WATERMARK = "SPECIMEN - SYNTHETIC TEST DOCUMENT"
ID_TYPES = ("id_document",)

# Titles for the paper documents, by document_type.
TITLES = {
    "registry_extract": "COMPANY REGISTER EXTRACT",
    "certificate_of_incorporation": "CERTIFICATE OF INCORPORATION",
    "tax_registration_certificate": "TAX REGISTRATION CERTIFICATE",
    "director_register": "REGISTER OF DIRECTORS",
    "authorised_signatory_list": "LIST OF AUTHORISED SIGNATORIES",
    "ubo_declaration": "DECLARATION OF BENEFICIAL OWNERSHIP",
    "ownership_chart": "OWNERSHIP STRUCTURE CHART",
    "shareholder_register": "REGISTER OF SHAREHOLDERS",
    "proof_of_address": "UTILITY ACCOUNT STATEMENT",
    "source_of_funds_declaration": "SOURCE OF FUNDS DECLARATION",
    "source_of_wealth_statement": "SOURCE OF WEALTH STATEMENT",
    "business_activity_description": "STATEMENT OF BUSINESS ACTIVITY",
    "bank_statement": "BANK ACCOUNT STATEMENT",
    "programme_business_plan": "PROGRAMME BUSINESS PLAN",
    "website_or_platform_details": "PLATFORM DETAILS",
    "liveness_selfie": "LIVENESS CAPTURE",
}
FIELD_LABELS = {
    "company_name": "Company name", "legal_name": "Legal name",
    "registration_number": "Registration number", "registered_address": "Registered address",
    "entity_status": "Status on the register", "registered_shareholder": "Registered shareholder",
    "incorporation_date": "Date of incorporation", "director_name": "Director",
    "director_name_2": "Director", "appointment_date": "Appointed",
    "signatory_name": "Authorised signatory", "ubo_name": "Beneficial owner",
    "ubo_name_2": "Beneficial owner", "ownership_percentage": "Holding (per cent)",
    "control_basis": "Basis of control", "indirect_ownership_path": "Ownership chain",
    "intermediate_entity": "Intermediate entity", "chart_summary": "Structure summary",
    "shareholder_name": "Shareholder", "shareholder_name_2": "Shareholder",
    "full_name": "Full name", "date_of_birth": "Date of birth",
    "expiry_date": "Expiry date", "document_number": "Document number",
    "document_date": "Statement date", "address": "Service address", "issuer": "Issued by",
    "declared_source": "Declared source", "declared_source_of_wealth": "Declared source",
    "expected_monthly_volume": "Expected monthly volume",
    "declared_industry": "Declared industry", "expected_card_usage": "Expected card usage",
    "account_holder": "Account holder", "vat_number": "VAT number",
    "tax_number": "Tax number", "target_segment": "Target segment",
    "expected_card_volume": "Expected volume", "platform_url": "Platform",
}


def invented_id_details(person: dict, document: dict) -> list[dict]:
    """What an identity document shows when nothing was extracted from it.

    Only used for a document the pipeline could not read: the details belong on
    the card, and their absence from extracted_field.csv is the point - they
    were there and nobody could make them out. Derived from the individual so
    the card is consistent with the rest of the case, with a document number and
    expiry invented deterministically.
    """
    initials = "".join(part[0] for part in person["full_name"].split()[:2]).upper()
    # crc32, not hash(): Python randomises hash() per process, which would give
    # the same person a different document number on every run.
    serial = zlib.crc32(person["individual_id"].encode()) % 9000000 + 1000000
    expiry = document.get("expiry_date") or f"20{28 + serial % 5}-0{1 + serial % 9}-1{serial % 9}"
    return [
        {"name": "full_name", "value": person["full_name"]},
        {"name": "date_of_birth", "value": person["date_of_birth"]},
        {"name": "document_number", "value": f"{person['nationality']}-{initials}-{serial}"},
        {"name": "expiry_date", "value": expiry},
    ]


def save_pdf(img, path: Path, upload_time: str) -> None:
    """Write a PDF whose timestamps come from the dataset, not from the clock.

    Pillow stamps the current time into every PDF it writes, which would make
    the generator produce different bytes on every run. The document's own
    upload time is both stable and the honest answer to "when was this made".
    """
    import io
    import re as _re

    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, "PDF", resolution=150)
    stamp = "D:" + _re.sub(r"[^0-9]", "", upload_time or "20260101000000")[:14].ljust(14, "0") + "Z"
    data = _re.sub(rb"D:\d{14}Z?", stamp.encode(), buffer.getvalue())
    path.write_bytes(data)


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _font(size: int):
    from PIL import ImageFont
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def paper(document, fields, issuer_line: str):
    """A plain printed document: title, issuer line, then the labelled values."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1240, 1754), "white")
    d = ImageDraw.Draw(img)
    title = TITLES.get(document["document_type"], document["document_type"].replace("_", " ").upper())

    d.rectangle([60, 60, 1180, 1694], outline=(120, 120, 120), width=2)
    d.text((100, 110), title, font=_font(38), fill=(20, 20, 20))
    d.line([100, 170, 1140, 170], fill=(120, 120, 120), width=2)
    d.text((100, 195), issuer_line, font=_font(20), fill=(90, 90, 90))
    d.text((100, 225), f"Reference {document['document_id']} / {document['case_id']}",
           font=_font(18), fill=(140, 140, 140))

    y = 300
    for f in fields:
        label = FIELD_LABELS.get(f["name"], f["name"].replace("_", " ").capitalize())
        d.text((100, y), f"{label}:", font=_font(22), fill=(90, 90, 90))
        for line in _wrap(f["value"], 58):
            d.text((430, y), line, font=_font(24), fill=(20, 20, 20))
            y += 34
        y += 22
    if not fields:
        d.text((100, y), "No extracted values are recorded for this document.",
               font=_font(22), fill=(120, 120, 120))

    d.text((100, 1600), WATERMARK, font=_font(26), fill=(170, 170, 170))
    d.text((100, 1640), "Invented for testing. This organisation does not exist.",
           font=_font(18), fill=(170, 170, 170))
    return img


def identity_card(document, fields):
    """A generic specimen ID card. Deliberately resembles no real document."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1000, 640), (247, 247, 250))
    d = ImageDraw.Draw(img)

    d.rectangle([12, 12, 988, 628], outline=(70, 80, 110), width=6)
    d.rectangle([12, 12, 988, 110], fill=(70, 80, 110))
    d.text((40, 42), "SYNTHETIC IDENTITY CARD", font=_font(34), fill="white")
    d.text((700, 52), "SPECIMEN", font=_font(26), fill=(210, 215, 235))

    # photo box: a plain silhouette, not a face
    d.rectangle([48, 150, 288, 470], outline=(120, 125, 150), width=3, fill=(232, 234, 242))
    d.ellipse([128, 210, 208, 290], fill=(190, 195, 215))
    d.ellipse([98, 310, 238, 450], fill=(190, 195, 215))
    d.text((92, 486), "PHOTOGRAPH", font=_font(18), fill=(140, 145, 170))

    y = 160
    for f in fields:
        label = FIELD_LABELS.get(f["name"], f["name"].replace("_", " ").capitalize())
        d.text((330, y), label.upper(), font=_font(16), fill=(120, 125, 150))
        d.text((330, y + 22), str(f["value"]), font=_font(28), fill=(20, 20, 30))
        y += 78
    if document.get("expiry_date") and not any(f["name"] == "expiry_date" for f in fields):
        d.text((330, y), "EXPIRY DATE", font=_font(16), fill=(120, 125, 150))
        d.text((330, y + 22), document["expiry_date"], font=_font(28), fill=(20, 20, 30))

    # watermark across the whole card, drawn last so it cannot be cropped off
    mark = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(mark).text((60, 300), WATERMARK, font=_font(46), fill=(200, 60, 60, 90))
    img = Image.alpha_composite(img.convert("RGBA"), mark.rotate(12, resample=3)).convert("RGB")

    d = ImageDraw.Draw(img)
    d.text((48, 560), "Not a government document. Invented for testing.",
           font=_font(18), fill=(120, 125, 150))
    return img


def blur(img, factor: int = 14):
    """Genuinely unreadable: downsample hard, blur, and scale back up."""
    from PIL import ImageFilter
    small = img.resize((max(1, img.width // factor), max(1, img.height // factor)))
    return small.resize(img.size).filter(ImageFilter.GaussianBlur(radius=3))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, line = str(text).split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > width and line:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return lines or [""]


# ---------------------------------------------------------------------------

def build(dataset: Path, out: Path, cases: tuple) -> list[dict]:
    documents = read_csv(dataset / "document.csv")
    applicants = {r["applicant_id"]: r for r in read_csv(dataset / "applicant.csv")}
    case_rows = {r["case_id"]: r for r in read_csv(dataset / "onboarding_case.csv")}
    fields_by_doc = {}
    for r in read_csv(dataset / "extracted_field.csv"):
        fields_by_doc.setdefault(r["document_id"], []).append(r)

    people = {r["individual_id"]: r for r in read_csv(dataset / "individual.csv")}

    made = []
    for doc in documents:
        if doc["case_id"] not in cases:
            continue
        applicant = applicants[case_rows[doc["case_id"]]["applicant_id"]]
        fields = fields_by_doc.get(doc["document_id"], [])
        folder = out / doc["case_id"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / doc["file_name"]

        drawn_only = []
        if doc["document_type"] in ID_TYPES:
            shown = fields
            if not fields and doc["subject_individual_id"] in people:
                # Nothing was extracted because nothing could be read. Draw the
                # details anyway - they were on the card all along.
                drawn_only = invented_id_details(people[doc["subject_individual_id"]], doc)
                shown = drawn_only
            img = identity_card(doc, shown)
            # Case 2's director ID is the one the quality screen must reject.
            if doc["quality_flags"] == "blurred_unreadable":
                img = blur(img)
        else:
            issuer = (f"Issued for {applicant['legal_name']} "
                      f"({applicant['registration_number']}), {applicant['country']}")
            img = paper(doc, fields, issuer)

        if path.suffix.lower() == ".pdf":
            save_pdf(img, path, doc.get("upload_time", ""))
        else:
            img.convert("RGB").save(path, quality=92)
        made.append({"path": path, "document_id": doc["document_id"],
                     "case_id": doc["case_id"], "file_name": doc["file_name"],
                     "document_type": doc["document_type"],
                     "fields": len(fields),
                     # exactly what was drawn onto the page, for the test to check
                     "printed": [(f["name"], f["value"]) for f in fields],
                     # details drawn on the card that no extraction recorded,
                     # because the document was unreadable
                     "drawn_only": [(f["name"], f["value"]) for f in drawn_only],
                     "blurred": doc["quality_flags"] == "blurred_unreadable"})
    return made


# ---------------------------------------------------------------------------
# Scenario 2b - the customer fixes case 2 through the portal
# ---------------------------------------------------------------------------

# Who the synthetic ownership declaration names. Case 2 declares no beneficial
# owners of its own, so these are invented for the demo; DEMO_SCRIPT.md tells
# the analyst to type exactly these values.
SCENARIO_2B_OWNERS = (("Denton Halliwell", "60"), ("Priya Nankivell", "40"))


def build_scenario_2b(dataset: Path, out: Path) -> list[Path]:
    """The two files a presenter uploads in Scenario 2b: the same director ID
    that case 2's blurred scan shows, drawn clearly this time, and the missing
    ownership declaration. Both are synthetic and marked as specimens."""
    people = {r["full_name"]: r for r in read_csv(dataset / "individual.csv")}
    blurred = next(r for r in read_csv(dataset / "document.csv")
                   if r["case_id"] == "WAL-ONB-0002"
                   and r["quality_flags"] == "blurred_unreadable")
    director = people["Denton Halliwell"]
    folder = out / "scenario_2b"
    folder.mkdir(parents=True, exist_ok=True)

    card = {**blurred, "document_id": "SCENARIO-2B", "expiry_date": ""}
    id_path = folder / "halliwell_id_clear.jpg"
    identity_card(card, invented_id_details(director, blurred)).convert("RGB").save(
        id_path, quality=92)

    declaration = {"document_type": "ubo_declaration", "document_id": "SCENARIO-2B",
                   "case_id": "WAL-ONB-0002"}
    fields = [{"name": "ubo_name", "value": SCENARIO_2B_OWNERS[0][0]},
              {"name": "ownership_percentage", "value": SCENARIO_2B_OWNERS[0][1]},
              {"name": "ubo_name_2", "value": SCENARIO_2B_OWNERS[1][0]},
              {"name": "control_basis", "value": "Direct shareholding"}]
    ubo_path = folder / "northbridge_ownership_declaration.pdf"
    save_pdf(paper(declaration, fields, "Declared by the directors of Northbridge Craft "
                                        "Supplies Ltd (UK-99010288), GB"),
             ubo_path, "2026-09-28T09:00:00Z")
    return [id_path, ubo_path]


# ---------------------------------------------------------------------------
# Scenario 8 - the demo upload pack for a brand-new portal application
# ---------------------------------------------------------------------------
#
# One fictional Estonian private limited company whose only director owns all
# of it: the shortest checklist the KB gives a company. FORM_VALUES is what the
# presenter types into the portal's application form; PACK is one file for
# every item that checklist asks for, plus one deliberately blurred ID. The
# manifest records each file's SHA-256 with the verdict and fields printed on
# it, which mock mode replays (orchestrator/demo_samples.py).

DEMO_PACK_DIR = "demo_pack"
PACK_COMPANY = "Lumen Harbour OU"
PACK_REG = "EE-16550321"
PACK_ADDRESS = "Narva mnt 7, 10117 Tallinn"
PACK_PERSON = "Kristiina Vaher"
PACK_DOB = "1988-04-14"

# (form step, label on the form, form field key, value to type or choose)
FORM_VALUES = [
    ("Your business", "Legal name of the business", "legal_name", PACK_COMPANY),
    ("Your business", "Trading name, if different", "trading_name", ""),
    ("Your business", "Legal form", "entity_type", "private_limited_company"),
    ("Your business", "Registration number", "registration_number", PACK_REG),
    ("Your business", "Country it is registered in", "country", "EE"),
    ("Your business", "Registered address", "registered_address", PACK_ADDRESS),
    ("Your business", "What the business does", "business_activity",
     "Online sales of handmade ceramics"),
    ("Your business", "Expected card spend per month", "monthly_spend", "8000"),
    ("Your business", "Currency", "currency", "EUR"),
    ("Contact", "Contact person's full name", "contact_name", PACK_PERSON),
    ("Contact", "Email address", "contact_email", "kristiina@lumen-harbour.example"),
    ("People", "Director 1 - Full name", "d1_name", PACK_PERSON),
    ("People", "Director 1 - Date of birth", "d1_dob", PACK_DOB),
    ("People", "Director 1 - Nationality", "d1_nationality", "EE"),
    ("People", "Director 1 - Country of residence", "d1_residence", "EE"),
    ("Owners", "Owner 1 - Full name", "o1_name", PACK_PERSON),
    ("Owners", "Owner 1 - How do they own it?", "o1_held", "direct"),
    ("Owners", "Owner 1 - If directly: percentage", "o1_pct", "100"),
    ("A few facts", "Does a company own any part of the business?", "fact_company_owner", "no"),
    ("A few facts", "Is any part held through a trust, or by a nominee?", "fact_trust", "no"),
    ("A few facts", "Is the business registered for VAT?", "fact_vat", "no"),
    ("A few facts", "Is the business registered with a national trade or business register?",
     "fact_trade_register", "yes"),
    ("A few facts", "Will you issue cards to your own customers, under your own brand?",
     "fact_partner", "no"),
    ("Check and send", "Every detail here is made up for the demo", "confirm_demo", "yes"),
]
# How a choice appears on screen, for FORM_VALUES.md.
_SHOWN = {"private_limited_company": "Private limited company (Ltd, OÜ)", "EE": "Estonia",
          "direct": "Directly, in their own name", "yes": "Yes", "no": "No"}


def _pack(today) -> list[dict]:
    """Every file in the pack: which checklist item it answers, and what it says.
    Dates that the age rules read are set from `today`, so a pack generated on
    the demo day is never too old; everything else is fixed."""
    from datetime import timedelta
    recent = (today - timedelta(days=12)).isoformat()
    card = [("full_name", PACK_PERSON), ("date_of_birth", PACK_DOB),
            ("document_number", "EE-KV-4470183"), ("expiry_date", "2031-05-31")]
    return [
        {"file": "01_certificate_of_incorporation.pdf", "type": "certificate_of_incorporation",
         "fields": [("company_name", PACK_COMPANY), ("registration_number", PACK_REG),
                    ("incorporation_date", "2021-03-15"), ("registered_address", PACK_ADDRESS)]},
        {"file": "02_company_registration_extract.pdf", "type": "registry_extract",
         "fields": [("company_name", PACK_COMPANY), ("legal_name", PACK_COMPANY),
                    ("registration_number", PACK_REG), ("registered_address", PACK_ADDRESS),
                    ("entity_status", "active")]},
        {"file": "03_tax_registration_certificate.pdf", "type": "tax_registration_certificate",
         "fields": [("tax_number", "EE-T-16550321")]},
        {"file": "04_register_of_directors.pdf", "type": "director_register",
         "fields": [("director_name", PACK_PERSON), ("appointment_date", "2021-03-15")]},
        {"file": "05_authorised_signatories.pdf", "type": "authorised_signatory_list",
         "fields": [("signatory_name", PACK_PERSON)]},
        {"file": "06_beneficial_owners_declaration.pdf", "type": "ubo_declaration",
         "fields": [("ubo_name", PACK_PERSON), ("ownership_percentage", "100"),
                    ("control_basis", "Direct shareholding")]},
        {"file": "07_identity_document_BLURRED.jpg", "type": "id_document", "person": PACK_PERSON,
         "fields": [], "drawn": card, "flags": ["blurred_unreadable"]},
        {"file": "08_identity_document_clear.jpg", "type": "id_document", "person": PACK_PERSON,
         "fields": card, "expiry_date": "2031-05-31"},
        {"file": "09_proof_of_address.pdf", "type": "proof_of_address", "person": PACK_PERSON,
         "fields": [("document_date", recent), ("full_name", PACK_PERSON),
                    ("address", "Pärnu mnt 22-5, 10141 Tallinn"),
                    ("issuer", "Harbour Utilities AS (fictional)")],
         "document_date": recent},
        {"file": "10_selfie.jpg", "type": "liveness_selfie", "person": PACK_PERSON, "fields": []},
        {"file": "11_source_of_funds_declaration.pdf", "type": "source_of_funds_declaration",
         "fields": [("declared_source", "Revenue from online sales of ceramics"),
                    ("expected_monthly_volume", "8000 EUR")]},
        {"file": "12_business_activity_statement.pdf", "type": "business_activity_description",
         "fields": [("declared_industry", "Retail - handmade ceramics"),
                    ("expected_card_usage", "Supplier payments and online advertising")]},
    ]


def _selfie(person: str):
    """A plain placeholder for a liveness capture: a silhouette, not a face."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (720, 900), (236, 238, 244))
    d = ImageDraw.Draw(img)
    d.ellipse([260, 170, 460, 370], fill=(190, 195, 215))
    d.ellipse([180, 400, 540, 820], fill=(190, 195, 215))
    d.text((40, 40), "LIVENESS CAPTURE - SPECIMEN", font=_font(30), fill=(70, 80, 110))
    d.text((40, 90), person, font=_font(26), fill=(40, 40, 50))
    d.text((40, 850), WATERMARK, font=_font(22), fill=(200, 60, 60))
    return img


def build_demo_pack(out: Path, today=None) -> dict:
    """Write the pack, its manifest and FORM_VALUES.md into out/demo_pack/."""
    import hashlib
    import json
    from app.customer_view import DOCUMENT_LABELS
    from orchestrator import clock, demo_samples

    today = today or clock.current().today()
    folder = out / DEMO_PACK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        if old.is_file():
            old.unlink()
    stamp = f"{today.isoformat()}T09:00:00Z"
    files = {}
    for n, item in enumerate(_pack(today), 1):
        document = {"document_type": item["type"], "document_id": f"PACK-{n:02d}",
                    "case_id": "demo pack", "expiry_date": item.get("expiry_date", "")}
        fields = [{"name": k, "value": v} for k, v in item["fields"]]
        path = folder / item["file"]
        if item["type"] in ID_TYPES:
            img = identity_card(document, fields or [{"name": k, "value": v}
                                                    for k, v in item.get("drawn", [])])
            if "blurred_unreadable" in item.get("flags", []):
                img = blur(img)
        elif item["type"] == "liveness_selfie":
            img = _selfie(item["person"])
        else:
            img = paper(document, fields, f"Issued for {PACK_COMPANY} ({PACK_REG}), EE")
        if path.suffix == ".pdf":
            save_pdf(img, path, stamp)
        else:
            img.convert("RGB").save(path, quality=92)
        files[hashlib.sha256(path.read_bytes()).hexdigest()] = {
            "file": item["file"], "document_type": item["type"],
            "person": item.get("person"),
            "quality_flags": item.get("flags", []),
            "expiry_date": item.get("expiry_date"),
            "document_date": item.get("document_date"),
            "fields": [{"name": k, "value": v, "confidence": 0.98, "source_page": 1}
                       for k, v in item["fields"]],
        }
    (folder / "manifest.json").write_text(json.dumps(
        {"label": demo_samples.LABEL, "generated_for": today.isoformat(),
         "company": PACK_COMPANY,
         # what the simulated register holds for the pack's company, for the
         # form's "Look up my company"
         "register": {"number": PACK_REG, "name": PACK_COMPANY, "address": PACK_ADDRESS},
         "files": files}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")

    lines = [f"# Demo upload pack - {PACK_COMPANY}", "",
             "Everything here is fictional and marked SPECIMEN. Generated for "
             f"{today.isoformat()} by `python tools/make_sample_documents.py`.", "",
             "## 1. Type these into the portal's application form", "",
             "Portal: http://127.0.0.1:8701/demo, then **Start a new demo application**. "
             "Leave every row not listed here blank (Director 2-4, Owner 2-4).", "",
             "| Step | Field | Type or choose | Form key |", "|---|---|---|---|"]
    for step, label, key, value in FORM_VALUES:
        lines.append(f"| {step} | {label} | {_SHOWN.get(value, value) or '*(leave blank)*'} "
                     f"| `{key}` |")
    lines += ["", "## 2. Upload these files on the Documents needed page", "",
              "Upload the **blurred** ID first to show the refusal, then the clear one.", "",
              "| File | Checklist item on the portal |", "|---|---|"]
    for item in _pack(today):
        label = DOCUMENT_LABELS[item["type"]] + (f" - {item['person']}" if item.get("person")
                                                 else "")
        note = " *(upload first: it is refused)*" if item.get("flags") else ""
        lines.append(f"| `{item['file']}` | {label}{note} |")
    lines += ["", "In mock mode these files are recognised by their contents and their "
              "verdicts and fields are replayed, labelled \"mock: recognised demo sample "
              "file\". Any other file goes to the ordinary visual check and typed fields.", ""]
    (folder / "FORM_VALUES.md").write_text("\n".join(lines), encoding="utf-8")
    return {"folder": folder, "files": files}


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the demo document files.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cases", nargs="*", default=list(DEMO_CASES))
    args = ap.parse_args()

    try:
        import PIL  # noqa: F401
    except ImportError:
        raise SystemExit("Pillow is needed to draw the documents: pip install pillow")

    made = build(args.dataset, args.out, tuple(args.cases))
    by_case = {}
    for m in made:
        by_case.setdefault(m["case_id"], []).append(m)
    for case_id, items in sorted(by_case.items()):
        blurred = sum(1 for i in items if i["blurred"])
        unreadable = sum(len(i["drawn_only"]) for i in items)
        print(f"{case_id}  {len(items):>2} files, {sum(i['fields'] for i in items):>2} printed "
              f"values" + (f", {blurred} deliberately blurred carrying {unreadable} "
                           f"unreadable value(s)" if blurred else ""))
    print(f"\n{len(made)} file(s) in {args.out}")
    for path in build_scenario_2b(args.dataset, args.out):
        print(f"Scenario 2b upload: {path}")
    pack = build_demo_pack(args.out)
    print(f"Scenario 8 demo pack: {len(pack['files'])} files in {pack['folder']} "
          f"(see FORM_VALUES.md)")


if __name__ == "__main__":
    main()
