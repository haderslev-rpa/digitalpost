from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from automation_server_client import WorkItemError
from q_haderslev_vbo.automation_server.ats_update_item_data import update_item_data

from ats_helpers import defer_item
from configuration import MAX_RETRY_ATTEMPTS
from integrations import build_document, download_document, submit_automatic, submit_only_digital

logger = logging.getLogger(__name__)


class States:
    DOCUMENTS_DOWNLOADED = "2.0 Dokumenter hentet"
    SUBMITTING = "3.0 Afsendelse startet"
    SUBMITTED = "4.0 Indsendt til Serviceplatformen"
    WAITING_FOR_RECEIPT = "5.0 Afventer positiv kvittering"


class PermanentItemError(WorkItemError):
    """Kødata er ugyldige og bør ikke prøves igen."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def box_for(item: Any) -> dict[str, Any]:
    data = item.data
    box = data.get("box") if isinstance(data, dict) else None
    if not isinstance(box, dict):
        raise PermanentItemError("item.data['box'] mangler eller er ugyldig")
    return box


def has_state(data: dict[str, Any], state: str) -> bool:
    return any(state in str(entry) for entry in data.get("state", []))


def save(item: Any, state: str | None = None, **updates: Any) -> None:
    box_for(item).update(updates)
    update_item_data(item.data, item=item, state=state)
    item.update(item.data)


def validate_box(box: dict[str, Any]) -> None:
    required = ("forsendelses_id", "recipient", "subject", "delivery_method", "documents")
    missing = [key for key in required if not box.get(key)]
    if missing:
        raise PermanentItemError("Manglende felt(er): " + ", ".join(missing))
    recipient = box["recipient"]
    if not isinstance(recipient, dict) or not recipient.get("id") or not recipient.get("id_type"):
        raise PermanentItemError("recipient skal indeholde id og id_type")
    if box["delivery_method"] not in {"ONLY_DIGITAL_POST", "DIGITAL_OR_PHYSICAL_POST"}:
        raise PermanentItemError(f"Ukendt delivery_method: {box['delivery_method']}")
    if box["delivery_method"] == "DIGITAL_OR_PHYSICAL_POST" and not isinstance(box.get("address"), dict):
        raise PermanentItemError("address kræves ved DIGITAL_OR_PHYSICAL_POST")
    documents = box["documents"]
    if not isinstance(documents, list) or len([d for d in documents if d.get("role") == "MAIN"]) != 1:
        raise PermanentItemError("documents skal indeholde præcis ét MAIN-dokument")
    for document in documents:
        for key in ("dokument_id", "file_name", "role", "drive_item_id"):
            if not document.get(key):
                raise PermanentItemError(f"Dokument mangler {key}")


def result_value(result: Any, *names: str) -> Any:
    for name in names:
        value = result.get(name) if isinstance(result, dict) else getattr(result, name, None)
        if value is not None:
            return value
    return None


async def load_documents(box: dict[str, Any]) -> tuple[Any, list[Any]]:
    built = []
    for metadata in box["documents"]:
        content = await download_document(metadata)
        built.append((metadata["role"], build_document(content, metadata)))
    main_document = next(document for role, document in built if role == "MAIN")
    attachments = [document for role, document in built if role == "ATTACHMENT"]
    return main_document, attachments


async def behandel_item(item: Any) -> None:
    """Indsend ét item og defer det. Output er ingen returværdi.

    Itemet completes aldrig her. En fremtidig kvitteringsproces skal complete det,
    når Serviceplatformens endelige positive svar er modtaget.
    """
    box = box_for(item)
    box["processing_attempts"] = int(box.get("processing_attempts", 0)) + 1
    box["last_attempt_at"] = utc_now()
    save(item)

    try:
        validate_box(box)

        already_submitted = has_state(item.data, States.SUBMITTED) or box.get("processing_state") in {
            "SUBMITTED", "WAITING_FOR_RECEIPT"
        }
        if not already_submitted:
            main_document, attachments = await load_documents(box)
            save(item, States.DOCUMENTS_DOWNLOADED, processing_state="DOCUMENTS_DOWNLOADED")
            save(item, States.SUBMITTING, processing_state="SUBMITTING")

            if box["delivery_method"] == "ONLY_DIGITAL_POST":
                result = await submit_only_digital(box, main_document, attachments)
            else:
                result = await submit_automatic(box, main_document, attachments)

            transaction_id = result_value(result, "serviceplatform_transaction_id", "transaction_id", "submission_id")
            message_uuid = result_value(result, "memo_message_uuid", "message_uuid", "digital_post_id")
            if not transaction_id or not message_uuid:
                raise RuntimeError("Serviceplatformens svar mangler transaction-id eller messageUUID")
            save(
                item,
                States.SUBMITTED,
                processing_state="SUBMITTED",
                serviceplatform_submission_id=str(transaction_id),
                digital_post_id=str(message_uuid),
                submitted_at=utc_now(),
                last_error=None,
            )

        save(
            item,
            States.WAITING_FOR_RECEIPT,
            processing_state="WAITING_FOR_RECEIPT",
            deferred_at=utc_now(),
            status_code="Afventer positiv kvittering",
        )
        defer_item(item, "Afsendt; afventer positiv kvittering fra Serviceplatformen")

    except PermanentItemError as exc:
        save(item, processing_state="FAILED", last_error=str(exc), failed_at=utc_now())
        item.fail(str(exc))
    except Exception as exc:
        attempts = int(box.get("processing_attempts", 1))
        save(item, processing_state="RETRY", last_error=repr(exc))
        logger.exception("Teknisk fejl for item %s, forsøg %s", item.reference, attempts)
        if attempts >= MAX_RETRY_ATTEMPTS:
            save(item, processing_state="FAILED", failed_at=utc_now())
            item.fail(f"Maksimalt antal forsøg nået: {exc}")
            return
        raise
