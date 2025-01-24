import json
import time
from collections.abc import Callable
from datetime import timedelta
from itertools import islice
from typing import Any

from celery import shared_task
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from pydantic import BaseModel
from redis import Redis
from redis.lock import Lock as RedisLock
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import Session

from onyx.background.celery.apps.app_base import task_logger
from onyx.background.celery.tasks.vespa.tasks import celery_get_queue_length
from onyx.configs.constants import CELERY_GENERIC_BEAT_LOCK_TIMEOUT
from onyx.configs.constants import ONYX_CLOUD_TENANT_ID
from onyx.configs.constants import OnyxCeleryQueues
from onyx.configs.constants import OnyxCeleryTask
from onyx.configs.constants import OnyxRedisLocks
from onyx.db.engine import get_all_tenant_ids
from onyx.db.engine import get_db_current_time
from onyx.db.engine import get_session_with_tenant
from onyx.db.enums import IndexingStatus
from onyx.db.enums import SyncType
from onyx.db.models import ConnectorCredentialPair
from onyx.db.models import DocumentSet
from onyx.db.models import IndexAttempt
from onyx.db.models import SyncRecord
from onyx.db.models import UserGroup
from onyx.db.search_settings import get_active_search_settings
from onyx.redis.redis_pool import get_redis_client
from onyx.redis.redis_pool import redis_lock_dump
from onyx.utils.telemetry import optional_telemetry
from onyx.utils.telemetry import RecordType

_MONITORING_SOFT_TIME_LIMIT = 60 * 5  # 5 minutes
_MONITORING_TIME_LIMIT = _MONITORING_SOFT_TIME_LIMIT + 60  # 6 minutes

_CONNECTOR_INDEX_ATTEMPT_START_LATENCY_KEY_FMT = (
    "monitoring_connector_index_attempt_start_latency:{cc_pair_id}:{index_attempt_id}"
)

_CONNECTOR_INDEX_ATTEMPT_RUN_SUCCESS_KEY_FMT = (
    "monitoring_connector_index_attempt_run_success:{cc_pair_id}:{index_attempt_id}"
)


def _mark_metric_as_emitted(redis_std: Redis, key: str) -> None:
    """Mark a metric as having been emitted by setting a Redis key with expiration"""
    redis_std.set(key, "1", ex=24 * 60 * 60)  # Expire after 1 day


def _has_metric_been_emitted(redis_std: Redis, key: str) -> bool:
    """Check if a metric has been emitted by checking for existence of Redis key"""
    return bool(redis_std.exists(key))


class Metric(BaseModel):
    key: str | None  # only required if we need to store that we have emitted this metric
    name: str
    value: Any
    tags: dict[str, str]

    def log(self) -> None:
        """Log the metric in a standardized format"""
        data = {
            "metric": self.name,
            "value": self.value,
            "tags": self.tags,
        }
        task_logger.info(json.dumps(data))

    def emit(self, tenant_id: str | None) -> None:
        # Convert value to appropriate type based on the input value
        bool_value = None
        float_value = None
        int_value = None
        string_value = None
        # NOTE: have to do bool first, since `isinstance(True, int)` is true
        # e.g. bool is a subclass of int
        if isinstance(self.value, bool):
            bool_value = self.value
        elif isinstance(self.value, int):
            int_value = self.value
        elif isinstance(self.value, float):
            float_value = self.value
        elif isinstance(self.value, str):
            string_value = self.value
        else:
            task_logger.error(
                f"Invalid metric value type: {type(self.value)} "
                f"({self.value}) for metric {self.name}."
            )
            return

        # don't send None values over the wire
        data = {
            k: v
            for k, v in {
                "metric_name": self.name,
                "float_value": float_value,
                "int_value": int_value,
                "string_value": string_value,
                "bool_value": bool_value,
                "tags": self.tags,
            }.items()
            if v is not None
        }
        optional_telemetry(
            record_type=RecordType.METRIC,
            data=data,
            tenant_id=tenant_id,
        )


