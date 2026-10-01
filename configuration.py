"""Fast konfiguration til processen digitalpost."""


# Det tekniske ID på Digital Post-køen i Automation Server.
DIGITAL_POST_QUEUE_ID = 13


# Hvor mange dage processen venter på en endelig kvittering.
RECEIPT_DEADLINE_DAYS = 3


# Hvor længe processen kører ved hver planlagte start.
PROCESS_RUNTIME_MINUTES = 55


# Fem minutters pause mellem behandlingerne.
POLL_INTERVAL_SECONDS = 5 * 60


# ------------------------------------------------------------
# MAIL VED UMATCHEDE KVITTERINGER
# ------------------------------------------------------------

# True:
#     Der sendes mail, når en kvittering ikke kan matches
#     med et ATS-item.
#
# False:
#     Den umatchede kvittering logges, men der sendes ingen mail.
#
# Kvitteringen bliver fortsat behandlet og fjernet fra Dueslaget.
SEND_UNMATCHED_RECEIPT_MAIL = True


# Faste mailadresser.
# Der læses ingen mailindstillinger eller mailadresser fra .env.
#
# MAIL_SENDER:
# Den tekniske Outlook-bruger, som q-outlook-api sender fra.
#
# MAIL_RECIPIENT:
# Modtageren af mails om kvitteringer, der ikke kunne matches.
MAIL_SENDER = "robot-data@haderslev.dk"
MAIL_RECIPIENT = "automatisering@haderslev.dk"


# ------------------------------------------------------------
# SHAREPOINT
# ------------------------------------------------------------

# SharePoint-placering for Digital Post-dokumenterne.
SHAREPOINT_SITE_NAME = "Automatisering"
SHAREPOINT_LIBRARY_NAME = "Digitalpost"


# Dokumenter må kun slettes, når det tilhørende ATS-item
# er completed, og dokumenterne er ældre end denne grænse.
CLEANUP_AGE_DAYS = 1


def validate_mail_configuration() -> None:
    """Kontrollér mailkonfigurationen.

    Output:
        Funktionen returnerer None, når konfigurationen er gyldig.

        Når SEND_UNMATCHED_RECEIPT_MAIL er False, skal
        mailadresserne ikke valideres, fordi de ikke anvendes.

    Fejl:
        TypeError, hvis SEND_UNMATCHED_RECEIPT_MAIL ikke er bool.

        RuntimeError, hvis mail er aktiveret, men MAIL_SENDER
        eller MAIL_RECIPIENT mangler.
    """
    if not isinstance(
        SEND_UNMATCHED_RECEIPT_MAIL,
        bool,
    ):
        raise TypeError(
            "SEND_UNMATCHED_RECEIPT_MAIL i configuration.py "
            "skal være True eller False."
        )

    if not SEND_UNMATCHED_RECEIPT_MAIL:
        return

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