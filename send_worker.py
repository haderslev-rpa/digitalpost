"""Afsendelsesworker til den centrale Digital Post-proces.

Ansvarsfordeling:
    q-digitalpost:
        Validerer bestillingen, uploader dokumenter til SharePoint og
        opretter ATS-itemet med processing_state=READY_TO_SEND.

    Denne worker:
        Henter dokumenterne, vælger Digital Post eller fysisk post,
        kalder q-serviceplatformen og sætter ATS-itemet til
        PENDING_USER_ACTION, mens den afventer kvittering.

    q-serviceplatformen:
        Udfører registreringsopslag og den faktiske afsendelse.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from ats_helpers import defer_item

from danish_time import DanishTime
from automation_server_client import (
    WorkItemError,
    WorkItemStatus,
)

from q_serviceplatformen.functionality.post_models import (
    PostDocument,
)
from q_serviceplatformen.functionality.registration import (
    check_registration,
)
from q_serviceplatformen.functionality.send_digital_post import (
    send_digital_post,
)
from q_serviceplatformen.functionality.send_physical_post import (
    send_physical_post,
)


from configuration import RECEIPT_DEADLINE_DAYS
from models import ProcessingState
from sharepoint_service import download_pdf


logger = logging.getLogger(__name__)

SubmissionType = Literal[
    "INITIAL",
    "MANUAL_RESEND",
]

DeliveryChannel = Literal[
    "DIGITAL_POST",
    "PHYSICAL_POST",
]


class PermanentItemError(WorkItemError):
    """Itemets data kan ikke behandles automatisk."""


def _box(item: Any) -> dict[str, Any]:
    """Returnér itemets box.

    Output:
        Dictionaryen item.data["box"].

    Fejl:
        PermanentItemError, hvis item.data eller box mangler.
    """
    if not isinstance(getattr(item, "data", None), dict):
        raise PermanentItemError(
            "Itemets data skal være en dictionary."
        )

    box = item.data.get("box")

    if not isinstance(box, dict):
        raise PermanentItemError(
            "Itemet mangler item.data['box']."
        )

    return box


def _validate_box(box: dict[str, Any]) -> None:
    """Kontrollér de felter, som afsendelsesworkeren kræver.

    Output:
        Funktionen returnerer None, når box er gyldig.

    Fejl:
        PermanentItemError ved manglende eller ugyldige kødata.
    """
    required_fields = (
        "forsendelses_id",
        "recipient",
        "subject",
        "delivery_method",
        "documents",
        "processing_state",
    )

    missing_fields = [
        field_name
        for field_name in required_fields
        if not box.get(field_name)
    ]

    if missing_fields:
        raise PermanentItemError(
            "Itemets box mangler felt(er): "
            + ", ".join(missing_fields)
        )

    recipient = box["recipient"]

    if not isinstance(recipient, dict):
        raise PermanentItemError(
            "box['recipient'] skal være en dictionary."
        )

    if not recipient.get("id"):
        raise PermanentItemError(
            "box['recipient']['id'] mangler."
        )

    if recipient.get("id_type") not in {"CPR", "CVR"}:
        raise PermanentItemError(
            "box['recipient']['id_type'] skal være CPR eller CVR."
        )

    if box["delivery_method"] not in {
        "ONLY_DIGITAL_POST",
        "DIGITAL_OR_PHYSICAL_POST",
    }:
        raise PermanentItemError(
            "Ukendt delivery_method: "
            f"{box['delivery_method']!r}."
        )

    documents = box["documents"]

    if not isinstance(documents, list) or not documents:
        raise PermanentItemError(
            "box['documents'] skal være en ikke-tom liste."
        )

    main_documents = [
        document
        for document in documents
        if document.get("role") == "MAIN"
    ]

    if len(main_documents) != 1:
        raise PermanentItemError(
            "Itemet skal indeholde præcis ét MAIN-dokument."
        )

    for document in documents:
        if not isinstance(document, dict):
            raise PermanentItemError(
                "Alle dokumentmetadata skal være dictionaries."
            )

        for field_name in (
            "dokument_id",
            "file_name",
            "role",
            "drive_item_id",
        ):
            if not document.get(field_name):
                raise PermanentItemError(
                    "Dokumentmetadata mangler feltet "
                    f"{field_name!r}."
                )

        if document["role"] not in {
            "MAIN",
            "ATTACHMENT",
        }:
            raise PermanentItemError(
                "Dokumentets role skal være MAIN eller ATTACHMENT."
            )

    if (
        box["delivery_method"]
        == "DIGITAL_OR_PHYSICAL_POST"
        and not isinstance(box.get("address"), dict)
    ):
        raise PermanentItemError(
            "DIGITAL_OR_PHYSICAL_POST kræver en adresse."
        )


def _save_item(item: Any) -> None:
    """Gem item.data i Automation Server og returnér None."""
    item.update(item.data)


async def _build_documents(
    box: dict[str, Any],
) -> tuple[PostDocument, list[PostDocument]]:
    """Hent alle PDF-filer og byg q-serviceplatformen-dokumenter.

    Output:
        Tuple med:
            1. Hoveddokumentet som PostDocument.
            2. En liste med bilag som PostDocument.
    """
    built_documents: list[
        tuple[str, PostDocument]
    ] = []

    for metadata in box["documents"]:
        content = await download_pdf(metadata)

        post_document = PostDocument(
            content=content,
            file_name=metadata["file_name"],
            document_id=metadata["dokument_id"],
            label=metadata["file_name"],
        )

        built_documents.append(
            (
                metadata["role"],
                post_document,
            )
        )

    main_document = next(
        document
        for role, document in built_documents
        if role == "MAIN"
    )

    attachments = [
        document
        for role, document in built_documents
        if role == "ATTACHMENT"
    ]

    return main_document, attachments


async def _choose_delivery_channel(
    box: dict[str, Any],
) -> DeliveryChannel:
    """Vælg den konkrete leveringskanal.

    Output:
        DIGITAL_POST:
            Ved ONLY_DIGITAL_POST eller når modtageren er registreret.

        PHYSICAL_POST:
            Ved DIGITAL_OR_PHYSICAL_POST, når modtageren ikke er
            registreret til Digital Post.

    Vigtigt:
        Tekniske fejl fra registreringsopslaget fortsætter som exceptions.
        En teknisk fejl bliver aldrig tolket som manglende registrering.
    """
    delivery_method = box["delivery_method"]

    if delivery_method == "ONLY_DIGITAL_POST":
        # q-digitalpost har allerede foretaget registreringsopslaget,
        # inden itemet blev oprettet. Workeren må kun sende digitalt.
        return "DIGITAL_POST"

    recipient = box["recipient"]

    is_registered = await asyncio.to_thread(
        check_registration,
        recipient_id=recipient["id"],
        recipient_id_type=recipient["id_type"],
        service="digitalpost",
    )

    if not isinstance(is_registered, bool):
        raise TypeError(
            "q-serviceplatformens check_registration() "
            "returnerede ikke True eller False."
        )

    if is_registered:
        return "DIGITAL_POST"

    return "PHYSICAL_POST"


async def _submit_digital_post(
    box: dict[str, Any],
    main_document: PostDocument,
    attachments: list[PostDocument],
) -> Any:
    """Send Digital Post og returnér DigitalPostSendResult."""
    recipient = box["recipient"]

    return await asyncio.to_thread(
        send_digital_post,
        recipient_id=recipient["id"],
        recipient_id_type=recipient["id_type"],
        subject=box["subject"],
        main_document=main_document,
        attachments=attachments,
        # Registreringsvalget er allerede udført af q-digitalpost eller
        # _choose_delivery_channel(). Undgå derfor et dobbelt opslag.
        check_registration=False,
    )


async def _submit_physical_post(
    box: dict[str, Any],
    main_document: PostDocument,
    attachments: list[PostDocument],
) -> Any:
    """Send fysisk post og returnér PhysicalPostSendResult.

    Fejl:
        PermanentItemError, hvis bestillingen har bilag. Den nuværende
        offentlige send_physical_post()-funktion understøtter ét dokument.
    """
    if attachments:
        raise PermanentItemError(
            "Fysisk post understøtter foreløbigt ikke bilag. "
            "Itemet har bilag og er derfor ikke sendt."
        )

    address = box["address"]

    required_address_fields = (
        "name",
        "street_name",
        "house_number",
        "postal_code",
        "city",
    )

    missing_address_fields = [
        field_name
        for field_name in required_address_fields
        if not address.get(field_name)
    ]

    if missing_address_fields:
        raise PermanentItemError(
            "Den fysiske adresse mangler felt(er): "
            + ", ".join(missing_address_fields)
        )

    return await asyncio.to_thread(
        send_physical_post,
        recipient_name=address["name"],
        street_name=address["street_name"],
        house_number=address["house_number"],
        post_code=address["postal_code"],
        city=address["city"],
        document=main_document,
        floor=address.get("floor") or None,
        door=address.get("door") or None,
        country_code=(
            address.get("country_code")
            or "DK"
        ),
    )


def _archive_active_submission(
    box: dict[str, Any],
) -> None:
    """Flyt den aktuelle afsendelsesidentifikation til historikken.

    Output:
        Funktionen returnerer None. Hvis der ikke findes en aktiv
        afsendelse, ændres box ikke.

    Formål:
        Beskytter historikken ved manuel genfremsendelse, så en forsinket
        kvittering fra en tidligere afsendelse stadig kan matches.
    """
    transaction_id = (
        box.get("serviceplatform_transaction_id")
        or box.get("serviceplatform_submission_id")
    )

    memo_message_uuid = (
        box.get("memo_message_uuid")
        or box.get("digital_post_id")
    )

    physical_shipment_id = box.get(
        "physical_shipment_id"
    )

    if not any(
        (
            transaction_id,
            memo_message_uuid,
            physical_shipment_id,
        )
    ):
        return

    submissions = box.setdefault(
        "submissions",
        [],
    )

    if not isinstance(submissions, list):
        raise PermanentItemError(
            "box['submissions'] skal være en liste."
        )

    # Undgå at indsætte samme aktive afsendelse to gange.
    for submission in submissions:
        if (
            transaction_id
            and submission.get(
                "serviceplatform_transaction_id"
            ) == transaction_id
        ):
            return

    submissions.append(
        {
            "submission_number": len(submissions) + 1,
            "submission_type": box.get(
                "last_submission_type",
                "INITIAL",
            ),
            "delivery_channel": box.get(
                "delivery_channel"
            ),
            "serviceplatform_transaction_id": (
                transaction_id
            ),
            "memo_message_uuid": memo_message_uuid,
            "physical_shipment_id": physical_shipment_id,
            "submitted_at": box.get("submitted_at"),
            "receipt_deadline_at": box.get(
                "receipt_deadline_at"
            ),
            "final_result": box.get(
                "receipt_status"
            ),
            "final_result_at": box.get(
                "receipt_received_at"
            ),
        }
    )

def _clean_waiting_item_data(
    box: dict[str, Any],
) -> None:
    """Fjern dubletter og tomme kvitteringsfelter efter afsendelse.

    Output:
        Funktionen returnerer None.

        box ændres direkte, så afsendelsesidentifikatorerne kun
        ligger i submissions-listen.

    Bevarer:
        - submissions
        - submission_count
        - manual_resend_count
        - last_submission_type
        - felter til manuel genforsendelse
    """
    duplicate_fields = (
        "submitted_at",
        "digital_post_id",
        "delivery_channel",
        "memo_message_uuid",
        "receipt_deadline_at",
        "physical_shipment_id",
        "serviceplatform_submission_id",
        "serviceplatform_transaction_id",
    )

    empty_receipt_fields = (
        "last_error",
        "completed_at",
        "receipt_status",
        "actual_delivery",
        "receipt_error_code",
        "receipt_received_at",
        "receipt_status_code",
        "receipt_status_message",
    )

    for field_name in duplicate_fields:
        box.pop(
            field_name,
            None,
        )

    for field_name in empty_receipt_fields:
        if box.get(
            field_name
        ) is None:
            box.pop(
                field_name,
                None,
            )

def _register_submission(
    *,
    item: Any,
    result: Any,
    submission_type: SubmissionType,
    delivery_channel: DeliveryChannel,
) -> None:
    """Registrér en accepteret afsendelse i dansk lokal tid.

    Output:
        Funktionen returnerer None.

        Itemet opdateres med:

        - afsendelsen i submissions
        - antal samlede afsendelser
        - antal manuelle genforsendelser
        - dansk afsendelsestidspunkt
        - dansk kvitteringsfrist
        - kort ventestatus

    Alle tidspunkter gemmes i Europe/Copenhagen.
    """
    box = _box(
        item
    )

    submitted_at = DanishTime.convert(
        result.submitted_at
    )

    receipt_deadline = (
        submitted_at
        + timedelta(
            days=RECEIPT_DEADLINE_DAYS
        )
    )

    transaction_id = getattr(
        result,
        "serviceplatform_transaction_id",
        None,
    )

    memo_message_uuid = getattr(
        result,
        "memo_message_uuid",
        None,
    )

    physical_shipment_id = getattr(
        result,
        "shipment_id",
        None,
    )

    if not transaction_id:
        raise PermanentItemError(
            "Serviceplatformens transaktions-ID mangler "
            "i afsendelsesresultatet."
        )

    if (
        delivery_channel == "DIGITAL_POST"
        and not memo_message_uuid
    ):
        raise PermanentItemError(
            "MessageUUID mangler efter afsendelse "
            "via Digital Post."
        )

    if (
        delivery_channel == "PHYSICAL_POST"
        and not physical_shipment_id
    ):
        raise PermanentItemError(
            "Shipment-ID mangler efter afsendelse "
            "via fysisk post."
        )

    submissions = box.setdefault(
        "submissions",
        [],
    )

    if not isinstance(
        submissions,
        list,
    ):
        raise PermanentItemError(
            "box['submissions'] skal være en liste."
        )

    submission_number = (
        len(submissions)
        + 1
    )

    submission = {
        "submission_number": (
            submission_number
        ),
        "submission_type": (
            submission_type
        ),
        "delivery_channel": (
            delivery_channel
        ),
        "submitted_at": DanishTime.iso(
            submitted_at
        ),
        "serviceplatform_transaction_id": (
            transaction_id
        ),
        "receipt_deadline_at": DanishTime.iso(
            receipt_deadline
        ),
        "final_result": None,
        "final_result_at": None,
    }

    if memo_message_uuid is not None:
        submission["memo_message_uuid"] = (
            memo_message_uuid
        )

    if physical_shipment_id is not None:
        submission["physical_shipment_id"] = (
            physical_shipment_id
        )

    submissions.append(
        submission
    )

    manual_resend_count = sum(
        1
        for existing_submission in submissions
        if (
            isinstance(
                existing_submission,
                dict,
            )
            and existing_submission.get(
                "submission_type"
            ) == "MANUAL_RESEND"
        )
    )

    box.update(
        {
            "processing_state": (
                ProcessingState.WAITING_FOR_RECEIPT
            ),
            "submission_count": (
                len(submissions)
            ),
            "manual_resend_count": (
                manual_resend_count
            ),
            "last_submission_type": (
                submission_type
            ),
            "manual_resend_requested": False,
            "requested_submission_type": (
                submission_type
            ),
            "requested_delivery_channel": (
                delivery_channel
            ),
        }
    )

    # Afsendelses-ID'erne findes i submissions.
    # Fjern derfor dubletter fra box-topniveauet.
    for field_name in (
        "submitted_at",
        "digital_post_id",
        "delivery_channel",
        "memo_message_uuid",
        "receipt_deadline_at",
        "physical_shipment_id",
        "serviceplatform_submission_id",
        "serviceplatform_transaction_id",
    ):
        box.pop(
            field_name,
            None,
        )

    # Tomme kvitteringsfelter oprettes først,
    # når der modtages en kvittering.
    for field_name in (
        "last_error",
        "completed_at",
        "receipt_status",
        "actual_delivery",
        "receipt_error_code",
        "receipt_received_at",
        "receipt_status_code",
        "receipt_status_message",
        "broker_message_id",
    ):
        if box.get(
            field_name
        ) is None:
            box.pop(
                field_name,
                None,
            )

    item.data["defer"] = DanishTime.iso(
        receipt_deadline
    )

    state_entries = item.data.setdefault(
        "state",
        [],
    )

    if not isinstance(
        state_entries,
        list,
    ):
        raise PermanentItemError(
            "item.data['state'] skal være en liste."
        )

    submitted_at_text = DanishTime.iso(
        submitted_at
    )

    if submission_type == "MANUAL_RESEND":
        state_message = (
            "Manuel genforsendelse "
            f"{submission_number} accepteret "
            f"{submitted_at_text} "
            f"via {delivery_channel}"
        )

        status_message = (
            "Afventer endelig kvittering fra SP "
            "efter manuel genforsendelse"
        )
    else:
        state_message = (
            "Første afsendelse accepteret "
            f"{submitted_at_text} "
            f"via {delivery_channel}"
        )

        status_message = (
            "Afventer endelig kvittering fra SP"
        )

    state_entries.append(
        state_message
    )

    item.data["status"] = {
        "status": "Pending User Action",
        "status_code": (
            status_message
        ),
    }

    _save_item(
        item
    )

    defer_item(
        item,
        status_message,
    )


def mark_receipt_timeout(item: Any) -> None:
    """Markér et udløbet ventende item som exception.

    Output:
        Funktionen returnerer None.

        processing_state ændres til READY_FOR_MANUAL_RESEND, og itemet
        markeres som failed. Brevet bliver ikke sendt automatisk igen.

        Hvis en bruger vælger Retry i ATS, bliver status NEW, mens
        processing_state bevares. Kombinationen
        NEW + READY_FOR_MANUAL_RESEND giver en manuel genfremsendelse.
    """
    box = _box(item)

    if (
        box.get("processing_state")
        != ProcessingState.WAITING_FOR_RECEIPT
    ):
        raise PermanentItemError(
            "Kun WAITING_FOR_RECEIPT kan timeout-markeres."
        )

    timeout_at = (
        DanishTime.now().isoformat()
    )

    message = (
        "Ingen endelig kvittering blev modtaget inden fristen. "
        "Forsendelsen er ikke automatisk genfremsendt. Vælg "
        "Retry efter manuel vurdering, hvis brevet aktivt skal "
        "genfremsendes. Genfremsendelse kan give modtageren "
        "brevet flere gange."
    )

    box.update(
        {
            "processing_state": (
                ProcessingState.READY_FOR_MANUAL_RESEND
            ),
            "receipt_timeout_at": timeout_at,
            "manual_resend_requested": False,
            "last_error": message,
        }
    )

    submissions = box.get(
        "submissions",
        [],
    )

    if submissions and isinstance(
        submissions[-1],
        dict,
    ):
        submissions[-1][
            "final_result"
        ] = "RECEIPT_TIMEOUT"
        submissions[-1][
            "final_result_at"
        ] = timeout_at

    state_entries = item.data.setdefault(
        "state",
        [],
    )

    if isinstance(state_entries, list):
        state_entries.append(
            f"Kvitteringsfrist udløbet {timeout_at}"
        )

    _save_item(item)
    item.fail(message)


async def send_item(
    item: Any,
    submission_type: SubmissionType,
) -> None:
    """Send eller genfremsend ét ATS-item.

    Output:
        Funktionen returnerer None efter accepteret indsendelse.
        Itemet sættes til PENDING_USER_ACTION af _register_submission().

    Kanalvalg:
        ONLY_DIGITAL_POST:
            Sendes altid som Digital Post. q-digitalpost har foretaget
            registreringskontrollen, før itemet blev oprettet.

        DIGITAL_OR_PHYSICAL_POST:
            check_registration() vælger Digital Post ved True og fysisk
            post ved False.
    """
    box = _box(item)
    _validate_box(box)

    main_document, attachments = (
        await _build_documents(box)
    )

    delivery_channel = (
        await _choose_delivery_channel(box)
    )

    box.update(
        {
            "processing_state": (
                ProcessingState.SUBMITTING
            ),
            "manual_resend_requested": (
                submission_type == "MANUAL_RESEND"
            ),
            "requested_submission_type": (
                submission_type
            ),
            "requested_delivery_channel": (
                delivery_channel
            ),
            "last_error": None,
        }
    )

    _save_item(item)

    if delivery_channel == "DIGITAL_POST":
        result = await _submit_digital_post(
            box,
            main_document,
            attachments,
        )
    else:
        result = await _submit_physical_post(
            box,
            main_document,
            attachments,
        )

    _register_submission(
        item=item,
        result=result,
        submission_type=submission_type,
        delivery_channel=delivery_channel,
    )


async def process_new_item(item: Any) -> None:
    """Behandl ét NEW-item ud fra processing_state.

    Output:
        Funktionen returnerer None.

    Regler:
        READY_TO_SEND:
            Første afsendelse.

        WAITING_FOR_RECEIPT:
            Defer-monitoren har frigivet itemet efter deadline.
            Itemet markeres som exception og sendes ikke igen.

        READY_FOR_MANUAL_RESEND:
            En bruger har valgt Retry i ATS. Itemet genfremsendes.

        Andre states:
            Itemet markeres som en permanent item-fejl.
    """
    box = _box(item)
    _validate_box(box)

    processing_state = box.get(
        "processing_state"
    )

    if processing_state == ProcessingState.READY_TO_SEND:
        await send_item(
            item,
            "INITIAL",
        )
        return

    if (
        processing_state
        == ProcessingState.WAITING_FOR_RECEIPT
    ):
        # VIGTIGT: NEW + WAITING_FOR_RECEIPT kommer fra
        # defer-monitorens udløb. Der må ikke sendes igen her.
        mark_receipt_timeout(item)
        return

    if (
        processing_state
        == ProcessingState.READY_FOR_MANUAL_RESEND
    ):
        # VIGTIGT: ATS Retry har gjort itemet NEW og har bevaret
        # processing_state. Derfor er dette en aktiv brugerbeslutning.
        await send_item(
            item,
            "MANUAL_RESEND",
        )
        return

    raise PermanentItemError(
        "Ukendt eller ikke-genfremsendelig processing_state: "
        f"{processing_state!r}."
    )


async def process_send_queue(
    workqueue: Any,
) -> int:
    """Behandl de valgte NEW-items ét ad gangen.

    Output:
        Antal items, som processen forsøgte at behandle.

    Fejlhåndtering:
        PermanentItemError:
            Itemet markeres som failed, og processen fortsætter.

        Andre exceptions:
            Itemdata gemmes med last_error, hvorefter fejlen rejses igen.
            Hele processen stopper, så Automation Server kan genstarte den.
    """
    processed_count = 0

    for item in workqueue:
        with item:
            processed_count += 1

            logger.info(
                "Behandler Digital Post-item: id=%s, reference=%s.",
                item.id,
                item.reference,
            )

            try:
                await process_new_item(item)

            except PermanentItemError as error:
                logger.error(
                    "Permanent itemfejl for reference=%s: %s",
                    item.reference,
                    error,
                )

                try:
                    box = _box(item)
                    box["last_error"] = str(error)
                    box["processing_state"] = (
                        ProcessingState.RECEIPT_REQUIRES_REVIEW
                    )
                    _save_item(item)
                except Exception:
                    logger.exception(
                        "Kunne ikke gemme den permanente fejl "
                        "på itemet."
                    )

                item.fail(str(error))

            except Exception as error:
                logger.exception(
                    "Uventet teknisk fejl for Digital Post-item "
                    "reference=%s.",
                    item.reference,
                )

                try:
                    box = _box(item)
                    box["last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )
                    _save_item(item)
                except Exception:
                    logger.warning(
                        "Kunne ikke gemme last_error på itemet.",
                        exc_info=True,
                    )

                raise

    return processed_count
