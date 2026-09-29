from __future__ import annotations

import asyncio
from collections.abc import Callable
from importlib import import_module
from typing import Any


class IntegrationConfigurationError(RuntimeError):
    """En nødvendig offentlig biblioteksfunktion kunne ikke findes."""


def resolve(candidates: list[tuple[str, str]]) -> Callable[..., Any]:
    errors: list[str] = []
    for module_name, attribute in candidates:
        try:
            value = getattr(import_module(module_name), attribute)
            if callable(value):
                return value
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module_name}.{attribute}: {exc}")
    raise IntegrationConfigurationError("Ingen understøttet integration fundet: " + "; ".join(errors))


async def call(function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Kald både synkrone og asynkrone biblioteksfunktioner korrekt."""
    if asyncio.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    return await asyncio.to_thread(function, *args, **kwargs)


async def download_document(document: dict[str, Any]) -> bytes:
    function = resolve([
        ("q_digitalpost.sharepoint", "download_document"),
        ("q_digitalpost.storage", "download_document"),
    ])
    result = await call(function, drive_item_id=document["drive_item_id"])
    if not isinstance(result, bytes) or not result.startswith(b"%PDF-"):
        raise ValueError(f"{document.get('file_name')} er ikke en gyldig PDF")
    return result


def build_document(content: bytes, metadata: dict[str, Any]) -> Any:
    document_class = resolve([
        ("q_serviceplatformen.digital_post", "DigitalPostDocument"),
        ("q_serviceplatformen", "DigitalPostDocument"),
    ])
    return document_class(
        content=content,
        file_name=metadata["file_name"],
        document_id=metadata["dokument_id"],
        label=metadata["file_name"],
    )


async def submit_only_digital(box: dict[str, Any], main_document: Any, attachments: list[Any]) -> Any:
    function = resolve([
        ("q_serviceplatformen.digital_post", "send_digital_post"),
        ("q_serviceplatformen", "send_digital_post"),
    ])
    recipient = box["recipient"]
    return await call(
        function,
        recipient_id=recipient["id"],
        recipient_id_type=recipient["id_type"],
        subject=box["subject"],
        main_document=main_document,
        attachments=attachments,
        check_registration=False,
    )


async def submit_automatic(box: dict[str, Any], main_document: Any, attachments: list[Any]) -> Any:
    function = resolve([
        ("q_serviceplatformen.digital_post", "send_post_automatically"),
        ("q_serviceplatformen", "send_post_automatically"),
    ])
    recipient = box["recipient"]
    return await call(
        function,
        recipient_id=recipient["id"],
        recipient_id_type=recipient["id_type"],
        subject=box["subject"],
        main_document=main_document,
        attachments=attachments,
        address=box["address"],
    )
