"""
Public interface for system prompts.

JARVIS_SYSTEM_PROMPT is assembled at import time from the six markdown files
in prompts/system/. To edit JARVIS's persona, edit those files — not this one.
"""
from pathlib import Path

from .loader import load_system_prompt


# Module-level assembly: pays the I/O cost once at import time.
# Restart the backend after editing any prompts/system/*.md file.
JARVIS_SYSTEM_PROMPT: str = load_system_prompt(
    Path(__file__).parent / "system"
)
