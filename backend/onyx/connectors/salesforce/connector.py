import gc
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from simple_salesforce import Salesforce

from onyx.configs.app_configs import INDEX_BATCH_SIZE
from onyx.connectors.interfaces import GenerateDocumentsOutput
from onyx.connectors.interfaces import GenerateSlimDocumentOutput
from onyx.connectors.interfaces import LoadConnector
from onyx.connectors.interfaces import PollConnector
from onyx.connectors.interfaces import SecondsSinceUnixEpoch
from onyx.connectors.interfaces import SlimConnector
from onyx.connectors.models import ConnectorMissingCredentialError
from onyx.connectors.models import Document
from onyx.connectors.models import SlimDocument
from onyx.connectors.models import TextSection
from onyx.connectors.salesforce.doc_conversion import convert_sf_object_to_doc
from onyx.connectors.salesforce.doc_conversion import ID_PREFIX
from onyx.connectors.salesforce.salesforce_calls import fetch_all_csvs_in_parallel
from onyx.connectors.salesforce.salesforce_calls import get_all_children_of_sf_type
from onyx.connectors.salesforce.sqlite_functions import get_affected_parent_ids_by_type
from onyx.connectors.salesforce.sqlite_functions import get_record
from onyx.connectors.salesforce.sqlite_functions import init_db
from onyx.connectors.salesforce.sqlite_functions import sqlite_log_stats
from onyx.connectors.salesforce.sqlite_functions import update_sf_db_with_csv
from onyx.connectors.salesforce.utils import BASE_DATA_PATH
from onyx.connectors.salesforce.utils import get_sqlite_db_path
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger
from shared_configs.configs import MULTI_TENANT

logger = setup_logger()


_DEFAULT_PARENT_OBJECT_TYPES = ["Account"]


