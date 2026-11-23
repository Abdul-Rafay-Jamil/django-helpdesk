import pytest
from django.urls import reverse

from helpdesk.models import Ticket


@pytest.mark.parametrize("route", ["staff-form", "ticket-api"])
def test_closed_to_resolved_is_rejected_without_a_write(client, make_ticket, staff, route):
    ticket = make_ticket(status=Ticket.CLOSED_STATUS, resolution="Existing resolution")
    before = (ticket.status, ticket.resolution, ticket.modified)
    client.force_login(staff)
    if route == "staff-form":
        response = client.post(reverse("helpdesk:update", args=[ticket.pk]), {
            "queue": ticket.queue_id, "title": ticket.title, "priority": ticket.priority,
            "submitter_email": ticket.submitter_email, "new_status": Ticket.RESOLVED_STATUS,
        })
    else:
        response = client.patch(reverse("helpdesk:ticket-detail", args=[ticket.pk]),
                                {"status": Ticket.RESOLVED_STATUS},
                                content_type="application/json")
    ticket.refresh_from_db()
    actual = (ticket.status, ticket.resolution, ticket.modified)
    history_count = ticket.followup_set.count()
    assert actual == before and history_count == 0, (
        f"{route} returned HTTP {response.status_code}, persisted {actual}, "
        f"and left {history_count} history rows. Expected {before} and zero rows."
    )
    if route == "ticket-api":
        assert 400 <= response.status_code < 500
