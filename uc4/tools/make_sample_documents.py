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
label saying "pretend this is blurry".

    python tools/make_sample_documents.py
    python tools/make_sample_documents.py --cases WAL-ONB-0001 --out sample_documents/
"""

import argparse
import csv
import sys
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

    made = []
    for doc in documents:
        if doc["case_id"] not in cases:
            continue
        applicant = applicants[case_rows[doc["case_id"]]["applicant_id"]]
        fields = fields_by_doc.get(doc["document_id"], [])
        folder = out / doc["case_id"]
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / doc["file_name"]

        if doc["document_type"] in ID_TYPES:
            img = identity_card(doc, fields)
            # Case 2's director ID is the one the quality screen must reject.
            if doc["quality_flags"] == "blurred_unreadable":
                img = blur(img)
        else:
            issuer = (f"Issued for {applicant['legal_name']} "
                      f"({applicant['registration_number']}), {applicant['country']}")
            img = paper(doc, fields, issuer)

        if path.suffix.lower() == ".pdf":
            img.convert("RGB").save(path, "PDF", resolution=150)
        else:
            img.convert("RGB").save(path, quality=92)
        made.append({"path": path, "document_id": doc["document_id"],
                     "case_id": doc["case_id"], "file_name": doc["file_name"],
                     "document_type": doc["document_type"],
                     "fields": len(fields),
                     # exactly what was drawn onto the page, for the test to check
                     "printed": [(f["name"], f["value"]) for f in fields],
                     "blurred": doc["quality_flags"] == "blurred_unreadable"})
    return made


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
        print(f"{case_id}  {len(items):>2} files, {sum(i['fields'] for i in items):>2} printed "
              f"values" + (f", {blurred} deliberately blurred" if blurred else ""))
    print(f"\n{len(made)} file(s) in {args.out}")


if __name__ == "__main__":
    main()
