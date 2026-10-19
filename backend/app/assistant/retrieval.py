"""Quote-only retrieval over a tenant's own pages.

No generation: a passage is returned only if it contains enough of the question's meaningful words,
and the answer is that passage with its source. Otherwise the answer is "not found".
"""

from dataclasses import dataclass

from app.assistant.privacy import content_terms, looks_like_injection, tokenize

MAX_CHUNK_CHARS = 600
MAX_CHUNKS_PER_PAGE = 400
MIN_COVERAGE = 0.6
MAX_PASSAGES = 3
SNIPPET_CHARS = 500


@dataclass(frozen=True)
class Chunk:
    text: str
    tokens: list[str]
    blocked: bool


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    page_id: str
    text: str
    tokens: list[str]
    title: str
    url: str | None


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    page_id: str
    text: str
    title: str
    url: str | None
    coverage: float


def chunk_text(content: str) -> list[Chunk]:
    """Splits on blank lines, then on sentence ends, keeping chunks under MAX_CHUNK_CHARS.

    A short line with no sentence punctuation is a heading: it is kept with the paragraph that
    follows it, so the heading and its answer are one passage.
    """
    chunks: list[Chunk] = []
    heading = ""
    for block in (b.strip() for b in content.replace("\r", "").split("\n\n")):
        if not block:
            continue
        if len(block) < 60 and "\n" not in block and not block.endswith((".", "?", "!", "।", ":")):
            heading = f"{heading} {block}".strip() if heading else block
            continue
        prefix, heading = (f"{heading}: " if heading else ""), ""
        pieces = [prefix + block] if len(prefix + block) <= MAX_CHUNK_CHARS else _split_long(block)
        for piece in pieces:
            piece = " ".join(piece.split())
            if len(piece) < 12:
                continue
            chunks.append(Chunk(piece, tokenize(piece), looks_like_injection(piece)))
            if len(chunks) >= MAX_CHUNKS_PER_PAGE:
                return chunks
    if heading and len(heading) >= 12:
        chunks.append(Chunk(heading, tokenize(heading), looks_like_injection(heading)))
    return chunks


def _split_long(block: str) -> list[str]:
    pieces: list[str] = []
    current = ""
    for sentence in block.replace("।", ".").replace("? ", "?. ").split(". "):
        if len(current) + len(sentence) + 2 > MAX_CHUNK_CHARS and current:
            pieces.append(current)
            current = ""
        while len(sentence) > MAX_CHUNK_CHARS:
            pieces.append(sentence[:MAX_CHUNK_CHARS])
            sentence = sentence[MAX_CHUNK_CHARS:]
        current = f"{current} {sentence}." if current else f"{sentence}."
    if current:
        pieces.append(current)
    return pieces


def rank(question: str, candidates: list[Candidate]) -> list[Passage]:
    """Returns the best passages, or an empty list when nothing covers the question well enough."""
    terms = content_terms(question)
    if not terms:
        return []
    needed = 1.0 if len(terms) == 1 else MIN_COVERAGE
    scored: list[tuple[float, float, Candidate]] = []
    for cand in candidates:
        if looks_like_injection(cand.text):
            continue  # also checked at index time; this guards content stored before a rule existed
        present = set(cand.tokens)
        hits = sum(1 for t in terms if t in present)
        coverage = hits / len(terms)
        if coverage >= needed:
            density = hits / max(len(cand.tokens), 1)
            scored.append((coverage, density, cand))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    passages: list[Passage] = []
    seen: set[str] = set()
    for coverage, _density, cand in scored:
        if cand.text in seen:
            continue
        seen.add(cand.text)
        passages.append(
            Passage(
                cand.chunk_id,
                cand.page_id,
                cand.text[:SNIPPET_CHARS],
                cand.title,
                cand.url,
                round(coverage, 2),
            )
        )
        if len(passages) == MAX_PASSAGES:
            break
    return passages
