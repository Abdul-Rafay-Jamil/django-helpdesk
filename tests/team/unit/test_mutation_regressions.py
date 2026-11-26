"""Persisted Ticket Management outcomes sharpened by mutation testing."""

from datetime import timedelta
from smtplib import SMTPException
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.models import EmailTemplate, Ticket
from helpdesk.update_ticket import (
    process_email_notifications_for_ticket_update,
    subscribe_to_ticket_updates,
    update_ticket,
)


def test_new_watcher_defaults_to_view_only(make_ticket):
    ticket = make_ticket()
    address = "watcher@example.test"

    first = subscribe_to_ticket_updates(ticket, email=address)
    second = subscribe_to_ticket_updates(ticket, email=address)

    saved = ticket.ticketcc_set.get()
    assert saved.pk == first.pk == second.pk
    assert saved.email == address
    assert saved.can_view is True
    assert saved.can_update is False


@pytest.mark.parametrize(
    "address,accepted",
    [("a@bc", False), ("a@b.c", True), ("a@b.co", True)],
    ids=["length-4", "length-5", "length-6"],
)
def test_email_only_watcher_length_boundary(make_ticket, address, accepted):
    ticket = make_ticket()

    if not accepted:
        with pytest.raises(
            ValidationError, match="must provide either a User or a valid email"
        ):
            subscribe_to_ticket_updates(ticket, email=address)
        assert not ticket.ticketcc_set.exists()
        return

    result = subscribe_to_ticket_updates(ticket, email=address)
    saved = ticket.ticketcc_set.get()
    assert saved.pk == result.pk
    assert saved.email == address
    assert saved.user_id is None


@pytest.mark.parametrize(
    "status,template",
    [(Ticket.RESOLVED_STATUS, "resolved"), (Ticket.CLOSED_STATUS, "closed")],
    ids=["resolved", "closed"],
)
def test_default_notification_is_a_status_change(monkeypatch, status, template):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_NOTIFY_SUBMITTER_FOR_ALL_TICKET_CHANGES", False
    )
    send = Mock(return_value=set())
    preferences = SimpleNamespace(
        email_on_ticket_assign=True, email_on_ticket_change=False
    )
    owner = SimpleNamespace(usersettings_helpdesk=preferences)
    ticket = SimpleNamespace(assigned_to=owner, send=send)
    follow_up = SimpleNamespace(new_status=status, comment="")
    context = {}

    process_email_notifications_for_ticket_update(
        True, ticket, follow_up, context, set(), []
    )

    assert [call.args[0] for call in send.call_args_list] == [
        {"submitter": (f"{template}_submitter", context)},
        {"ticket_cc": (f"{template}_cc", context)},
    ]


@pytest.mark.parametrize("failing_role", ["submitter", "owner", "cc"])
def test_followup_records_successful_deliveries_when_one_mail_fails(
    make_ticket, staff, monkeypatch, failing_role
):
    owner = get_user_model().objects.create_user(
        "mutation-owner", email="owner@example.test"
    )
    preferences = owner.usersettings_helpdesk
    preferences.email_on_ticket_change = True
    preferences.save(update_fields=["email_on_ticket_change"])
    ticket = make_ticket(assigned_to=owner)
    ticket.queue.updated_ticket_cc = "cc@example.test"
    ticket.queue.save(update_fields=["updated_ticket_cc"])
    addresses = {
        "submitter": ticket.submitter_email,
        "owner": owner.email,
        "cc": ticket.queue.updated_ticket_cc,
    }
    for role in addresses:
        EmailTemplate.objects.get_or_create(
            template_name=f"resolved_{role}",
            locale="en",
            defaults={"subject": f"Resolved {role}"},
        )
    attempted = []

    def deliver(message):
        recipient = message.to[0]
        attempted.append(recipient)
        if recipient == addresses[failing_role]:
            raise SMTPException("Simulated delivery failure")
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, "send", deliver)
    resolution = "Replaced the feed roller."
    follow_up = update_ticket(
        staff,
        ticket,
        new_status=Ticket.RESOLVED_STATUS,
        comment=resolution,
        public=True,
    )

    ticket.refresh_from_db()
    saved = ticket.followup_set.get()
    assert (ticket.status, ticket.resolution) == (Ticket.RESOLVED_STATUS, resolution)
    assert saved.pk == follow_up.pk
    assert saved.email_recipients == sorted(
        set(addresses.values()) - {addresses[failing_role]}
    )
    assert sorted(attempted) == sorted(addresses.values())


