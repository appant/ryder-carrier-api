"""Azure Blob implementation of DeadLetterStore.

One JSON blob per dead-lettered row at ``{pipeline}/{key}.json``, holding the
exact payload we built plus the failure context — enough to inspect and re-POST
after fixing the underlying issue. Re-failing the same key overwrites.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import asdict
from typing import Any

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

from .base import DeadLetterRecord, DeadLetterStore


class BlobDeadLetterStore(DeadLetterStore):
    def __init__(
        self,
        account_url: str | None = None,
        container_name: str = "deadletter",
        credential: DefaultAzureCredential | None = None,
        connection_string: str | None = None,
    ) -> None:
        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        elif account_url:
            service = BlobServiceClient(
                account_url=account_url,
                credential=credential or DefaultAzureCredential(),
            )
        else:
            raise ValueError("BlobDeadLetterStore requires account_url or connection_string")
        self._container = service.get_container_client(container_name)
        with contextlib.suppress(ResourceExistsError):
            self._container.create_container()

    def put(self, record: DeadLetterRecord) -> None:
        blob_name = f"{record.pipeline}/{record.key}.json"
        body = json.dumps(_to_jsonable(record), indent=2, default=str)
        self._container.upload_blob(name=blob_name, data=body, overwrite=True)


def _to_jsonable(record: DeadLetterRecord) -> dict[str, Any]:
    data = asdict(record)
    data["failed_at_utc"] = record.failed_at_utc.isoformat()
    return data
