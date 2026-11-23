from itertools import product
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import ValidationError
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.models import Queue, Ticket, TicketCC
from helpdesk.update_ticket import (
    add_staff_subscription,
    create_ticket_backlinks,
    process_email_notifications_for_ticket_update,
    return_ticketccstring_and_show_subscribe,
    subscribe_to_ticket_updates,
    update_ticket,
)


def bit_rows(width):
    return [
        pytest.param(*bits, id="".join(str(int(bit)) for bit in bits))
        for bits in product((False, True), repeat=width)
    ]


@pytest.mark.parametrize("suppress,private", bit_rows(2))
def test_private_notification_gate(monkeypatch, suppress, private):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PRIVATE_FOLLOWUP_MEANS_NO_EMAILS", suppress
    )
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_NOTIFY_SUBMITTER_FOR_ALL_TICKET_CHANGES", True
    )
    send = Mock(return_value=set())
    process_email_notifications_for_ticket_update(
        not private,
        SimpleNamespace(assigned_to=None, send=send),
        SimpleNamespace(comment="", new_status=None),
        {},
        set(),
        [],
    )
    assert send.call_count == (0 if suppress and private else 2)


@pytest.mark.parametrize("assigned,change,reassigned,assign", bit_rows(4))
def test_assignee_notification_table(monkeypatch, assigned, change, reassigned, assign):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PRIVATE_FOLLOWUP_MEANS_NO_EMAILS", False
    )
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_NOTIFY_SUBMITTER_FOR_ALL_TICKET_CHANGES", False
    )
    prefs = SimpleNamespace(
        email_on_ticket_change=change, email_on_ticket_assign=assign
    )
    owner = SimpleNamespace(usersettings_helpdesk=prefs) if assigned else None
    send = Mock(return_value=set())
    process_email_notifications_for_ticket_update(
        False,
        SimpleNamespace(assigned_to=owner, send=send),
        SimpleNamespace(comment="", new_status=None),
        {},
        set(),
        [],
        reassigned=reassigned,
    )
    roles = [next(iter(call.args[0])) for call in send.call_args_list]
    assert roles == (
        ["assigned_to", "ticket_cc"]
        if assigned and (change or (reassigned and assign))
        else ["ticket_cc"]
    )


@pytest.mark.parametrize("enabled,authenticated,eligible", bit_rows(3))
def test_auto_subscription_gate_persists_only_eligible_rows(
    make_ticket, staff, monkeypatch, enabled, authenticated, eligible
):
    ticket = make_ticket()
    actor = staff if authenticated else AnonymousUser()
    decision = Mock(return_value=("", eligible))
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_AUTO_SUBSCRIBE_ON_TICKET_RESPONSE", enabled
    )
    monkeypatch.setattr(
        "helpdesk.update_ticket.return_ticketccstring_and_show_subscribe", decision
    )
    add_staff_subscription(actor, ticket)
    add_staff_subscription(actor, ticket)
    assert decision.call_count == (2 if enabled and authenticated else 0)
    assert list(ticket.ticketcc_set.values_list("user_id", flat=True)) == (
        [staff.pk] if enabled and authenticated and eligible else []
    )


@pytest.mark.parametrize("user_present,short", bit_rows(2))
def test_subscription_validation_and_repeat(make_ticket, staff, user_present, short):
    ticket = make_ticket()
    user_id = staff.pk if user_present else None
    email = "a@b" if short else "watcher@example.test"
    if not user_present and short:
        with pytest.raises(ValidationError, match="must provide"):
            subscribe_to_ticket_updates(ticket, user_id=user_id, email=email)
        assert not TicketCC.objects.exists()
        return
    created = subscribe_to_ticket_updates(ticket, user_id=user_id, email=email)
    repeated = subscribe_to_ticket_updates(ticket, user_id=user_id, email=email)
    assert ticket.ticketcc_set.get() == created == repeated
    assert (created.user_id, created.email, created.can_view) == (user_id, email, True)


def test_subscription_offer_uses_existing_recipient_relationships(make_ticket, staff):
    ticket = make_ticket(assigned_to=staff)
    assert return_ticketccstring_and_show_subscribe(staff, ticket) == ("", False)
    ticket.assigned_to = None
    ticket.save()
    assert return_ticketccstring_and_show_subscribe(staff, ticket) == ("", True)
    subscribe_to_ticket_updates(ticket, user_id=staff.pk)
    subscribe_to_ticket_updates(ticket, email="watcher@example.test")
    display, offered = return_ticketccstring_and_show_subscribe(staff, ticket)
    assert not offered
    assert set(display.split(", ")) == {staff.username, "watcher@example.test"}
    assert ticket.ticketcc_set.count() == 2


