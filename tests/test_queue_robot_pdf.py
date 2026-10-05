"""Manuel integrationstest, som opretter ét rigtigt Digital Post-køelement.

Placering:
    tests/test_queue_robot_pdf.py

Kør fra repositoryets rodmappe:
    uv run python tests/test_queue_robot_pdf.py

Vigtigt:
    TEST_CPR skal stå i projektets .env-fil.

    Testen uploader PDF-filen til SharePoint og opretter et rigtigt ATS-item.

    Testen sender ikke selv brevet til Serviceplatformen. Det gør main.py,
    når Digital Post-processen bagefter behandler køelementet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from pprint import pprint

from dotenv import load_dotenv


# Gør repositoryets rodmappe tilgængelig, når filen køres fra tests-mappen.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from q_digitalpost.digital_post import DigitalPost
from q_digitalpost.models import (
    DeliveryMethod,
    DigitalPostAddress,
    DigitalPostResult,
)


# Indlæs miljøvariabler fra projektets .env-fil.
load_dotenv(PROJECT_ROOT / ".env")


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PDF-FIL TIL TESTEN
# ---------------------------------------------------------------------------

# Ret kun denne sti, når testen skal bruge en anden PDF.
#
# Stien starter fra repositoryets rodmappe.
# Eksempel:
#     tests_local/test robot - Maria.pdf
TEST_PDF_PATH = PROJECT_ROOT / "tests_local" / "test robot - Maria.pdf"


# ---------------------------------------------------------------------------
# TESTDATA
# ---------------------------------------------------------------------------

# TEST_CPR skal stå i projektets .env-fil og må ikke hardcodes her.
#
# Eksempel i .env:
# TEST_CPR=0101901234
TEST_CPR = os.getenv("TEST_CPR", "").strip()

TEST_SUBJECT = "Integrationstest af Digital Post-processen"
TEST_SOURCE_PROCESS = "digitalpost-manuel-integrationstest"
TEST_SOURCE_ITEM_ID = "robot-test-pdf-001"

# Tillad automatisk valg mellem Digital Post og fysisk post.
TEST_DELIVERY_METHOD = DeliveryMethod.DIGITAL_OR_PHYSICAL_POST

TEST_ADDRESS = DigitalPostAddress(
    name="Maria Hedegaard",
    street_name="Langstrengvej",
    house_number="14",
    postal_code="6100",
    city="Haderslev",
    floor="",
    door="",
    co_name="",
    country_code="DK",
)


def validate_test_configuration() -> None:
    """Kontrollér TEST_CPR og den angivne PDF-sti.

    Funktionen returnerer None, når konfigurationen er gyldig.

    Fejl:
        RuntimeError:
            Hvis TEST_CPR mangler i .env.

        ValueError:
            Hvis TEST_CPR ikke indeholder præcis 10 cifre.

        FileNotFoundError:
            Hvis PDF-filen ikke findes.
    """
    if not TEST_CPR:
        raise RuntimeError(
            "Miljøvariablen TEST_CPR mangler. "
            "Tilføj TEST_CPR=0101901234 i projektets .env-fil."
        )

    normalized_cpr = "".join(
        character
        for character in TEST_CPR
        if character.isdigit()
    )

    if len(normalized_cpr) != 10:
        raise ValueError(
            "TEST_CPR i .env skal indeholde præcis 10 cifre."
        )

    if not TEST_PDF_PATH.is_file():
        raise FileNotFoundError(
            "PDF-filen blev ikke fundet.\n"
            f"Den angivne sti er:\n{TEST_PDF_PATH}\n\n"
            "Kontrollér TEST_PDF_PATH øverst i testfilen."
        )


def validate_pdf(pdf_path: Path) -> bytes:
    """Læs og validér test-PDF'en.

    Output:
        PDF-filens indhold som bytes.

    Fejl:
        FileNotFoundError:
            Hvis filen ikke findes.

        ValueError:
            Hvis filen er tom eller ikke har en PDF-signatur.
    """
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
    """Upload PDF-filen og opret ét rigtigt Digital Post-køelement.

    Input:
        pdf_path:
            Den præcise sti til PDF-filen.

    Output:
        DigitalPostResult fra q-digitalpost.
    """
    validate_test_configuration()

    pdf_content = validate_pdf(pdf_path)

    normalized_cpr = "".join(
        character
        for character in TEST_CPR
        if character.isdigit()
    )

    digital_post = DigitalPost()

    result = await digital_post.send_digital_post(
        pdf_content=pdf_content,
        file_name=pdf_path.name,
        cpr_or_cvr=normalized_cpr,
        subject=TEST_SUBJECT,
        source_process=TEST_SOURCE_PROCESS,
        source_item_id=TEST_SOURCE_ITEM_ID,
        delivery_method=TEST_DELIVERY_METHOD,
        attachments=[],
        address=TEST_ADDRESS,
    )

    return result


def validate_result(
    result: DigitalPostResult,
) -> None:
    """Kontrollér resultatet fra q-digitalpost.

    Funktionen returnerer None, når resultatet er QUEUED og indeholder
    de forventede identifikatorer.

    Fejl:
        RuntimeError:
            Hvis bestillingen ikke blev lagt korrekt i køen.
    """
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
    logger.info("STARTER DIGITAL POST FEEDER-TEST")
    logger.info("PDF: %s", pdf_path)
    logger.info("TEST_CPR er indlæst fra .env.")
    logger.info(
        "Leveringsmetode: %s",
        TEST_DELIVERY_METHOD.value,
    )
    logger.info(
        "Kildeproces: %s",
        TEST_SOURCE_PROCESS,
    )
    logger.info(
        "Kildeelement: %s",
        TEST_SOURCE_ITEM_ID,
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
    logger.info(
        "Queue-ID: %s",
        result.queue_id,
    )
    logger.info(
        "Dokumenter: %s",
        result.document_count,
    )
    logger.info("")
    logger.info("NÆSTE TRIN:")
    logger.info("1. Kør: uv run python main.py --debug")
    logger.info(
        "2. Kontrollér at itemet bliver PENDING_USER_ACTION."
    )
    logger.info(
        "3. Kontrollér senere at kvitteringen completer "
        "eller fejler itemet."
    )
    logger.info(
        "4. Test cleanup separat med: "
        "uv run python main.py --cleanup"
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