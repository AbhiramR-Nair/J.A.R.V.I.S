"""Pydantic model for structured paper summaries produced by the summarizer.

Passed directly to Gemini as response_schema — field descriptions are read by
the model when generating JSON, so they are prompt text, not just developer docs.
"""

from pydantic import BaseModel, Field


class PaperSummary(BaseModel):
    """Structured summary of a single research paper.

    Fields are ordered so the LLM produces high-signal content first.
    TTS reads key_claims + relevance_to_user; the rest renders in the chat panel.
    """

    title: str = Field(
        description="The paper's title as best identified from the text."
    )
    key_claims: list[str] = Field(
        description=(
            "3-7 bullet points stating the paper's main findings or arguments. "
            "Each claim is a complete sentence. Include specific numbers, gene "
            "names, drug names, or method names where present."
        )
    )
    methods: str = Field(
        description="2-4 sentences on the methodology. What did they do?"
    )
    results: str = Field(
        description="2-4 sentences on the findings. What did they observe?"
    )
    limitations: str = Field(
        description=(
            "2-3 sentences on caveats, scope, or weaknesses the authors "
            "or readers should note."
        )
    )
    relevance_to_user: str = Field(
        description=(
            "1-2 sentences connecting the paper to the user's active research "
            "project. If no clear connection, say so plainly."
        )
    )
