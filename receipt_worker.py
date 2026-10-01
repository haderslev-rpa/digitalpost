"""Behandling af kvitteringer fra KOMBIT Beskedfordeleren."""
from __future__ import annotations

import base64
import logging
from typing import Any
from xml.etree import ElementTree

from automation_server_client import (
    WorkItemStatus,
)
from q_outlook_api.functionality.mail_api import (
    send_mail,
)
from q_serviceplatformen.configuration import (
    get_related_object_id,
)
from q_serviceplatformen.message_broker import (
    MessageBroker,
)

from ats_repository import (
    find_matching_work_item,
)
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

def _local_xml_name(tag: str) -> str:
    """
    Fjern namespace fra et XML-elementnavn.

    Output:
        Eksempel:

            {urn:oio:besked:kuvert:1.0}BeskedId

        bliver til:

            BeskedId
    """
    return tag.rsplit(
        "}",
        maxsplit=1,
    )[-1]


def _receipt_belongs_to_our_system(
    receipt: Any,
) -> bool:
    """
    Kontrollér om en kvittering tilhører vores system.

    Input:
        receipt:
            Kvitteringen fra q-serviceplatformens parser.

    Output:
        True:
            receipt.related_object_id er lig med det
            related_object_id, som ligger i Automation Server-
            credentialen SERVICEPLATFORMEN.

        False:
            ID'et mangler eller tilhører et andet system.

    Fejl:
        Konfigurationsfejl fra get_related_object_id() fortsætter.
        En ugyldig credential må ikke medføre, at kvitteringer
        slettes som fremmede.
    """
    receipt_related_object_id = getattr(
        receipt,
        "related_object_id",
        None,
    )

    if not isinstance(
        receipt_related_object_id,
        str,
    ):
        return False

    cleaned_receipt_id = (
        receipt_related_object_id
        .strip()
        .casefold()
    )

    if not cleaned_receipt_id:
        return False

    configured_related_object_id = (
        get_related_object_id()
        .strip()
        .casefold()
    )

    return (
        cleaned_receipt_id
        == configured_related_object_id
    )


def _sanitize_xml_element(
    element: ElementTree.Element,
) -> None:
    """
    Maskér sikkerhedsfølsomme værdier i et XML-træ.

    Funktionen ændrer XML-træet direkte.

    Felter, som maskeres:
        KildesystemAkkreditiver

    Formål:
        Mailen må gerne indeholde XML-strukturen og de tekniske
        identifikatorer, men ikke de tekniske akkreditiver.
    """
    sensitive_element_names = {
        "KildesystemAkkreditiver",
    }

    for child in element.iter():
        if (
            _local_xml_name(child.tag)
            in sensitive_element_names
        ):
            child.text = (
                "[TEKNISKE AKKREDITIVER FJERNET]"
            )


def _decode_receipt_payload(
    envelope: ElementTree.Element,
) -> str:
    """
    Base64-dekod den indlejrede PKO_PostStatus.

    Output:
        Den indlejrede XML som læsbar Unicode-tekst.

        Hvis feltet mangler eller ikke kan afkodes, returneres en
        beskrivende fejltekst. Mailafsendelsen må stadig fortsætte.
    """
    base64_element = next(
        (
            element
            for element in envelope.iter()
            if _local_xml_name(element.tag)
            == "Base64"
        ),
        None,
    )

    if (
        base64_element is None
        or not (base64_element.text or "").strip()
    ):
        return (
            "[Beskeddata/Base64 blev ikke fundet]"
        )

    try:
        payload_bytes = base64.b64decode(
            base64_element.text.strip(),
            validate=True,
        )
    except (ValueError, TypeError) as error:
        return (
            "[Beskeddata/Base64 kunne ikke afkodes: "
            f"{type(error).__name__}: {error}]"
        )

    try:
        payload_root = ElementTree.fromstring(
            payload_bytes
        )
    except ElementTree.ParseError:
        return payload_bytes.decode(
            "utf-8",
            errors="replace",
        )

    ElementTree.indent(
        payload_root,
        space="  ",
    )

    return ElementTree.tostring(
        payload_root,
        encoding="unicode",
    )


