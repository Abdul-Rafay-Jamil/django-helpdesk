"""Shared-record, transition, access and recovery checks for the selected API."""

import base64
from itertools import product

import pytest
from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.test import Client
from django.urls import reverse

from helpdesk.models import CustomField, Ticket, TicketChange
from tests.team.api.test_generated_inputs import issue, snapshot
from tests.team.state_oracle import ALLOWED, LABELS


def detail(ticket):
    return reverse("helpdesk:ticket-detail", args=[ticket.pk])


@pytest.mark.parametrize(
    "source,target",
    [
        pytest.param(source, target, id=f"{LABELS[source]}-{LABELS[target]}")
        for source, target in product(ALLOWED, repeat=2)
    ],
)
def test_put_transition_and_idempotent_retry(
    client, make_ticket, staff, source, target
):
    ticket = make_ticket(status=source, resolution="Stored prior resolution")
    client.force_login(staff)
    payload = {
        "queue": ticket.queue_id,
        "title": ticket.title,
        "status": target,
        "description": "Inspected spooler",
        "assigned_to": staff.pk,
    }
    before = snapshot()
    result = issue(client, "put", detail(ticket), payload)
    if target not in ALLOWED[source]:
        assert result.status_code == 400 and "status" in result.json()
        assert snapshot() == before
        return
    assert result.status_code == 200
    once = snapshot()
    assert issue(client, "put", detail(ticket), payload).status_code == 200
    stored = Ticket.objects.get()
    read = client.get(detail(ticket)).json()
    assert stored.pk == ticket.pk == read["id"]
    assert stored.status == read["status"] == target
    assert stored.assigned_to_id == read["assigned_to"] == staff.pk
    assert stored.resolution == "Stored prior resolution"
    assert snapshot()[1:] == once[1:] == before[1:]


def test_customer_intake_staff_assignment_private_resolution_and_readback(
    client, queue, staff
):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    account = get_user_model()
    customer = account.objects.create_user(
        "journey-customer", email="journey@example.test"
    )
    outsider = account.objects.create_user(
        "unrelated-customer", email="outside@example.test"
    )
    private = CustomField.objects.create(
        name="internal", label="Internal", data_type="varchar", staff_only=True
    )
    client.force_login(customer)
    submission = client.post(
        reverse("helpdesk:submit"),
        {
            "queue": queue.pk,
            "title": "Customer intake",
            "body": "Printer has jammed",
            "priority": 2,
            "submitter_email": customer.email,
        },
    )
    assert submission.status_code == 302
    ticket = Ticket.objects.get()
    assert ticket.status == Ticket.OPEN_STATUS
    intake = ticket.followup_set.get()
    assert intake.public and intake.comment == ticket.description
    before = snapshot()
    assert (
        issue(client, "patch", detail(ticket), {"assigned_to": staff.pk}).status_code
        == 403
    )
    assert snapshot() == before

    client.force_login(staff)
    for _ in range(2):
        assignment = issue(
            client,
            "patch",
            detail(ticket),
            {
                "assigned_to": staff.pk,
                "custom_internal": "Staff diagnostic",
            },
        )
        assert assignment.status_code == 200
    ticket.refresh_from_db()
    assert ticket.assigned_to_id == staff.pk
    assert (
        ticket.ticketcustomfieldvalue_set.get(field=private).value == "Staff diagnostic"
    )
    assert ticket.followup_set.get() == intake
    followup_url = reverse("helpdesk:followups-list")
    payload = {
        "ticket": ticket.pk,
        "comment": "Roller replaced",
        "public": False,
        "new_status": Ticket.RESOLVED_STATUS,
    }
    first = issue(client, "post", followup_url, payload)
    second = issue(client, "post", followup_url, payload)
    assert first.status_code == second.status_code == 201
    ticket.refresh_from_db()
    assert (ticket.pk, ticket.status, ticket.resolution) == (
        Ticket.objects.get().pk,
        Ticket.RESOLVED_STATUS,
        payload["comment"],
    )
    ids = [first.json()["id"], second.json()["id"]]
    assert len(set(ids)) == 2
    assert ticket.followup_set.count() == 3
    assert TicketChange.objects.filter(followup_id__in=ids, field="Status").count() == 1
    history = client.get(detail(ticket)).json()["followup_set"]
    assert {row["id"] for row in history} == {intake.pk, *ids}
    assert all(not row["public"] for row in history if row["id"] in ids)

    user_url = reverse("helpdesk:user_tickets-detail", args=[ticket.pk])
    client.force_login(customer)
    public = client.get(user_url)
    assert public.status_code == 200 and public.json()["id"] == ticket.pk
    assert public.json()["status"] == "Resolved"
    assert not {"custom_internal", "resolution", "followup_set"} & public.json().keys()
    assert (
        client.get(reverse("helpdesk:followups-detail", args=[ids[0]])).status_code
        == 403
    )
    client.force_login(outsider)
    assert client.get(user_url).status_code == 404


def test_api_failure_before_write_and_retry(client, staff, make_ticket, monkeypatch):
    ticket = make_ticket()
    client.force_login(staff)
    client.raise_request_exception = False
    url = reverse("helpdesk:followups-list")
    payload = {
        "ticket": ticket.pk,
        "new_status": Ticket.RESOLVED_STATUS,
        "comment": "Recovered",
    }
    before = snapshot()

    def fail_before_save(*_args, **_kwargs):
        raise RuntimeError("Injected pre-write storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(Ticket, "save", fail_before_save)
        failed = issue(client, "post", url, payload)
    assert failed.status_code == 500
    assert snapshot() == before
    recovered = issue(client, "post", url, payload)
    assert recovered.status_code == 201
    ticket.refresh_from_db()
    assert ticket.pk == Ticket.objects.get().pk
    assert ticket.status == Ticket.RESOLVED_STATUS
    followup = ticket.followup_set.get()
    assert followup.pk == recovered.json()["id"]
    assert followup.ticketchange_set.get(field="Status").new_value == "Resolved"


def test_transport_authentication_and_invalid_requests(client, staff, make_ticket, rf):
    ticket = make_ticket()
    url = detail(ticket)
    prior = snapshot()
    assert client.get(url).status_code in (401, 403)
    staff.set_password("LocalTeamFixture-Pass!")
    staff.save(update_fields=["password"])
    good = base64.b64encode(b"sqm-staff:LocalTeamFixture-Pass!").decode()
    wrong = base64.b64encode(b"sqm-staff:wrong").decode()
    assert client.get(url, HTTP_AUTHORIZATION=f"Basic {good}").status_code == 200
    assert client.get(url, HTTP_AUTHORIZATION=f"Basic {wrong}").status_code in (
        401,
        403,
    )
    client.force_login(staff)
    assert client.get(url, HTTP_ACCEPT="application/unknown").status_code == 406
    assert client.patch(url, "plain", content_type="text/plain").status_code == 415
    assert client.patch(url, "{", content_type="application/json").status_code == 400
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(staff)
    assert issue(csrf_client, "patch", url, {"priority": 1}).status_code == 403
    assert snapshot() == prior

    request = rf.get(url)
    token = get_token(request)
    csrf_client.cookies["csrftoken"] = request.META["CSRF_COOKIE"]
    accepted = csrf_client.patch(
        url, {"priority": 1}, content_type="application/json", HTTP_X_CSRFTOKEN=token
    )
    assert accepted.status_code == 200
    ticket.refresh_from_db()
    assert ticket.priority == accepted.json()["priority"] == 1
    assert snapshot()[1:] == prior[1:]
