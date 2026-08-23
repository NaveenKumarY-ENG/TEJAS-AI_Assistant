"""
Semantic memory: ChromaDB-backed vector store.
Lets the assistant recall past conversations by *meaning*, not exact keywords —
e.g. "what did I say about my trip" finds it even if the word "trip" wasn't used.
"""
import uuid
from datetime import datetime

import chromadb

from config import config

_client = chromadb.PersistentClient(path=config.vector_store_path)
_collection = _client.get_or_create_collection(name="conversation_memory")


def get_client() -> chromadb.ClientAPI:
    """Shared PersistentClient accessor — memory/knowledge.py reuses this
    same instance for its own collection rather than opening a second
    client at the same path, which risks SQLite file-locking contention."""
    return _client


def remember(text: str, metadata: dict | None = None) -> None:
    """Store a piece of text (a conversation turn, a fact, anything) for later semantic recall."""
    meta = {"timestamp": datetime.utcnow().isoformat()}
    if metadata:
        meta.update(metadata)
    _collection.add(documents=[text], metadatas=[meta], ids=[str(uuid.uuid4())])


# ChromaDB L2 distance cutoff, calibrated empirically against real
# conversation history in this collection: genuinely relevant past turns
# land under ~0.65, unrelated ones start around ~1.4+ — a tighter range than
# memory/knowledge.py's document chunks, since short conversational snippets
# ("User: ...\nAssistant: ...") share more generic structural language with
# each other, compressing distances. Without this, query() always returns
# its n_results nearest neighbors regardless of relevance, meaning an
# unrelated question could pull an unrelated past exchange into context.
_MAX_RELEVANT_DISTANCE = 1.3


def recall(query: str, n_results: int = 3) -> list[str]:
    """Retrieve the most semantically relevant stored memories for a query,
    excluding anything too far from the query to actually be relevant."""
    if _collection.count() == 0:
        return []
    results = _collection.query(query_texts=[query], n_results=min(n_results, _collection.count()))
    docs = results["documents"][0] if results["documents"] else []
    dists = results["distances"][0] if results["distances"] else []
    return [d for d, dist in zip(docs, dists) if dist <= _MAX_RELEVANT_DISTANCE]