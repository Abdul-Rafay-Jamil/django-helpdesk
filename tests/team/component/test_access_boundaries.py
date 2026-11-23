from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.models import Queue


def test_customer_cannot_assign_existing_ticket(client, make_ticket, staff):
    ticket = make_ticket()
    before = ticket.modified
    submitter = get_user_model().objects.create_user(
        "customer-owner", email=ticket.submitter_email
    )
    client.force_login(submitter)
    response = client.patch(
        reverse("helpdesk:ticket-detail", args=[ticket.pk]),
        {"assigned_to": staff.pk},
        content_type="application/json",
    )
    assert response.status_code in (401, 403)
    ticket.refresh_from_db()
    assert ticket.assigned_to_id is None
    assert ticket.modified == before
    assert not ticket.followup_set.exists()


def test_staff_queue_detail_access_and_private_history(
    client, make_ticket, staff, monkeypatch
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    visible = make_ticket()
    visible.followup_set.create(
        title="Visible internal note", date=timezone.now(), public=False
    )
    hidden = make_ticket()
    hidden.queue = Queue.objects.create(title="Restricted team", slug="restricted-team")
    hidden.save(update_fields=["queue"])
    hidden.followup_set.create(
        title="Restricted internal note", date=timezone.now(), public=False
    )
    permission = Permission.objects.get(
        codename=visible.queue.permission_name.split(".")[1]
    )
    staff.user_permissions.add(permission)
    client.force_login(staff)
    before = hidden.modified

    response = client.get(reverse("helpdesk:view", args=[visible.pk]))
    assert response.status_code == 200
    assert b"Visible internal note" in response.content
    response = client.get(reverse("helpdesk:view", args=[hidden.pk]))
    assert response.status_code == 302
    assert b"Restricted internal note" not in response.content
    response = client.get(reverse("helpdesk:ticket-detail", args=[hidden.pk]))
    assert response.status_code == 404
    hidden.refresh_from_db()
    assert hidden.modified == before
    assert hidden.followup_set.count() == visible.followup_set.count() == 1
