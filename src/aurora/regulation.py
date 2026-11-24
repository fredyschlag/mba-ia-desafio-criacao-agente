"""Chapter-by-chapter access to the internal regulation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from aurora.config import DATA_DIR

CHAPTER_HEADING = re.compile(r"^## Capítulo [IVXLCDM]+: (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    text: str


@lru_cache(maxsize=1)
def load_chapters() -> tuple[Chapter, ...]:
    content = (DATA_DIR / "regulamento.md").read_text(encoding="utf-8")
    headings = list(CHAPTER_HEADING.finditer(content))
    chapters: list[Chapter] = []
    for number, heading in enumerate(headings, start=1):
        end = headings[number].start() if number < len(headings) else len(content)
        chapters.append(
            Chapter(number, heading.group(1).strip(), content[heading.start():end].strip())
        )
    return tuple(chapters)


def chapter_index() -> str:
    """Builds the list of chapter titles, without any article text."""
    return "\n".join(f"{chapter.number}. {chapter.title}" for chapter in load_chapters())


def ler_capitulo(numero: int) -> dict[str, Any]:
    """Reads a single chapter of the condominium regulation.

    Args:
        numero: Chapter number from the index.

    Returns:
        The full text of that chapter only.
    """
    chapters = load_chapters()
    if isinstance(numero, bool) or not isinstance(numero, int):
        return {"status": "capitulo_inexistente", "capitulos": f"1 a {len(chapters)}"}
    if not 1 <= numero <= len(chapters):
        return {"status": "capitulo_inexistente", "capitulos": f"1 a {len(chapters)}"}
    return {"status": "ok", "capitulo": chapters[numero - 1].text}
