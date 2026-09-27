"""
The demo document files must say what the dataset says they say.

A demo where the file on screen disagrees with the extracted value behind it is
worse than no demo, so this checks every generated file against its
extracted_field rows, and checks that the one document that is supposed to be
unreadable actually is.

Run: python -m pytest tests -q
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.dataset_to_applications import DEFAULT_DATASET            # noqa: E402
from tools import make_sample_documents as maker                     # noqa: E402

pytest.importorskip("PIL", reason="Pillow is needed to draw the documents")


def _csv(name):
    with open(DEFAULT_DATASET / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    out = tmp_path_factory.mktemp("sample_documents")
    return maker.build(DEFAULT_DATASET, out, maker.DEMO_CASES), out


def test_the_blurred_id_carries_real_details_under_the_blur(made):
    """The data is on the card. It simply cannot be read - which is why the
    dataset records no extracted fields for it."""
    records, _ = made
    blurred = next(r for r in records if r["blurred"])
    assert blurred["printed"] == [],         "nothing was extracted from this document, and nothing should claim to be"

    drawn = dict(blurred["drawn_only"])
    assert drawn, "a blank card is a different defect from an unreadable one"
    assert drawn["full_name"] == "Denton Halliwell"
    assert drawn["date_of_birth"] == "1978-06-02"
    assert drawn["document_number"].startswith("GB-DH-")
    assert len(drawn["expiry_date"]) == 10 and drawn["expiry_date"].startswith("20")

    # every other ID prints what was extracted from it and invents nothing
    for record in records:
        if record["document_type"] == "id_document" and not record["blurred"]:
            assert record["drawn_only"] == []


def test_the_generator_is_deterministic(tmp_path):
    """Two runs, identical bytes. Document numbers come from crc32 rather than
    hash(), which Python randomises per process, and PDF timestamps come from
    the dataset rather than the clock."""
    first = maker.build(DEFAULT_DATASET, tmp_path / "a", ("WAL-ONB-0002",))
    second = maker.build(DEFAULT_DATASET, tmp_path / "b", ("WAL-ONB-0002",))
    assert len(first) == len(second) == 10

    for a, b in zip(first, second):
        assert a["file_name"] == b["file_name"]
        assert a["path"].read_bytes() == b["path"].read_bytes(),             f"{a['file_name']} differs between runs"
        assert a["drawn_only"] == b["drawn_only"]


def test_a_file_exists_for_every_document_on_the_demo_cases(made):
    records, _ = made
    expected = [d for d in _csv("document.csv") if d["case_id"] in maker.DEMO_CASES]
    assert len(records) == len(expected) == 61

    names = {(r["case_id"], r["file_name"]) for r in records}
    for d in expected:
        assert (d["case_id"], d["file_name"]) in names, \
            f"no file generated for {d['document_id']} ({d['file_name']})"
        path = next(r["path"] for r in records
                    if r["case_id"] == d["case_id"] and r["file_name"] == d["file_name"])
        assert path.name == d["file_name"], "the file name must match document.csv exactly"
        assert path.exists() and path.stat().st_size > 1000, f"{path.name} is empty or tiny"


def test_every_printed_value_matches_its_extracted_field_rows(made):
    records, _ = made
    fields = {}
    for r in _csv("extracted_field.csv"):
        fields.setdefault(r["document_id"], []).append((r["name"], r["value"]))

    checked = 0
    for record in records:
        expected = sorted(fields.get(record["document_id"], []))
        printed = sorted(record["printed"])
        assert printed == expected, (
            f"{record['file_name']} prints {printed} but the dataset records {expected}")
        checked += len(expected)
    assert checked == 108, f"expected 108 printed values across the demo cases, got {checked}"


def test_the_blurred_id_is_genuinely_unreadable(made):
    """Case 2's director ID must fail a quality screen on the pixels, not a label."""
    from PIL import Image, ImageFilter, ImageStat

    records, _ = made
    blurred = [r for r in records if r["blurred"]]
    assert len(blurred) == 1, "exactly one demo document should be blurred"
    assert blurred[0]["file_name"] == "director_id_halliwell_scan.jpg"

    def edge_energy(path):
        img = Image.open(path).convert("L").filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(img).stddev[0]

    clean = next(r["path"] for r in records
                 if r["document_type"] == "id_document" and not r["blurred"])
    assert edge_energy(blurred[0]["path"]) < edge_energy(clean) / 3, (
        "the blurred ID still has too much edge detail to count as unreadable")


def test_identity_documents_carry_the_specimen_watermark(made):
    """Every ID is marked as synthetic, and the mark is drawn over the card."""
    from PIL import Image

    records, _ = made
    ids = [r for r in records if r["document_type"] == "id_document"]
    assert ids, "no identity documents were generated"
    for record in ids:
        img = Image.open(record["path"]).convert("RGB")
        # The watermark is the only red ink on an otherwise blue-grey card. The
        # blurred ID keeps it but with less colour separation, which is what
        # blurring does to everything on the card, the mark included.
        margin = 8 if record["blurred"] else 25
        reds = sum(1 for px in img.getdata() if px[0] > px[2] + margin)
        assert reds > 2000, f"{record['file_name']} has no visible specimen watermark"
    assert maker.WATERMARK == "SPECIMEN - SYNTHETIC TEST DOCUMENT"


def test_the_generated_files_only_cover_the_demo_cases(made):
    records, _ = made
    assert {r["case_id"] for r in records} == set(maker.DEMO_CASES)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
