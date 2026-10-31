"""
Runbook Knowledge Base and BM25 / Keyword Retrieval Engine.
Provides deterministic, sub-millisecond retrieval across the 12 operational runbooks
without requiring heavyweight external vector databases or network calls.
"""

import os
import re
import math
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


class RunbookDocument(BaseModel):
    id: str
    title: str
    content: str
    tokens: List[str] = []


class RunbookRAGEngine:
    _instance: Optional["RunbookRAGEngine"] = None

    def __init__(self, runbooks_dir: Optional[str] = None):
        if not runbooks_dir:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            runbooks_dir = os.path.join(base_dir, "runbooks")
        self.runbooks_dir = runbooks_dir
        self.documents: Dict[str, RunbookDocument] = {}
        self.doc_freq: Dict[str, int] = {}
        self.avg_doc_len: float = 0.0
        self.load_runbooks()

    @classmethod
    def get_instance(cls) -> "RunbookRAGEngine":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """Simple deterministic tokenizer: lowercase, alphanumeric words, splitting underscores."""
        cleaned = text.replace("_", " ")
        words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_\-]+\b", text) if len(w) > 1]
        split_words = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9\-]+\b", cleaned) if len(w) > 1]
        return list(words + split_words)

    def load_runbooks(self):
        """Loads and indexes all .md files in the runbooks directory."""
        if not os.path.exists(self.runbooks_dir):
            return

        docs = []
        total_tokens = 0

        for fname in sorted(os.listdir(self.runbooks_dir)):
            if fname.endswith(".md"):
                fpath = os.path.join(self.runbooks_dir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()

                # Extract runbook ID from filename or first line
                rb_id = fname.replace(".md", "").upper()
                lines = content.strip().split("\n")
                title = lines[0].replace("#", "").strip() if lines else rb_id

                tokens = self.tokenize(content)
                doc = RunbookDocument(id=rb_id, title=title, content=content, tokens=tokens)
                docs.append(doc)
                self.documents[rb_id] = doc
                total_tokens += len(tokens)

        num_docs = len(docs)
        self.avg_doc_len = (total_tokens / num_docs) if num_docs > 0 else 1.0

        # Compute document frequencies
        self.doc_freq = {}
        for doc in docs:
            unique_terms = set(doc.tokens)
            for t in unique_terms:
                self.doc_freq[t] = self.doc_freq.get(t, 0) + 1

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Retrieves top_k relevant runbooks using BM25 scoring algorithm.
        """
        q_tokens = self.tokenize(query)
        if not q_tokens or not self.documents:
            return []

        scores: Dict[str, float] = {}
        N = len(self.documents)
        k1 = 1.5
        b = 0.75

        for rb_id, doc in self.documents.items():
            doc_len = len(doc.tokens)
            doc_score = 0.0

            # Count term frequencies in doc
            tf_dict: Dict[str, int] = {}
            for t in doc.tokens:
                tf_dict[t] = tf_dict.get(t, 0) + 1

            for t in q_tokens:
                if t in tf_dict:
                    tf = tf_dict[t]
                    df = self.doc_freq.get(t, 0)
                    # IDF with smoothing
                    idf = math.log(1.0 + (N - df + 0.5) / (df + 0.5))
                    # BM25 TF formula
                    denom = tf + k1 * (1.0 - b + b * (doc_len / self.avg_doc_len))
                    term_score = idf * (tf * (k1 + 1.0) / denom)
                    doc_score += term_score

            if doc_score > 0:
                scores[rb_id] = doc_score

        # Sort descending by score
        ranked_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]

        # If no lexical overlap found, fallback to RB-012
        if not ranked_ids and "RB-012" in self.documents:
            ranked_ids = ["RB-012"]

        results = []
        for rid in ranked_ids:
            doc = self.documents[rid]
            results.append({
                "id": doc.id,
                "title": doc.title,
                "content": doc.content,
                "score": round(scores.get(rid, 0.0), 3)
            })
        return results

    def is_valid_citation(self, citation: str) -> bool:
        """True if citation exists in our indexed runbook repository."""
        clean_id = citation.strip("[]").upper()
        return clean_id in self.documents


RAG = RunbookRAGEngine.get_instance()
