"""ChromaDB wrapper for project-scoped semantic memory.

Uses Gemini embeddings (gemini-embedding-001) to vectorise text.
All public functions are async because the embedding call is a network round-trip.
ChromaDB operations are sync but fast (local disk reads/writes).

Isolation guarantee: each project gets its own named collection ("project_<id>").
Cross-project leakage is architecturally impossible — search() only queries the
calling project's collection. Do not collapse projects into a single collection
with metadata filtering; separate collections are simpler and safer.
"""

import os
import uuid
from datetime import datetime, timezone

import chromadb
from google import genai
from google.genai import types
from loguru import logger

from backend.config.settings import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

# chromadb.PersistentClient is a factory function in ChromaDB 1.x, not a class.
# Use the underlying API type for annotation, or Any to avoid the TypeError.
_chroma_client: chromadb.api.ClientAPI | None = None
_genai_client: genai.Client | None = None


def _get_client() -> chromadb.api.ClientAPI:
    """Return the singleton ChromaDB persistent client, creating it on first call.

    Uses an absolute path so the data directory resolves correctly regardless
    of which directory uvicorn was started from.
    """
    global _chroma_client
    if _chroma_client is None:
        chroma_path = os.path.abspath(settings.chroma_persist_dir)
        _chroma_client = chromadb.PersistentClient(path=chroma_path)
        logger.info(f"vector_store: ChromaDB initialised at {chroma_path}")
    return _chroma_client


def _get_genai_client() -> genai.Client:
    """Return the singleton Gemini client used for embeddings."""
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client


def _get_collection(project_id: int) -> chromadb.Collection:
    """Return (or create) the ChromaDB collection for this project.

    hnsw:space=cosine: angular distance is the right metric for sentence
    embeddings — it measures semantic direction, not vector magnitude.
    The default (L2/Euclidean) gives noticeably worse retrieval quality here.
    Note: metadata is only applied at creation time; ignored if collection exists.
    """
    return _get_client().get_or_create_collection(
        name=f"project_{project_id}",
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------

async def _embed(text: str, task_type: str) -> list[float]:
    """Call Gemini embedding API and return the raw float vector.

    task_type matters for retrieval quality:
      "RETRIEVAL_DOCUMENT" — when storing a text (optimised for being found)
      "RETRIEVAL_QUERY"    — when searching (optimised for finding documents)
    Using the wrong type degrades recall — they are not interchangeable.
    """
    client = _get_genai_client()
    result = await client.aio.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return result.embeddings[0].values


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def add(
    text: str,
    project_id: int,
    metadata: dict | None = None,
) -> str:
    """Embed text and insert it into the project's ChromaDB collection.

    Returns the document UUID. Callers should store this as chroma_id in SQLite
    so the two stores stay in sync. Raises on failure — caller decides whether
    to swallow (memory storage is non-fatal) or surface (Task 1 completion check).
    """
    doc_id = str(uuid.uuid4())
    embedding = await _embed(text, task_type="RETRIEVAL_DOCUMENT")

    # Always include project_id and timestamp; callers can pass extra fields
    # (e.g. importance score, role) to make future filtering easier.
    base_meta: dict = {
        "project_id": project_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        base_meta.update(metadata)

    collection = _get_collection(project_id)
    collection.add(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[base_meta],
    )
    logger.debug(f"vector_store.add: doc {doc_id} stored in project_{project_id}")
    return doc_id


async def search(
    query: str,
    project_id: int,
    k: int = 5,
) -> list[str]:
    """Return up to k semantically relevant texts from the project's collection.

    Returns an empty list if the collection has no documents — does not raise.
    ChromaDB raises if n_results > document count, so we clamp k to count first.
    """
    collection = _get_collection(project_id)
    count = collection.count()
    if count == 0:
        return []

    k = min(k, count)
    embedding = await _embed(query, task_type="RETRIEVAL_QUERY")

    results = collection.query(
        query_embeddings=[embedding],
        n_results=k,
    )
    # results["documents"] is a list-of-lists (one inner list per query sent).
    # We always send exactly one query, so we take index [0].
    docs: list[str] = results["documents"][0] if results["documents"] else []
    logger.debug(f"vector_store.search: {len(docs)} results for project_{project_id}")
    return docs