def test_omitted_visibility_keeps_followup_private(make_ticket, staff, monkeypatch):
    ticket = make_ticket()
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PRIVATE_FOLLOWUP_MEANS_NO_EMAILS", True
    )
    send = Mock(return_value=set())
    monkeypatch.setattr(Ticket, "send", send)

    update_ticket(staff, ticket, comment="Staff-only diagnosis")

    ticket.refresh_from_db()
    saved = ticket.followup_set.get()
    assert ticket.status == Ticket.OPEN_STATUS
    assert saved.comment == "Staff-only diagnosis"
    assert saved.public is False
    assert saved.email_recipients == []
    send.assert_not_called()


def test_assignment_notifies_owner_who_only_opts_in_to_assignment(
    make_ticket, staff, monkeypatch
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PRIVATE_FOLLOWUP_MEANS_NO_EMAILS", False
    )
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_NOTIFY_SUBMITTER_FOR_ALL_TICKET_CHANGES", False
    )
    ticket = make_ticket()
    owner = get_user_model().objects.create_user(
        "mutation-assignee", email="assignee@example.test"
    )
    preferences = owner.usersettings_helpdesk
    preferences.email_on_ticket_assign = True
    preferences.email_on_ticket_change = False
    preferences.save(update_fields=["email_on_ticket_assign", "email_on_ticket_change"])
    EmailTemplate.objects.get_or_create(
        template_name="assigned_owner", locale="en", defaults={"subject": "Assignment"}
    )
    delivered = []

    def deliver(message):
        delivered.extend(message.to)
        return 1

    monkeypatch.setattr(EmailMultiAlternatives, "send", deliver)

    update_ticket(staff, ticket, owner=owner.pk)

    ticket.refresh_from_db()
    saved = ticket.followup_set.get()
    assert ticket.assigned_to_id == owner.pk
    assert saved.ticketchange_set.get(field="Owner").new_value == owner.username
    assert saved.email_recipients == delivered == [owner.email]


def test_unknown_owner_is_not_treated_as_the_keep_owner_sentinel(make_ticket, staff):
    ticket = make_ticket(assigned_to=staff)
    prior_modified = ticket.modified

    with pytest.raises(get_user_model().DoesNotExist):
        update_ticket(staff, ticket, owner=-2, comment="Invalid assignee")

    ticket.refresh_from_db()
    assert ticket.assigned_to_id == staff.pk
    assert ticket.modified == prior_modified
    assert not ticket.followup_set.exists()


def test_omitted_due_date_preserves_the_existing_due_date(make_ticket, staff):
    due_date = timezone.now() + timedelta(days=2)
    ticket = make_ticket(due_date=due_date)

    update_ticket(staff, ticket, comment="Keep the original deadline")

    ticket.refresh_from_db()
    saved = ticket.followup_set.get()
    assert ticket.due_date == due_date
    assert not saved.ticketchange_set.filter(field="Due on").exists()


def test_checklist_audit_records_both_directions_once_on_retry(make_ticket, staff):
    ticket = make_ticket()
    checklist = ticket.checklists.create(name="Triage")
    pending = checklist.tasks.create(description="Inspect", position=0)
    finished = checklist.tasks.create(
        description="Contact", position=1, completion_date=timezone.now()
    )
    chosen = {checklist.pk: {pending.pk}}

    first = update_ticket(staff, ticket, new_checklists=chosen)
    second = update_ticket(staff, ticket, new_checklists=chosen)

    pending.refresh_from_db()
    finished.refresh_from_db()
    assert pending.completion_date is not None
    assert finished.completion_date is None
    changes = first.ticketchange_set.values_list("field", "old_value", "new_value")
    assert set(changes) == {
        ("[Triage] Inspect", "To do", "Completed"),
        ("[Triage] Contact", "Completed", "To do"),
    }
    assert not second.ticketchange_set.filter(field__startswith="[Triage]").exists()
