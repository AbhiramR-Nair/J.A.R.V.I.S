"""
Loader for modular system prompts.

Discovers every .md file in a given directory, sorts them by filename, and
concatenates their contents with blank-line separators. The numeric prefix
on filenames (00_, 10_, 20_, ...) controls the order of concatenation, so
the order is visible from the directory listing without reading this file.

Missing or empty files are skipped with a warning rather than crashing the
backend — a typo in a filename should not brick the assistant.
"""
from pathlib import Path

from loguru import logger


# Why sorted(): Path.glob does not guarantee order. We rely on lexicographic
# sort of the numeric-prefixed filenames to define concatenation order.
def load_system_prompt(directory: Path) -> str:
    if not directory.is_dir():
        logger.warning(f"system-prompt directory missing: {directory}")
        return ""

    parts: list[str] = []
    for md_path in sorted(directory.glob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            logger.warning(f"could not read {md_path.name}: {e}")
            continue
        if not text:
            logger.debug(f"skipping empty prompt file: {md_path.name}")
            continue
        parts.append(text)

    if not parts:
        logger.warning(f"no prompt files loaded from {directory}")
        return ""

    return "\n\n".join(parts)