def _collect_queue_metrics(redis_celery: Redis) -> list[Metric]:
    """Collect metrics about queue lengths for different Celery queues"""
    metrics = []
    queue_mappings = {
        "celery_queue_length": "celery",
        "indexing_queue_length": "indexing",
        "sync_queue_length": "sync",
        "deletion_queue_length": "deletion",
        "pruning_queue_length": "pruning",
        "permissions_sync_queue_length": OnyxCeleryQueues.CONNECTOR_DOC_PERMISSIONS_SYNC,
        "external_group_sync_queue_length": OnyxCeleryQueues.CONNECTOR_EXTERNAL_GROUP_SYNC,
        "permissions_upsert_queue_length": OnyxCeleryQueues.DOC_PERMISSIONS_UPSERT,
    }

    for name, queue in queue_mappings.items():
        metrics.append(
            Metric(
                key=None,
                name=name,
                value=celery_get_queue_length(queue, redis_celery),
                tags={"queue": name},
            )
        )

    return metrics


def _build_connector_start_latency_metric(
    cc_pair: ConnectorCredentialPair,
    recent_attempt: IndexAttempt,
    second_most_recent_attempt: IndexAttempt | None,
    redis_std: Redis,
) -> Metric | None:
    if not recent_attempt.time_started:
        return None

    # check if we already emitted a metric for this index attempt
    metric_key = _CONNECTOR_INDEX_ATTEMPT_START_LATENCY_KEY_FMT.format(
        cc_pair_id=cc_pair.id,
        index_attempt_id=recent_attempt.id,
    )
    if _has_metric_been_emitted(redis_std, metric_key):
        task_logger.info(
            f"Skipping metric for connector {cc_pair.connector.id} "
            f"index attempt {recent_attempt.id} because it has already been "
            "emitted"
        )
        return None

    # Connector start latency
    # first run case - we should start as soon as it's created
    if not second_most_recent_attempt:
        desired_start_time = cc_pair.connector.time_created
    else:
        if not cc_pair.connector.refresh_freq:
            task_logger.error(
                "Found non-initial index attempt for connector "
                "without refresh_freq. This should never happen."
            )
            return None

        desired_start_time = second_most_recent_attempt.time_updated + timedelta(
            seconds=cc_pair.connector.refresh_freq
        )

    start_latency = (recent_attempt.time_started - desired_start_time).total_seconds()

    task_logger.info(
        f"Start latency for index attempt {recent_attempt.id}: {start_latency:.2f}s "
        f"(desired: {desired_start_time}, actual: {recent_attempt.time_started})"
    )
    return Metric(
        key=metric_key,
        name="connector_start_latency",
        value=start_latency,
        tags={},
    )


def _build_run_success_metrics(
    cc_pair: ConnectorCredentialPair,
    recent_attempts: list[IndexAttempt],
    redis_std: Redis,
) -> list[Metric]:
    metrics = []
    for attempt in recent_attempts:
        metric_key = _CONNECTOR_INDEX_ATTEMPT_RUN_SUCCESS_KEY_FMT.format(
            cc_pair_id=cc_pair.id,
            index_attempt_id=attempt.id,
        )

        if _has_metric_been_emitted(redis_std, metric_key):
            task_logger.info(
                f"Skipping metric for connector {cc_pair.connector.id} "
                f"index attempt {attempt.id} because it has already been "
                "emitted"
            )
            continue

        if attempt.status in [
            IndexingStatus.SUCCESS,
            IndexingStatus.FAILED,
            IndexingStatus.CANCELED,
        ]:
            task_logger.info(
                f"Adding run success metric for index attempt {attempt.id} with status {attempt.status}"
            )
            metrics.append(
                Metric(
                    key=metric_key,
                    name="connector_run_succeeded",
                    value=attempt.status == IndexingStatus.SUCCESS,
                    tags={"source": str(cc_pair.connector.source)},
                )
            )

    return metrics


