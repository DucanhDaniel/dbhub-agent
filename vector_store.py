import os
import json
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

# Setup persistent storage (paths relative to this script's directory)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHROMA_DB_PATH = os.path.join(_BASE_DIR, "chroma_db")
KNOWLEDGE_FILE = os.path.join(_BASE_DIR, "dataset_knowledge.json")

client = chromadb.PersistentClient(path=CHROMA_DB_PATH)


def _load_knowledge() -> dict:
    """Load external knowledge descriptions from JSON file."""
    if not os.path.exists(KNOWLEDGE_FILE):
        print(f"[VectorStore] Knowledge file not found: {KNOWLEDGE_FILE}")
        return {"datasets": {}, "dashboards": {}}
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ds_count = len(data.get("datasets", {}))
        db_count = len(data.get("dashboards", {}))
        print(f"[VectorStore] Loaded knowledge: {ds_count} dataset descriptions, {db_count} dashboard descriptions.")
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"[VectorStore] Warning: Failed to load {KNOWLEDGE_FILE}: {e}")
        return {"datasets": {}, "dashboards": {}}


def get_embedding_function():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing from environment")
    return OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name="text-embedding-3-small"
    )

def get_dataset_collection():
    return client.get_or_create_collection(
        name="superset_datasets",
        embedding_function=get_embedding_function()
    )

def get_dashboard_collection():
    return client.get_or_create_collection(
        name="superset_dashboards",
        embedding_function=get_embedding_function()
    )

def sync_datasets(datasets: list[dict]):
    """Upserts a list of dataset dictionaries into ChromaDB, enriched with external knowledge."""
    if not datasets:
        return
        
    knowledge = _load_knowledge()
    ds_knowledge = knowledge.get("datasets", {})
    
    collection = get_dataset_collection()
    
    ids = []
    documents = []
    metadatas = []
    
    for ds in datasets:
        ds_id = str(ds.get("id"))
        table_name = ds.get("table_name", "")
        schema = ds.get("schema", "")
        database = ds.get("database", {}).get("database_name", "") if isinstance(ds.get("database"), dict) else ""
        description = ds.get("description", "") or ""
        
        # Merge external knowledge if available
        extra_desc = ""
        extra_keywords = ""
        if table_name in ds_knowledge:
            entry = ds_knowledge[table_name]
            extra_desc = entry.get("description", "")
            keywords = entry.get("keywords", [])
            if keywords:
                extra_keywords = "Keywords: " + ", ".join(keywords)
        
        # Combine all relevant text for embedding
        parts = [f"Dataset: {table_name}", f"Schema: {schema}", f"Database: {database}"]
        if extra_desc:
            parts.append(f"Description: {extra_desc}")
        elif description:
            parts.append(f"Description: {description}")
        if extra_keywords:
            parts.append(extra_keywords)
            
        doc_text = "\n".join(parts)
        
        ids.append(ds_id)
        documents.append(doc_text)
        metadatas.append({
            "id": ds_id,
            "table_name": table_name,
            "schema": schema,
            "type": "dataset"
        })
        
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    enriched = sum(1 for ds in datasets if ds.get("table_name", "") in ds_knowledge)
    print(f"[VectorStore] Upserted {len(ids)} datasets ({enriched} enriched with knowledge).")

def sync_dashboards(dashboards: list[dict]):
    """Upserts a list of dashboard dictionaries into ChromaDB, enriched with external knowledge."""
    if not dashboards:
        return
    
    knowledge = _load_knowledge()
    db_knowledge = knowledge.get("dashboards", {})
        
    collection = get_dashboard_collection()
    
    ids = []
    documents = []
    metadatas = []
    
    for db in dashboards:
        db_id = str(db.get("id"))
        title = db.get("dashboard_title", "")
        slug = db.get("slug", "") or ""
        
        # Merge external knowledge if available
        extra_desc = ""
        lookup_key = title or slug
        if lookup_key in db_knowledge:
            extra_desc = db_knowledge[lookup_key].get("description", "")
        
        parts = [f"Dashboard: {title}", f"Slug: {slug}"]
        if extra_desc:
            parts.append(f"Description: {extra_desc}")
            
        doc_text = "\n".join(parts)
        
        ids.append(db_id)
        documents.append(doc_text)
        metadatas.append({
            "id": db_id,
            "title": title,
            "type": "dashboard"
        })
        
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    enriched = sum(1 for db in dashboards if (db.get("dashboard_title", "") or db.get("slug", "")) in db_knowledge)
    print(f"[VectorStore] Upserted {len(ids)} dashboards ({enriched} enriched with knowledge).")

def search_dataset(query: str, n_results: int = 3):
    """Searches for datasets by semantic query."""
    collection = get_dataset_collection()
    if collection.count() == 0:
        return []
        
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )
    
    matches = []
    if results["ids"] and results["ids"][0]:
        for idx in range(len(results["ids"][0])):
            matches.append({
                "id": results["ids"][0][idx],
                "metadata": results["metadatas"][0][idx],
                "document": results["documents"][0][idx],
                "distance": results["distances"][0][idx] if "distances" in results and results["distances"] else None
            })
    return matches

def search_dashboard(query: str, n_results: int = 3):
    """Searches for dashboards by semantic query."""
    collection = get_dashboard_collection()
    if collection.count() == 0:
        return []
        
    results = collection.query(
        query_texts=[query],
        n_results=n_results
    )
    
    matches = []
    if results["ids"] and results["ids"][0]:
        for idx in range(len(results["ids"][0])):
            matches.append({
                "id": results["ids"][0][idx],
                "metadata": results["metadatas"][0][idx],
                "document": results["documents"][0][idx],
                "distance": results["distances"][0][idx] if "distances" in results and results["distances"] else None
            })
    return matches
