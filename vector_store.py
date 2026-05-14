import os
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction

# Setup persistent storage
CHROMA_DB_PATH = "./chroma_db"
client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

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
    """Upserts a list of dataset dictionaries into ChromaDB."""
    if not datasets:
        return
        
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
        
        # Combine all relevant text for embedding
        doc_text = f"Dataset: {table_name}\nSchema: {schema}\nDatabase: {database}\nDescription: {description}"
        
        ids.append(ds_id)
        documents.append(doc_text)
        metadatas.append({
            "id": ds_id,
            "table_name": table_name,
            "schema": schema,
            "type": "dataset"
        })
        
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"[VectorStore] Upserted {len(ids)} datasets.")

def sync_dashboards(dashboards: list[dict]):
    """Upserts a list of dashboard dictionaries into ChromaDB."""
    if not dashboards:
        return
        
    collection = get_dashboard_collection()
    
    ids = []
    documents = []
    metadatas = []
    
    for db in dashboards:
        db_id = str(db.get("id"))
        title = db.get("dashboard_title", "")
        slug = db.get("slug", "") or ""
        
        doc_text = f"Dashboard: {title}\nSlug: {slug}"
        
        ids.append(db_id)
        documents.append(doc_text)
        metadatas.append({
            "id": db_id,
            "title": title,
            "type": "dashboard"
        })
        
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"[VectorStore] Upserted {len(ids)} dashboards.")

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
