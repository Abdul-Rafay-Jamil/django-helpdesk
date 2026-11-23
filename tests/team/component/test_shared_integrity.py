from django.contrib.auth import get_user_model
from django.urls import reverse

from helpdesk.models import Ticket


def test_invalid_priority_and_customer_resolution_preserve_shared_ticket(
    client, staff, make_ticket
):
    ticket = make_ticket(assigned_to=staff, priority=2)
    other = make_ticket(priority=4)
    client.force_login(staff)
    detail = reverse("helpdesk:ticket-detail", args=[ticket.pk])
    response = client.post(
        reverse("helpdesk:followups-list"),
        {"ticket": ticket.pk, "title": "Internal triage", "comment": "Internal detail"},
        content_type="application/json",
    )
    assert response.status_code == 201
    private = ticket.followup_set.get()
    assert private.public is False
    ticket.refresh_from_db()
    modified = ticket.modified
    response = client.patch(detail, {"priority": 6}, content_type="application/json")
    assert response.status_code == 400
    assert "priority" in response.json()

    customer = get_user_model().objects.create_user(
        "sqe-reporter", email=ticket.submitter_email
    )
    client.force_login(customer)
    response = client.patch(
        detail, {"status": Ticket.RESOLVED_STATUS}, content_type="application/json"
    )
    assert response.status_code in (401, 403)
    response = client.get(detail)
    assert response.status_code in (401, 403)
    response = client.get(reverse("helpdesk:user_tickets-detail", args=[ticket.pk]))
    assert response.status_code == 200
    assert response.json()["id"] == ticket.pk
    assert "Internal detail" not in response.content.decode()

    ticket.refresh_from_db()
    other.refresh_from_db()
    assert (ticket.priority, ticket.status, ticket.assigned_to_id, ticket.modified) == (
        2,
        Ticket.OPEN_STATUS,
        staff.pk,
        modified,
    )
    assert ticket.followup_set.get() == private
    assert other.priority == 4
    assert not other.followup_set.exists()