def _collect_connector_metrics(db_session: Session, redis_std: Redis) -> list[Metric]:
    """Collect metrics about connector runs from the past hour"""
    # NOTE: use get_db_current_time since the IndexAttempt times are set based on DB time
    one_hour_ago = get_db_current_time(db_session) - timedelta(hours=1)

    # Get all connector credential pairs
    cc_pairs = db_session.scalars(select(ConnectorCredentialPair)).all()

    active_search_settings = get_active_search_settings(db_session)
    metrics = []

    for cc_pair, search_settings in zip(cc_pairs, active_search_settings):
        recent_attempts = (
            db_session.query(IndexAttempt)
            .filter(
                IndexAttempt.connector_credential_pair_id == cc_pair.id,
                IndexAttempt.search_settings_id == search_settings.id,
            )
            .order_by(IndexAttempt.time_created.desc())
            .limit(2)
            .all()
        )
        if not recent_attempts:
            continue

        most_recent_attempt = recent_attempts[0]
        second_most_recent_attempt = (
            recent_attempts[1] if len(recent_attempts) > 1 else None
        )

        if one_hour_ago > most_recent_attempt.time_created:
            continue

        # Connector start latency
        start_latency_metric = _build_connector_start_latency_metric(
            cc_pair, most_recent_attempt, second_most_recent_attempt, redis_std
        )
        if start_latency_metric:
            metrics.append(start_latency_metric)

        # Connector run success/failure
        run_success_metrics = _build_run_success_metrics(
            cc_pair, recent_attempts, redis_std
        )
        metrics.extend(run_success_metrics)

    return metrics


def _collect_sync_metrics(db_session: Session, redis_std: Redis) -> list[Metric]:
    """Collect metrics about document set and group syncing speed"""
    # NOTE: use get_db_current_time since the SyncRecord times are set based on DB time
    one_hour_ago = get_db_current_time(db_session) - timedelta(hours=1)

    # Get all sync records from the last hour
    recent_sync_records = db_session.scalars(
        select(SyncRecord)
        .where(SyncRecord.sync_start_time >= one_hour_ago)
        .order_by(SyncRecord.sync_start_time.desc())
    ).all()

    metrics = []
    for sync_record in recent_sync_records:
        # Skip if no end time (sync still in progress)
        if not sync_record.sync_end_time:
            continue

        # Check if we already emitted a metric for this sync record
        metric_key = (
            f"sync_speed:{sync_record.sync_type}:"
            f"{sync_record.entity_id}:{sync_record.id}"
        )
        if _has_metric_been_emitted(redis_std, metric_key):
            task_logger.info(
                f"Skipping metric for sync record {sync_record.id} "
                "because it has already been emitted"
            )
            continue

        # Calculate sync duration in minutes
        sync_duration_mins = (
            sync_record.sync_end_time - sync_record.sync_start_time
        ).total_seconds() / 60.0

        # Calculate sync speed (docs/min) - avoid division by zero
        sync_speed = (
            sync_record.num_docs_synced / sync_duration_mins
            if sync_duration_mins > 0
            else None
        )

        if sync_speed is None:
            task_logger.error(
                f"Something went wrong with sync speed calculation. "
                f"Sync record: {sync_record.id}, duration: {sync_duration_mins}, "
                f"docs synced: {sync_record.num_docs_synced}"
            )
            continue

        task_logger.info(
            f"Calculated sync speed for record {sync_record.id}: {sync_speed} docs/min"
        )
        metrics.append(
            Metric(
                key=metric_key,
                name="sync_speed_docs_per_min",
                value=sync_speed,
                tags={
                    "sync_type": str(sync_record.sync_type),
                    "status": str(sync_record.sync_status),
                },
            )
        )

        # Add sync start latency metric
        start_latency_key = (
            f"sync_start_latency:{sync_record.sync_type}"
            f":{sync_record.entity_id}:{sync_record.id}"
        )
        if _has_metric_been_emitted(redis_std, start_latency_key):
            task_logger.info(
                f"Skipping start latency metric for sync record {sync_record.id} "
                "because it has already been emitted"
            )
            continue

        # Get the entity's last update time based on sync type
        entity: DocumentSet | UserGroup | None = None
        if sync_record.sync_type == SyncType.DOCUMENT_SET:
            entity = db_session.scalar(
                select(DocumentSet).where(DocumentSet.id == sync_record.entity_id)
            )
        elif sync_record.sync_type == SyncType.USER_GROUP:
            entity = db_session.scalar(
                select(UserGroup).where(UserGroup.id == sync_record.entity_id)
            )
        else:
            # Skip other sync types
            task_logger.info(
                f"Skipping sync record {sync_record.id} "
                f"with type {sync_record.sync_type} "
                f"and id {sync_record.entity_id} "
                "because it is not a document set or user group"
            )
            continue

        if entity is None:
            task_logger.error(
                f"Could not find entity for sync record {sync_record.id} "
                f"with type {sync_record.sync_type} and id {sync_record.entity_id}"
            )
            continue

        # Calculate start latency in seconds
        start_latency = (
            sync_record.sync_start_time - entity.time_last_modified_by_user
        ).total_seconds()
        task_logger.info(
            f"Calculated start latency for sync record {sync_record.id}: {start_latency} seconds"
        )
        if start_latency < 0:
            task_logger.error(
                f"Start latency is negative for sync record {sync_record.id} "
                f"with type {sync_record.sync_type} and id {sync_record.entity_id}. "
                f"Sync start time: {sync_record.sync_start_time}, "
                f"Entity last modified: {entity.time_last_modified_by_user}"
            )
            continue

        metrics.append(
            Metric(
                key=start_latency_key,
                name="sync_start_latency_seconds",
                value=start_latency,
                tags={
                    "sync_type": str(sync_record.sync_type),
                },
            )
        )

    return metrics


