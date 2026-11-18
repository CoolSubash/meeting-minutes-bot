"""Meeting history in Azure Cosmos DB, or in memory for local development."""
from __future__ import annotations

import os

from errors import PipelineError

TTL_SECONDS = 30 * 24 * 60 * 60
SUMMARY_FIELDS = ("id", "title", "meeting_date", "decision_count", "action_item_count")


def _summary(meeting: dict) -> dict:
    return {
        "meeting_id": meeting["id"],
        "title": meeting["title"],
        "meeting_date": meeting["meeting_date"],
        "decision_count": meeting["decision_count"],
        "action_item_count": meeting["action_item_count"],
    }


def _public(meeting: dict) -> dict:
    return {key: value for key, value in meeting.items() if not key.startswith("_")}


class MemoryStore:
    def __init__(self) -> None:
        self.meetings: dict[str, dict] = {}

    def save(self, meeting: dict) -> None:
        self.meetings[meeting["id"]] = meeting

    def list(self) -> list[dict]:
        ordered = sorted(self.meetings.values(), key=lambda m: m["meeting_date"], reverse=True)
        return [_summary(meeting) for meeting in ordered]

    def get(self, meeting_id: str) -> dict | None:
        return self.meetings.get(meeting_id)

    def delete(self, meeting_id: str) -> bool:
        return self.meetings.pop(meeting_id, None) is not None


class CosmosStore:
    def __init__(self, endpoint: str, key: str) -> None:
        from azure.cosmos import CosmosClient, PartitionKey

        client = CosmosClient(endpoint, credential=key)
        database = client.create_database_if_not_exists(os.environ.get("COSMOS_DATABASE", "minutes"))
        self.container = database.create_container_if_not_exists(
            id=os.environ.get("COSMOS_CONTAINER", "meetings"),
            partition_key=PartitionKey(path="/id"),
            default_ttl=TTL_SECONDS,
            offer_throughput=400,
        )

    def save(self, meeting: dict) -> None:
        self.container.upsert_item(meeting)

    def list(self) -> list[dict]:
        query = f"SELECT {', '.join('c.' + field for field in SUMMARY_FIELDS)} FROM c ORDER BY c.meeting_date DESC"
        items = self.container.query_items(query, enable_cross_partition_query=True)
        return [_summary(item) for item in items]

    def get(self, meeting_id: str) -> dict | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return _public(self.container.read_item(meeting_id, partition_key=meeting_id))
        except CosmosResourceNotFoundError:
            return None

    def delete(self, meeting_id: str) -> bool:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            self.container.delete_item(meeting_id, partition_key=meeting_id)
            return True
        except CosmosResourceNotFoundError:
            return False


_store = None


def get_store():
    global _store
    if _store is None:
        endpoint, key = os.environ.get("COSMOS_ENDPOINT"), os.environ.get("COSMOS_KEY")
        try:
            _store = CosmosStore(endpoint, key) if endpoint and key else MemoryStore()
        except Exception as exc:
            raise PipelineError("Meeting history is unavailable.", 502) from exc
    return _store


def reset_store() -> None:
    global _store
    _store = None