def _prepare_xml_for_mail(
    raw_xml: bytes,
) -> tuple[str, str]:
    """
    Klargør Beskedfordelerens XML til sporingsmailen.

    Input:
        raw_xml:
            Hele den rå AMQP-besked.

    Output:
        En tuple med:

        1. Den ydre Haendelsesbesked som læsbar XML.
        2. Den afkodede PKO_PostStatus som læsbar XML.

    Sikkerhed:
        KildesystemAkkreditiver maskeres.

        Base64-feltets lange indhold erstattes i den ydre XML,
        fordi den afkodede XML vises separat. Dermed undgås en
        meget stor og svært læselig dublet i mailen.
    """
    try:
        envelope = ElementTree.fromstring(
            raw_xml
        )
    except ElementTree.ParseError as error:
        raw_text = raw_xml.decode(
            "utf-8",
            errors="replace",
        )

        return (
            "[Den ydre besked kunne ikke parses som XML]\n"
            + raw_text,
            "[PKO_PostStatus kunne ikke udlæses, fordi "
            f"kuverten var ugyldig: {error}]",
        )

    decoded_payload = _decode_receipt_payload(
        envelope
    )

    _sanitize_xml_element(
        envelope
    )

    for element in envelope.iter():
        if _local_xml_name(element.tag) == "Base64":
            element.text = (
                "[BASE64 ER AFKODET OG VIST NEDENFOR]"
            )

    ElementTree.indent(
        envelope,
        space="  ",
    )

    envelope_text = ElementTree.tostring(
        envelope,
        encoding="unicode",
    )

    return (
        envelope_text,
        decoded_payload,
    )

