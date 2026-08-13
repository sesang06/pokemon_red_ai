from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class MemoryRecord:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] | None = None


class QdrantMemoryStore:
    def __init__(self, collection: str, url: str = "http://localhost:6333"):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import PointStruct
        except ImportError as exc:
            raise RuntimeError("Install qdrant-client to use QdrantMemoryStore.") from exc

        self.collection = collection
        self.client = QdrantClient(url=url)
        self.point_struct = PointStruct

    def remember(self, record: MemoryRecord) -> str:
        if record.vector is None:
            raise ValueError("Qdrant records need an embedding vector.")

        point_id = str(uuid4())
        self.client.upsert(
            collection_name=self.collection,
            points=[
                self.point_struct(
                    id=point_id,
                    vector=record.vector,
                    payload={"text": record.text, **record.metadata},
                )
            ],
        )
        return point_id
