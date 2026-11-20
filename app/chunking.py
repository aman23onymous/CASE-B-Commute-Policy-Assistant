import hashlib
from dataclasses import dataclass
from typing import Dict, List, Any
from app.policy_parser import ParsedPolicy, ParsedSection


@dataclass
class PolicyChunk:
    chunk_id: str
    company_id: str
    policy_version_id: str
    version: int
    document_title: str
    section_id: str
    section_title: str
    chunk_index: int
    source_sha256: str
    text: str
    excerpt: str

    def to_metadata(self) -> Dict[str, Any]:
        """Return scalar metadata for Chroma."""
        return {
            "company_id": str(self.company_id),
            "policy_version_id": str(self.policy_version_id),
            "version": int(self.version),
            "document_title": str(self.document_title),
            "section_id": str(self.section_id),
            "section_title": str(self.section_title),
            "chunk_index": int(self.chunk_index),
            "source_sha256": str(self.source_sha256),
            "excerpt": str(self.excerpt),
        }


def generate_chunk_id(
    version_id: str, section_id: str, chunk_index: int, text: str
) -> str:
    """Generate deterministic chunk ID based on version, section, index and text hash."""
    seed = f"{version_id}:{section_id}:{chunk_index}:{text}"
    return f"chk_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def chunk_section(
    section: ParsedSection,
    company_id: str,
    policy_version_id: str,
    version: int,
    document_title: str,
    source_sha256: str,
    target_size: int = 700,
    overlap: int = 80,
) -> List[PolicyChunk]:
    """
    Deterministically chunk a single section.
    Never crosses section boundaries. Short sections stay intact.
    """
    content = section.content.strip()
    chunks: List[PolicyChunk] = []

    if len(content) <= target_size:
        # Single intact chunk for short section
        chunk_text = f"[{document_title} - Section {section.section_id}: {section.section_title}]\n{content}"
        excerpt = content[:200] + ("..." if len(content) > 200 else "")
        chk_id = generate_chunk_id(policy_version_id, section.section_id, 0, content)
        chunks.append(
            PolicyChunk(
                chunk_id=chk_id,
                company_id=company_id,
                policy_version_id=policy_version_id,
                version=version,
                document_title=document_title,
                section_id=section.section_id,
                section_title=section.section_title,
                chunk_index=0,
                source_sha256=source_sha256,
                text=chunk_text,
                excerpt=excerpt,
            )
        )
        return chunks

    # Split into sliding windows within this section
    paragraphs = content.split("\n\n")
    current_text = ""
    chunk_index = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current_text) + len(para) + 2 <= target_size:
            current_text = f"{current_text}\n\n{para}".strip()
        else:
            if current_text:
                chunk_text = f"[{document_title} - Section {section.section_id}: {section.section_title}]\n{current_text}"
                excerpt = current_text[:200] + ("..." if len(current_text) > 200 else "")
                chk_id = generate_chunk_id(
                    policy_version_id, section.section_id, chunk_index, current_text
                )
                chunks.append(
                    PolicyChunk(
                        chunk_id=chk_id,
                        company_id=company_id,
                        policy_version_id=policy_version_id,
                        version=version,
                        document_title=document_title,
                        section_id=section.section_id,
                        section_title=section.section_title,
                        chunk_index=chunk_index,
                        source_sha256=source_sha256,
                        text=chunk_text,
                        excerpt=excerpt,
                    )
                )
                chunk_index += 1

                # Retain overlap from end of current_text
                overlap_text = current_text[-overlap:] if len(current_text) > overlap else ""
                current_text = f"{overlap_text}\n\n{para}".strip()
            else:
                # Paragraph itself exceeds target size; hard slice
                start = 0
                while start < len(para):
                    slice_text = para[start : start + target_size]
                    chunk_text = f"[{document_title} - Section {section.section_id}: {section.section_title}]\n{slice_text}"
                    excerpt = slice_text[:200] + ("..." if len(slice_text) > 200 else "")
                    chk_id = generate_chunk_id(
                        policy_version_id, section.section_id, chunk_index, slice_text
                    )
                    chunks.append(
                        PolicyChunk(
                            chunk_id=chk_id,
                            company_id=company_id,
                            policy_version_id=policy_version_id,
                            version=version,
                            document_title=document_title,
                            section_id=section.section_id,
                            section_title=section.section_title,
                            chunk_index=chunk_index,
                            source_sha256=source_sha256,
                            text=chunk_text,
                            excerpt=excerpt,
                        )
                    )
                    chunk_index += 1
                    start += target_size - overlap
                current_text = ""

    if current_text:
        chunk_text = f"[{document_title} - Section {section.section_id}: {section.section_title}]\n{current_text}"
        excerpt = current_text[:200] + ("..." if len(current_text) > 200 else "")
        chk_id = generate_chunk_id(
            policy_version_id, section.section_id, chunk_index, current_text
        )
        chunks.append(
            PolicyChunk(
                chunk_id=chk_id,
                company_id=company_id,
                policy_version_id=policy_version_id,
                version=version,
                document_title=document_title,
                section_id=section.section_id,
                section_title=section.section_title,
                chunk_index=chunk_index,
                source_sha256=source_sha256,
                text=chunk_text,
                excerpt=excerpt,
            )
        )

    return chunks


def chunk_policy(
    parsed: ParsedPolicy,
    company_id: str,
    policy_version_id: str,
    version: int,
) -> List[PolicyChunk]:
    """Chunk all sections of a parsed policy."""
    all_chunks: List[PolicyChunk] = []
    for section in parsed.sections:
        sec_chunks = chunk_section(
            section=section,
            company_id=company_id,
            policy_version_id=policy_version_id,
            version=version,
            document_title=parsed.document_title,
            source_sha256=parsed.sha256,
        )
        all_chunks.extend(sec_chunks)
    return all_chunks
