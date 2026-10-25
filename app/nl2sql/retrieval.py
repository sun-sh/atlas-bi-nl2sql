from __future__ import annotations

import math
import re

from app.nl2sql.models import SchemaHit, TableInfo
from app.nl2sql.schema_catalog import SCHEMA
from app.nl2sql.settings import get_settings


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[\u4e00-\u9fff]{1,8}")


def _tokens(text: str) -> list[str]:
    raw = [x.lower() for x in TOKEN_RE.findall(text)]
    grams = []
    for token in raw:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) > 1:
                grams.extend(token[i : i + 2] for i in range(len(token) - 1))
            grams.append(token)
        else:
            grams.append(token)
    return list(dict.fromkeys(grams))


def _table_text(table: TableInfo) -> str:
    parts = [table.name, table.label, table.description, table.grain, *table.aliases]
    parts.extend(f"{c.name} {c.description}" for c in table.columns)
    return " ".join(parts)


class HybridSchemaRetriever:
    def __init__(self, settings=None) -> None:
        self.settings = settings or get_settings()
        self.tables = list(SCHEMA)
        self.corpus = [_table_text(t) for t in self.tables]
        self.doc_tokens = [set(_tokens(x)) for x in self.corpus]
        self.df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            for token in tokens:
                self.df[token] = self.df.get(token, 0) + 1
        self.embedder = None
        self.vectors = None
        self.qdrant = None
        self.qdrant_collection = None
        self.elastic = None
        self.elastic_scores = None
        self._load_elastic()
        self._load_semantic()

    def _load_elastic(self) -> None:
        if not self.settings.elasticsearch_url:
            return
        try:
            from elasticsearch import Elasticsearch

            client = Elasticsearch(self.settings.elasticsearch_url, request_timeout=2)
            if not client.ping():
                return
            index = "schema_tables"
            if client.indices.exists(index=index):
                client.indices.delete(index=index)
            client.indices.create(index=index)
            for table in self.tables:
                client.index(
                    index=index,
                    id=table.name,
                    document={"table": table.name, "label": table.label, "text": _table_text(table)},
                    refresh=True,
                )
            self.elastic = client
        except Exception:
            self.elastic = None

    def _load_semantic(self) -> None:
        if not self.settings.use_bge:
            return
        try:
            from fastembed import TextEmbedding

            self.embedder = TextEmbedding(model_name=self.settings.embedding_model)
            docs = [f"电商数据库表：{x}" for x in self.corpus]
            self.vectors = list(self.embedder.embed(docs))
            self._load_qdrant()
        except Exception:
            self.embedder = None
            self.vectors = None
            self.qdrant = None

    def _load_qdrant(self) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        client = (
            QdrantClient(url=self.settings.qdrant_url, timeout=3)
            if self.settings.qdrant_url
            else QdrantClient(path=":memory:")
        )
        name = "schema_tables"
        try:
            if self.settings.qdrant_url:
                try:
                    client.delete_collection(collection_name=name)
                except Exception:
                    pass
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=len(self.vectors[0]), distance=Distance.COSINE),
            )
            points = [
                PointStruct(
                    id=i,
                    vector=[float(x) for x in vec],
                    payload={"table": table.name, "label": table.label},
                )
                for i, (table, vec) in enumerate(zip(self.tables, self.vectors))
            ]
            client.upsert(collection_name=name, points=points, wait=True)
            self.qdrant = client
            self.qdrant_collection = name
        except Exception:
            client.close()
            self.qdrant = None
            self.qdrant_collection = None

    def _lexical(self, query: str, index: int) -> tuple[float, tuple[str, ...]]:
        query_tokens = set(_tokens(query))
        doc = self.doc_tokens[index]
        if not query_tokens:
            return 0.0, ()
        matched = tuple(sorted(query_tokens & doc))
        total = len(self.tables)
        score = 0.0
        for token in matched:
            idf = math.log((total + 1) / (self.df.get(token, 0) + 1)) + 1
            score += idf
        score /= math.sqrt(max(1, len(doc)))
        return score, matched

    def _refresh_elastic_scores(self, query: str) -> None:
        if self.elastic is None:
            self.elastic_scores = None
            return
        try:
            result = self.elastic.search(
                index="schema_tables",
                query={"multi_match": {"query": query, "fields": ["label^3", "text"]}},
                size=len(self.tables),
                filter_path=["hits.hits._id", "hits.hits._score"],
            )
            self.elastic_scores = {
                hit["_id"]: float(hit["_score"])
                for hit in result.get("hits", {}).get("hits", [])
            }
        except Exception:
            self.elastic_scores = None

    def _semantic(self, query: str) -> list[float]:
        if self.embedder is None or self.vectors is None:
            return [0.0] * len(self.tables)
        try:
            q = list(self.embedder.embed([f"电商经营分析问题：{query}"]))[0]
            if self.qdrant is not None and self.qdrant_collection:
                points = self.qdrant.query_points(
                    collection_name=self.qdrant_collection,
                    query=[float(x) for x in q],
                    limit=len(self.tables),
                ).points
                score_by_table = {p.payload["table"]: float(p.score) for p in points}
                return [score_by_table.get(table.name, -1.0) for table in self.tables]
            return [float(sum(float(a) * float(b) for a, b in zip(q, vec))) for vec in self.vectors]
        except Exception:
            return [0.0] * len(self.tables)

    def search(self, query: str, limit: int = 4) -> list[SchemaHit]:
        self._refresh_elastic_scores(query)
        lexical = [self._lexical(query, i) for i in range(len(self.tables))]
        semantic = self._semantic(query)
        lex_max = max((x[0] for x in lexical), default=0.0) or 1.0
        sem_min = min(semantic) if semantic else 0.0
        sem_max = max(semantic) if semantic else 0.0
        sem_span = max(0.0001, sem_max - sem_min)
        hits = []
        for i, table in enumerate(self.tables):
            lex_score, matched = lexical[i]
            sem_score = (semantic[i] - sem_min) / sem_span
            if self.elastic_scores is not None:
                keyword_score = min(1.0, self.elastic_scores.get(table.name, 0.0) / 10.0)
            else:
                keyword_score = lex_score / lex_max
            score = 0.56 * sem_score + 0.44 * keyword_score
            if table.name == "orders":
                score += 0.015
            hits.append(
                SchemaHit(
                    table=table.name,
                    label=table.label,
                    description=table.description,
                    score=score,
                    vector_score=sem_score,
                    lexical_score=keyword_score,
                    matched_terms=matched,
                    columns=table.columns,
                )
            )
        hits.sort(key=lambda x: x.score, reverse=True)
        return hits[:limit]

    def describe(self) -> dict:
        return {
            "tables": len(self.tables),
            "semantic_backend": "Qdrant + BGE" if self.qdrant is not None else ("BGE / fastembed" if self.embedder else "local fallback"),
            "lexical_backend": "Elasticsearch BM25" if self.elastic is not None else "BM25-style IDF",
            "elasticsearch_enabled": self.elastic is not None,
            "qdrant_mode": "remote" if self.settings.qdrant_url else "in-memory",
            "fusion": "0.56 semantic + 0.44 lexical",
            "fusion_basis": (
                "ES BM25 -> min(score/10, 1)"
                if self.elastic is not None
                else "normalized IDF"
            ),
            "embedding_model": self.settings.embedding_model if self.embedder else None,
            "qdrant_enabled": self.qdrant is not None,
        }