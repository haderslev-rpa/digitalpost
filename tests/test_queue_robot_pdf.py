"""Manuel integrationstest, som opretter ét rigtigt Digital Post-køelement.

Placering:
tests/test_queue_robot_pdf.py

Kør fra repositoryets rodmappe:
    uv run python tests/test_queue_robot_pdf.py

Eller angiv PDF-sti:
    uv run python tests/test_queue_robot_pdf.py --pdf "Robot - test.pdf"

Vigtigt:
    TEST_CPR skal stå i projektets .env-fil.
    Testen uploader PDF-filen til SharePoint og opretter et rigtigt ATS-item.
    Testen sender ikke selv brevet til Serviceplatformen. Det gør main.py,
    når Digital Post-processen bagefter behandler køelementet.
"""

from __future__ import annotations

import argparse
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


load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TESTDATA
# ---------------------------------------------------------------------------
# TEST_CPR skal stå i .env og må ikke hardcodes i testfilen.
# Eksempel i .env:
# TEST_CPR=0101901234
TEST_CPR = os.getenv("TEST_CPR", "").strip()
TEST_SUBJECT = "Integrationstest af Digital Post-processen"
TEST_SOURCE_PROCESS = "digitalpost-manuel-integrationstest"
TEST_SOURCE_ITEM_ID = "robot-test-pdf-001"

# DIGITAL_OR_PHYSICAL_POST anvendes, så testen efterligner en fagproces,
# der tillader automatisk valg mellem Digital Post og fysisk post.
TEST_DELIVERY_METHOD = DeliveryMethod.DIGITAL_OR_PHYSICAL_POST

TEST_ADDRESS = DigitalPostAddress(
    name="Rune Hedegaard",
    street_name="Farrisvej",
    house_number="31",
    postal_code="6580",
    city="Ødis Bramdrup",
    floor="",
    door="",
    co_name="",
    country_code="DK",
)

# De mest sandsynlige PDF-navne. Den første eksisterende fil bruges.
DEFAULT_PDF_CANDIDATES = (
    PROJECT_ROOT / "Robot - test.pdf",
    PROJECT_ROOT / "Robot test.pdf",
    Path(__file__).resolve().parent / "Robot - test.pdf",
    Path(__file__).resolve().parent / "Robot test.pdf",
)


def validate_test_configuration() -> None:
    """Kontrollér testkonfigurationen.

    Output:
        Funktionen returnerer None, når TEST_CPR indeholder præcis 10 cifre.

    Fejl:
        RuntimeError, hvis TEST_CPR mangler i .env.
        ValueError, hvis TEST_CPR ikke indeholder præcis 10 cifre.
    """
    if not TEST_CPR:
        raise RuntimeError(
            "Miljøvariablen TEST_CPR mangler. Tilføj eksempelvis "
            "TEST_CPR=0101901234 i projektets .env-fil."
        )

    normalized_cpr = "".join(
        character for character in TEST_CPR if character.isdigit()
    )

    if len(normalized_cpr) != 10:
        raise ValueError(
            "TEST_CPR i .env skal indeholde præcis 10 cifre."
        )


def find_default_pdf() -> Path:
    """Find standard-test-PDF'en.

    Output:
        Path til den første eksisterende PDF blandt DEFAULT_PDF_CANDIDATES.

    Fejl:
        FileNotFoundError med de undersøgte placeringer, hvis filen mangler.
    """
    for candidate in DEFAULT_PDF_CANDIDATES:
        if candidate.is_file():
            return candidate

    checked_paths = "\n".join(
        f"  - {candidate}" for candidate in DEFAULT_PDF_CANDIDATES
    )
    raise FileNotFoundError(
        "Test-PDF'en blev ikke fundet. Der blev søgt her:\n"
        f"{checked_paths}"
    )


