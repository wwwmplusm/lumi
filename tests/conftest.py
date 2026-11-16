import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.project import Project  # noqa: E402

PAGES = {
    "W1": ["Journal of Tests 12 (2024)\nTitle page.\n101",
           "102\nExport controls reduced GPU access for Chinese la-\nboratories after October 2022 (Smith et al., 2021).\nMore text here.",
           "103\nChinese labs shifted toward efficient training methods such as quantization, which may reduce compute needs.\nEnd."],
    "W2": ["Abstract.\n1", "After the 2022 export controls, Chinese AI laboratories had less access to GPUs.\n2",
           "International co-authorship between US and China declined by 12% between 2019 and 2023.\n3"],
}


def work(wid, surname, year, title, doi=None, first=None, last=None):
    return {"id": f"https://openalex.org/{wid}", "display_name": title, "publication_year": year, "doi": doi,
            "authorships": [{"author": {"display_name": f"Ann {surname}"}}, {"author": {"display_name": "Bo Li"}}, {"author": {"display_name": "Cy Wu"}}],
            "biblio": {"first_page": first, "last_page": last}, "abstract_inverted_index": {"Short": [0], "abstract.": [1]},
            "referenced_works": ["https://openalex.org/W99", "https://openalex.org/W98"]}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "literature").mkdir(parents=True)
    (root / "lumi.json").write_text(json.dumps({"name": "Test", "context": "export controls"}))
    (root / "research").mkdir()
    (root / "research" / "questions.md").write_text("- H1: Starvation | keywords: GPU access, export controls\n- H2: Efficiency | keywords: quantization\n")
    metas = {"W1": work("W1", "Gao", 2024, "Export controls and Chinese AI labs: evidence", "https://doi.org/10.1/x", 101, 103),
             "W2": work("W2", "Gao", 2024, "Collaboration under sanctions", None)}
    for wid, pages in PAGES.items():
        folder = root / "literature" / wid
        folder.mkdir()
        (folder / "pages.json").write_text(json.dumps({"pages": pages, "pages_layout": pages}))
        key = "Gao2024" if wid == "W1" else "Gao2024b"
        meta = {"work": metas[wid], "key": key, "fulltext": "yes", "questions": []}
        if wid == "W1":
            meta.update(page_labels=["101", "102", "103"], page_source="header")
        else:
            meta.update(page_labels=None, page_source="none")
        (folder / "metadata.json").write_text(json.dumps(meta))
    return Project(root)
