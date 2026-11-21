"""
RAG = Retrieval-Augmented Generation.

This module is the "Retrieval" half:
  1. Load every runbook (.md file) in knowledge_base/.
  2. Turn each runbook into a vector (an "embedding") using Gemini's embedding model.
  3. For a question, embed the question too and return the runbooks whose vectors
     are closest to it (cosine similarity).

The agent then reads those runbooks and writes its answer ("Generation").

If no API key is set (or the embedding call fails), we fall back to TF-IDF keyword
vectors from scikit-learn, so the app still works offline.
"""
import hashlib
import json

import numpy as np

from . import config


class RunbookIndex:
    def __init__(self):
        self.docs = self._load_docs()
        self.backend = "none"
        self.vectors = None
        self._client = None
        self._tfidf = None
        self._build()

    # ---------- loading ----------
    @staticmethod
    def _load_docs():
        docs = []
        for path in sorted(config.KNOWLEDGE_BASE_DIR.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip() if text else path.stem
            docs.append({"file": path.name, "title": title, "text": text})
        return docs

    # ---------- building the vector index ----------
    def _build(self):
        if config.GEMINI_API_KEY:
            try:
                self._build_gemini()
                self.backend = f"gemini ({config.EMBED_MODEL})"
                return
            except Exception as exc:  # network down, quota, wrong model name...
                print(f"[rag] Gemini embeddings unavailable ({exc}); falling back to TF-IDF.")
        self._build_tfidf()
        self.backend = "tf-idf (offline fallback)"

    def _build_gemini(self):
        from google import genai
        from google.genai import types

        self._client = genai.Client(api_key=config.GEMINI_API_KEY)
        # Cache embeddings on disk so we only pay for them once.
        digest = hashlib.sha256(
            (config.EMBED_MODEL + "".join(d["text"] for d in self.docs)).encode()
        ).hexdigest()[:16]
        cache_file = config.CACHE_DIR / f"index-{digest}.npy"
        if cache_file.exists():
            self.vectors = np.load(cache_file)
            return
        vectors = []
        for d in self.docs:  # one call per runbook keeps us well inside free-tier limits
            result = self._client.models.embed_content(
                model=config.EMBED_MODEL,
                contents=d["text"],
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT", title=d["title"]),
            )
            vectors.append(result.embeddings[0].values)
        self.vectors = self._normalise(np.array(vectors, dtype=np.float32))
        config.CACHE_DIR.mkdir(exist_ok=True)
        np.save(cache_file, self.vectors)

    def _build_tfidf(self):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self._tfidf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.vectors = self._normalise(self._tfidf.fit_transform([d["text"] for d in self.docs]).toarray())

    @staticmethod
    def _normalise(m):
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return m / norms

    # ---------- searching ----------
    def _embed_query(self, query):
        if self._tfidf is not None:
            return self._normalise(self._tfidf.transform([query]).toarray())[0]
        from google.genai import types

        result = self._client.models.embed_content(
            model=config.EMBED_MODEL,
            contents=[query],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        return self._normalise(np.array([result.embeddings[0].values], dtype=np.float32))[0]

    def search(self, query, k=2):
        """Return the k most relevant runbooks for the query, best first."""
        q = self._embed_query(query)
        scores = self.vectors @ q            # cosine similarity (vectors are normalised)
        best = np.argsort(-scores)[:k]
        return [
            {
                "runbook": self.docs[i]["file"],
                "title": self.docs[i]["title"],
                "similarity": round(float(scores[i]), 3),
                "content": self.docs[i]["text"],
            }
            for i in best
        ]


_index = None


def get_index():
    """Build the index once and reuse it."""
    global _index
    if _index is None:
        _index = RunbookIndex()
    return _index


if __name__ == "__main__":
    import sys

    idx = get_index()
    print("backend:", idx.backend)
    for hit in idx.search(" ".join(sys.argv[1:]) or "pod restarting exit code 137"):
        print(json.dumps({k: v for k, v in hit.items() if k != "content"}))
