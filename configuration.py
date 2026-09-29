"""Fast konfiguration til processen digitalpost."""

DIGITAL_POST_QUEUE_ID = 13

# Hvor mange dage processen venter på en endelig kvittering.
RECEIPT_DEADLINE_DAYS = 3


# Hvor længe processen kører ved hver planlagte start.
PROCESS_RUNTIME_MINUTES = 55


# Fem minutters pause mellem behandlingerne.
POLL_INTERVAL_SECONDS = 5 * 60


# Faste mailadresser.
# Der læses ingen mailadresser fra .env.
#
# MAIL_SENDER:
# Den tekniske Outlook-bruger, som q-outlook-api sender fra.
#
# MAIL_RECIPIENT:
# Modtageren af mails om kvitteringer, der ikke kunne matches.
MAIL_SENDER = "robot-data@haderslev.dk"
MAIL_RECIPIENT = "automatisering@haderslev.dk"


# SharePoint-placering for Digital Post-dokumenterne.
SHAREPOINT_SITE_NAME = "Automatisering"
SHAREPOINT_LIBRARY_NAME = "Digitalpost"


# Dokumenter må kun slettes, når det tilhørende ATS-item
# er completed, og dokumenterne er ældre end denne grænse.
CLEANUP_AGE_DAYS = 30


def validate_mail_configuration() -> None:
    """Kontrollér de faste mailadresser.

    Output:
        Funktionen returnerer None, når MAIL_SENDER og
        MAIL_RECIPIENT er udfyldt korrekt.

    Fejl:
        RuntimeError, hvis en af mailadresserne mangler.
    """
    if (
        not isinstance(MAIL_SENDER, str)
        or not MAIL_SENDER.strip()
    ):
        raise RuntimeError(
            "MAIL_SENDER mangler i configuration.py."
        )

    if (
        not isinstance(MAIL_RECIPIENT, str)
        or not MAIL_RECIPIENT.strip()
    ):
        raise RuntimeError(
            "MAIL_RECIPIENT mangler i configuration.py."
        )