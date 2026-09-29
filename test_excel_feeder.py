from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from configuration import EXCEL_SHEET_NAME, TEST_SOURCE_PROCESS, WORD_TEMPLATE_PATH
from q_datafordeleren_api.functionality.datafordeler_use import get_aktuel_navn_og_adresse
from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import DeliveryMethod, DigitalPostAddress, DigitalPostResult

logger = logging.getLogger(__name__)

# Excel-arket skal som minimum have kolonnerne:
# cpr, emne, delivery_method
#
# Valgfrie kolonner:
# filnavn, source_item_id og bookmark:<bogmaerkenavn>


def read_rows(excel_path: Path) -> list[dict[str, Any]]:
    """Læs Excel og returnér én dictionary pr. datarække."""
    frame = pd.read_excel(
        excel_path,
        sheet_name=EXCEL_SHEET_NAME,
        engine="openpyxl",
        dtype=str,
    ).fillna("")

    required_columns = {"cpr", "emne", "delivery_method"}
    missing_columns = required_columns.difference(frame.columns)
    if missing_columns:
        raise ValueError(
            "Excel mangler kolonne(r): "
            + ", ".join(sorted(missing_columns))
        )
    return frame.to_dict(orient="records")


def build_bookmarks(
    row: dict[str, Any],
    person_data: dict[str, Any],
) -> list[dict[str, str]]:
    """Returnér bogmærker som [{"name": str, "value": str}, ...]."""
    bookmarks = [
        {"name": "navn", "value": str(person_data.get("navn") or "")},
        {
            "name": "vejnavn",
            "value": str(
                person_data.get("vejadresseringsnavn")
                or person_data.get("vejnavn")
                or ""
            ),
        },
        {"name": "husnummer", "value": str(person_data.get("husnummer") or "")},
        {"name": "etage", "value": str(person_data.get("etage") or "")},
        {"name": "sidedoer", "value": str(person_data.get("sidedoer") or "")},
        {"name": "postnummer", "value": str(person_data.get("postnummer") or "")},
        {
            "name": "by",
            "value": str(
                person_data.get("postdistrikt")
                or person_data.get("bynavn")
                or ""
            ),
        },
    ]

    for column_name, value in row.items():
        if column_name.startswith("bookmark:"):
            bookmarks.append(
                {
                    "name": column_name.removeprefix("bookmark:").strip(),
                    "value": str(value),
                }
            )
    return bookmarks


async def create_pdf_in_memory(
    word_content: bytes,
    bookmarks: list[dict[str, str]],
) -> bytes:
    """Returnér udfyldt PDF som bytes i memory.

    NOTE OM KOMMENDE q-word:
    q-word findes ikke endnu. Feederens forventede kontrakt er:

        await fill_bookmarks_and_convert_to_pdf(
            word_content: bytes,
            bookmarks: list[dict[str, str]],
        ) -> bytes

    Når q-word er færdig, skal kun importen og eventuelt funktionsnavnet
    i denne funktion tilpasses.
    """
    try:
        from q_word import fill_bookmarks_and_convert_to_pdf
    except ImportError as exc:
        raise RuntimeError(
            "q-word er ikke installeret endnu. Den forventede funktion er "
            "fill_bookmarks_and_convert_to_pdf(word_content, bookmarks) -> bytes."
        ) from exc

    pdf_content = fill_bookmarks_and_convert_to_pdf(
        word_content=word_content,
        bookmarks=bookmarks,
    )
    if asyncio.iscoroutine(pdf_content):
        pdf_content = await pdf_content

    if not isinstance(pdf_content, bytes):
        raise TypeError("q-word skal returnere PDF-indhold som bytes.")
    if not pdf_content.startswith(b"%PDF-"):
        raise ValueError("q-word returnerede bytes, men indholdet er ikke en PDF.")
    return pdf_content


def normalize_delivery_method(value: str) -> DeliveryMethod:
    """Returnér en gyldig DeliveryMethod fra Excel-tekst."""
    normalized = str(value).strip().upper()
    try:
        return DeliveryMethod(normalized)
    except ValueError as exc:
        allowed = ", ".join(method.value for method in DeliveryMethod)
        raise ValueError(
            f"Ukendt delivery_method '{value}'. Tilladte værdier: {allowed}."
        ) from exc


async def queue_row(
    row_number: int,
    row: dict[str, Any],
    word_template: bytes,
) -> DigitalPostResult:
    """Opret én rigtig q-digitalpost-bestilling og returnér DigitalPostResult."""
    cpr = "".join(character for character in str(row["cpr"]) if character.isdigit())
    if len(cpr) != 10:
        raise ValueError(f"Række {row_number}: CPR skal indeholde 10 cifre.")

    person_data = await asyncio.to_thread(
        get_aktuel_navn_og_adresse,
        cpr,
    )
    if not isinstance(person_data, dict):
        raise TypeError("q-datafordeleren returnerede ikke en dictionary.")
    if not person_data.get("kan_sendes_brev", False):
        raise ValueError(
            str(
                person_data.get("kan_sendes_brev_aarsag")
                or "Datafordeleren oplyser, at der ikke kan sendes brev."
            )
        )

    delivery_method = normalize_delivery_method(row["delivery_method"])
    address = None
    if delivery_method == DeliveryMethod.DIGITAL_OR_PHYSICAL_POST:
        address = DigitalPostAddress.from_datafordeler(person_data)

    bookmarks = build_bookmarks(row, person_data)
    pdf_content = await create_pdf_in_memory(word_template, bookmarks)

    result = await DigitalPost().send_digital_post(
        pdf_content=pdf_content,
        file_name=str(row.get("filnavn") or f"Brev_{row_number}.pdf").strip(),
        cpr_or_cvr=cpr,
        subject=str(row["emne"]).strip(),
        source_process=TEST_SOURCE_PROCESS,
        source_item_id=str(row.get("source_item_id") or f"excel-row-{row_number}"),
        delivery_method=delivery_method,
        attachments=[],
        address=address,
    )
    return result


async def run_feeder(excel_path: Path, template_path: Path) -> None:
    """Læs Excel, opret PDF'er i memory og bestil Digital Post rækkevis."""
    rows = read_rows(excel_path)
    word_template = template_path.read_bytes()

    queued = 0
    not_sent = 0
    failed = 0

    for row_number, row in enumerate(rows, start=2):
        try:
            result = await queue_row(row_number, row, word_template)
            if result.status == "QUEUED":
                queued += 1
            elif result.status == "NOT_SENT":
                not_sent += 1
            logger.info(
                "Række %s: status=%s, forsendelses_id=%s, queue_id=%s, message=%s",
                row_number,
                result.status,
                result.forsendelses_id,
                result.queue_id,
                result.message,
            )
        except Exception:
            failed += 1
            logger.exception("Række %s kunne ikke bestilles", row_number)

    logger.info(
        "Feeder afsluttet: QUEUED=%s, NOT_SENT=%s, FEJL=%s",
        queued,
        not_sent,
        failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manuel Excel-feeder til q-digitalpost"
    )
    parser.add_argument("excel", type=Path, help="Sti til Excel-filen")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(WORD_TEMPLATE_PATH),
        help="Sti til Word-skabelonen",
    )
    arguments = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(run_feeder(arguments.excel, arguments.template))


if __name__ == "__main__":
    main()
