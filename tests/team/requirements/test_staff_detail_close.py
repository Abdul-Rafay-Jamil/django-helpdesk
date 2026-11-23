import pytest
from django.urls import reverse

from helpdesk.models import Ticket


@pytest.mark.parametrize("owned", [False, True], ids=["unassigned", "assigned"])
def test_staff_detail_close_persists_allowed_closed_transition(client, make_ticket, staff, owned):
    owner = staff if owned else None
    ticket = make_ticket(status=Ticket.RESOLVED_STATUS, assigned_to=owner,
                         resolution="Paper path cleared")
    client.force_login(staff)
    response = client.get(reverse("helpdesk:view", args=[ticket.pk]), {"close": "1"})
    ticket.refresh_from_db()
    history = list(ticket.followup_set.values_list("new_status", "comment"))
    assert ticket.status == Ticket.CLOSED_STATUS, (
        f"HTTP {response.status_code} followed by persisted status {ticket.status}, "
        f"resolution {ticket.resolution!r} and history {history!r}"
    )
    assert ticket.assigned_to == owner
    assert ticket.resolution == "Paper path cleared"
    followup = ticket.followup_set.get()
    assert followup.new_status == Ticket.CLOSED_STATUS
    assert followup.ticketchange_set.filter(field="Status").count() == 1
