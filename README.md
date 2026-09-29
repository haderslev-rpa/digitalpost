# digitalpost

Proces til afsendelse af Digital Post og fysisk post samt behandling af kvitteringer fra KOMBIT Beskedfordeleren.

## Installation

Installér og opdatér projektets afhængigheder:

```bash
uv sync
```

## Kør processen

Start den normale proces:

```bash
uv run python main.py
```

Processen:

1. Behandler nye ATS-items.
2. Sender Digital Post eller fysisk post.
3. Henter kvitteringer fra Beskedfordeleren.
4. Opdaterer det tilhørende ATS-item.
5. Gentager behandlingen hvert femte minut.

Kun én Automation Server-worker må have:

```text
supports = digitalpost
```

## Kør cleanup

```bash
uv run python main.py --cleanup
```

## Manuel genfremsendelse

Hvis en endelig kvittering ikke kommer inden fristen, markeres itemet til manuel vurdering.

Vælg **Retry** i Automation Server for aktivt at genfremsende brevet.

Processen gemmer:

```text
submission_count
manual_resend_count
last_submission_type
submissions
```

så alle afsendelser og manuelle genfremsendelser kan spores.

## Dansk tid

Alle nye tidspunkter gemmes i dansk tid gennem:

```text
danish_time.py
```

Tidszonen skifter automatisk mellem dansk sommer- og vintertid.

## Mail ved umatchede kvitteringer

Mail er som standard slået fra:

```dotenv
SEND_UNMATCHED_RECEIPT_MAIL=false
```

Aktivér mail med:

```dotenv
SEND_UNMATCHED_RECEIPT_MAIL=true
```

## Test koden

Kontrollér Python-filerne:

```bash
uv run python -m py_compile \
    danish_time.py \
    receipt_worker.py \
    send_worker.py \
    main.py
```

## Test Beskedfordeleren

Kør den sikre test i `q-serviceplatformen`:

```bash
uv run python tests/test_check_message_broker.py
```

Testen henter højst én besked og lægger den tilbage i Dueslaget.
