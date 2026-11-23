from itertools import product

import pytest
from django.urls import reverse
from django.utils import timezone

from helpdesk.models import Ticket
from tests.team.state_oracle import ALLOWED, CLOSED, LABELS, RESOLVED


ROUTES = ("staff-form", "ticket-api", "followup-api")
MATRIX = [
    pytest.param(route, source, target, id=f"{route}-{LABELS[source]}-{LABELS[target]}")
    for route, source, target in product(ROUTES, ALLOWED, ALLOWED)
    # These two rows keep their existing independently reported test IDs.
    if (source, target) != (CLOSED, RESOLVED) or route == "followup-api"
]


def send_status(client, route, ticket, target):
    if route == "staff-form":
        return client.post(reverse("helpdesk:update", args=[ticket.pk]), {
            "queue": ticket.queue_id, "title": ticket.title, "priority": ticket.priority,
            "submitter_email": ticket.submitter_email, "new_status": target,
        })
    if route == "ticket-api":
        return client.patch(reverse("helpdesk:ticket-detail", args=[ticket.pk]),
                            {"status": target}, content_type="application/json")
    return client.post(reverse("helpdesk:followups-list"),
                       {"ticket": ticket.pk, "title": ticket.title, "new_status": target},
                       content_type="application/json")


def snapshot(ticket):
    ticket.refresh_from_db()
    return (ticket.status, ticket.resolution, ticket.modified, ticket.assigned_to_id,
            list(ticket.followup_set.values_list("pk", "new_status", "comment").order_by("pk")))


@pytest.mark.parametrize("route,source,target", MATRIX)
def test_configured_transition_and_history(client, make_ticket, staff, route, source, target):
    ticket = make_ticket(status=source, resolution="Previous resolution", assigned_to=staff)
    existing = ticket.followup_set.create(date=timezone.now(), title="Intake", comment="Intake note")
    before = snapshot(ticket)
    client.force_login(staff)

    response = send_status(client, route, ticket, target)
    after = snapshot(ticket)
    if target not in ALLOWED[source]:
        assert after == before, f"HTTP {response.status_code}; stored status and history: {after}"
        if route != "staff-form":
            assert 400 <= response.status_code < 500
        return

    assert response.status_code == {"staff-form": 302, "ticket-api": 200, "followup-api": 201}[route]
    assert ticket.status == target
    assert ticket.assigned_to_id == staff.pk
    assert ticket.followup_set.get(pk=existing.pk).comment == "Intake note"
    if route == "followup-api" or (route == "staff-form" and source != target):
        followup = ticket.followup_set.exclude(pk=existing.pk).get()
        assert followup.new_status == (target if source != target else None)
        assert followup.ticketchange_set.filter(field="Status").count() == int(source != target)
    else:
        assert ticket.followup_set.count() == 1

    # A follow-up POST explicitly creates one comment/history object per request.
    if route != "followup-api":
        retry = send_status(client, route, ticket, target)
        assert retry.status_code == response.status_code
        retried = snapshot(ticket)
        # An API PATCH may update its modified timestamp even for a no-op.
        assert retried[:2] + retried[3:] == after[:2] + after[3:]
        if route == "staff-form":
            assert retried[2] == after[2]
    assert Ticket.objects.count() == 1
