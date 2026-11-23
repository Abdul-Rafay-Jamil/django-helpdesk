from datetime import timedelta
from itertools import product
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.models import ChecklistTemplate, Queue, Ticket
from helpdesk.views import staff as views


def test_take_assigns_ticket_and_persists_owner_history(rf, make_ticket, staff):
    ticket = make_ticket()
    other = make_ticket()
    request = rf.get(reverse("helpdesk:view", args=[ticket.pk]), {"take": "1"})
    request.user = staff
    response = views.view_ticket(request, ticket.pk)
    assert response.url == ticket.get_absolute_url()
    ticket.refresh_from_db()
    other.refresh_from_db()
    assert ticket.assigned_to_id == staff.pk
    assert ticket.status == Ticket.OPEN_STATUS
    history = ticket.followup_set.get()
    assert history.user_id == staff.pk
    assert history.ticketchange_set.get().field == "Owner"
    assert other.assigned_to_id is None and not other.followup_set.exists()


@pytest.mark.parametrize(
    "valid,template",
    [
        pytest.param(v, t, id=f"{int(v)}{int(t)}")
        for v, t in product((False, True), repeat=2)
    ],
)
def test_staff_checklist_creation_dispatch(
    rf, make_ticket, staff, monkeypatch, valid, template
):
    ticket = make_ticket()
    other = make_ticket()
    record = ChecklistTemplate.objects.create(
        name="Diagnostic", task_list=["Inspect", "Retest"]
    )
    monkeypatch.setattr(views, "render", lambda request, name, context: context)
    request = rf.post(
        reverse("helpdesk:view", args=[ticket.pk]),
        {
            "name": "Printer follow-up" if valid else "",
            "checklist_template": record.pk if template else "",
        },
    )
    request.user = staff
    before = (ticket.status, ticket.modified)
    result = views.view_ticket(request, ticket.pk)
    if valid:
        saved = ticket.checklists.get(name="Printer follow-up")
        assert result.url == reverse(
            "helpdesk:edit_ticket_checklist", args=[ticket.pk, saved.pk]
        )
        assert list(
            saved.tasks.values_list("position", "description", "completion_date")
        ) == ([(0, "Inspect", None), (1, "Retest", None)] if template else [])
    else:
        assert "name" in result["checklist_form"].errors
        assert not ticket.checklists.exists()
    ticket.refresh_from_db()
    assert (ticket.status, ticket.modified) == before
    assert not other.checklists.exists()
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "close,resolved,owned",
    [
        pytest.param(c, r, o, id=f"{int(c)}{int(r)}{int(o)}")
        for c, r, o in product((False, True), repeat=3)
    ],
)
def test_staff_close_dispatch_and_owner_selection(
    rf, make_ticket, staff, monkeypatch, close, resolved, owned
):
    ticket = make_ticket(
        status=Ticket.RESOLVED_STATUS if resolved else Ticket.OPEN_STATUS,
        assigned_to=staff if owned else None,
    )
    before = (ticket.status, ticket.modified, ticket.assigned_to_id)
    writer = Mock()
    monkeypatch.setattr(views, "update_ticket", writer)
    monkeypatch.setattr(views, "render", lambda request, name, context: context)
    request = rf.get(
        reverse("helpdesk:view", args=[ticket.pk]), {"close": "1"} if close else {}
    )
    request.user = staff
    response = views.view_ticket(request, ticket.pk)
    if close and resolved:
        assert response.status_code == 302
        writer.assert_called_once()
        assert writer.call_args.args == (staff, ticket)
        assert writer.call_args.kwargs["owner"] == (staff.pk if owned else 0)
    else:
        writer.assert_not_called()
        assert response["ticket"] == ticket
    ticket.refresh_from_db()
    assert (ticket.status, ticket.modified, ticket.assigned_to_id) == before
    assert not ticket.followup_set.exists()


