"""HTTP and database assertions driven by OpenAPI EP and BVA rows."""

from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_duration

from helpdesk.models import (
    CustomField,
    FollowUp,
    FollowUpAttachment,
    Ticket,
    TicketChange,
    TicketCustomFieldValue,
)
from tests.team.api.generate_cases import generate, load_spec, operations
from tests.team.state_oracle import ALLOWED

SPEC = load_spec()
OPERATIONS = {
    op["operationId"]: (path, method, op) for path, method, op in operations(SPEC)
}
CREATE = {"createTicket", "submitPublic"}
UPDATE = {"patchTicket", "putTicket"}
PAGED = {"listTickets", "listCustomerTickets", "listFollowUps"}
CUSTOMER = "customer@example.test"
READ_ONLY = {
    "id",
    "followup_set",
    "followupattachment_set",
    "date",
    "message_id",
    "email_recipients",
}


def snapshot():
    return tuple(
        list(model.objects.order_by("pk").values())
        for model in (
            Ticket,
            FollowUp,
            TicketChange,
            FollowUpAttachment,
            TicketCustomFieldValue,
        )
    )


def reference(value, queue, assignee):
    if isinstance(value, list):
        return [reference(item, queue, assignee) for item in value]
    if not isinstance(value, str) or not value.startswith("$fixture:"):
        return value
    fixtures = {
        "queue": queue.pk,
        "ticket": 1,
        "other_ticket": 2,
        "assignee": assignee.pk,
    }
    name = value.removeprefix("$fixture:")
    if name in fixtures:
        return fixtures[name]
    filename, content = {
        "file": ("sample.txt", b"Helpdesk fixture"),
        "empty_file": ("empty.txt", b""),
        "one_byte_file": ("one.txt", b"x"),
        "two_byte_file": ("two.txt", b"xy"),
        "blocked_file": ("blocked.exe", b"Plain text fixture"),
    }[name]
    return SimpleUploadedFile(filename, content, content_type="text/plain")


def issue(client, method, url, body, multipart=False):
    if method == "get":
        return client.get(url)
    if multipart and method != "post":
        from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart

        return getattr(client, method)(
            url, encode_multipart(BOUNDARY, body), content_type=MULTIPART_CONTENT
        )
    if multipart:
        return client.post(url, body)
    return getattr(client, method)(url, body, content_type="application/json")


def prepare_pagination(operation, queue, ticket):
    now = timezone.now()
    if operation == "listFollowUps":
        FollowUp.objects.bulk_create(
            [
                FollowUp(ticket=ticket, title=f"Entry {i}", date=now)
                for i in range(2, 27)
            ]
        )
    else:
        Ticket.objects.bulk_create(
            [
                Ticket(
                    queue=queue,
                    title=f"Ticket {i}",
                    submitter_email=CUSTOMER,
                    created=now,
                )
                for i in range(3, 27)
            ]
        )


