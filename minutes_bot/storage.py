"""Meeting history in Azure Cosmos DB, or in memory for local development."""
from __future__ import annotations

import os

from .errors import PipelineError

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
        self.users: dict[str, dict] = {}

    def create_user(self, user: dict) -> bool:
        if user["id"] in self.users:
            return False
        self.users[user["id"]] = user
        return True

    def get_user(self, user_id: str) -> dict | None:
        return self.users.get(user_id)

    def save(self, meeting: dict) -> None:
        self.meetings[meeting["id"]] = meeting

    def list(self, owner_id: str | None = None) -> list[dict]:
        owned = [m for m in self.meetings.values() if m.get("owner_id") == owner_id]
        ordered = sorted(owned, key=lambda m: m["meeting_date"], reverse=True)
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
        # Accounts have no default TTL, so they do not expire with the meetings.
        self.users = database.create_container_if_not_exists(
            id=os.environ.get("COSMOS_USERS_CONTAINER", "users"),
            partition_key=PartitionKey(path="/id"),
            offer_throughput=400,
        )

    def create_user(self, user: dict) -> bool:
        from azure.cosmos.exceptions import CosmosResourceExistsError

        try:
            self.users.create_item(user)
            return True
        except CosmosResourceExistsError:
            return False

    def get_user(self, user_id: str) -> dict | None:
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            return _public(self.users.read_item(user_id, partition_key=user_id))
        except CosmosResourceNotFoundError:
            return None

    def save(self, meeting: dict) -> None:
        self.container.upsert_item(meeting)

    def list(self, owner_id: str | None = None) -> list[dict]:
        owner_filter = "c.owner_id = @owner_id" if owner_id else "NOT IS_DEFINED(c.owner_id)"
        fields = ", ".join("c." + field for field in SUMMARY_FIELDS)
        query = f"SELECT {fields} FROM c WHERE {owner_filter} ORDER BY c.meeting_date DESC"
        parameters = [{"name": "@owner_id", "value": owner_id}] if owner_id else []
        items = self.container.query_items(query, parameters=parameters, enable_cross_partition_query=True)
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
