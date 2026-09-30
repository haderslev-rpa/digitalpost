"""Behandling af kvitteringer fra KOMBIT Beskedfordeleren."""
from __future__ import annotations

import logging
from typing import Any

from automation_server_client import WorkItemStatus
from q_outlook_api.functionality.mail_api import send_mail
from q_serviceplatformen.message_broker import MessageBroker

from ats_repository import find_matching_work_item
from configuration import (
    MAIL_RECIPIENT,
    MAIL_SENDER,
    SEND_UNMATCHED_RECEIPT_MAIL,
    validate_mail_configuration,
)
from danish_time import DanishTime
from models import PollResult, ProcessingState

logger = logging.getLogger(__name__)


def _format_mail_value(value: Any) -> str:
    """Returnér en læsbar værdi til sporingsmail."""
    if value is None:
        return "Ikke oplyst"
    if hasattr(value, "isoformat"):
        return DanishTime.iso(value)
    return str(value)


def send_unmatched_receipt_mail(
    receipt: Any,
    raw_xml: bytes,
    metadata: dict[str, Any],
) -> None:
    """Log en umatchet kvittering og send eventuelt en mail.

    Input:
        receipt:
            Den fortolkede kvittering fra q-serviceplatformen.

        raw_xml:
            Den oprindelige XML-besked som bytes.

            Parameteren bevares, fordi funktionen kaldes med XML-data
            fra Dueslaget. XML-filen vedhæftes ikke mailen.

        metadata:
            Brokerens metadata, herunder routing key.

    Output:
        Funktionen returnerer None.

        Kvitteringen logges altid.

        Der sendes kun mail, når:

            SEND_UNMATCHED_RECEIPT_MAIL = True

        i configuration.py.
    """
    logger.warning(
        "Umatchet kvittering: status=%r, MessageUUID=%r, "
        "transaction_id=%r, broker_message_id=%r",
        getattr(receipt, "status", None),
        getattr(
            receipt,
            "memo_message_uuid",
            None,
        ),
        getattr(
            receipt,
            "serviceplatform_transaction_id",
            None,
        ),
        getattr(
            receipt,
            "broker_message_id",
            None,
        ),
    )

    if not SEND_UNMATCHED_RECEIPT_MAIL:
        logger.info(
            "Mail om umatchet kvittering er slået fra "
            "i configuration.py."
        )
        return

    validate_mail_configuration()

    mail_body = (
        "En kvittering fra KOMBIT Beskedfordeleren kunne ikke "
        "matches med et ATS-item.\n\n"
        f"Status: "
        f"{_format_mail_value(getattr(receipt, 'status', None))}\n"
        f"MessageUUID: "
        f"{_format_mail_value(getattr(receipt, 'memo_message_uuid', None))}\n"
        "Transaction ID: "
        f"{_format_mail_value(getattr(receipt, 'serviceplatform_transaction_id', None))}\n"
        "Broker message ID: "
        f"{_format_mail_value(getattr(receipt, 'broker_message_id', None))}\n"
        "Routing key: "
        f"{_format_mail_value(metadata.get('routing_key'))}\n"
    )

    mail = {
        "message": {
            "subject": (
                "Digital Post: Umatchet kvittering"
            ),
            "body": {
                "contentType": "Text",
                "content": mail_body,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": MAIL_RECIPIENT,
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    send_mail(
        MAIL_SENDER,
        mail,
    )


def _submission_matches_receipt(
    submission: dict[str, Any], receipt: Any
) -> bool:
    """Returnér True, når mindst ét afsendelses-ID matcher kvitteringen."""
    comparisons = (
        (submission.get("memo_message_uuid"), getattr(receipt, "memo_message_uuid", None)),
        (
            submission.get("serviceplatform_transaction_id"),
            getattr(receipt, "serviceplatform_transaction_id", None),
        ),
        (
            submission.get("physical_shipment_id"),
            getattr(receipt, "physical_shipment_id", None),
        ),
    )
    return any(
        left is not None
        and right is not None
        and str(left).strip().casefold() == str(right).strip().casefold()
        for left, right in comparisons
    )


def _matching_submission(
    box: dict[str, Any], receipt: Any
) -> dict[str, Any] | None:
    """Returnér den submission, som kvitteringen vedrører, eller None."""
    submissions = box.get("submissions", [])
    if not isinstance(submissions, list):
        return None
    return next(
        (
            submission
            for submission in submissions
            if isinstance(submission, dict)
            and _submission_matches_receipt(submission, receipt)
        ),
        None,
    )


def _already_processed(item: Any, receipt: Any) -> bool:
    """Returnér True, når itemet allerede er endeligt afsluttet."""
    box = item.data.get("box", {})
    if not isinstance(box, dict):
        return False
    state = getattr(box.get("processing_state"), "value", box.get("processing_state"))
    normalized = str(state or "").strip().upper().replace(" ", "_")
    return normalized in {"DELIVERED", "FAILED"} and bool(
        getattr(receipt, "is_final", False)
    )


def _mark_submission_result(box: dict[str, Any], receipt: Any) -> None:
    """Gem et endeligt kvitteringsresultat på den relevante submission."""
    submission = _matching_submission(box, receipt)
    if submission is None or not receipt.is_final:
        return
    submission["final_result"] = receipt.status
    submission["final_result_at"] = DanishTime.iso(receipt.received_at)


def _refresh_submission_counters(box: dict[str, Any]) -> None:
    """Opdatér samlet antal afsendelser og manuelle genforsendelser."""
    submissions = box.get("submissions", [])
    if not isinstance(submissions, list):
        submissions = []
        box["submissions"] = submissions
    box["submission_count"] = len(submissions)
    box["manual_resend_count"] = sum(
        1
        for submission in submissions
        if isinstance(submission, dict)
        and submission.get("submission_type") == "MANUAL_RESEND"
    )
    if submissions and isinstance(submissions[-1], dict):
        box["last_submission_type"] = submissions[-1].get("submission_type")


def _remove_finished_fields(box: dict[str, Any]) -> None:
    """Fjern midlertidige dubletfelter efter en endelig kvittering."""
    for field_name in (
        "last_error",
        "receipt_error_code",
        "receipt_status_message",
        "manual_resend_requested",
        "requested_submission_type",
        "requested_delivery_channel",
        "serviceplatform_submission_id",
        "digital_post_id",
        "memo_message_uuid",
        "serviceplatform_transaction_id",
        "physical_shipment_id",
        "submitted_at",
        "receipt_deadline_at",
    ):
        box.pop(field_name, None)


def update_item_from_receipt(
    item: Any,
    receipt: Any,
) -> str:
    """Opdatér itemet ud fra en modtaget kvittering.

    Output:
        "PENDING":
            Kvitteringen er foreløbig, og itemet venter fortsat.

        "COMPLETED":
            Forsendelsen er endeligt leveret.

        "FAILED":
            Forsendelsen har modtaget en endelig negativ kvittering.

    Standard:
        item.data["defer"] bevares altid.

        Ved PENDING indeholder defer fortsat fristen.

        Ved COMPLETED eller FAILED sættes defer til None,
        hvilket bliver til null i JSON.
    """
    box = item.data.get(
        "box",
        {},
    )

    if not isinstance(
        box,
        dict,
    ):
        raise RuntimeError(
            "Itemet mangler en gyldig box."
        )

    _mark_submission_result(
        box,
        receipt,
    )

    _refresh_submission_counters(
        box
    )

    box.update(
        {
            "receipt_status": (
                receipt.status
            ),
            "receipt_status_code": (
                receipt.status_code
            ),
            "receipt_received_at": DanishTime.iso(
                receipt.received_at
            ),
            "actual_delivery": DanishTime.iso_or_none(
                receipt.actual_delivery
            ),
            "broker_message_id": (
                receipt.broker_message_id
            ),
        }
    )

    if (
        receipt.status == "PENDING"
        and not receipt.is_final
    ):
        box["processing_state"] = (
            ProcessingState.WAITING_FOR_RECEIPT
        )

        status_message = (
            receipt.status_message
            or receipt.status_code
            or "Afventer endelig kvittering fra SP"
        )

        item.data["status"] = {
            "status": "Pending User Action",
            "status_code": (
                status_message
            ),
        }

        item.update(
            item.data
        )

        item.update_status(
            WorkItemStatus.PENDING_USER_ACTION.value,
            status_message,
        )

        return "PENDING"

    if (
        receipt.status == "DELIVERED"
        and receipt.is_final
        and receipt.is_success
    ):
        box["processing_state"] = (
            ProcessingState.DELIVERED
        )

        box["completed_at"] = (
            DanishTime.now().isoformat()
        )

        _remove_finished_fields(
            box
        )

        # Behold defer-feltet, men nulstil værdien.
        item.data["defer"] = None

        status_message = (
            receipt.status_message
            or receipt.status_code
            or "Forsendelsen er leveret"
        )

        item.data["status"] = {
            "status": "Completed",
            "status_code": (
                status_message
            ),
        }

        item.update(
            item.data
        )

        item.complete(
            status_message
        )

        return "COMPLETED"

    error_message = (
        receipt.status_message
        or receipt.status_code
        or receipt.error_code
        or (
            "Forsendelsen modtog en endelig "
            "negativ kvittering"
        )
    )

    # Din ProcessingState-model har ikke nødvendigvis FAILED.
    box["processing_state"] = (
        "FAILED"
    )

    box["last_error"] = (
        error_message
    )

    box["completed_at"] = (
        DanishTime.now().isoformat()
    )

    # Behold defer-feltet, men nulstil værdien.
    item.data["defer"] = None

    item.data["status"] = {
        "status": "Failed",
        "status_code": (
            error_message
        ),
    }

    item.update(
        item.data
    )

    item.fail(
        error_message
    )

    return "FAILED"


def drain_receipt_queue() -> PollResult:
    """Tøm Dueslaget én gang og returnér tællinger i PollResult."""
    result = PollResult()
    with MessageBroker() as broker:
        while True:
            message = broker.get_message()
            if message is None:
                return result
            result.messages += 1
            try:
                receipt = message.parse_receipt()
                item = find_matching_work_item(receipt)
                if item is None:
                    send_unmatched_receipt_mail(receipt, message.body, message.metadata)
                    message.ack()
                    result.unmatched += 1
                    continue
                if _already_processed(item, receipt):
                    message.ack()
                    result.matched += 1
                    continue
                outcome = update_item_from_receipt(item, receipt)
                message.ack()
                result.matched += 1
                if outcome == "COMPLETED":
                    result.completed += 1
                elif outcome == "FAILED":
                    result.failed += 1
                else:
                    result.pending += 1
            except Exception:
                message.requeue()
                logger.exception("Beskeden blev lagt tilbage i Dueslaget.")
                raise
