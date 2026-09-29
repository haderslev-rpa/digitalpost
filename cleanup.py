"""SharePoint-cleanup uden ændring af ATS."""
import logging
from ats_repository import get_cleanup_candidates
from configuration import CLEANUP_AGE_DAYS
from sharepoint_service import delete_pdf
logger = logging.getLogger(__name__)

async def cleanup_sharepoint_documents() -> dict[str, int]:
    """Slet filer for completed items ældre end grænsen og returnér tællinger."""
    result = {"items": 0, "deleted": 0, "errors": 0}
    for item in get_cleanup_candidates(CLEANUP_AGE_DAYS):
        result["items"] += 1
        for document in item.data.get("box", {}).get("documents", []):
            try:
                await delete_pdf(document)
                result["deleted"] += 1
            except Exception:
                result["errors"] += 1
                logger.exception("Kunne ikke slette %s", document.get("drive_item_id"))
    return result
