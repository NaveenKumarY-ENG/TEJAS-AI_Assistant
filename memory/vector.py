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


def remember(text: str, metadata: dict | None = None) -> None:
    """Store a piece of text (a conversation turn, a fact, anything) for later semantic recall."""
    meta = {"timestamp": datetime.utcnow().isoformat()}
    if metadata:
        meta.update(metadata)
    _collection.add(documents=[text], metadatas=[meta], ids=[str(uuid.uuid4())])


def recall(query: str, n_results: int = 3) -> list[str]:
    """Retrieve the most semantically relevant stored memories for a query."""
    if _collection.count() == 0:
        return []
    results = _collection.query(query_texts=[query], n_results=min(n_results, _collection.count()))
    return results["documents"][0] if results["documents"] else []