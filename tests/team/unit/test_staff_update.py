from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.http import QueryDict
from rest_framework.exceptions import ValidationError

from helpdesk.forms import TicketForm
from helpdesk.models import Ticket
from helpdesk.serializers import FollowUpSerializer, TicketSerializer
from helpdesk.update_ticket import update_ticket
from helpdesk.views.api import FollowUpViewSet
from helpdesk.views.staff import save_ticket_update


def test_ticket_serializer_rejects_invalid_status_choice(make_ticket):
    ticket = make_ticket(status=Ticket.CLOSED_STATUS)
    serializer = TicketSerializer(ticket, data={"status": 999}, partial=True)
    with pytest.raises(ValidationError) as error:
        serializer.is_valid(raise_exception=True)
    assert set(error.value.detail) == {"status"}
    ticket.refresh_from_db()
    assert ticket.status == Ticket.CLOSED_STATUS
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize("user_from_view", [True, False], ids=["view", "context"])
def test_followup_serializer_persists_readable_comment(
    make_ticket, staff, user_from_view
):
    ticket = make_ticket()
    request = SimpleNamespace(user=staff)
    serializer = FollowUpSerializer(
        data={"ticket": ticket.pk, "title": "Triage", "comment": "Printer inspected"},
        context={"request": request},
    )
    assert serializer.is_valid(), serializer.errors
    if user_from_view:
        view = FollowUpViewSet()
        view.request = request
        view.perform_create(serializer)
    else:
        serializer.save(user=None)
    stored = ticket.followup_set.get()
    assert stored.user_id == staff.pk
    assert FollowUpSerializer(stored).data["comment"] == "Printer inspected"


@pytest.mark.parametrize("target", [Ticket.OPEN_STATUS, Ticket.RESOLVED_STATUS])
def test_staff_save_skips_only_unchanged_status(make_ticket, staff, target):
    ticket = make_ticket()
    previous_modified = ticket.modified
    data = QueryDict(mutable=True)
    data.update(
        {
            "queue": ticket.queue_id,
            "title": ticket.title,
            "priority": ticket.priority,
            "submitter_email": ticket.submitter_email,
            "new_status": target,
        }
    )
    form = TicketForm(
        data=data,
        body_reqd=False,
        instance=ticket,
        queue_choices=[(ticket.queue_id, ticket.queue.title)],
    )
    assert form.is_valid(), form.errors
    assert save_ticket_update(form, ticket, staff) is ticket
    ticket.refresh_from_db()
    assert ticket.status == target
    if target == Ticket.OPEN_STATUS:
        assert ticket.modified == previous_modified
        assert not ticket.followup_set.exists()
    else:
        assert ticket.followup_set.get().new_status == target


@pytest.mark.parametrize(
    "assigned,operation,expected_owner,changes",
    [
        (False, "keep", None, False),
        (True, "keep", "staff", False),
        (False, "clear", None, False),
        (True, "clear", None, True),
        (False, "staff", "staff", True),
        (True, "staff", "staff", False),
        (True, "other", "other", True),
    ],
)
def test_owner_update_decision(
    make_ticket, staff, assigned, operation, expected_owner, changes
):
    other = get_user_model().objects.create_user("sqm-other", is_staff=True)
    owner_ids = {
        "keep": -1,
        "clear": 0,
        "staff": staff.pk,
        "other": other.pk,
        None: None,
    }
    ticket = make_ticket(assigned_to=staff if assigned else None)
    follow_up = update_ticket(staff, ticket, owner=owner_ids[operation])
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == owner_ids[expected_owner]
    assert follow_up.ticketchange_set.filter(field="Owner").count() == int(changes)
    if operation == "clear" and changes:
        assert follow_up.title == "Unassigned"
