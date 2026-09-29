"""Hentning og sletning af Digital Post-dokumenter i SharePoint.

Filen fungerer som en lille adapter mellem Digital Post-processen
og de generelle dokumentfunktioner i q-sharepoint-api.

Filen kender ikke Automation Server-statusser og ændrer aldrig
et ATS-item.
"""

from __future__ import annotations

import asyncio
from typing import Any

from q_sharepoint_api.functionality.sp_documents import (
    delete_document,
    download_document,
)

from configuration import (
    SHAREPOINT_LIBRARY_NAME,
    SHAREPOINT_SITE_NAME,
)


def validate_document_metadata(
    document: dict[str, Any],
) -> None:
    """Kontrollér metadata for ét dokument.

    Input:
        document:
            Dokumentmetadata fra item.data["box"]["documents"].

    Output:
        Funktionen returnerer None, når metadata indeholder:

        - dokument_id
        - file_name
        - role
        - drive_item_id

    Fejl:
        TypeError, hvis document ikke er en dictionary.

        ValueError, hvis et obligatorisk felt mangler.
    """
    if not isinstance(document, dict):
        raise TypeError(
            "Dokumentmetadata skal være en dictionary."
        )

    required_fields = (
        "dokument_id",
        "file_name",
        "role",
        "drive_item_id",
    )

    missing_fields = [
        field_name
        for field_name in required_fields
        if not document.get(field_name)
    ]

    if missing_fields:
        raise ValueError(
            "Dokumentmetadata mangler felt(er): "
            + ", ".join(missing_fields)
        )


async def download_pdf(
    document: dict[str, Any],
) -> bytes:
    """Hent ét PDF-dokument fra SharePoint til memory.

    Input:
        document:
            Dokumentmetadata fra ATS-itemets box.

            Eksempel:

            {
                "dokument_id": "...",
                "file_name": "Robot - test.pdf",
                "role": "MAIN",
                "drive_item_id": "01ABC...",
            }

    Output:
        PDF-filens indhold som bytes.

    Fejl:
        Valideringsfejl fra validate_document_metadata().

        HTTP-, autentificerings- og SharePoint-fejl fra
        download_document() fortsætter til den kaldende funktion.

        ValueError, hvis det hentede indhold ikke er bytes,
        er tomt eller ikke begynder med PDF-signaturen.
    """
    validate_document_metadata(document)

    drive_item_id = document["drive_item_id"]
    file_name = document["file_name"]

    content = await asyncio.to_thread(
        download_document,
        site_name=SHAREPOINT_SITE_NAME,
        library_name=SHAREPOINT_LIBRARY_NAME,
        drive_item_id=drive_item_id,
    )

    if not isinstance(content, bytes):
        raise TypeError(
            "SharePoint returnerede ikke dokumentet som bytes. "
            f"Filnavn={file_name!r}, "
            f"drive_item_id={drive_item_id!r}."
        )

    if not content:
        raise ValueError(
            "SharePoint returnerede et tomt dokument. "
            f"Filnavn={file_name!r}, "
            f"drive_item_id={drive_item_id!r}."
        )

    if not content.startswith(b"%PDF-"):
        raise ValueError(
            "Dokumentet fra SharePoint er ikke en PDF. "
            f"Filnavn={file_name!r}, "
            f"drive_item_id={drive_item_id!r}."
        )

    return content


async def delete_pdf(
    document: dict[str, Any],
) -> None:
    """Slet én Digital Post-fil fra SharePoint.

    Input:
        document:
            Dokumentmetadata fra ATS-itemets box.

    Output:
        Funktionen returnerer None, når dokumentet er slettet,
        eller når SharePoint oplyser, at dokumentet allerede
        er væk.

    Vigtigt:
        Funktionen ændrer ikke ATS-itemet.

        Funktionen ændrer ikke itemets status.

        Funktionen tilføjer ikke et deleted-felt.

        Den offentlige delete_document()-funktion behandler
        SharePoint HTTP 404 som succes.
    """
    validate_document_metadata(document)

    await asyncio.to_thread(
        delete_document,
        site_name=SHAREPOINT_SITE_NAME,
        library_name=SHAREPOINT_LIBRARY_NAME,
        drive_item_id=document["drive_item_id"],
    )
