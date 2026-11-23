from itertools import product
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from helpdesk import settings as helpdesk_settings
from helpdesk.models import FollowUp, FollowUpAttachment, Ticket
from helpdesk.views import public


def public_request(rf, ticket, **fields):
    request = rf.post(
        reverse("helpdesk:update", args=[ticket.pk]),
        {
            "mail": ticket.submitter_email,
            "key": ticket.secret_key,
            "new_status": Ticket.RESOLVED_STATUS,
            **fields,
        },
    )
    request.user = AnonymousUser()
    return request


def stored_state(ticket):
    ticket.refresh_from_db()
    return (
        ticket.status,
        ticket.resolution,
        ticket.modified,
        tuple(ticket.followup_set.order_by("pk").values_list("pk", flat=True)),
    )


@pytest.mark.parametrize(
    "comment,file,changed",
    [
        pytest.param(c, f, s, id=f"{int(c)}{int(f)}{int(s)}")
        for c, f, s in product((False, True), repeat=3)
    ],
)
def test_public_followup_dispatch_table(
    rf, make_ticket, monkeypatch, comment, file, changed
):
    ticket = make_ticket(status=Ticket.REOPENED_STATUS)
    before = stored_state(ticket)
    dispatch = Mock()
    monkeypatch.setattr("helpdesk.update_ticket.update_ticket", dispatch)
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_ATTACHMENTS", True)
    fields = {
        "comment": "Note" if comment else "   ",
        "new_status": Ticket.RESOLVED_STATUS if changed else ticket.status,
    }
    if file:
        fields["attachment"] = SimpleUploadedFile("note.txt", b"note")
    assert (
        public.update_ticket_from_public_view(
            public_request(rf, ticket, **fields), ticket.pk
        ).status_code
        == 302
    )
    assert dispatch.call_count == int(comment or file or changed)
    assert stored_state(ticket) == before


def test_public_update_rejects_unverified_request_without_writes(rf, make_ticket):
    ticket = make_ticket(status=Ticket.REOPENED_STATUS)
    before = stored_state(ticket)
    route = reverse("helpdesk:update", args=[ticket.pk])
    request = rf.post(route, {"new_status": Ticket.RESOLVED_STATUS})
    request.user = AnonymousUser()
    response = public.update_ticket_from_public_view(request, ticket.pk)
    assert response.url.startswith("/login/")
    assert stored_state(ticket) == before


def test_public_update_with_attachments_disabled_leaves_ticket_untouched(
    rf, make_ticket, monkeypatch
):
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_ATTACHMENTS", False)
    ticket = make_ticket(status=Ticket.REOPENED_STATUS)
    before = stored_state(ticket)
    request = public_request(
        rf,
        ticket,
        new_status=ticket.status,
        attachment=SimpleUploadedFile("note.txt", b"note"),
    )
    assert public.update_ticket_from_public_view(request, ticket.pk).status_code == 302
    assert stored_state(ticket) == before
    assert not FollowUpAttachment.objects.exists()


def test_public_attachment_failure_rolls_back_then_retry_persists_once(
    rf, make_ticket, monkeypatch, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ENABLE_ATTACHMENTS", True)
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_VALIDATE_ATTACHMENT_TYPES", True)
    messages_error = Mock()
    monkeypatch.setattr(public.messages, "error", messages_error)
    ticket = make_ticket(status=Ticket.REOPENED_STATUS)
    before = stored_state(ticket)
    flags = {"comment": "Restored"}
    bad = public_request(
        rf, ticket, **flags, attachment=SimpleUploadedFile("note.invalid", b"note")
    )
    assert public.update_ticket_from_public_view(bad, ticket.pk).status_code == 302
    assert "Unsupported file extension" in messages_error.call_args.args[1]
    assert stored_state(ticket) == before
    assert not FollowUpAttachment.objects.exists()

    good = public_request(
        rf,
        ticket,
        **flags,
        attachment=SimpleUploadedFile("note.txt", b"note", content_type="text/plain"),
    )
    assert public.update_ticket_from_public_view(good, ticket.pk).status_code == 302
    ticket.refresh_from_db()
    assert (ticket.status, ticket.resolution) == (Ticket.RESOLVED_STATUS, "Restored")
    followup = ticket.followup_set.get()
    attachment = followup.followupattachment_set.get()
    assert (attachment.filename, attachment.size) == ("note.txt", 4)
    assert followup.ticketchange_set.filter(field="Status").count() == 1
    assert Ticket.objects.count() == 1


def test_prewrite_failure_and_status_retry_leave_single_transition(rf, make_ticket):
    ticket = make_ticket(status=Ticket.REOPENED_STATUS)
    before = stored_state(ticket)
    with (
        patch.object(
            Ticket, "save", autospec=True, side_effect=RuntimeError("store unavailable")
        ),
        pytest.raises(RuntimeError, match="store unavailable"),
    ):
        public.update_ticket_from_public_view(public_request(rf, ticket), ticket.pk)
    assert stored_state(ticket) == before
    for _ in range(2):
        assert (
            public.update_ticket_from_public_view(
                public_request(rf, ticket), ticket.pk
            ).status_code
            == 302
        )
    ticket.refresh_from_db()
    assert ticket.status == Ticket.RESOLVED_STATUS
    assert (
        ticket.followup_set.get().ticketchange_set.filter(field="Status").count() == 1
    )
    assert Ticket.objects.count() == 1


def test_public_create_uses_configured_defaults_and_persists_open_history(
    rf, queue, settings
):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    settings.HELPDESK_PUBLIC_TICKET_QUEUE = queue.slug
    settings.HELPDESK_PUBLIC_TICKET_PRIORITY = 2
    url = reverse("helpdesk:submit")
    request = rf.post(
        url,
        {
            "title": "Configured intake",
            "body": "Cannot print",
            "submitter_email": "reporter@example.test",
            "priority": 6,
        },
    )
    request.user = AnonymousUser()
    response = public.CreateTicketView.as_view()(request)
    assert response.status_code == 302
    ticket = Ticket.objects.get(title="Configured intake")
    assert (ticket.status, ticket.queue_id, ticket.priority) == (
        Ticket.OPEN_STATUS,
        queue.pk,
        2,
    )
    assert ticket.followup_set.get().comment == "Cannot print"


def test_public_create_rejects_invalid_data_without_partial_rows(rf, queue):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    target = reverse("helpdesk:submit") + "?" + urlencode({"queue": queue.slug})
    request = rf.post(
        target,
        {
            "title": "Bad intake",
            "body": "Cannot print",
            "queue": queue.pk,
            "priority": 6,
            "submitter_email": "reporter@example.test",
        },
    )
    request.user = AnonymousUser()
    response = public.CreateTicketView.as_view()(request)
    assert "priority" in response.context_data["form"].errors
    assert not Ticket.objects.exists()
    assert not FollowUp.objects.exists()