class SalesforceConnector(LoadConnector, PollConnector, SlimConnector):
    MAX_BATCH_BYTES = 1024 * 1024

    def __init__(
        self,
        batch_size: int = INDEX_BATCH_SIZE,
        requested_objects: list[str] = [],
    ) -> None:
        self.batch_size = batch_size
        self._sf_client: Salesforce | None = None
        self.parent_object_list = (
            [obj.capitalize() for obj in requested_objects]
            if requested_objects
            else _DEFAULT_PARENT_OBJECT_TYPES
        )

    def load_credentials(
        self,
        credentials: dict[str, Any],
    ) -> dict[str, Any] | None:
        domain = "test" if credentials.get("is_sandbox") else None
        self._sf_client = Salesforce(
            username=credentials["sf_username"],
            password=credentials["sf_password"],
            security_token=credentials["sf_security_token"],
            domain=domain,
        )
        return None

    @property
    def sf_client(self) -> Salesforce:
        if self._sf_client is None:
            raise ConnectorMissingCredentialError("Salesforce")
        return self._sf_client

    @staticmethod
    def reconstruct_object_types(directory: str) -> dict[str, list[str] | None]:
        """
        Scans the given directory for all CSV files and reconstructs the available object types.
        Assumes filenames are formatted as "ObjectType.filename.csv" or "ObjectType.csv".

        Args:
            directory (str): The path to the directory containing CSV files.

        Returns:
            dict[str, list[str]]: A dictionary mapping object types to lists of file paths.
        """
        object_types = defaultdict(list)

        for filename in os.listdir(directory):
            if filename.endswith(".csv"):
                parts = filename.split(".", 1)  # Split on the first period
                object_type = parts[0]  # Take the first part as the object type
                object_types[object_type].append(os.path.join(directory, filename))

        return dict(object_types)

    @staticmethod
    def _download_object_csvs(
        directory: str,
        parent_object_list: list[str],
        sf_client: Salesforce,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
    ) -> None:
        all_object_types: set[str] = set(parent_object_list)

        logger.info(
            f"Parent object types: num={len(parent_object_list)} list={parent_object_list}"
        )

        # This takes like 20 seconds
        for parent_object_type in parent_object_list:
            child_types = get_all_children_of_sf_type(sf_client, parent_object_type)
            all_object_types.update(child_types)
            logger.debug(
                f"Found {len(child_types)} child types for {parent_object_type}"
            )

        # Always want to make sure user is grabbed for permissioning purposes
        all_object_types.add("User")

        logger.info(
            f"All object types: num={len(all_object_types)} list={all_object_types}"
        )

        # gc.collect()

        # checkpoint - we've found all object types, now time to fetch the data
        logger.info("Fetching CSVs for all object types")

        # This takes like 30 minutes first time and <2 minutes for updates
        object_type_to_csv_path = fetch_all_csvs_in_parallel(
            sf_client=sf_client,
            object_types=all_object_types,
            start=start,
            end=end,
            target_dir=directory,
        )

        # print useful information
        num_csvs = 0
        num_bytes = 0
        for object_type, csv_paths in object_type_to_csv_path.items():
            if not csv_paths:
                continue

            for csv_path in csv_paths:
                if not csv_path:
                    continue

                file_path = Path(csv_path)
                file_size = file_path.stat().st_size
                num_csvs += 1
                num_bytes += file_size
                logger.info(
                    f"CSV info: object_type={object_type} path={csv_path} bytes={file_size}"
                )

        logger.info(f"CSV info total: total_csvs={num_csvs} total_bytes={num_bytes}")

    @staticmethod
    def _load_csvs_to_db(csv_directory: str, db_directory: str) -> set[str]:
        updated_ids: set[str] = set()

        object_type_to_csv_path = SalesforceConnector.reconstruct_object_types(
            csv_directory
        )

        # This takes like 10 seconds
        # This is for testing the rest of the functionality if data has
        # already been fetched and put in sqlite
        # from import onyx.connectors.salesforce.sf_db.sqlite_functions find_ids_by_type
        # for object_type in self.parent_object_list:
        #     updated_ids.update(list(find_ids_by_type(object_type)))

        # This takes 10-70 minutes first time (idk why the range is so big)
        total_types = len(object_type_to_csv_path)
        logger.info(f"Starting to process {total_types} object types")

        for i, (object_type, csv_paths) in enumerate(
            object_type_to_csv_path.items(), 1
        ):
            logger.info(f"Processing object type {object_type} ({i}/{total_types})")
            # If path is None, it means it failed to fetch the csv
            if csv_paths is None:
                continue

            # Go through each csv path and use it to update the db
            for csv_path in csv_paths:
                logger.debug(
                    f"Processing CSV: object_type={object_type} "
                    f"csv={csv_path} "
                    f"len={Path(csv_path).stat().st_size}"
                )
                new_ids = update_sf_db_with_csv(
                    db_directory,
                    object_type=object_type,
                    csv_download_path=csv_path,
                )
                updated_ids.update(new_ids)
                logger.debug(
                    f"Added {len(new_ids)} new/updated records for {object_type}"
                )

                os.remove(csv_path)

        return updated_ids

    def _fetch_from_salesforce(
        self,
        temp_dir: str,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
    ) -> GenerateDocumentsOutput:
        logger.info("_fetch_from_salesforce starting.")
        if not self._sf_client:
            raise RuntimeError("self._sf_client is None!")

        init_db(temp_dir)

        sqlite_log_stats(temp_dir)

        # Step 1 - download
        SalesforceConnector._download_object_csvs(
            temp_dir, self.parent_object_list, self._sf_client, start, end
        )
        gc.collect()

        # Step 2 - load CSV's to sqlite
        updated_ids = SalesforceConnector._load_csvs_to_db(temp_dir, temp_dir)
        gc.collect()

        logger.info(f"Found {len(updated_ids)} total updated records")
        logger.info(
            f"Starting to process parent objects of types: {self.parent_object_list}"
        )

        # Step 3 - extract and index docs
        batches_processed = 0
        docs_processed = 0
        docs_to_yield: list[Document] = []
        docs_to_yield_bytes = 0

        # Takes 15-20 seconds per batch
        for parent_type, parent_id_batch in get_affected_parent_ids_by_type(
            temp_dir,
            updated_ids=list(updated_ids),
            parent_types=self.parent_object_list,
        ):
            batches_processed += 1
            logger.info(
                f"Processing batch: index={batches_processed} "
                f"object_type={parent_type} "
                f"len={len(parent_id_batch)} "
                f"processed={docs_processed} "
                f"remaining={len(updated_ids) - docs_processed}"
            )
            for parent_id in parent_id_batch:
                if not (parent_object := get_record(temp_dir, parent_id, parent_type)):
                    logger.warning(
                        f"Failed to get parent object {parent_id} for {parent_type}"
                    )
                    continue

                doc = convert_sf_object_to_doc(
                    temp_dir,
                    sf_object=parent_object,
                    sf_instance=self.sf_client.sf_instance,
                )
                doc_sizeof = sys.getsizeof(doc)
                docs_to_yield_bytes += doc_sizeof
                docs_to_yield.append(doc)
                docs_processed += 1

                # memory usage is sensitive to the input length, so we're yielding immediately
                # if the batch exceeds a certain byte length
                if (
                    len(docs_to_yield) >= self.batch_size
                    or docs_to_yield_bytes > SalesforceConnector.MAX_BATCH_BYTES
                ):
                    yield docs_to_yield
                    docs_to_yield = []
                    docs_to_yield_bytes = 0

                    # observed a memory leak / size issue with the account table if we don't gc.collect here.
                    gc.collect()

        yield docs_to_yield
        logger.info(
            f"Final processing stats: "
            f"processed={docs_processed} "
            f"remaining={len(updated_ids) - docs_processed}"
        )

    def load_from_state(self) -> GenerateDocumentsOutput:
        if MULTI_TENANT:
            # if multi tenant, we cannot expect the sqlite db to be cached/present
            with tempfile.TemporaryDirectory() as temp_dir:
                return self._fetch_from_salesforce(temp_dir)

        # nuke the db since we're starting from scratch
        sqlite_db_path = get_sqlite_db_path(BASE_DATA_PATH)
        if os.path.exists(sqlite_db_path):
            logger.info(f"load_from_state: Removing db at {sqlite_db_path}.")
            os.remove(sqlite_db_path)
        return self._fetch_from_salesforce(BASE_DATA_PATH)

    def poll_source(
        self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch
    ) -> GenerateDocumentsOutput:
        if MULTI_TENANT:
            # if multi tenant, we cannot expect the sqlite db to be cached/present
            with tempfile.TemporaryDirectory() as temp_dir:
                return self._fetch_from_salesforce(temp_dir, start=start, end=end)

        if start == 0:
            # nuke the db if we're starting from scratch
            sqlite_db_path = get_sqlite_db_path(BASE_DATA_PATH)
            if os.path.exists(sqlite_db_path):
                logger.info(
                    f"poll_source: Starting at time 0, removing db at {sqlite_db_path}."
                )
                os.remove(sqlite_db_path)

        return self._fetch_from_salesforce(BASE_DATA_PATH)

    def retrieve_all_slim_documents(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,
    ) -> GenerateSlimDocumentOutput:
        doc_metadata_list: list[SlimDocument] = []
        for parent_object_type in self.parent_object_list:
            query = f"SELECT Id FROM {parent_object_type}"
            query_result = self.sf_client.query_all(query)
            doc_metadata_list.extend(
                SlimDocument(
                    id=f"{ID_PREFIX}{instance_dict.get('Id', '')}",
                    perm_sync_data={},
                )
                for instance_dict in query_result["records"]
            )

        yield doc_metadata_list


if __name__ == "__main__":
    import time

    connector = SalesforceConnector(requested_objects=["Account"])

    connector.load_credentials(
        {
            "sf_username": os.environ["SF_USERNAME"],
            "sf_password": os.environ["SF_PASSWORD"],
            "sf_security_token": os.environ["SF_SECURITY_TOKEN"],
        }
    )
    start_time = time.monotonic()
    doc_count = 0
    section_count = 0
    text_count = 0
    for doc_batch in connector.load_from_state():
        doc_count += len(doc_batch)
        print(f"doc_count: {doc_count}")
        for doc in doc_batch:
            section_count += len(doc.sections)
            for section in doc.sections:
                if isinstance(section, TextSection) and section.text is not None:
                    text_count += len(section.text)
    end_time = time.monotonic()

    print(f"Doc count: {doc_count}")
    print(f"Section count: {section_count}")
    print(f"Text count: {text_count}")
    print(f"Time taken: {end_time - start_time}")
