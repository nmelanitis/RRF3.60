"""Extract RRF project funding identifiers from official decisions."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from typing import Final

from pypdf import PdfReader


OPS_CODE: Final = re.compile(
    r"(?:ΟΠΣ(?:\s+ΤΑ)?|MIS\s*\(ΟΠΣ\))\s*[:\-]?\s*(\d{6,10})",
    re.IGNORECASE,
)
PDE_CODE: Final = re.compile(r"20\d{2}\s*[ΤT]\s*[ΑA]\s*\d{8}", re.IGNORECASE)


@dataclass(frozen=True)
class RrfProjectFundingReference:
    """A Public Investment Programme code attributed to one RRF project."""

    ops_code: str
    pde_code: str


def _unique_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    return list(dict.fromkeys(pattern.findall(text)))


def _normalise_pde_code(value: str) -> str:
    compact = re.sub(r"\s+", "", value).upper()
    return compact.replace("T", "Τ").replace("A", "Α")


def extract_rrf_project_funding_references(
    pdf_bytes: bytes,
) -> list[RrfProjectFundingReference]:
    """Extract unambiguous OPS-to-PDE references from a decision PDF.

    Documents concerning several OPS projects are left unresolved rather than
    assigning a funding code to the wrong project.
    """
    reader = PdfReader(BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    ops_codes = _unique_matches(OPS_CODE, text)
    if len(ops_codes) != 1:
        return []
    pde_codes = [
        _normalise_pde_code(value) for value in _unique_matches(PDE_CODE, text)
    ]
    return [
        RrfProjectFundingReference(ops_code=ops_codes[0], pde_code=pde_code)
        for pde_code in pde_codes
    ]
