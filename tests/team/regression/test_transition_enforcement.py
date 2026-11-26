import pytest
from django.urls import reverse

from helpdesk.models import Ticket
from helpdesk.update_ticket import InvalidStatusTransition, update_ticket


def test_shared_update_rejects_before_other_fields_change(make_ticket, staff):
    ticket = make_ticket(status=Ticket.CLOSED_STATUS, resolution="Existing resolution")
    original = (ticket.title, ticket.status, ticket.assigned_to_id, ticket.modified)

    with pytest.raises(InvalidStatusTransition):
        update_ticket(
            staff,
            ticket,
            title="Would be changed",
            owner=staff.pk,
            comment="Would become history",
            new_status=Ticket.RESOLVED_STATUS,
        )

    ticket.refresh_from_db()
    assert (
        ticket.title,
        ticket.status,
        ticket.assigned_to_id,
        ticket.modified,
    ) == original
    assert ticket.resolution == "Existing resolution"
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize("allowed", [False, True], ids=["forbidden", "configured"])
def test_ticket_patch_applies_configured_flow_before_saving_fields(
    client, make_ticket, staff, monkeypatch, allowed
):
    ticket = make_ticket(status=Ticket.CLOSED_STATUS)
    original = ticket.title, ticket.modified
    if allowed:
        monkeypatch.setattr(
            Ticket,
            "STATUS_CHOICES_FLOW",
            {Ticket.CLOSED_STATUS: (Ticket.RESOLVED_STATUS,)},
        )
    client.force_login(staff)
    response = client.patch(
        reverse("helpdesk:ticket-detail", args=[ticket.pk]),
        {"title": "New title", "status": Ticket.RESOLVED_STATUS},
        content_type="application/json",
    )
    ticket.refresh_from_db()
    if allowed:
        assert response.status_code == 200
        assert (ticket.title, ticket.status) == ("New title", Ticket.RESOLVED_STATUS)
    else:
        assert response.status_code == 400
        assert "status" in response.json()
        assert (ticket.title, ticket.modified) == original
        assert ticket.status == Ticket.CLOSED_STATUS
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize("staff_detail", [False, True], ids=["public", "staff"])
def test_detail_close_respects_configured_flow(
    client, make_ticket, staff, monkeypatch, staff_detail
):
    ticket = make_ticket(
        status=Ticket.RESOLVED_STATUS, resolution="Existing resolution"
    )
    original = ticket.modified
    monkeypatch.setattr(
        Ticket,
        "STATUS_CHOICES_FLOW",
        {Ticket.RESOLVED_STATUS: (Ticket.REOPENED_STATUS,)},
    )
    if staff_detail:
        client.force_login(staff)
        response = client.get(
            reverse("helpdesk:view", args=[ticket.pk]), {"close": "1"}
        )
        assert response.status_code == 302
    else:
        response = client.get(ticket.ticket_url + "&close=1")
        assert response.status_code == 403
    ticket.refresh_from_db()
    assert ticket.status == Ticket.RESOLVED_STATUS
    assert ticket.resolution == "Existing resolution"
    assert ticket.modified == original
    assert not ticket.followup_set.exists()