@shared_task(
    name=OnyxCeleryTask.MONITOR_BACKGROUND_PROCESSES,
    soft_time_limit=_MONITORING_SOFT_TIME_LIMIT,
    time_limit=_MONITORING_TIME_LIMIT,
    queue=OnyxCeleryQueues.MONITORING,
    bind=True,
)
def monitor_background_processes(self: Task, *, tenant_id: str | None) -> None:
    """Collect and emit metrics about background processes.
    This task runs periodically to gather metrics about:
    - Queue lengths for different Celery queues
    - Connector run metrics (start latency, success rate)
    - Syncing speed metrics
    - Worker status and task counts
    """
    task_logger.info("Starting background monitoring")
    r = get_redis_client(tenant_id=tenant_id)

    lock_monitoring: RedisLock = r.lock(
        OnyxRedisLocks.MONITOR_BACKGROUND_PROCESSES_LOCK,
        timeout=_MONITORING_SOFT_TIME_LIMIT,
    )

    # these tasks should never overlap
    if not lock_monitoring.acquire(blocking=False):
        task_logger.info("Skipping monitoring task because it is already running")
        return None

    try:
        # Get Redis client for Celery broker
        redis_celery = self.app.broker_connection().channel().client  # type: ignore
        redis_std = get_redis_client(tenant_id=tenant_id)

        # Define metric collection functions and their dependencies
        metric_functions: list[Callable[[], list[Metric]]] = [
            lambda: _collect_queue_metrics(redis_celery),
            lambda: _collect_connector_metrics(db_session, redis_std),
            lambda: _collect_sync_metrics(db_session, redis_std),
        ]
        # Collect and log each metric
        with get_session_with_tenant(tenant_id) as db_session:
            for metric_fn in metric_functions:
                metrics = metric_fn()
                for metric in metrics:
                    metric.log()
                    metric.emit(tenant_id)
                    if metric.key:
                        _mark_metric_as_emitted(redis_std, metric.key)

        task_logger.info("Successfully collected background metrics")
    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception as e:
        task_logger.exception("Error collecting background process metrics")
        raise e
    finally:
        if lock_monitoring.owned():
            lock_monitoring.release()

        task_logger.info("Background monitoring task finished")


