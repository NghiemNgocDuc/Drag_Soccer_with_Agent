import logging
from config import PINECONE_API_KEY, PINECONE_ENV, PINECONE_INDEX

_client = None
_index = None


def get_client():
    global _client
    if _client is None and PINECONE_API_KEY:
        try:
            from pinecone import Pinecone
            _client = Pinecone(api_key=PINECONE_API_KEY)
        except Exception as e:
            logging.warning("Failed to init Pinecone: %s", e)
    return _client


def get_index():
    global _index
    if _index is None:
        client = get_client()
        if client and PINECONE_INDEX:
            try:
                _index = client.Index(PINECONE_INDEX)
            except Exception as e:
                logging.warning("Failed to get Pinecone index: %s", e)
    return _index


def upsert_vector(vector_id: str, values: list[float], metadata: dict | None = None):
    idx = get_index()
    if not idx:
        logging.warning("Pinecone not configured — skipping upsert")
        return False
    try:
        idx.upsert(vectors=[(vector_id, values, metadata or {})])
        return True
    except Exception as e:
        logging.error("Pinecone upsert error: %s", e)
        return False


def query_vector(values: list[float], top_k: int = 5, filter: dict | None = None):
    idx = get_index()
    if not idx:
        logging.warning("Pinecone not configured — skipping query")
        return []
    try:
        result = idx.query(vector=values, top_k=top_k, filter=filter, include_metadata=True)
        return result.get("matches", [])
    except Exception as e:
        logging.error("Pinecone query error: %s", e)
        return []


def describe_index_stats() -> dict:
    idx = get_index()
    if not idx:
        return {}
    try:
        return idx.describe_index_stats()
    except Exception as e:
        logging.error("Pinecone stats error: %s", e)
        return {}
