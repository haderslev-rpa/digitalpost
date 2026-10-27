"""Manuel integrationstest af Digital Post til CVR og P-nummer.

Placering:
    tests/test_queue_p_number.py

Kør fra repositoryets rodmappe:
    uv run python tests/test_queue_p_number.py

Testdata læses ikke fra .env. Testen uploader PDF-filen til SharePoint
og opretter et rigtigt ATS-køelement. Selve afsendelsen udføres bagefter
af digitalpost-processens main.py.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from pprint import pprint


PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import (
    DeliveryMethod,
    DigitalPostResult,
)


logger = logging.getLogger(__name__)


# Ret kun filstien, hvis testen skal bruge en anden PDF.
TEST_PDF_PATH = (
    PROJECT_ROOT
    / "tests_local"
    / "Robot - test.pdf"
)

# Modtagerdata er bevidst placeret direkte i testfilen.
TEST_CVR = "29189757"

# Indsæt det rigtige P-nummer her før testen køres.
# P-nummeret skal indeholde præcis 10 cifre.
TEST_P_NUMBER = "1003323413"

# Navnet er valgfrit. None betyder, at kun P-nummeret medsendes.
TEST_PRODUCTION_UNIT_NAME: str | None = None

TEST_SUBJECT = "Integrationstest Send til rujo@haderlev.dk"
TEST_SOURCE_PROCESS = "digitalpost-manuel-p-nummer-test"
TEST_SOURCE_ITEM_ID = "p-nummer-test-pdf-001"
TEST_DELIVERY_METHOD = DeliveryMethod.ONLY_DIGITAL_POST


def validate_test_configuration() -> None:
    """Kontrollér CVR, P-nummer og PDF-sti."""
    if len(TEST_CVR) != 8 or not TEST_CVR.isdigit():
        raise ValueError(
            "TEST_CVR skal indeholde præcis 8 cifre."
        )

    if (
        len(TEST_P_NUMBER) != 10
        or not TEST_P_NUMBER.isdigit()
    ):
        raise ValueError(
            "Indsæt det rigtige P-nummer i TEST_P_NUMBER. "
            "P-nummeret skal indeholde præcis 10 cifre."
        )

    if (
        TEST_PRODUCTION_UNIT_NAME is not None
        and not TEST_PRODUCTION_UNIT_NAME.strip()
    ):
        raise ValueError(
            "TEST_PRODUCTION_UNIT_NAME skal være udfyldt tekst "
            "eller None."
        )

    if not TEST_PDF_PATH.is_file():
        raise FileNotFoundError(
            "PDF-filen blev ikke fundet.\n"
            f"Den angivne sti er:\n{TEST_PDF_PATH}"
        )


def validate_pdf(pdf_path: Path) -> bytes:
    """Læs og validér test-PDF'en og returnér indholdet som bytes."""
    if not pdf_path.is_file():
        raise FileNotFoundError(
            f"PDF-filen blev ikke fundet: {pdf_path}"
        )

    pdf_content = pdf_path.read_bytes()

    if not pdf_content:
        raise ValueError(
            f"PDF-filen er tom: {pdf_path}"
        )

    if not pdf_content.startswith(b"%PDF-"):
        raise ValueError(
            f"Filen er ikke en gyldig PDF: {pdf_path}"
        )

    return pdf_content


async def create_test_queue_item(
    pdf_path: Path,
) -> DigitalPostResult:
    """Upload PDF'en og opret et Digital Post-køelement."""
    validate_test_configuration()
    pdf_content = validate_pdf(pdf_path)

    digital_post = DigitalPost()

    return await digital_post.send_digital_post(
        pdf_content=pdf_content,
        file_name=pdf_path.name,
        cpr_or_cvr=TEST_CVR,
        p_number=TEST_P_NUMBER,
        production_unit_name=TEST_PRODUCTION_UNIT_NAME,
        subject=TEST_SUBJECT,
        source_process=TEST_SOURCE_PROCESS,
        source_item_id=TEST_SOURCE_ITEM_ID,
        delivery_method=TEST_DELIVERY_METHOD,
        attachments=[],
        address=None,
    )


def validate_result(
    result: DigitalPostResult,
) -> None:
    """Kontrollér at bestillingen blev lagt korrekt i køen."""
    if result.status != "QUEUED":
        raise RuntimeError(
            "Testen forventede status QUEUED, men modtog "
            f"{result.status!r}. "
            f"Reason={result.reason!r}, "
            f"message={result.message!r}"
        )

    if not result.forsendelses_id:
        raise RuntimeError(
            "q-digitalpost returnerede ikke et forsendelses_id."
        )

    if result.queue_id is None:
        raise RuntimeError(
            "q-digitalpost returnerede ikke et queue_id."
        )

    if result.document_count != 1:
        raise RuntimeError(
            "Testen forventede ét uploadet dokument, men modtog "
            f"document_count={result.document_count}."
        )


async def run_test(
    pdf_path: Path,
) -> None:
    """Kør hele integrationstesten med den valgte PDF-fil."""
    logger.info("=" * 70)
    logger.info("STARTER DIGITAL POST-TEST TIL P-NUMMER")
    logger.info("PDF: %s", pdf_path)
    logger.info("CVR: %s", TEST_CVR)
    logger.info("P-nummer: %s", TEST_P_NUMBER)
    logger.info(
        "Produktionsenhedsnavn: %s",
        TEST_PRODUCTION_UNIT_NAME,
    )
    logger.info(
        "Leveringsmetode: %s",
        TEST_DELIVERY_METHOD.value,
    )
    logger.info("=" * 70)

    result = await create_test_queue_item(pdf_path)

    logger.info("Resultat fra q-digitalpost:")
    pprint(result)

    validate_result(result)

    logger.info("=" * 70)
    logger.info(
        "TEST OK: Forsendelsen er lagt i Digital Post-køen."
    )
    logger.info(
        "Forsendelses-ID: %s",
        result.forsendelses_id,
    )
    logger.info("Queue-ID: %s", result.queue_id)
    logger.info(
        "Dokumenter: %s",
        result.document_count,
    )
    logger.info("")
    logger.info("NÆSTE TRIN:")
    logger.info("1. Kør: uv run python main.py --debug")
    logger.info(
        "2. Kontrollér at Digital Post-itemet bliver indsendt."
    )
    logger.info(
        "3. Kontrollér at kvitteringen senere afslutter "
        "eller fejler itemet."
    )
    logger.info("=" * 70)


def main() -> None:
    """Start integrationstesten."""
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s [%(levelname)s] "
            "%(name)s: %(message)s"
        ),
        stream=sys.stdout,
        force=True,
    )

    asyncio.run(
        run_test(TEST_PDF_PATH.resolve())
    )


if __name__ == "__main__":
    main()