def validate_pdf(pdf_path: Path) -> bytes:
    """Læs og validér test-PDF'en.

    Output:
        PDF-filens indhold som bytes.

    Fejl:
        FileNotFoundError, hvis filen mangler.
        ValueError, hvis filen er tom eller ikke starter med PDF-signaturen.
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
    """Upload test-PDF'en og opret ét rigtigt Digital Post-item.

    Input:
        pdf_path:
            Sti til den PDF, som skal bruges som hoveddokument.

    Output:
        DigitalPostResult fra q-digitalpost.

        Ved succes forventes blandt andet:
            status = "QUEUED"
            forsendelses_id = UUID for forsendelsen
            queue_id = Digital Post-køens tekniske ID
            document_count = 1

    Bemærk:
        Funktionen efterligner en anden robotproces. Funktionen sender ikke
        direkte til Serviceplatformen, men bestiller forsendelsen gennem
        q-digitalposts offentlige standardfunktion.
    """
    validate_test_configuration()
    pdf_content = validate_pdf(pdf_path)

    normalized_cpr = "".join(
        character for character in TEST_CPR if character.isdigit()
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


def validate_result(result: DigitalPostResult) -> None:
    """Kontrollér resultatet fra q-digitalpost.

    Output:
        Funktionen returnerer None, når resultatet er QUEUED og indeholder
        de forventede identifikatorer.

    Fejl:
        RuntimeError, hvis bestillingen ikke blev lagt korrekt i køen.
    """
    if result.status != "QUEUED":
        raise RuntimeError(
            "Testen forventede status QUEUED, men modtog "
            f"{result.status!r}. Reason={result.reason!r}, "
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


async def run_test(pdf_path: Path) -> None:
    """Kør hele feeder-testen og log resultatet.

    Output:
        Funktionen returnerer None efter et valideret QUEUED-resultat.
    """
    logger.info("=" * 70)
    logger.info("STARTER DIGITAL POST FEEDER-TEST")
    logger.info("PDF: %s", pdf_path)
    logger.info("TEST_CPR er indlæst fra .env.")
    logger.info("Leveringsmetode: %s", TEST_DELIVERY_METHOD.value)
    logger.info("Kildeproces: %s", TEST_SOURCE_PROCESS)
    logger.info("Kildeelement: %s", TEST_SOURCE_ITEM_ID)
    logger.info("=" * 70)

    result = await create_test_queue_item(pdf_path)

    logger.info("Resultat fra q-digitalpost:")
    pprint(result)

    validate_result(result)

    logger.info("=" * 70)
    logger.info("TEST OK: Forsendelsen er lagt i Digital Post-køen.")
    logger.info("Forsendelses-ID: %s", result.forsendelses_id)
    logger.info("Queue-ID: %s", result.queue_id)
    logger.info("Dokumenter: %s", result.document_count)
    logger.info("")
    logger.info("NÆSTE TRIN:")
    logger.info("1. Kør: uv run python main.py --debug")
    logger.info("2. Kontrollér at itemet bliver PENDING_USER_ACTION.")
    logger.info("3. Kontrollér senere at kvitteringen completer eller fejler itemet.")
    logger.info("4. Test cleanup separat med: uv run python main.py --cleanup")
    logger.info("=" * 70)


def parse_arguments() -> argparse.Namespace:
    """Læs kommandolinjeargumenter.

    Output:
        argparse.Namespace med:

        pdf:
            Path til en PDF, hvis --pdf er angivet.

            None, hvis standardplaceringerne skal bruges.

    Bemærkning:
        VS Code kan ved lokal debug tilføje et tomt argument:

            ""

        Tomme argumenter fjernes derfor, inden argparse
        behandler argumentlisten.

        Andre ukendte argumenter giver fortsat en tydelig fejl.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Opret ét rigtigt Digital Post-køelement "
            "fra Robot-test-PDF'en."
        )
    )

    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help=(
            "Valgfri sti til test-PDF. Hvis argumentet "
            "udelades, søges der efter 'Robot - test.pdf' "
            "og 'Robot test.pdf' i projektroden "
            "og tests-mappen."
        ),
    )

    # VS Code-debuggeren kan tilføje et tomt argument.
    # Rigtige argumenter bevares uændret.
    cleaned_arguments = [
        argument
        for argument in sys.argv[1:]
        if argument.strip()
    ]

    return parser.parse_args(
        cleaned_arguments
    )


def main() -> None:
    """Start feeder-testen."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )

    arguments = parse_arguments()
    pdf_path = arguments.pdf or find_default_pdf()

    asyncio.run(run_test(pdf_path.resolve()))


if __name__ == "__main__":
    main()
