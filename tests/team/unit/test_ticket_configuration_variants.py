from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.urls import reverse

from helpdesk import settings as helpdesk_settings
from helpdesk.forms import PublicTicketForm
from helpdesk.models import FollowUp, Ticket
from helpdesk.views import public
from helpdesk.views import staff as staff_views


class ConfiguredIntakeForm(PublicTicketForm):
    def clean_title(self):
        return f"Configured intake: {self.cleaned_data['title']}"


def intake_fields(queue):
    return {
        "queue": queue.pk,
        "priority": 3,
        "title": "Paper jam",
        "body": "Please inspect the printer",
        "submitter_email": "owner@example.test",
    }


@pytest.mark.parametrize(
    "granted", [False, True], ids=["public-picker-only", "queue-granted"]
)
def test_staff_creation_redirect_respects_queue_access(
    rf, queue, staff, monkeypatch, granted
):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    if granted:
        staff.user_permissions.add(
            Permission.objects.get(codename=queue.permission_name.split(".")[1])
        )
    request = rf.post(reverse("helpdesk:submit"), intake_fields(queue))
    request.user = staff
    result = public.create_ticket(request)
    ticket = Ticket.objects.get()
    assert (ticket.queue_id, ticket.status, ticket.title) == (
        queue.pk,
        Ticket.OPEN_STATUS,
        "Paper jam",
    )
    assert ticket.followup_set.get().user_id == staff.pk
    assert result.url == (
        ticket.get_absolute_url() if granted else reverse("helpdesk:dashboard")
    )


def test_public_configured_form_preserves_readonly_title_and_due_default(
    rf, queue, settings, monkeypatch
):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    due = datetime(2027, 2, 4, 12, tzinfo=UTC)
    settings.HELPDESK_PUBLIC_TICKET_DUE_DATE = due
    monkeypatch.setattr(
        helpdesk_settings,
        "HELPDESK_PUBLIC_TICKET_FORM_CLASS",
        f"{__name__}.ConfiguredIntakeForm",
    )
    request = rf.post(
        reverse("helpdesk:submit"),
        intake_fields(queue),
        query_params={
            "_hide_fields_": "priority",
            "_readonly_fields_": "title",
            "title": "Queue form",
        },
    )
    request.user = AnonymousUser()
    response = public.create_ticket(request)
    assert response.status_code == 302
    ticket = Ticket.objects.get()
    assert (ticket.title, ticket.due_date, ticket.status) == (
        "Configured intake: Queue form",
        due,
        Ticket.OPEN_STATUS,
    )
    assert ticket.followup_set.get().comment == "Please inspect the printer"
    assert parse_qs(urlsplit(response.url).query)["ticket"] == [ticket.ticket_for_url]


@pytest.mark.parametrize(
    "form_class",
    [
        "missing_intake_module.PublicTicketForm",
        "helpdesk.forms.MissingIntakeForm",
    ],
    ids=["unknown-module", "unknown-class"],
)
def test_invalid_public_form_configuration_rejects_without_ticket(
    rf, queue, monkeypatch, form_class
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PUBLIC_TICKET_FORM_CLASS", form_class
    )
    request = rf.post(reverse("helpdesk:submit"), intake_fields(queue))
    request.user = AnonymousUser()
    with pytest.raises(ImproperlyConfigured, match="Invalid custom form class"):
        public.create_ticket(request)
    assert not Ticket.objects.exists() and not FollowUp.objects.exists()


@pytest.mark.parametrize(
    "queue_exists", [False, True], ids=["missing-queue", "nonpublic-queue"]
)
def test_public_default_queue_must_accept_public_submission(
    rf, queue, settings, queue_exists
):
    settings.HELPDESK_PUBLIC_TICKET_QUEUE = queue.slug if queue_exists else "missing"
    request = rf.post(reverse("helpdesk:submit"), intake_fields(queue))
    request.user = AnonymousUser()
    with pytest.raises(ImproperlyConfigured, match="Wrong public queue configuration"):
        public.create_ticket(request)
    assert not Ticket.objects.exists() and not FollowUp.objects.exists()


