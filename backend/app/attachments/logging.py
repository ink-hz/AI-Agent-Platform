import logging
import re


CONTENT_PATH = re.compile(r"/api/attachments/content/[^?\s]+")
REDACTED_PATH = "/api/attachments/content/[REDACTED]"


class AttachmentTicketRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str):
                args[2] = CONTENT_PATH.sub(REDACTED_PATH, args[2])
                record.args = tuple(args)
                return True
        message = record.getMessage()
        redacted = CONTENT_PATH.sub(REDACTED_PATH, message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def install_attachment_ticket_redaction() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(
        isinstance(item, AttachmentTicketRedactionFilter)
        for item in access_logger.filters
    ):
        access_logger.addFilter(AttachmentTicketRedactionFilter())