def assert_read(operation, response, query, pk):
    if query.get("format") == "api":
        assert response["Content-Type"].startswith("text/html")
        return
    body = response.json()
    if operation in PAGED:
        assert set(body) == {"count", "next", "previous", "results"}
        expected = 26
        if "status" in query:
            codes = {
                int(s)
                for s in query["status"].split(",")
                if s in {"1", "2", "3", "4", "5"}
            }
            if codes:
                expected = Ticket.objects.filter(status__in=codes).count()
                assert all(row["status"] in codes for row in body["results"])
        assert body["count"] == expected
        page_size = query.get("page_size", 25)
        if not str(page_size).isdigit() or int(page_size) < 1:
            page_size = 25
        page_size = int(page_size)
        page = query.get("page") or 1
        if page == "last":
            page = max(1, (expected + page_size - 1) // page_size)
        assert len(body["results"]) == min(
            page_size, max(0, expected - (int(page) - 1) * page_size)
        )
        assert len({row["id"] for row in body["results"]}) == len(body["results"])
    else:
        assert body["id"] == pk
        if operation == "getCustomerTicket":
            assert body["submitter"] == CUSTOMER
            assert not ({"resolution", "assigned_to", "followup_set"} & body.keys())
        if operation == "getFollowUp":
            assert body["ticket"] == pk


def normalize(field, value):
    if value is None:
        return value
    if field == "due_date":
        return (
            parse_datetime(value)
            if "T" in value
            else datetime(2026, 12, 15, tzinfo=UTC)
        )
    if field in {
        "queue",
        "assigned_to",
        "merged_to",
        "priority",
        "status",
        "new_status",
    }:
        return int(value)
    if field in {"public", "on_hold"}:
        return value is True or value == "true"
    return value


def assert_create(operation, response, payload, field):
    ticket = Ticket.objects.latest("pk")
    if operation != "submitPublic":
        assert response.json()["id"] == ticket.pk
    assert ticket.status == Ticket.OPEN_STATUS
    assert ticket.title == payload["title"]
    assert (
        ticket.description
        == payload["body" if operation == "submitPublic" else "description"]
    )
    assert ticket.queue_id == int(payload["queue"])
    assert ticket.priority == int(payload["priority"])
    assert ticket.submitter_email == (payload.get("submitter_email") or "")
    assert ticket.assigned_to_id == payload.get("assigned_to")
    assert (
        ticket.merged_to_id is None and ticket.resolution is None and not ticket.on_hold
    )
    history = ticket.followup_set.get()
    assert history.comment == ticket.description and history.public
    attachments = history.followupattachment_set.all()
    assert attachments.count() == int("attachment" in payload)
    if "attachment" in payload:
        assert attachments.get().filename == payload["attachment"].name
    if "due_date" in payload:
        assert ticket.due_date == normalize("due_date", payload["due_date"] or None)
    if field == "custom_asset" and payload.get(field):
        assert (
            ticket.ticketcustomfieldvalue_set.get(field__name="asset").value
            == payload[field]
        )


def assert_update(ticket, response, payload, field, before):
    ticket.refresh_from_db()
    result = response.json()
    assert ticket.pk == result["id"]
    for name in {
        "queue",
        "assigned_to",
        "merged_to",
        "title",
        "description",
        "resolution",
        "submitter_email",
        "status",
        "on_hold",
        "priority",
        "due_date",
    } & payload.keys():
        model_name = (
            f"{name}_id" if name in {"queue", "assigned_to", "merged_to"} else name
        )
        assert getattr(ticket, model_name) == normalize(name, payload[name])
    if field == "custom_asset" and field in payload:
        assert (
            ticket.ticketcustomfieldvalue_set.get(field__name="asset").value
            == payload[field]
        )
    assert snapshot()[1:4] == before[1:4]
    assert len(snapshot()[0]) == len(before[0])


def assert_followup(ticket, staff, response, payload, prior_status, before):
    ticket.refresh_from_db()
    history = ticket.followup_set.get(pk=response.json()["id"])
    target = normalize("new_status", payload.get("new_status")) or prior_status
    assert ticket.status == target
    assert history.comment == payload.get("comment", "")
    assert history.public == normalize("public", payload.get("public", False))
    assert history.user_id == staff.pk
    assert history.time_spent == (
        parse_duration(payload["time_spent"])
        if payload.get("time_spent") is not None
        else None
    )
    assert history.new_status == (target if target != prior_status else None)
    assert history.ticketchange_set.filter(field="Status").count() == int(
        target != prior_status
    )
    assert history.followupattachment_set.count() == len(payload.get("attachments", []))
    if payload.get("title"):
        assert ticket.title == payload["title"]
    assert history.message_id is None
    assert len(snapshot()[1]) == len(before[1]) + 1


@pytest.mark.parametrize(
    "case", [pytest.param(row, id=row["id"]) for row in generate(SPEC)]
)
def test_openapi_input(case, client, make_ticket, queue, staff, settings, tmp_path):
    operation, name = case["operation"], case["input"]
    path, method, definition = OPERATIONS[operation]
    client.raise_request_exception = False
    settings.MEDIA_ROOT = tmp_path
    assignee = get_user_model().objects.create_user("api-assignee", is_staff=True)
    customer = get_user_model().objects.create_user("api-customer", email=CUSTOMER)
    client.force_login(
        customer if definition["x-sqe-audience"] == "customer" else staff
    )
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    ticket = None
    if operation not in CREATE or name == "merged_to":
        ticket = make_ticket(id=1, status=Ticket.OPEN_STATUS)
        make_ticket(id=2, status=Ticket.RESOLVED_STATUS)
        if operation in {"listFollowUps", "getFollowUp"}:
            FollowUp.objects.create(
                id=1, ticket=ticket, title="History", date=timezone.now()
            )
            if operation == "getFollowUp":
                FollowUp.objects.create(
                    id=2, ticket_id=2, title="Other history", date=timezone.now()
                )
        if operation in PAGED:
            prepare_pagination(operation, queue, ticket)
    if name == "custom_asset":
        CustomField.objects.create(
            name="asset", label="Asset", data_type="varchar", max_length=12
        )
    payload = {
        key: reference(x, queue, assignee)
        for key, x in definition.get("x-sqe-seed", {}).items()
    }
    query = {}
    pk = 1
    value = reference(case["value"], queue, assignee)
    if case["location"] == "body":
        if case["omitted"]:
            payload.pop(name, None)
        else:
            payload[name] = value
    elif case["location"] == "query" and not case["omitted"]:
        query[name] = value
    elif case["location"] == "path":
        pk = value
        if operation in UPDATE and case["valid"]:
            ticket = Ticket.objects.get(pk=pk)
    url = path.format(id=pk)
    if query:
        url += "?" + urlencode(query)
    prior = ticket.status if ticket else None
    before = snapshot()
    files = (
        isinstance(value, SimpleUploadedFile)
        or isinstance(value, list)
        and any(isinstance(item, SimpleUploadedFile) for item in value)
    )
    response = issue(client, method, url, payload, operation == "submitPublic" or files)
    expected = case["status"] or (
        302
        if operation == "submitPublic"
        else 201
        if operation in CREATE | {"createFollowUp"}
        else 200
    )
    if operation == "submitPublic" and not case["valid"]:
        expected = 200
    if (
        name in {"status", "new_status"}
        and case["location"] == "body"
        and case["valid"]
        and operation not in CREATE
        and value is not None
        and int(value) not in ALLOWED[prior]
    ):
        expected = 400
    if (
        case["location"] == "query"
        and name == "status"
        and operation not in PAGED
        and str(value).split(",")[0] == str(Ticket.RESOLVED_STATUS)
    ):
        expected = 404
    rejected = expected >= 400 or operation == "submitPublic" and expected == 200
    if rejected or method == "get" or response.status_code >= 400:
        assert snapshot() == before, (
            f"{case['id']} HTTP {response.status_code} changed stored records"
        )
    assert response.status_code == expected, (
        f"{case['id']} returned HTTP {response.status_code}"
    )
    if rejected:
        if operation == "submitPublic":
            assert response.context["form"].errors
        elif expected == 400:
            assert response.json()
        return
    if method == "get":
        assert_read(operation, response, query, pk)
    elif operation in CREATE:
        assert len(snapshot()[0]) == len(before[0]) + 1
        assert_create(operation, response, payload, name)
    elif operation in UPDATE:
        assert_update(ticket, response, payload, name, before)
    else:
        assert_followup(ticket, staff, response, payload, prior, before)