def send_unmatched_receipt_mail(
    receipt: Any,
    raw_xml: bytes,
    metadata: dict[str, Any],
) -> None:
    """
    Send en detaljeret mail om en umatchet kvittering fra vores system.

    Funktionen må kun kaldes, når:
        1. Kvitteringen ikke kunne matches med et ATS-item.
        2. Kvitteringens related_object_id tilhører vores system.

    Mailen indeholder:
        - alle returnerede felter fra DigitalPostReceipt
        - AMQP-metadata
        - den ydre XML-kuvert
        - den base64-afkodede PKO_PostStatus

    Sikkerhed:
        KildesystemAkkreditiver fjernes fra XML'en før mailafsendelse.

    Output:
        None efter vellykket afsendelse.

    Fejl:
        Mail- og konfigurationsfejl fortsætter som exceptions.

        Den kaldende worker skal i så fald requeue brokerbeskeden,
        så vores egen umatchede kvittering ikke går tabt.
    """
    logger.warning(
        (
            "Umatchet kvittering fra vores system: "
            "related_object_id=%r, status=%r, channel=%r, "
            "physical_shipment_id=%r, MessageUUID=%r, "
            "transaction_id=%r, broker_message_id=%r"
        ),
        getattr(
            receipt,
            "related_object_id",
            None,
        ),
        getattr(
            receipt,
            "status",
            None,
        ),
        getattr(
            receipt,
            "channel",
            None,
        ),
        getattr(
            receipt,
            "physical_shipment_id",
            None,
        ),
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
        logger.warning(
            "Kvitteringen tilhører vores system, men mail om "
            "umatchende kvitteringer er slået fra."
        )
        return

    validate_mail_configuration()

    envelope_xml, payload_xml = (
        _prepare_xml_for_mail(
            raw_xml
        )
    )

    mail_body = (
        "En kvittering fra KOMBIT Beskedfordeleren tilhører "
        "vores system, men kunne ikke matches med et ATS-item.\n\n"
        "Mailen indeholder alle fortolkede felter samt den "
        "maskerede XML-kuvert og den afkodede PKO_PostStatus. "
        "Oplysningerne kan bruges til at undersøge, hvordan "
        "fysiske postkvitteringer skal matches.\n\n"

        "STATUS\n"
        "======\n"
        "Status: "
        f"{_format_mail_value(getattr(receipt, 'status', None))}\n"
        "Endelig status: "
        f"{_format_mail_value(getattr(receipt, 'is_final', None))}\n"
        "Positiv status: "
        f"{_format_mail_value(getattr(receipt, 'is_success', None))}\n"
        "Kanal: "
        f"{_format_mail_value(getattr(receipt, 'channel', None))}\n"
        "Modtaget: "
        f"{_format_mail_value(getattr(receipt, 'received_at', None))}\n"
        "Faktisk levering: "
        f"{_format_mail_value(getattr(receipt, 'actual_delivery', None))}\n"
        "Statuskode: "
        f"{_format_mail_value(getattr(receipt, 'status_code', None))}\n"
        "Statusbesked: "
        f"{_format_mail_value(getattr(receipt, 'status_message', None))}\n"
        "Fejlkode: "
        f"{_format_mail_value(getattr(receipt, 'error_code', None))}\n\n"

        "IDENTIFIKATORER\n"
        "===============\n"
        "Relateret objekt-ID: "
        f"{_format_mail_value(getattr(receipt, 'related_object_id', None))}\n"
        "Serviceplatform transaction ID: "
        f"{_format_mail_value(getattr(receipt, 'serviceplatform_transaction_id', None))}\n"
        "Memo Message UUID: "
        f"{_format_mail_value(getattr(receipt, 'memo_message_uuid', None))}\n"
        "Digital Post ID: "
        f"{_format_mail_value(getattr(receipt, 'digital_post_id', None))}\n"
        "Physical shipment ID: "
        f"{_format_mail_value(getattr(receipt, 'physical_shipment_id', None))}\n"
        "Correlation ID: "
        f"{_format_mail_value(getattr(receipt, 'correlation_id', None))}\n"
        "Broker message ID: "
        f"{_format_mail_value(getattr(receipt, 'broker_message_id', None))}\n"
        "Subscription expression ID: "
        f"{_format_mail_value(getattr(receipt, 'subscription_expression_id', None))}\n\n"

        "AMQP-METADATA\n"
        "=============\n"
        "Routing key: "
        f"{_format_mail_value(metadata.get('routing_key'))}\n"
        "Redelivered: "
        f"{_format_mail_value(metadata.get('redelivered'))}\n"
        "Delivery tag: "
        f"{_format_mail_value(metadata.get('delivery_tag'))}\n\n"

        "YDRE BESKEDFORDELER-KUVERT\n"
        "==========================\n"
        f"{envelope_xml}\n\n"

        "AFKODET PKO_POSTSTATUS\n"
        "======================\n"
        f"{payload_xml}\n"
    )

    mail = {
        "message": {
            "subject": (
                "Digital Post: Umatchet kvittering "
                "fra vores system"
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
    """
    Tøm Dueslaget én gang og returnér behandlingstællinger.

    Flow:
        1. Hent næste besked med auto_ack=False.
        2. Parse beskeden.
        3. Forsøg at matche den med et ATS-item.
        4. Behandl matchede kvitteringer normalt.
        5. Sortér umatchede kvitteringer efter related_object_id.

    Umatchet kvittering fra vores system:
        - Send detaljeret mail.
        - Ack først efter vellykket mailafsendelse.
        - Ved mailfejl requeues beskeden.

    Umatchet kvittering fra et andet system:
        - Send ingen mail.
        - Ack beskeden.
        - Beskeden fjernes fra Dueslaget.

    Manglende RelateretObjekt-ID:
        Behandles som en potentiel fejl i stedet for som et andet
        system. Beskeden requeues, og processen stopper med exception.

        Dette er en sikkerhedsregel, så en besked ikke slettes alene,
        fordi systemidentifikationen mangler.

    Output:
        PollResult med antallet af:
        - hentede beskeder
        - matchede kvitteringer
        - umatchede kvitteringer
        - completed items
        - failed items
        - pending items
    """
    result = PollResult()

    with MessageBroker() as broker:
        while True:
            message = broker.get_message()

            if message is None:
                return result

            result.messages += 1

            try:
                receipt = message.parse_receipt()

                item = find_matching_work_item(
                    receipt
                )

                if item is None:
                    related_object_id = getattr(
                        receipt,
                        "related_object_id",
                        None,
                    )

                    if (
                        not isinstance(
                            related_object_id,
                            str,
                        )
                        or not related_object_id.strip()
                    ):
                        raise RuntimeError(
                            "Den umatchede kvittering mangler "
                            "RelateretObjekt/ObjektId. Beskeden "
                            "fjernes derfor ikke automatisk."
                        )

                    if _receipt_belongs_to_our_system(
                        receipt
                    ):
                        send_unmatched_receipt_mail(
                            receipt,
                            message.body,
                            message.metadata,
                        )

                        logger.warning(
                            (
                                "Umatchet kvittering fra vores "
                                "system blev sendt på mail og "
                                "fjernes nu fra Dueslaget. "
                                "related_object_id=%r, "
                                "broker_message_id=%r"
                            ),
                            related_object_id,
                            getattr(
                                receipt,
                                "broker_message_id",
                                None,
                            ),
                        )
                    else:
                        logger.info(
                            (
                                "Umatchet kvittering fra et andet "
                                "system fjernes uden mail. "
                                "related_object_id=%r, "
                                "broker_message_id=%r"
                            ),
                            related_object_id,
                            getattr(
                                receipt,
                                "broker_message_id",
                                None,
                            ),
                        )

                    message.ack()
                    result.unmatched += 1
                    continue

                if _already_processed(
                    item,
                    receipt,
                ):
                    message.ack()
                    result.matched += 1
                    continue

                outcome = update_item_from_receipt(
                    item,
                    receipt,
                )

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

                logger.exception(
                    "Beskeden blev lagt tilbage i Dueslaget."
                )

                raise