# digitalpost

Kør normal proces med `uv run python main.py` og cleanup med `uv run python main.py --cleanup`.

Processen behandler først NEW-items og derefter Beskedfordeleren hvert femte minut i 55 minutter. Kun én Automation Server-worker må have `supports = digitalpost`.

## Manuel genfremsendelse

Defer-monitoren gør et udløbet WAITING_FOR_RECEIPT-item NEW. Processen markerer det derefter som exception og READY_FOR_MANUAL_RESEND uden at sende. ATS-knappen Retry gør itemet NEW og bevarer state. Kun kombinationen NEW + READY_FOR_MANUAL_RESEND udløser en aktiv genfremsendelse.

## Krævet adapter

Læs REQUIRED_Q_HADERSLEV_VBO.txt. Kontrollér også den konkrete mailimport i mail_service.py og download_document i q-sharepoint-api.


## Mail ved umatchbar kvittering

Mail sendes gennem `q_outlook_api.functionality.mail_api.send_mail`.
Afsenderen hentes fra miljøvariablen `mail`. Modtageren hentes først fra
`DIGITALPOST_UNMATCHED_MAIL`, derefter fra `runemail`, og ellers bruges
`automatisering@haderslev.dk`.

Mailteksten indeholder alle identifikatorer, AMQP-metadata og den rå XML.
Brokerbeskeden ackes først, når `send_mail()` er gennemført uden exception.
