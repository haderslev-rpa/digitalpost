"""Hjælpefunktioner til Automation Server-items."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from automation_server_client import WorkItemStatus


def defer_item(
    item: Any,
    message: str,
) -> None:
    """Sæt et afsendt item til Pending User Action.

    Input:
        item:
            WorkItem-objektet fra Automation Server.

        message:
            Statusbeskeden, som skal vises på itemet.

    Output:
        Funktionen returnerer None.

        Itemets egentlige Automation Server-status ændres til:

            PENDING_USER_ACTION

        Funktionen forventer, at den kaldende kode allerede har
        gemt defer-tidspunktet i:

            item.data["defer"]

    Vigtigt:
        automation-server-client version 0.3.0 har ikke en
        item.defer()-metode. Derfor anvendes update_status().

        Når funktionen returnerer uden exception, forlader
        process_send_queue() efterfølgende `with item:` normalt.
        Dermed afsluttes den aktive behandling korrekt.
    """
    update_status = getattr(
        item,
        "update_status",
        None,
    )

    if not callable(update_status):
        raise RuntimeError(
            "Den installerede Automation Server-klient "
            "har ingen item.update_status()-funktion."
        )

    if not isinstance(
        getattr(item, "data", None),
        dict,
    ):
        raise RuntimeError(
            "Itemet mangler en gyldig data-dictionary."
        )

    defer_value = item.data.get("defer")

    if not isinstance(defer_value, str) or not defer_value.strip():
        raise RuntimeError(
            "item.data['defer'] skal være gemt, før "
            "defer_item() kaldes."
        )

    if not isinstance(message, str) or not message.strip():
        raise ValueError(
            "message skal være udfyldt tekst."
        )

    # Opdatér også statusvisningen inde i item.data.
    # Dette er ikke ATS-databasens egentlige statuskolonne,
    # men det gør JSON-visningen forståelig.
    item.data["status"] = {
        "status": "Pending User Action",
        "status_code": (
            "Afventer endelig kvittering"
        ),
    }

    # Gem den opdaterede JSON-data, før status ændres.
    item.update(
        item.data
    )

    # Opdatér Automation Servers egentlige status.
    update_status(
        WorkItemStatus.PENDING_USER_ACTION.value,
        message.strip(),
    )


def item_is_completed(
    item: Any,
) -> bool:
    """Kontrollér om et WorkItem er completed.

    Output:
        True, hvis itemets status er COMPLETED.

        False ved alle andre statusser.
    """
    status = getattr(
        item,
        "status",
        None,
    )

    if (
        status is None
        and isinstance(
            getattr(item, "data", None),
            dict,
        )
    ):
        embedded_status = item.data.get(
            "status"
        )

        if isinstance(
            embedded_status,
            dict,
        ):
            status = embedded_status.get(
                "status"
            )
        else:
            status = embedded_status

    status_value = getattr(
        status,
        "value",
        status,
    )

    return (
        str(status_value or "")
        .strip()
        .upper()
        .replace(" ", "_")
        == "COMPLETED"
    )


def parse_datetime(
    value: Any,
) -> datetime:
    """Fortolk et tidspunkt.

    Input:
        value:
            En datetime eller ISO-formateret tekst.

    Output:
        En timezone-aware datetime.

        Datetime uden tidszone fortolkes som UTC.
    """
    if isinstance(value, datetime):
        result = value

    elif isinstance(value, str):
        result = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )

    else:
        raise ValueError(
            "Kan ikke fortolke tidspunkt: "
            f"{value!r}"
        )

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result