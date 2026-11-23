import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from helpdesk.models import Ticket


def test_public_submission_and_staff_api_readback(client, queue, staff):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    customer = get_user_model().objects.create_user(
        "sqm-customer", email="customer@example.test"
    )
    client.force_login(customer)
    response = client.post(
        reverse("helpdesk:submit"),
        {
            "queue": queue.pk,
            "title": "Printer cannot connect",
            "body": "The test printer is offline.",
            "submitter_email": customer.email,
            "priority": 2,
        },
    )
    assert response.status_code == 302
    ticket = Ticket.objects.get(title="Printer cannot connect")
    assert ticket.status == Ticket.OPEN_STATUS
    assert ticket.submitter_email == customer.email
    client.force_login(staff)
    ticket_url = reverse("helpdesk:ticket-detail", args=[ticket.pk])
    response = client.patch(
        ticket_url, {"assigned_to": staff.pk}, content_type="application/json"
    )
    assert response.status_code == 200
    comment = "Inspected the printer"
    response = client.post(
        reverse("helpdesk:followups-list"),
        {"ticket": ticket.pk, "title": "Triage", "comment": comment},
        content_type="application/json",
    )
    assert response.status_code == 201
    follow_up_id = response.json()["id"]
    response = client.get(ticket_url)
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == ticket.pk
    assert result["assigned_to"] == staff.pk
    assert result["priority"] == 2
    history = {item["id"]: item for item in result["followup_set"]}
    assert history[follow_up_id]["comment"] == comment
    assert history[follow_up_id]["user"] == staff.pk


def test_staff_update_assigns_resolves_and_skips_identical_status_retry(
    client, make_ticket, staff
):
    ticket = make_ticket()
    assignee = get_user_model().objects.create_user("sqm-assignee", is_staff=True)
    client.force_login(staff)
    url = reverse("helpdesk:update", args=[ticket.pk])
    data = {
        "queue": ticket.queue_id,
        "title": ticket.title,
        "priority": ticket.priority,
        "submitter_email": ticket.submitter_email,
        "owner": assignee.pk,
        "new_status": Ticket.RESOLVED_STATUS,
    }
    assert client.post(url, {**data, "comment": "Printer restored"}).status_code == 302
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == assignee.pk
    assert ticket.status == Ticket.RESOLVED_STATUS
    assert ticket.resolution == "Printer restored"
    follow_up = ticket.followup_set.get()
    assert follow_up.user_id == staff.pk
    assert set(follow_up.ticketchange_set.values_list("field", flat=True)) == {
        "Owner",
        "Status",
    }
    modified = ticket.modified
    assert client.post(url, data).status_code == 302
    ticket.refresh_from_db()
    assert ticket.modified == modified
    assert ticket.followup_set.get() == follow_up
    assert Ticket.objects.count() == 1


@pytest.mark.parametrize("role,expected", [("anonymous", 302), ("customer", 403)])
def test_staff_update_denial_preserves_ticket(client, make_ticket, role, expected):
    ticket = make_ticket()
    before = ticket.modified
    if role == "customer":
        client.force_login(get_user_model().objects.create_user("sqm-customer"))
    response = client.post(
        reverse("helpdesk:update", args=[ticket.pk]),
        {"new_status": Ticket.RESOLVED_STATUS},
    )
    assert response.status_code == expected
    ticket.refresh_from_db()
    assert ticket.status == Ticket.OPEN_STATUS
    assert ticket.modified == before
    assert not ticket.followup_set.exists()
