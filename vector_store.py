"""
Vector Store Module - ChromaDB Integration with Reranking and TTL
Provides local memory for research with per-language indexing and smart caching.
"""

import os
import json
import hashlib
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer, CrossEncoder
import numpy as np

class ResearchVectorStore:
    """
    Vector store for research data with:
    - Per-language collections (EN/ES/DE)
    - Reranking for better precision
    - TTL-based expiration for news vs evergreen content
    - Deduplication by content hash
    """
    
    def __init__(self, persist_dir: str = "./chroma_db", enable_reranker: bool = True):
        """Initialize vector store with optional persistence."""
        self.persist_dir = persist_dir
        self.enable_reranker = enable_reranker
        
        # Lazy load embedding model to speed up startup
        self._embedder = None
        self._reranker = None
        self._reranker_enabled = enable_reranker
        
        # Initialize ChromaDB client
        if os.path.exists(persist_dir):
            # Use persistent storage
            self.client = chromadb.PersistentClient(
                path=persist_dir,
                settings=Settings(anonymized_telemetry=False)
            )
        else:
            # Use in-memory storage
            self.client = chromadb.Client(
                Settings(anonymized_telemetry=False)
            )
        
        # Create per-language collections
        self.collections = {}
        for lang in ['en', 'es', 'de']:
            collection_name = f"research_{lang}"
            try:
                self.collections[lang] = self.client.get_or_create_collection(
                    name=collection_name,
                    metadata={"language": lang}
                )
            except:
                # If collection exists, get it
                self.collections[lang] = self.client.get_collection(collection_name)
    
    @property
    def embedder(self):
        """Lazy load embedding model on first use."""
        if self._embedder is None:
            print("Loading embedding model (first use)...")
            self._embedder = SentenceTransformer('all-MiniLM-L6-v2')
        return self._embedder
    
    @property
    def reranker(self):
        """Lazy load reranker model on first use."""
        if self._reranker is None and self._reranker_enabled:
            try:
                print("Loading reranker model (first use)...")
                self._reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
            except:
                print("Reranker model not available, proceeding without reranking")
                self._reranker_enabled = False
        return self._reranker
    
    def _get_content_hash(self, text: str) -> str:
        """Generate hash for content deduplication."""
        return hashlib.md5(text.encode()).hexdigest()[:16]
    
    def _determine_ttl_category(self, text: str, url: str = "") -> str:
        """
        Determine if content is news (short TTL) or evergreen (long TTL).
        Returns: 'news' or 'evergreen'
        """
        # News indicators
        news_keywords = ['breaking', 'latest', 'today', 'yesterday', 'update', 
                        'announces', 'reported', 'news', 'current', 'recent']
        news_domains = ['reuters.com', 'bloomberg.com', 'cnn.com', 'bbc.com', 
                       'apnews.com', 'theguardian.com']
        
        text_lower = text.lower()
        url_lower = url.lower()
        
        # Check for news indicators
        is_news = (
            any(keyword in text_lower[:200] for keyword in news_keywords) or
            any(domain in url_lower for domain in news_domains)
        )
        
        return 'news' if is_news else 'evergreen'
    
    def _is_expired(self, metadata: Dict[str, Any]) -> bool:
        """Check if a document has expired based on TTL."""
        if 'timestamp' not in metadata or 'ttl_category' not in metadata:
            return False
        
        timestamp = datetime.fromisoformat(metadata['timestamp'])
        category = metadata['ttl_category']
        
        # TTL settings
        ttl_days = {
            'news': 3,  # News expires after 3 days
            'evergreen': 30  # Evergreen content lasts 30 days
        }
        
        expiry = timestamp + timedelta(days=ttl_days.get(category, 7))
        return datetime.now() > expiry
    
    def add_research_data(self, 
                         query: str,
                         sources: List[Dict[str, Any]], 
                         summary_sections: Optional[Dict[str, Any]] = None,
                         language: str = 'en',
                         dedupe_summaries: bool = True) -> int:
        """
        Add research data to vector store.
        
        Args:
            query: The research query
            sources: List of source documents with 'content', 'url', 'title'
            summary_sections: Optional final summary sections to store
            language: Language code (en/es/de)
        
        Returns:
            Number of documents added
        """
        if language not in self.collections:
            language = 'en'  # Fallback to English
        
        collection = self.collections[language]
        documents = []
        metadatas = []
        ids = []
        
        # Process source documents
        for source in sources:
            content = source.get('content', '')
            if not content or len(content) < 50:  # Skip very short content
                continue
            
            # Generate unique ID based on content hash
            content_hash = self._get_content_hash(content)
            doc_id = f"src_{content_hash}"
            
            # Check if already exists
            try:
                existing = collection.get(ids=[doc_id])
                if existing and existing['ids']:
                    continue  # Skip duplicates
            except:
                pass
            
            # Determine TTL category
            ttl_category = self._determine_ttl_category(
                content, 
                source.get('url', '')
            )
            
            # Prepare document
            documents.append(content)
            metadatas.append({
                'type': 'source',
                'url': source.get('url', ''),
                'title': source.get('title', ''),
                'query': query,
                'timestamp': datetime.now().isoformat(),
                'ttl_category': ttl_category,
                'content_hash': content_hash
            })
            ids.append(doc_id)
        
        # Process summary sections if provided
        if summary_sections:
            sections = summary_sections.get('sections', [])
            
            # If deduping summaries, check for similar existing ones
            if dedupe_summaries:
                try:
                    # Get existing summaries for this query
                    existing_summaries = collection.get(
                        where={"$and": [
                            {"type": "summary"},
                            {"query": query}
                        ]}
                    )
                    # If we already have summaries for this exact query, skip
                    if existing_summaries and existing_summaries['ids'] and len(existing_summaries['ids']) >= len(sections):
                        print(f"Skipping summary storage - already have {len(existing_summaries['ids'])} summaries for this query")
                        # Still process sources if any
                        if documents:
                            embeddings = self.embedder.encode(documents).tolist()
                            collection.add(
                                documents=documents,
                                metadatas=metadatas,
                                ids=ids,
                                embeddings=embeddings
                            )
                            return len(documents)
                        return 0
                except:
                    pass
            
            for section in sections:
                content = section.get('content', '')
                if not content or len(content) < 50:
                    continue
                
                content_hash = self._get_content_hash(content)
                doc_id = f"summary_{content_hash}"
                
                # Check if already exists
                try:
                    existing = collection.get(ids=[doc_id])
                    if existing and existing['ids']:
                        continue
                except:
                    pass
                
                documents.append(content)
                metadatas.append({
                    'type': 'summary',
                    'section_title': section.get('title', ''),
                    'query': query,
                    'timestamp': datetime.now().isoformat(),
                    'ttl_category': 'evergreen',  # Summaries are evergreen
                    'content_hash': content_hash
                })
                ids.append(doc_id)
        
        # Add to collection if we have documents
        if documents:
            embeddings = self.embedder.encode(documents).tolist()
            collection.add(
                documents=documents,
                metadatas=metadatas,
                ids=ids,
                embeddings=embeddings
            )
            return len(documents)
        
        return 0
    
    def search(self, 
               query: str, 
               language: str = 'en',
               top_k: int = 10,
               filter_expired: bool = True) -> List[Dict[str, Any]]:
        """
        Search for relevant documents.
        
        Args:
            query: Search query
            language: Language code
            top_k: Number of results to return
            filter_expired: Whether to filter out expired documents
        
        Returns:
            List of relevant documents with metadata
        """
        if language not in self.collections:
            language = 'en'
        
        collection = self.collections[language]
        
        # Generate query embedding
        query_embedding = self.embedder.encode([query])[0].tolist()
        
        # Search with larger k for reranking
        search_k = top_k * 3 if self.reranker else top_k
        
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(search_k, 100)
            )
        except:
            return []
        
        if not results or not results['documents']:
            return []
        
        # Process results
        documents = []
        for i, doc in enumerate(results['documents'][0]):
            metadata = results['metadatas'][0][i] if results['metadatas'] else {}
            
            # Skip expired documents if filtering is enabled
            if filter_expired and self._is_expired(metadata):
                continue
            
            documents.append({
                'content': doc,
                'metadata': metadata,
                'distance': results['distances'][0][i] if results['distances'] else 0
            })
        
        # Rerank if enabled and reranker is available
        if self.reranker and documents:
            # Prepare pairs for reranking
            pairs = [[query, doc['content']] for doc in documents]
            
            try:
                # Get reranking scores
                scores = self.reranker.predict(pairs)
                
                # Add scores to documents
                for i, doc in enumerate(documents):
                    doc['rerank_score'] = float(scores[i])
                
                # Sort by rerank score (higher is better)
                documents.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
            except:
                # If reranking fails, continue with original order
                pass
        
        # Return top_k results
        return documents[:top_k]
    
    def get_stats(self, language: str = 'en') -> Dict[str, Any]:
        """Get statistics about the vector store."""
        if language not in self.collections:
            language = 'en'
        
        collection = self.collections[language]
        
        try:
            # Get all documents to count
            all_docs = collection.get()
            total = len(all_docs['ids']) if all_docs and all_docs['ids'] else 0
            
            # Count by type and TTL category
            sources = 0
            summaries = 0
            news = 0
            evergreen = 0
            expired = 0
            
            if all_docs and all_docs['metadatas']:
                for metadata in all_docs['metadatas']:
                    if metadata.get('type') == 'source':
                        sources += 1
                    elif metadata.get('type') == 'summary':
                        summaries += 1
                    
                    if metadata.get('ttl_category') == 'news':
                        news += 1
                    elif metadata.get('ttl_category') == 'evergreen':
                        evergreen += 1
                    
                    if self._is_expired(metadata):
                        expired += 1
            
            return {
                'total_documents': total,
                'sources': sources,
                'summaries': summaries,
                'news_content': news,
                'evergreen_content': evergreen,
                'expired': expired,
                'active': total - expired
            }
        except:
            return {
                'total_documents': 0,
                'sources': 0,
                'summaries': 0,
                'news_content': 0,
                'evergreen_content': 0,
                'expired': 0,
                'active': 0
            }
    
    def clear_expired(self, language: Optional[str] = None) -> int:
        """Clear expired documents from the store."""
        languages = [language] if language else ['en', 'es', 'de']
        total_cleared = 0
        
        for lang in languages:
            if lang not in self.collections:
                continue
            
            collection = self.collections[lang]
            
            try:
                # Get all documents
                all_docs = collection.get()
                if not all_docs or not all_docs['ids']:
                    continue
                
                # Find expired document IDs
                expired_ids = []
                for i, metadata in enumerate(all_docs['metadatas']):
                    if self._is_expired(metadata):
                        expired_ids.append(all_docs['ids'][i])
                
                # Delete expired documents
                if expired_ids:
                    collection.delete(ids=expired_ids)
                    total_cleared += len(expired_ids)
            except:
                continue
        
        return total_cleared
    
    def clear_all(self, language: Optional[str] = None) -> bool:
        """Clear all documents from the store."""
        languages = [language] if language else ['en', 'es', 'de']
        
        for lang in languages:
            if lang not in self.collections:
                continue
            
            try:
                # Recreate collection to clear it
                collection_name = f"research_{lang}"
                self.client.delete_collection(collection_name)
                self.collections[lang] = self.client.create_collection(
                    name=collection_name,
                    metadata={"language": lang}
                )
            except:
                return False
        
        return True

