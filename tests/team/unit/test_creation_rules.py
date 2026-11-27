import pytest
from rest_framework.exceptions import ValidationError

from helpdesk.models import FollowUp, Ticket
from helpdesk.serializers import TicketSerializer


@pytest.mark.parametrize("priority", [1, 2, 3, 4, 5, "3"], ids=str)
def test_valid_priority_is_persisted(ticket_payload, priority):
    ticket_payload["data"]["priority"] = priority
    serializer = TicketSerializer(**ticket_payload)
    assert serializer.is_valid(), serializer.errors
    ticket = serializer.save()
    ticket.refresh_from_db()
    assert ticket.priority == int(priority)


@pytest.mark.parametrize(
    "priority", [0, 6, -1, 1.5, "urgent", "", None, True, [], {}], ids=repr
)
def test_invalid_priority_creates_no_records(ticket_payload, priority):
    ticket_payload["data"]["priority"] = priority
    serializer = TicketSerializer(**ticket_payload)
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"priority"}
    assert not Ticket.objects.exists()
    assert not FollowUp.objects.exists()


def test_omitted_priority_is_rejected_by_creation_form(ticket_payload):
    serializer = TicketSerializer(**ticket_payload)
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(ValidationError) as error:
        serializer.save()
    assert set(error.value.detail) == {"priority"}
    assert not Ticket.objects.exists()
    assert not FollowUp.objects.exists()


@pytest.mark.parametrize(
    "status",
    [
        None,
        Ticket.OPEN_STATUS,
        Ticket.REOPENED_STATUS,
        Ticket.RESOLVED_STATUS,
        Ticket.CLOSED_STATUS,
        Ticket.DUPLICATE_STATUS,
    ],
    ids=["omitted", "open", "reopened", "resolved", "closed", "duplicate"],
)
def test_creation_starts_open(ticket_payload, status):
    data = ticket_payload["data"]
    data["priority"] = 3
    if status is not None:
        data["status"] = status
    serializer = TicketSerializer(**ticket_payload)
    assert serializer.is_valid(), serializer.errors
    ticket = Ticket.objects.get(pk=serializer.save().pk)
    assert ticket.status == Ticket.OPEN_STATUS
    assert ticket.queue_id == data["queue"]
    assert ticket.title == data["title"]
    assert ticket.description == data["description"]
    assert ticket.submitter_email == data["submitter_email"]
    assert ticket.followup_set.count() == 1


@pytest.mark.parametrize("status", [0, 6, "unknown", None], ids=repr)
def test_invalid_creation_status_is_rejected(ticket_payload, status):
    ticket_payload["data"].update(priority=3, status=status)
    serializer = TicketSerializer(**ticket_payload)
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"status"}
    assert not Ticket.objects.exists()
    assert not FollowUp.objects.exists()


def test_creation_persists_staff_assignee(ticket_payload, staff):
    ticket_payload["data"].update(priority=3, assigned_to=staff.pk)
    serializer = TicketSerializer(**ticket_payload)
    assert serializer.is_valid(), serializer.errors
    ticket = Ticket.objects.get(pk=serializer.save().pk)
    assert ticket.assigned_to_id == staff.pk
    assert ticket.followup_set.get().user_id == staff.pk