@pytest.mark.parametrize("assignee,submitter", bit_rows(2))
def test_subscription_offer_excludes_submitter_or_assignee(
    make_ticket, staff, assignee, submitter
):
    staff.email = "agent@example.test"
    staff.save(update_fields=["email"])
    ticket = make_ticket(assigned_to=staff if assignee else None)
    if submitter:
        ticket.submitter_email = staff.email
        ticket.save(update_fields=["submitter_email"])
    assert return_ticketccstring_and_show_subscribe(staff, ticket) == (
        "",
        not (assignee or submitter),
    )
    assert not ticket.ticketcc_set.exists()


@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
@pytest.mark.parametrize(
    "actor_kind", ["none", "no-auth-property", "anonymous", "staff"]
)
def test_backlink_eligibility_persists_only_authorized_targets(
    make_ticket, staff, monkeypatch, enabled, actor_kind
):
    source = make_ticket()
    destination = make_ticket()
    inaccessible = make_ticket()
    inaccessible.queue = Queue.objects.create(
        title="Other queue", slug="backlink-other"
    )
    inaccessible.save(update_fields=["queue"])
    staff.user_permissions.add(
        Permission.objects.get(codename=source.queue.permission_name.split(".")[1])
    )
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_BACKLINKS", enabled)
    followup = source.followup_set.create(
        date=timezone.now(),
        title="Related",
        comment=(
            f"See #{destination.pk}, #{destination.pk}, #{source.pk} and #{inaccessible.pk}"
        ),
    )
    actor = {
        "none": None,
        "no-auth-property": object(),
        "anonymous": AnonymousUser(),
        "staff": staff,
    }[actor_kind]
    create_ticket_backlinks(source, followup, actor)
    create_ticket_backlinks(source, followup, actor)
    if enabled and actor_kind == "staff":
        backlink = destination.followup_set.get()
        assert backlink.title == f"Referenced in ticket #{source.pk}"
        assert backlink.user_id == staff.pk and not backlink.public
    else:
        assert not destination.followup_set.exists()
    assert source.followup_set.get() == followup
    assert not inaccessible.followup_set.exists()
    assert Ticket.objects.count() == 3


@pytest.mark.parametrize("comment_kind", ["blank", "self-only", "missing"])
def test_backlink_without_reachable_reference_creates_no_history(
    make_ticket, staff, monkeypatch, comment_kind
):
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_BACKLINKS", True)
    ticket = make_ticket()
    note = ticket.followup_set.create(
        date=timezone.now(),
        title="Related",
        comment={
            "blank": "",
            "self-only": f"See #{ticket.pk}",
            "missing": f"See #{ticket.pk + 1}",
        }[comment_kind],
    )
    create_ticket_backlinks(ticket, note, staff)
    assert ticket.followup_set.get() == note
    assert Ticket.objects.count() == 1


def test_public_followup_records_delivery_to_duplicate_recipient_once(
    make_ticket, staff, monkeypatch
):
    ticket = make_ticket()
    queue = ticket.queue
    queue.enable_notifications_on_email_events = True
    queue.updated_ticket_cc = ticket.submitter_email
    queue.save()
    subscribe_to_ticket_updates(ticket, email=ticket.submitter_email)
    outgoing = Mock(return_value=True)
    monkeypatch.setattr("helpdesk.models.send_templated_mail", outgoing)
    followup = update_ticket(staff, ticket, comment="Status information", public=True)
    followup.refresh_from_db()
    assert followup.email_recipients == [ticket.submitter_email]
    assert [call.args[2] for call in outgoing.call_args_list] == [
        ticket.submitter_email
    ]
    assert ticket.ticketcc_set.count() == 1


@pytest.mark.parametrize("address,new,role", bit_rows(3))
def test_mail_delivery_guard(make_ticket, monkeypatch, address, new, role):
    ticket = make_ticket()
    ticket.submitter_email = "submitter@example.test" if address else ""
    outgoing = Mock(return_value=True)
    monkeypatch.setattr("helpdesk.models.send_templated_mail", outgoing)
    sent_to = set()
    ticket.send(
        {"submitter": ("updated_submitter", {})} if role else {},
        dont_send_to=set() if new else {ticket.submitter_email},
        sent_to=sent_to,
    )
    assert sent_to == (
        {"submitter@example.test"} if address and new and role else set()
    )
    assert outgoing.call_count == int(address and new and role)
