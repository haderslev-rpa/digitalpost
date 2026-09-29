"""Fælles håndtering af danske tidspunkter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


class DanishTime:
    """Konvertér og formatér tidspunkter i dansk lokal tid.

    Tidszonen Europe/Copenhagen håndterer automatisk:

    - dansk normaltid, UTC+01:00
    - dansk sommertid, UTC+02:00

    Alle metoder returnerer timezone-aware værdier.
    """

    TIME_ZONE = ZoneInfo(
        "Europe/Copenhagen"
    )

    @classmethod
    def now(
        cls,
    ) -> datetime:
        """Returnér det aktuelle tidspunkt i dansk tid.

        Output:
            En timezone-aware datetime.

            Eksempel om sommeren:

                2026-09-29 21:45:51.561961+02:00

            Eksempel om vinteren:

                2026-12-29 20:45:51.561961+01:00
        """
        return datetime.now(
            cls.TIME_ZONE
        )

    @classmethod
    def convert(
        cls,
        value: datetime,
    ) -> datetime:
        """Konvertér et tidspunkt til dansk tid.

        Input:
            value:
                En datetime med eller uden tidszone.

                En datetime uden tidszone fortolkes som UTC.
                Dette er en bevidst sikkerhedsregel, fordi svar
                fra Serviceplatformen typisk er UTC.

        Output:
            En timezone-aware datetime i Europe/Copenhagen.

        Fejl:
            TypeError, hvis value ikke er en datetime.
        """
        if not isinstance(
            value,
            datetime,
        ):
            raise TypeError(
                "value skal være en datetime."
            )

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            cls.TIME_ZONE
        )

    @classmethod
    def parse(
        cls,
        value: str,
    ) -> datetime:
        """Fortolk ISO-tekst og konvertér til dansk tid.

        Input:
            value:
                ISO-formateret tekst.

                Understøtter både:

                    2026-09-29T19:45:51.561961+00:00

                og:

                    2026-09-29T19:45:51.561961Z

        Output:
            En timezone-aware datetime i dansk tid.

        Fejl:
            ValueError ved tom eller ugyldig tekst.
        """
        if not isinstance(
            value,
            str,
        ):
            raise TypeError(
                "value skal være tekst."
            )

        cleaned_value = value.strip()

        if not cleaned_value:
            raise ValueError(
                "value må ikke være tom."
            )

        try:
            parsed_value = datetime.fromisoformat(
                cleaned_value.replace(
                    "Z",
                    "+00:00",
                )
            )
        except ValueError as error:
            raise ValueError(
                "Tidspunktet er ikke gyldig ISO-tekst: "
                f"{value!r}"
            ) from error

        return cls.convert(
            parsed_value
        )

    @classmethod
    def iso(
        cls,
        value: datetime,
    ) -> str:
        """Returnér et tidspunkt som dansk ISO-tekst.

        Input:
            value:
                En datetime med eller uden tidszone.

        Output:
            ISO-tekst med korrekt dansk UTC-offset.

            Eksempel om sommeren:

                2026-09-29T21:45:51.561961+02:00

            Eksempel om vinteren:

                2026-12-29T20:45:51.561961+01:00
        """
        return cls.convert(
            value
        ).isoformat()

    @classmethod
    def iso_or_none(
        cls,
        value: datetime | None,
    ) -> str | None:
        """Returnér dansk ISO-tekst eller None.

        Output:
            Dansk ISO-tekst, hvis value er en datetime.

            None, hvis value er None.
        """
        if value is None:
            return None

        return cls.iso(
            value
        )

    @classmethod
    def from_value(
        cls,
        value: Any,
    ) -> datetime:
        """Konvertér datetime eller ISO-tekst til dansk tid.

        Input:
            value:
                Enten datetime eller ISO-formateret tekst.

        Output:
            En timezone-aware datetime i dansk tid.
        """
        if isinstance(
            value,
            datetime,
        ):
            return cls.convert(
                value
            )

        if isinstance(
            value,
            str,
        ):
            return cls.parse(
                value
            )

        raise TypeError(
            "value skal være datetime eller ISO-tekst."
        )