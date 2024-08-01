from datetime import datetime
from datetime import timezone
from typing import Any
from typing import Optional

import requests

from danswer.configs.app_configs import INDEX_BATCH_SIZE
from danswer.configs.constants import DocumentSource
from danswer.connectors.cross_connector_utils.rate_limit_wrapper import (
    rate_limit_builder,
)
from danswer.connectors.cross_connector_utils.retry_wrapper import retry_builder
from danswer.connectors.interfaces import GenerateDocumentsOutput
from danswer.connectors.interfaces import LoadConnector
from danswer.connectors.interfaces import PollConnector
from danswer.connectors.interfaces import SecondsSinceUnixEpoch
from danswer.connectors.models import BasicExpertInfo
from danswer.connectors.models import ConnectorMissingCredentialError
from danswer.connectors.models import Document
from danswer.connectors.models import Section


MONDAY_API_BASE_URL = "https://api.monday.com/v2"
MONDAY_API_KEY = "eyJhbGciOiJIUzI1NiJ9.eyJ0aWQiOjI1NTkwMjQ4MiwiYWFpIjoxMSwidWlkIjozNDQyNzMwNSwiaWFkIjoiMjAyMy0wNS0xMVQxNDoyNTozMS4wMDBaIiwicGVyIjoibWU6d3JpdGUiLCJhY3RpZCI6MTM0NzMwMzEsInJnbiI6InVzZTEifQ.iSrIBZvjW35N-wGXJS1aUGAgNHNkBCvt-NQUNw_cgjs"


class MondayConnector(LoadConnector, PollConnector):
    def __init__(
        self,
        batch_size: int = INDEX_BATCH_SIZE,
        api_token: str | None = None,
        board_id: int | None = None,
        team_id: str | None = None,
        connector_type: str | None = None,
        connector_ids: list[str] | None = None,
        retrieve_task_comments: bool = True,
    ) -> None:
        self.batch_size = batch_size
        self.api_token = api_token
        self.board_id = board_id
        # self.team_id = team_id
        # self.connector_type = connector_type if connector_type else "workspace"
        # self.connector_ids = connector_ids
        # self.retrieve_task_comments = retrieve_task_comments

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        self.api_token = credentials["monday_api_token"]
        # self.team_id = credentials["clickup_team_id"]
        return None

    @retry_builder()
    @rate_limit_builder(max_calls=100, period=60)
    def _make_request(self, query: str) -> Any:
        if not self.api_token:
            raise ConnectorMissingCredentialError("Monday.com")

        headers = {
            "Authorization": self.api_token,
            'API-Version': '2024-01'}

        response = requests.post(
            self.MONDAY_API_BASE_URL, headers=headers, json={"query": query}
        )

        response.raise_for_status()

        return response.json()

    def _get_board_items(self, board_id: str) -> list[Document]:
        query = """
        {
            boards(ids: %s) {
                id
                name
                items_page {
                    items {
                        name
                        group {
                            title
                            id
                        }
                        column_values {
                            id
                            type
                            value
                            text
                            column {
                                title
                            }
                        }
                        
                    }
                }
            }
        }
        """ % board_id

        response = self._make_request(query)
        items = response["data"]["boards"][0]["items_page"]["items"]
        remappedItems = self.remap_column_values(items)
        documents = []

        for item in remappedItems:
            # Creazione di oggetti Document con i dati degli Items
            document = Document(
                id=item["id"],
                title=item["name"],
                # Altri campi da aggiungere
            )
            documents.append(document)

        return documents

    def remap_column_values(items):
        if not items or not len(items):
            return []

        remapped_items = []

        for item in items:
            columns = {}
            for column_value in item.get("column_values", []):
                column_title = (
                    column_value.get("column", {}).get(
                        "title", column_value.get("title"))
                )
                columns[column_title] = column_value.get("text", "")

            remapped_item = {
                "name": item.get("name"),
                "id": item.get("id"),
                "parentName": item.get("parentName"),
                "group": item.get("group"),
                "subitems": item.get("subitems"),
                **columns
            }

        remapped_items.append(remapped_item)

        return remapped_items

    def load_from_state(self) -> GenerateDocumentsOutput:
        if self.api_token is None:
            raise ConnectorMissingCredentialError("Monday.com")

        return self._get_board_items(None, self.board_id)

    def poll_source(
        self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch
    ) -> GenerateDocumentsOutput:
        if self.api_token is None:
            raise ConnectorMissingCredentialError("Monday.com")

        return self._get_board_items(None, self.board_id)


if __name__ == "__main__":
    import os

    clickup_connector = MondayConnector()

    clickup_connector.load_credentials(
        {
            "monday_api_token": os.environ["monday_api_token"],
        }
    )
    latest_docs = clickup_connector.load_from_state()

    for doc in latest_docs:
        print(doc)
