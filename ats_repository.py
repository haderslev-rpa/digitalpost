"""Adapter mellem Digital Post-processen og Automation Server.

Filen bruger de generelle ATS-funktioner i q-haderslev-vbo:

- get_one_workitem_by_box_value() til kvitteringsmatchning
- get_completed_items_in_period() til cleanup-kandidater
- get_workitem_object_by_database_row() til at hente WorkItem-objektet

Databasen bruges kun til læsning. Alle ændringer foretages gennem
Automation Server-klientens WorkItem-objekter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from configuration import DIGITAL_POST_QUEUE_ID

from q_haderslev_vbo.automation_server.ats_find_items_by_box import (
    get_one_workitem_by_box_value,
)
from q_haderslev_vbo.automation_server.ats_queue_status import (
    get_completed_items_in_period,
)
from q_haderslev_vbo.automation_server.ats_workitem_api import (
    get_workitem_object_by_database_row,
)


# Kvitteringer forventes normalt på items, som afventer brugerhandling.
# FAILED og COMPLETED medtages, så forsinkede eller dublerede kvitteringer
# fortsat kan kobles til det rigtige item.
RECEIPT_MATCH_STATUSES = (
    "PENDING_USER_ACTION",
    "FAILED",
    "COMPLETED",
    "IN_PROGRESS",
)

# Cleanup behøver en nedre periodegrænse, fordi den generelle ATS-funktion
# arbejder med et datointerval. Digital Post-processen er nyere end år 2000.
CLEANUP_EARLIEST_DATETIME = "2000-01-01T00:00:00Z"


_LOOKUP_FIELDS = (
    (
        "memo_message_uuid",
        (
            "memo_message_uuid",
            "digital_post_id",
            "submissions[].memo_message_uuid",
        ),
    ),
    (
        "serviceplatform_transaction_id",
        (
            "serviceplatform_transaction_id",
            "serviceplatform_submission_id",
            "submissions[].serviceplatform_transaction_id",
        ),
    ),
    (
        "physical_shipment_id",
        (
            "physical_shipment_id",
            "submissions[].physical_shipment_id",
        ),
    ),
    (
        "correlation_id",
        (
            "correlation_id",
            "submissions[].correlation_id",
        ),
    ),
)


def find_matching_work_item(
    receipt: Any,
) -> Any | None:
    """Find det ATS-item, som hører til en kvittering.

    Input:
        receipt:
            Den fortolkede kvittering fra q-serviceplatformen.

    Output:
        Et WorkItem-objekt, hvis præcis ét ATS-item matcher.

        None, hvis ingen af kvitteringens identifikatorer findes i køen.

    Fejl:
        RuntimeError, hvis identifikatorerne peger på flere forskellige
        ATS-items. Processen vælger aldrig automatisk mellem flere items.
    """
    matching_rows_by_id: dict[int, dict[str, Any]] = {}

    for receipt_attribute, box_paths in _LOOKUP_FIELDS:
        value = getattr(
            receipt,
            receipt_attribute,
            None,
        )

        if value is None:
            continue

        normalized_value = str(value).strip()

        if not normalized_value:
            continue

        for box_path in box_paths:
            database_row = get_one_workitem_by_box_value(
                queue_id=DIGITAL_POST_QUEUE_ID,
                box_path=box_path,
                value=normalized_value,
                statuses=RECEIPT_MATCH_STATUSES,
            )

            if database_row is None:
                continue

            item_id = int(database_row["id"])
            matching_rows_by_id[item_id] = database_row

    if not matching_rows_by_id:
        return None

    if len(matching_rows_by_id) > 1:
        matching_item_ids = sorted(
            matching_rows_by_id
        )

        raise RuntimeError(
            "Kvitteringens identifikatorer matcher flere forskellige "
            f"ATS-items: {matching_item_ids}. Kvitteringen er ikke "
            "behandlet automatisk."
        )

    database_row = next(
        iter(matching_rows_by_id.values())
    )

    return get_workitem_object_by_database_row(
        database_row
    )


def get_cleanup_candidates(
    days: int,
    limit: int = 250,
) -> list[Any]:
    """Find completed Digital Post-items til SharePoint-cleanup.

    Input:
        days:
            Items skal være ældre end dette antal dage.

        limit:
            Maksimalt antal WorkItem-objekter, som returneres.

    Output:
        En liste med WorkItem-objekter, som:

        - tilhører Digital Post-køen
        - har ATS-status COMPLETED
        - er oprettet før aldersgrænsen
        - har processing_state DELIVERED

        Listen er tom, hvis ingen items matcher.

    Vigtigt:
        Funktionen ændrer ingen ATS-items og sletter ingen filer.
    """
    validated_days = _validate_positive_int(
        days,
        "days",
    )
    validated_limit = _validate_positive_int(
        limit,
        "limit",
    )

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=validated_days)
    )

    result = get_completed_items_in_period(
        queue_id=DIGITAL_POST_QUEUE_ID,
        start_datetime=CLEANUP_EARLIEST_DATETIME,
        end_datetime=cutoff.isoformat(),
        updated_at=False,
    )

    candidate_rows = []

    for database_row in result["items"]:
        data = database_row.get("data") or {}
        box = (
            data.get("box", {})
            if isinstance(data, dict)
            else {}
        )

        if not isinstance(box, dict):
            continue

        if box.get("processing_state") != "DELIVERED":
            continue

        candidate_rows.append(database_row)

        if len(candidate_rows) >= validated_limit:
            break

    return [
        get_workitem_object_by_database_row(
            database_row
        )
        for database_row in candidate_rows
    ]


def _validate_positive_int(
    value: int | str,
    field_name: str,
) -> int:
    """Returnér et valideret positivt helt tal.

    Output:
        Inputværdien som int, når værdien er større end nul.
    """
    if value is None or isinstance(value, bool):
        raise ValueError(
            f"{field_name} skal være et positivt helt tal."
        )

    try:
        validated_value = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{field_name} skal være et helt tal."
        ) from error

    if validated_value <= 0:
        raise ValueError(
            f"{field_name} skal være større end 0."
        )

    return validated_value


