"""Samlet cron-styret Digital Post-proces.

Processen følger Haderslevs normale processkabelon uden Playwright.

Normal drift:
    1. Behandl alle NEW-items i Digital Post-køen.
    2. Hent og behandl alle aktuelle kvitteringer fra Beskedfordeleren.
    3. Vent det konfigurerede interval.
    4. Gentag indtil den maksimale køretid er nået.

Lokal debug:
    Sæt DEBUG_ITEM_REFERENCE i .env og kør:
        uv run python main.py --debug

    Ved lokal debug behandles kun det valgte NEW-item. Derefter kontrolleres
    Beskedfordeleren én gang, og processen afslutter. Dermed forsøger næste
    cyklus ikke at hente det samme item som NEW igen.

Cleanup:
    uv run python main.py --cleanup
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from time import monotonic

from automation_server_client import (
    AutomationServer,
    WorkItemStatus,
    Workqueue,
)

from cleanup import cleanup_sharepoint_documents
from configuration import (
    POLL_INTERVAL_SECONDS,
    PROCESS_RUNTIME_MINUTES,
)
from receipt_worker import drain_receipt_queue
from send_worker import process_send_queue


# ------------------------------------------------------------
# LOGGING (STANDARD)
# ------------------------------------------------------------
DEBUG = "--debug" in sys.argv
CLEANUP_MODE = "--cleanup" in sys.argv

LOG_LEVEL = (
    logging.DEBUG
    if DEBUG
    else logging.INFO
)

logging.basicConfig(
    level=LOG_LEVEL,
    format=(
        "%(asctime)s [%(levelname)s] "
        "%(name)s: %(message)s"
    ),
    stream=sys.stdout,
    force=True,
)

for logger_name in (
    "pika",
    "pika.adapters",
    "pika.connection",
    "pika.channel",
    "pika.adapters.blocking_connection",
    "pika.adapters.utils.connection_workflow",
    "pika.adapters.utils.io_services_utils",
):
    logging.getLogger(
        logger_name
    ).setLevel(
        logging.WARNING
    )

logging.getLogger("httpx").setLevel(
    logging.WARNING
)
logging.getLogger("httpcore").setLevel(
    logging.WARNING
)
logging.getLogger(
    "automation_server_client"
).setLevel(logging.WARNING)
logging.getLogger("debugpy").setLevel(
    logging.WARNING
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# DEBUG-UDVÆLGELSE
# ------------------------------------------------------------
def _vaelg_items_til_behandling(
    workqueue: Workqueue,
) -> Workqueue | list:
    """Vælg items til behandling.

    Output:
        Hvis DEBUG_ITEM_REFERENCE mangler eller er tom:
            Returneres selve workqueue-objektet til normal
            behandling af køen.

        Hvis DEBUG_ITEM_REFERENCE har en værdi:
            Returneres en liste med det første fundne NEW-item
            med den angivne reference.

    Vigtigt:
        DEBUG_ITEM_REFERENCE bruges altid, når værdien findes
        i .env. Det kræver ikke --debug.

        Funktionen ændrer ikke selv itemets status.
        `with item:` i process_send_queue() håndterer den
        aktive behandling og itemets lock.
    """
    item_reference = os.getenv(
        "DEBUG_ITEM_REFERENCE",
        "",
    ).strip()

    if not item_reference:
        logger.info(
            "DEBUG_ITEM_REFERENCE er ikke angivet. "
            "Hele workqueuen behandles normalt."
        )

        return workqueue

    logger.info(
        "DEBUG_ITEM_REFERENCE er angivet. "
        "Søger efter NEW-item med reference=%s.",
        item_reference,
    )

    items = workqueue.get_item_by_reference(
        reference=item_reference,
        status=WorkItemStatus.NEW,
    )

    if not items:
        raise RuntimeError(
            "Ingen NEW-items blev fundet med reference: "
            f"{item_reference}"
        )

    item = items[0]

    logger.info(
        "Item valgt via DEBUG_ITEM_REFERENCE: "
        "id=%s, reference=%s.",
        item.id,
        item.reference,
    )

    return [item]


# ------------------------------------------------------------
# PROCESS-MODE (WORKER)
# ------------------------------------------------------------
async def process_cycle(
    workqueue: Workqueue,
    *,
    debug: bool,
    cycle_number: int,
) -> None:
    """Kør én komplet Digital Post-cyklus.

    Rækkefølge:
        1. Vælg og behandl NEW-items.
        2. Hent og behandl aktuelle kvitteringer.
        3. Log resultatet.

    Output:
        Funktionen returnerer None.
    """
    logger.info("=" * 70)
    logger.info(
        "STARTER DIGITAL POST-CYKLUS %s",
        cycle_number,
    )
    logger.info(
        "Debug-logning: %s",
        debug,
    )
    logger.info("=" * 70)

    selected_items = _vaelg_items_til_behandling(
        workqueue
    )

    processed_items = await process_send_queue(
        selected_items
    )

    receipts = await asyncio.to_thread(
        drain_receipt_queue
    )

    logger.info("=" * 70)
    logger.info(
        "RESULTAT FRA DIGITAL POST-CYKLUS %s",
        cycle_number,
    )
    logger.info(
        "Behandlede NEW-items: %s",
        processed_items,
    )
    logger.info(
        "Brokerbeskeder: %s",
        receipts.messages,
    )
    logger.info(
        "Matchede kvitteringer: %s",
        receipts.matched,
    )
    logger.info(
        "Umatchede kvitteringer: %s",
        receipts.unmatched,
    )
    logger.info(
        "Completed via kvittering: %s",
        receipts.completed,
    )
    logger.info(
        "Failed via kvittering: %s",
        receipts.failed,
    )
    logger.info(
        (
            "Kvitteringer behandlet som fortsat "
            "pending i denne cyklus: %s"
        ),
        receipts.pending,
    )
    logger.info("=" * 70)


async def process_workqueue(
    workqueue: Workqueue,
    debug: bool,
) -> None:
    """Kør Digital Post-processen.

    Normal drift:
        Hvis DEBUG_ITEM_REFERENCE mangler, behandles køen
        med det konfigurerede interval, indtil den maksimale
        køretid er nået.

    Bestemt item:
        Hvis DEBUG_ITEM_REFERENCE er angivet, behandles kun
        det valgte item. Beskedfordeleren kontrolleres bagefter,
        og processen afslutter efter den første cyklus.

    Output:
        Funktionen returnerer None ved normal afslutning.
    """
    item_reference = os.getenv(
        "DEBUG_ITEM_REFERENCE",
        "",
    ).strip()

    single_item_mode = bool(
        item_reference
    )

    logger.info("=" * 70)
    logger.info(
        "STARTER DIGITAL POST-PROCES"
    )
    logger.info(
        "Debug-logning: %s",
        debug,
    )
    logger.info(
        "DEBUG_ITEM_REFERENCE: %s",
        (
            item_reference
            if single_item_mode
            else "Ikke angivet"
        ),
    )
    logger.info(
        "Maksimal køretid: %s minutter",
        PROCESS_RUNTIME_MINUTES,
    )
    logger.info(
        "Kontrolinterval: %s sekunder",
        POLL_INTERVAL_SECONDS,
    )
    logger.info("=" * 70)

    started_at = monotonic()
    runtime_seconds = (
        PROCESS_RUNTIME_MINUTES * 60
    )
    cycle_number = 0

    while (
        monotonic() - started_at
        < runtime_seconds
    ):
        cycle_number += 1

        await process_cycle(
            workqueue,
            debug=debug,
            cycle_number=cycle_number,
        )

        # Når en bestemt reference er angivet, skal processen
        # ikke forsøge at finde samme item som NEW igen.
        if single_item_mode:
            logger.info(
                "DEBUG_ITEM_REFERENCE er angivet. "
                "Processen afsluttes efter én komplet cyklus."
            )
            break

        elapsed_seconds = (
            monotonic() - started_at
        )
        remaining_seconds = (
            runtime_seconds
            - elapsed_seconds
        )

        if remaining_seconds <= 0:
            break

        sleep_seconds = min(
            POLL_INTERVAL_SECONDS,
            remaining_seconds,
        )

        logger.info(
            "Venter %.0f sekunder før næste cyklus.",
            sleep_seconds,
        )

        await asyncio.sleep(
            sleep_seconds
        )

    logger.info("=" * 70)
    logger.info(
        "DIGITAL POST-PROCESSEN ER AFSLUTTET"
    )
    logger.info(
        "Gennemførte cyklusser: %s",
        cycle_number,
    )
    logger.info("=" * 70)



# ------------------------------------------------------------
# CLEANUP-MODE
# ------------------------------------------------------------
async def run_cleanup() -> None:
    """Kør SharePoint-cleanup uden at ændre ATS-items.

    Output:
        Funktionen returnerer None. Antal undersøgte items,
        slettede filer og fejl skrives til loggen.
    """
    logger.info("=" * 70)
    logger.info(
        "STARTER DIGITAL POST-CLEANUP"
    )
    logger.info("=" * 70)

    result = (
        await cleanup_sharepoint_documents()
    )

    logger.info("=" * 70)
    logger.info(
        "RESULTAT FRA DIGITAL POST-CLEANUP"
    )
    logger.info(
        "Undersøgte items: %s",
        result["items"],
    )
    logger.info(
        "Slettede SharePoint-filer: %s",
        result["deleted"],
    )
    logger.info(
        "Fejl: %s",
        result["errors"],
    )
    logger.info("=" * 70)

    if result["errors"] > 0:
        raise RuntimeError(
            "Digital Post-cleanup havde "
            f"{result['errors']} fejl."
        )


# ------------------------------------------------------------
# MAIN ENTRY POINT
# ------------------------------------------------------------
def main() -> None:
    """Start cleanup eller den samlede Digital Post-proces.

    Output:
        Funktionen returnerer None ved normal afslutning.

    Procesvalg:
        --cleanup:
            Kører kun SharePoint-cleanup og afslutter.

        Uden --cleanup:
            Kører afsendelsesflowet og kvitteringsflowet.
    """
    try:
        if CLEANUP_MODE:
            asyncio.run(
                run_cleanup()
            )
            return

        automation_server = (
            AutomationServer.from_environment()
        )
        workqueue = (
            automation_server.workqueue()
        )

        if workqueue is None:
            raise RuntimeError(
                "Automation Server returnerede "
                "ingen workqueue. Kontrollér proces- "
                "og worker-konfigurationen i ATS."
            )

        asyncio.run(
            process_workqueue(
                workqueue,
                debug=DEBUG,
            )
        )

    except Exception:
        logger.exception(
            "Digital Post-processen fejlede."
        )
        raise


if __name__ == "__main__":
    main()