# Singleton instance (optional, for easy access)
_vector_store = None

# Module-level lazy reranker for standalone reranking of fresh web results
# (usable without initializing the full vector store).
_module_reranker = None

def get_standalone_reranker():
    """Lazy load the shared CrossEncoder for standalone reranking."""
    global _module_reranker
    if _module_reranker is None:
        print("Loading reranker model (first use)...")
        _module_reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _module_reranker

def rerank_documents(query: str,
                     documents: List[Dict[str, Any]],
                     top_k: int = 10) -> List[Dict[str, Any]]:
    """
    Rerank a list of result documents ({'title', 'content', 'url', ...})
    against a query using the local cross-encoder and return the top_k best.
    Raises on reranker failure; callers should degrade gracefully.
    """
    if not documents or top_k <= 0:
        return list(documents)[:top_k] if documents else []

    pairs = [[query, doc.get('content', '')] for doc in documents]
    scores = get_standalone_reranker().predict(pairs)

    scored = []
    for i, doc in enumerate(documents):
        doc = dict(doc)
        doc['rerank_score'] = float(scores[i])
        scored.append(doc)

    scored.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
    return scored[:top_k]

def get_vector_store(persist_dir: str = "./chroma_db", enable_reranker: bool = True) -> ResearchVectorStore:
    """Get or create the singleton vector store instance."""
    global _vector_store
    if _vector_store is None:
        _vector_store = ResearchVectorStore(persist_dir, enable_reranker)
    return _vector_store
