"""SharePoint-cleanup for afsluttede Digital Post-items.

Cleanup udfører følgende:

1. Finder completed Digital Post-items, som er ældre end grænsen.
2. Behandler alle SharePoint-dokumenter på hvert item.
3. Gemmer cleanup-resultatet på ATS-itemet.
4. Sikrer, at samme item ikke vælges ved næste cleanup.

Cleanup ændrer ikke itemets completed-status.
"""

from __future__ import annotations

import logging
from typing import Any

from ats_repository import get_cleanup_candidates
from configuration import CLEANUP_AGE_DAYS
from danish_time import DanishTime
from sharepoint_service import delete_pdf


logger = logging.getLogger(__name__)


async def _cleanup_item_documents(
    item: Any,
) -> tuple[int, int]:
    """Ryd op i dokumenterne for ét completed ATS-item.

    Output:
        En tuple med:

        1. Antal dokumenter, som blev behandlet uden fejl.
        2. Antal dokumenter, som gav fejl.

    Bemærk:
        Et vellykket slettekald kan betyde enten:
        - at filen blev slettet nu
        - at filen allerede var væk

        SharePoint-adapteren behandler begge resultater som succes.
    """
    if not isinstance(
        getattr(item, "data", None),
        dict,
    ):
        raise RuntimeError(
            "Cleanup-itemet mangler en gyldig data-dictionary."
        )

    box = item.data.get("box")

    if not isinstance(box, dict):
        raise RuntimeError(
            "Cleanup-itemet mangler item.data['box']."
        )

    documents = box.get("documents", [])

    if not isinstance(documents, list):
        raise RuntimeError(
            "box['documents'] skal være en liste."
        )

    processed_documents = 0
    errors = 0
    error_messages: list[str] = []

    for document in documents:
        try:
            await delete_pdf(
                document
            )

            processed_documents += 1

            logger.info(
                "SharePoint-dokument behandlet af cleanup. "
                "reference=%s, drive_item_id=%s",
                item.reference,
                (
                    document.get("drive_item_id")
                    if isinstance(document, dict)
                    else None
                ),
            )

        except Exception as error:
            errors += 1

            drive_item_id = (
                document.get("drive_item_id")
                if isinstance(document, dict)
                else None
            )

            error_messages.append(
                f"{drive_item_id}: "
                f"{type(error).__name__}: {error}"
            )

            logger.exception(
                "Kunne ikke rydde SharePoint-dokument. "
                "reference=%s, drive_item_id=%s",
                item.reference,
                drive_item_id,
            )

    cleanup_at = DanishTime.now().isoformat()

    box["documents_cleanup_attempted_at"] = cleanup_at
    box["documents_cleanup_document_count"] = len(
        documents
    )
    box["documents_cleanup_processed_count"] = (
        processed_documents
    )
    box["documents_cleanup_error_count"] = errors

    if errors == 0:
        # Dette felt bruges af get_cleanup_candidates() til at sikre,
        # at itemet ikke bliver behandlet igen ved næste cleanup.
        box["documents_cleaned_up_at"] = cleanup_at
        box["documents_cleanup_status"] = "COMPLETED"
        box["documents_cleanup_errors"] = []

        state_entries = item.data.setdefault(
            "state",
            [],
        )

        if isinstance(state_entries, list):
            state_entries.append(
                "SharePoint-dokumenter ryddet "
                f"{cleanup_at}"
            )
    else:
        # Feltet documents_cleaned_up_at sættes ikke ved fejl.
        # Derfor vil itemet kunne forsøges igen ved næste cleanup.
        box.pop(
            "documents_cleaned_up_at",
            None,
        )
        box["documents_cleanup_status"] = "FAILED"
        box["documents_cleanup_errors"] = error_messages

    item.update(
        item.data
    )

    return (
        processed_documents,
        errors,
    )


async def cleanup_sharepoint_documents() -> dict[str, int]:
    """Ryd SharePoint-filer for kvalificerede completed items.

    Output:
        En dictionary med:

        items:
            Antal undersøgte ATS-items.

        completed_items:
            Antal items, hvor alle dokumenter blev behandlet
            uden fejl og cleanup-markeringen blev gemt.

        processed_documents:
            Antal dokumenter, hvor slettekaldet lykkedes.
            Dokumentet kan være slettet nu eller allerede have
            været væk.

        errors:
            Antal dokumenter, som gav en exception.
    """
    result = {
        "items": 0,
        "completed_items": 0,
        "processed_documents": 0,
        "errors": 0,
    }

    cleanup_candidates = get_cleanup_candidates(
        CLEANUP_AGE_DAYS
    )

    for item in cleanup_candidates:
        result["items"] += 1

        processed_documents, errors = (
            await _cleanup_item_documents(
                item
            )
        )

        result["processed_documents"] += (
            processed_documents
        )
        result["errors"] += errors

        if errors == 0:
            result["completed_items"] += 1

    return result