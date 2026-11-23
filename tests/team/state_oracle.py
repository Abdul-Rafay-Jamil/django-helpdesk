"""BR04 transition oracle from the documented default workflow."""

from helpdesk.models import Ticket

OPEN = Ticket.OPEN_STATUS
REOPENED = Ticket.REOPENED_STATUS
RESOLVED = Ticket.RESOLVED_STATUS
CLOSED = Ticket.CLOSED_STATUS
DUPLICATE = Ticket.DUPLICATE_STATUS

ALLOWED = {
    OPEN: (OPEN, RESOLVED, CLOSED, DUPLICATE),
    REOPENED: (REOPENED, RESOLVED, CLOSED, DUPLICATE),
    RESOLVED: (REOPENED, RESOLVED, CLOSED),
    CLOSED: (REOPENED, CLOSED),
    DUPLICATE: (REOPENED, DUPLICATE),
}
LABELS = dict(zip(ALLOWED, ("Open", "Reopened", "Resolved", "Closed", "Duplicate")))