def test_staff_detail_exposes_matching_submitter_profile(
    rf, make_ticket, staff, monkeypatch
):
    ticket = make_ticket()
    submitter = get_user_model().objects.create_user(
        "matching-submitter", email=ticket.submitter_email
    )
    monkeypatch.setattr(views, "render", lambda request, name, context: context)
    request = rf.get(reverse("helpdesk:view", args=[ticket.pk]))
    request.user = staff
    context = views.view_ticket(request, ticket.pk)
    assert context["submitter_userprofile_url"] == reverse(
        "admin:auth_user_change", args=[submitter.pk]
    )
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "take,subscribe,close,resolved,eligible,winner",
    [
        pytest.param(True, True, True, True, True, "take", id="take-before-all"),
        pytest.param(
            False, True, True, True, True, "subscribe", id="subscribe-before-close"
        ),
        pytest.param(
            False,
            True,
            True,
            True,
            False,
            "close",
            id="ineligible-subscribe-falls-through-to-close",
        ),
        pytest.param(
            False, False, True, True, True, "close", id="close-before-checklist"
        ),
        pytest.param(
            False,
            True,
            True,
            False,
            False,
            "checklist",
            id="ineligible-actions-fall-through-to-checklist",
        ),
    ],
)
def test_competing_staff_actions_run_only_the_first_eligible_action(
    rf,
    make_ticket,
    staff,
    monkeypatch,
    take,
    subscribe,
    close,
    resolved,
    eligible,
    winner,
):
    ticket = make_ticket(
        status=Ticket.RESOLVED_STATUS if resolved else Ticket.OPEN_STATUS,
        resolution="Existing resolution",
    )
    if not eligible:
        ticket.ticketcc_set.create(user=staff)
    before = (ticket.status, ticket.assigned_to_id, ticket.resolution, ticket.modified)
    # Keep the known Close defect out of the routing oracle.
    update = Mock()
    monkeypatch.setattr(views, "update_ticket", update)
    route = reverse("helpdesk:view", args=[ticket.pk])
    actions = {
        action: "1"
        for action, enabled in (
            ("take", take),
            ("subscribe", subscribe),
            ("close", close),
        )
        if enabled
    }
    request = rf.post(route, {"name": "Requested checklist"}, query_params=actions)
    request.user = staff
    assert views.view_ticket(request, ticket.pk).status_code == 302

    if winner == "take":
        update.assert_called_once_with(staff, ticket, owner=staff.pk)
    elif winner == "close":
        update.assert_called_once()
        assert update.call_args.args == (staff, ticket)
        assert update.call_args.kwargs["owner"] == 0
        assert "comment" in update.call_args.kwargs
    else:
        update.assert_not_called()
    assert list(ticket.ticketcc_set.values_list("user_id", flat=True)) == (
        [staff.pk] if winner == "subscribe" or not eligible else []
    )
    assert list(ticket.checklists.values_list("name", flat=True)) == (
        ["Requested checklist"] if winner == "checklist" else []
    )
    ticket.refresh_from_db()
    assert (
        ticket.status,
        ticket.assigned_to_id,
        ticket.resolution,
        ticket.modified,
    ) == before
    assert not ticket.followup_set.exists()


def test_staff_detail_queue_denial_precedes_all_action_side_effects(
    rf, make_ticket, staff, monkeypatch
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    ticket = make_ticket(
        status=Ticket.RESOLVED_STATUS, resolution="Existing resolution"
    )
    ticket.queue = Queue.objects.create(title="Separate team", slug="separate-team")
    ticket.save(update_fields=["queue"])
    original = (
        ticket.status,
        ticket.assigned_to_id,
        ticket.resolution,
        ticket.modified,
    )
    notice = Mock()
    monkeypatch.setattr(views.messages, "error", notice)
    request = rf.post(
        reverse("helpdesk:view", args=[ticket.pk]),
        {"name": "Requested checklist"},
        query_params={"take": "1", "subscribe": "1", "close": "1"},
    )
    request.user = staff
    assert views.view_ticket(request, ticket.pk).url == reverse("helpdesk:list")
    notice.assert_called_once_with(
        request, f"You don't have permission to view ticket {ticket.pk}."
    )
    ticket.refresh_from_db()
    assert (
        ticket.status,
        ticket.assigned_to_id,
        ticket.resolution,
        ticket.modified,
    ) == original
    assert not ticket.ticketcc_set.exists()
    assert not ticket.checklists.exists()
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "newest_first", [False, True], ids=["oldest-first", "newest-first"]
)
def test_staff_detail_history_order_follows_configuration(
    rf, make_ticket, staff, monkeypatch, newest_first
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_FOLLOWUP_NEWEST_FIRST", newest_first
    )
    monkeypatch.setattr(views, "render", lambda request, name, context: context)
    ticket = make_ticket()
    now = timezone.now()
    older = ticket.followup_set.create(title="Intake", date=now)
    newer = ticket.followup_set.create(title="Review", date=now + timedelta(minutes=1))
    request = rf.get(reverse("helpdesk:view", args=[ticket.pk]))
    request.user = staff
    context = views.view_ticket(request, ticket.pk)
    expected = [newer.pk, older.pk] if newest_first else [older.pk, newer.pk]
    assert [row.pk for row in context["followups"]] == expected
    assert ticket.followup_set.count() == 2
