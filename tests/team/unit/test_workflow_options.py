from datetime import UTC, datetime, timedelta
from itertools import product
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.forms import PublicTicketForm, TicketForm
from helpdesk.models import Ticket
from helpdesk.views.staff import get_due_date_from_form_or_ticket, save_ticket_update


def bound_form(ticket, **updates):
    data = QueryDict(mutable=True)
    data.update(
        {
            "queue": ticket.queue_id,
            "title": ticket.title,
            "priority": ticket.priority,
            "submitter_email": ticket.submitter_email,
            **updates,
        }
    )
    form = TicketForm(
        data=data,
        body_reqd=False,
        instance=ticket,
        queue_choices=[(ticket.queue_id, ticket.queue.title)],
    )
    assert form.is_valid(), form.errors
    return form


@pytest.mark.parametrize(
    "year,month,day",
    [
        pytest.param(y, m, d, id=f"{int(y)}{int(m)}{int(d)}")
        for y, m, d in product((False, True), repeat=3)
    ],
)
def test_legacy_due_date_requires_all_three_parts(monkeypatch, year, month, day):
    now = datetime(2026, 11, 23, tzinfo=UTC)
    monkeypatch.setattr(timezone, "now", lambda: now)
    form = SimpleNamespace(
        cleaned_data={
            "due_date_year": 2027 if year else 0,
            "due_date_month": 2 if month else 0,
            "due_date_day": 3 if day else 0,
        }
    )
    due = get_due_date_from_form_or_ticket(form, SimpleNamespace(due_date=None))
    assert due == (datetime(2027, 2, 3, tzinfo=UTC) if year and month and day else None)


def test_existing_due_date_is_kept_for_complete_legacy_input():
    existing = datetime(2027, 2, 4, tzinfo=UTC)
    form = SimpleNamespace(
        cleaned_data={"due_date_year": 2027, "due_date_month": 2, "due_date_day": 3}
    )
    assert (
        get_due_date_from_form_or_ticket(form, SimpleNamespace(due_date=existing))
        == existing
    )


@pytest.mark.parametrize(
    "initial,choice,writes",
    [
        ("empty", "keep", False),
        ("assigned", "keep", False),
        ("empty", "clear", False),
        ("assigned", "clear", True),
        ("empty", "staff", True),
        ("assigned", "staff", False),
        ("assigned", "other", True),
    ],
)
def test_staff_owner_noop_and_dispatch(make_ticket, staff, initial, choice, writes):
    other = get_user_model().objects.create_user("sqm-other-owner", is_staff=True)
    ticket = make_ticket(assigned_to=staff if initial == "assigned" else None)
    owner = {"keep": -1, "clear": 0, "staff": staff.pk, "other": other.pk}[choice]
    before = ticket.assigned_to_id, ticket.modified
    save_ticket_update(bound_form(ticket, owner=owner), ticket, staff)
    ticket.refresh_from_db()
    if writes:
        assert ticket.assigned_to_id == (owner or None)
        assert ticket.followup_set.get().ticketchange_set.get(field="Owner")
    else:
        assert (ticket.assigned_to_id, ticket.modified) == before
        assert not ticket.followup_set.exists()


def test_staff_followup_persists_due_date_and_time_spent(make_ticket, staff):
    ticket = make_ticket()
    expected = datetime(2027, 3, 4, 16, 30, tzinfo=UTC)
    form = bound_form(
        ticket, due_date=expected.isoformat(), time_spent="01:25", comment="Bench test"
    )
    save_ticket_update(form, ticket, staff)
    ticket.refresh_from_db()
    followup = ticket.followup_set.get()
    assert ticket.due_date == expected
    assert followup.time_spent == timedelta(hours=1, minutes=25)
    assert followup.ticketchange_set.filter(field="Due on").count() == 1


def test_ticket_form_handles_assignee_removed_after_validation(
    queue, staff, monkeypatch
):
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_ATTACHMENTS", False)
    assignee = get_user_model().objects.create_user("sqm-departed-owner", is_staff=True)
    form = TicketForm(
        data={
            "queue": queue.pk,
            "title": "Printer outage",
            "body": "No output",
            "submitter_email": "reporter@example.test",
            "priority": 2,
            "assigned_to": assignee.pk,
        },
        queue_choices=[(queue.pk, queue.title)],
    )
    assert form.is_valid(), form.errors
    assignee.delete()
    saved = form.save(user=staff)
    ticket = Ticket.objects.get(pk=saved.pk)
    assert (ticket.status, ticket.assigned_to_id, ticket.priority) == (
        Ticket.OPEN_STATUS,
        None,
        2,
    )
    history = ticket.followup_set.get()
    assert history.user_id == staff.pk
    assert history.comment == "No output"


@pytest.mark.parametrize(
    "configuration,present",
    [
        ("absent", True),
        ("none", False),
        ("value", False),
    ],
)
def test_public_due_date_field_respects_configuration(
    queue, settings, configuration, present
):
    configured_date = datetime(2027, 2, 4, tzinfo=UTC)
    if configuration != "absent":
        settings.HELPDESK_PUBLIC_TICKET_DUE_DATE = (
            configured_date if configuration == "value" else None
        )
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    form = PublicTicketForm(
        data={
            "queue": queue.pk,
            "priority": 3,
            "title": "Repair",
            "body": "Print defect",
            "submitter_email": "reporter@example.test",
        }
    )
    assert ("due_date" in form.fields) is present
    assert form.is_valid(), form.errors
    ticket = Ticket.objects.get(pk=form.save(user=None).pk)
    assert ticket.due_date == (configured_date if configuration == "value" else None)
    assert ticket.followup_set.count() == 1
