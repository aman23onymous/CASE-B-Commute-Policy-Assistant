import hashlib
import re
from dataclasses import dataclass
from typing import List


@dataclass
class ParsedSection:
    section_id: str
    section_title: str
    content: str


@dataclass
class ParsedPolicy:
    document_title: str
    sections: List[ParsedSection]
    raw_text: str
    sha256: str


class PolicyParseError(ValueError):
    """Raised when policy document syntax or section structure is invalid."""
    pass


def parse_policy_document(text: str, fallback_title: str = "Corporate Transport Policy") -> ParsedPolicy:
    """
    Parse a Markdown or structured plain-text policy document.
    Spec §8 requirement:
    - Top-level title `# <title>`
    - Section headings `## <section ID>. <title>`
    - Stable explicit section identifiers
    - Strict validation: non-empty body, unique section IDs, no unstructured documents.
    """
    if not text or not text.strip():
        raise PolicyParseError("Policy document content cannot be empty.")

    clean_text = text.replace("\r\n", "\n").strip()
    sha256 = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

    lines = clean_text.split("\n")
    doc_title = fallback_title

    # Detect top-level title: e.g., # Aurora Employee Transport Policy
    title_match = re.search(r"^#\s+(.+)$", clean_text, re.MULTILINE)
    if title_match:
        doc_title = title_match.group(1).strip()

    # Pattern for section headings: e.g., "## 2. Night Shift Drops" or "## Section 2: Night Shift Drops"
    section_pattern = re.compile(r"^##\s+(?:Section\s+)?([0-9A-Za-z_-]+)[\.\:]?\s+(.+)$")

    sections: List[ParsedSection] = []
    current_sec_id: str = ""
    current_sec_title: str = ""
    current_body_lines: List[str] = []

    for line in lines:
        match = section_pattern.match(line.strip())
        if match:
            # Commit previous section if exists
            if current_sec_id:
                body = "\n".join(current_body_lines).strip()
                if not body:
                    raise PolicyParseError(
                        f"Section §{current_sec_id} '{current_sec_title}' has empty content."
                    )
                sections.append(
                    ParsedSection(
                        section_id=current_sec_id,
                        section_title=current_sec_title,
                        content=body,
                    )
                )
                current_body_lines = []

            current_sec_id = match.group(1).strip()
            current_sec_title = match.group(2).strip()
        else:
            # If line is inside a section, accumulate
            if current_sec_id:
                current_body_lines.append(line)

    # Commit last section
    if current_sec_id:
        body = "\n".join(current_body_lines).strip()
        if not body:
            raise PolicyParseError(
                f"Section §{current_sec_id} '{current_sec_title}' has empty content."
            )
        sections.append(
            ParsedSection(
                section_id=current_sec_id,
                section_title=current_sec_title,
                content=body,
            )
        )

    if not sections:
        raise PolicyParseError(
            "Document lacks valid structured sections. Expected headings matching '## <section_id>. <title>'."
        )

    # Validate section uniqueness
    seen_ids = set()
    for s in sections:
        if s.section_id in seen_ids:
            raise PolicyParseError(f"Duplicate section ID detected: '§{s.section_id}'.")
        seen_ids.add(s.section_id)

    return ParsedPolicy(
        document_title=doc_title,
        sections=sections,
        raw_text=clean_text,
        sha256=sha256,
    )