@shared_task(
    name=OnyxCeleryTask.CLOUD_CHECK_ALEMBIC,
)
def cloud_check_alembic() -> bool | None:
    """A task to verify that all tenants are on the same alembic revision.

    This check is expected to fail if a cloud alembic migration is currently running
    across all tenants.

    TODO: have the cloud migration script set an activity signal that this check
    uses to know it doesn't make sense to run a check at the present time.
    """
    time_start = time.monotonic()

    redis_client = get_redis_client(tenant_id=ONYX_CLOUD_TENANT_ID)

    lock_beat: RedisLock = redis_client.lock(
        OnyxRedisLocks.CLOUD_CHECK_ALEMBIC_BEAT_LOCK,
        timeout=CELERY_GENERIC_BEAT_LOCK_TIMEOUT,
    )

    # these tasks should never overlap
    if not lock_beat.acquire(blocking=False):
        return None

    last_lock_time = time.monotonic()

    tenant_to_revision: dict[str, str | None] = {}
    revision_counts: dict[str, int] = {}
    out_of_date_tenants: dict[str, str | None] = {}
    top_revision: str = ""

    try:
        # map each tenant_id to its revision
        tenant_ids = get_all_tenant_ids()
        for tenant_id in tenant_ids:
            current_time = time.monotonic()
            if current_time - last_lock_time >= (CELERY_GENERIC_BEAT_LOCK_TIMEOUT / 4):
                lock_beat.reacquire()
                last_lock_time = current_time

            if tenant_id is None:
                continue

            with get_session_with_tenant(tenant_id=None) as session:
                result = session.execute(
                    text(f'SELECT * FROM "{tenant_id}".alembic_version LIMIT 1')
                )

                result_scalar: str | None = result.scalar_one_or_none()
                tenant_to_revision[tenant_id] = result_scalar

        # get the total count of each revision
        for k, v in tenant_to_revision.items():
            if v is None:
                continue

            revision_counts[v] = revision_counts.get(v, 0) + 1

        # get the revision with the most counts
        sorted_revision_counts = sorted(
            revision_counts.items(), key=lambda item: item[1], reverse=True
        )

        if len(sorted_revision_counts) == 0:
            task_logger.error(
                f"cloud_check_alembic - No revisions found for {len(tenant_ids)} tenant ids!"
            )
        else:
            top_revision, _ = sorted_revision_counts[0]

            # build a list of out of date tenants
            for k, v in tenant_to_revision.items():
                if v == top_revision:
                    continue

                out_of_date_tenants[k] = v

    except SoftTimeLimitExceeded:
        task_logger.info(
            "Soft time limit exceeded, task is being terminated gracefully."
        )
    except Exception:
        task_logger.exception("Unexpected exception during cloud alembic check")
        raise
    finally:
        if lock_beat.owned():
            lock_beat.release()
        else:
            task_logger.error("cloud_check_alembic - Lock not owned on completion")
            redis_lock_dump(lock_beat, redis_client)

    if len(out_of_date_tenants) > 0:
        task_logger.error(
            f"Found out of date tenants: "
            f"num_out_of_date_tenants={len(out_of_date_tenants)} "
            f"num_tenants={len(tenant_ids)} "
            f"revision={top_revision}"
        )
        for k, v in islice(out_of_date_tenants.items(), 5):
            task_logger.info(f"Out of date tenant: tenant={k} revision={v}")
    else:
        task_logger.info(
            f"All tenants are up to date: num_tenants={len(tenant_ids)} revision={top_revision}"
        )

    time_elapsed = time.monotonic() - time_start
    task_logger.info(
        f"cloud_check_alembic finished: num_tenants={len(tenant_ids)} elapsed={time_elapsed:.2f}"
    )
    return True
