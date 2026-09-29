"""Interne tilstande og resultatmodeller til digitalpost-processen."""

from dataclasses import dataclass
from enum import StrEnum


class ProcessingState(StrEnum):
    READY_TO_SEND = "READY_TO_SEND"
    SUBMITTING = "SUBMITTING"
    WAITING_FOR_RECEIPT = "WAITING_FOR_RECEIPT"
    READY_FOR_MANUAL_RESEND = "READY_FOR_MANUAL_RESEND"
    DELIVERED = "DELIVERED"
    RECEIPT_FAILED = "RECEIPT_FAILED"
    RECEIPT_REJECTED = "RECEIPT_REJECTED"
    RECEIPT_REQUIRES_REVIEW = "RECEIPT_REQUIRES_REVIEW"


@dataclass(slots=True)
class PollResult:
    messages: int = 0
    matched: int = 0
    unmatched: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0