@pytest.mark.parametrize(
    "assignee_mail,queue_events",
    [
        pytest.param(False, False, id="00"),
        pytest.param(False, True, id="01"),
        pytest.param(True, False, id="10"),
        pytest.param(True, True, id="11"),
    ],
)
def test_staff_recipient_preview_respects_preferences_and_deduplicates(
    rf, make_ticket, staff, monkeypatch, assignee_mail, queue_events
):
    staff.email = "viewing-agent@example.test"
    staff.save(update_fields=["email"])
    assignee = get_user_model().objects.create_user(
        "preview-assignee",
        email="assignee@example.test",
        is_staff=True,
    )
    preferences = assignee.usersettings_helpdesk
    preferences.email_on_ticket_change = assignee_mail
    preferences.save(update_fields=["email_on_ticket_change"])
    ticket = make_ticket(assigned_to=assignee)
    ticket.queue.updated_ticket_cc = ticket.submitter_email
    ticket.queue.enable_notifications_on_email_events = queue_events
    ticket.queue.save()
    for email in (staff.email, "watcher@example.test", ticket.submitter_email):
        ticket.ticketcc_set.create(email=email)
    before = ticket.modified
    monkeypatch.setattr(staff_views, "render", lambda request, name, context: context)
    request = rf.get(reverse("helpdesk:view", args=[ticket.pk]))
    request.user = staff
    context = staff_views.view_ticket(request, ticket.pk)
    expected = {ticket.submitter_email}
    if assignee_mail:
        expected.add(assignee.email)
    if queue_events:
        expected.add("watcher@example.test")
    assert context["notification_recipients"] == sorted(expected)
    ticket.refresh_from_db()
    assert ticket.modified == before and ticket.ticketcc_set.count() == 3
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "landing,expected",
    [
        ("list", "helpdesk:list"),
        ("dashboard", "helpdesk:dashboard"),
        ("missing-preferences", "helpdesk:dashboard"),
    ],
)
def test_enabled_nonstaff_landing_uses_saved_preference(
    rf, queue, monkeypatch, landing, expected
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE", True
    )
    actor = get_user_model().objects.create_user("enabled-nonstaff")
    preferences = actor.usersettings_helpdesk
    if landing == "missing-preferences":
        preferences.delete()
        actor = get_user_model().objects.get(pk=actor.pk)
    else:
        preferences.login_view_ticketlist = landing == "list"
        preferences.save(update_fields=["login_view_ticketlist"])
    request = rf.post(reverse("helpdesk:submit"), intake_fields(queue))
    request.user = actor
    assert public.create_ticket(request).url == reverse(expected)
    assert not Ticket.objects.exists() and not FollowUp.objects.exists()


@pytest.mark.parametrize(
    "target,allowed",
    [
        pytest.param(Ticket.DUPLICATE_STATUS, True, id="allow-newly-configured-target"),
        pytest.param(Ticket.CLOSED_STATUS, False, id="deny-removed-default-target"),
    ],
)
def test_public_transition_uses_alternative_configured_flow(
    rf, make_ticket, monkeypatch, target, allowed
):
    ticket = make_ticket(
        status=Ticket.RESOLVED_STATUS, resolution="Existing resolution"
    )
    original = (ticket.status, ticket.resolution, ticket.modified)
    monkeypatch.setattr(
        Ticket,
        "STATUS_CHOICES_FLOW",
        {
            **Ticket.STATUS_CHOICES_FLOW,
            Ticket.RESOLVED_STATUS: (Ticket.RESOLVED_STATUS, Ticket.DUPLICATE_STATUS),
        },
    )
    request = rf.post(ticket.ticket_url, {"new_status": target})
    request.user = AnonymousUser()
    if allowed:
        assert public.ViewTicket.as_view()(request).status_code == 302
        ticket.refresh_from_db()
        assert (ticket.status, ticket.resolution) == (target, original[1])
        assert ticket.followup_set.get().new_status == target
    else:
        with pytest.raises(PermissionDenied):
            public.ViewTicket.as_view()(request)
        ticket.refresh_from_db()
        assert (ticket.status, ticket.resolution, ticket.modified) == original
        assert not ticket.followup_set.exists()
