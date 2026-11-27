from itertools import product
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.exceptions import PermissionDenied

from helpdesk import settings as helpdesk_settings
from helpdesk.models import Queue
from helpdesk.user import HelpdeskUser
from helpdesk.views.staff import get_ticket_from_request_with_authorisation


@pytest.mark.parametrize("limited,granted", list(product((False, True), repeat=2)))
@pytest.mark.parametrize("role", ["customer", "staff", "superuser"])
def test_queue_authorization_table(queue, monkeypatch, limited, granted, role):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", limited
    )
    user = SimpleNamespace(
        is_superuser=role == "superuser",
        is_staff=role != "customer",
        is_authenticated=True,
        is_active=True,
        has_perm=lambda permission: granted and permission == queue.permission_name,
    )
    helper = HelpdeskUser(user)
    if role == "superuser":
        expected = True
    elif limited:
        expected = granted
    else:
        expected = role == "staff"
    assert helper.can_access_queue(queue) is expected


NONSTAFF_CASES = [
    ("000", False),
    ("001", False),
    ("010", False),
    ("011", False),
    ("100", False),
    ("101", False),
    ("110", False),
    ("111", True),
]


@pytest.mark.parametrize(
    "bits,expected", NONSTAFF_CASES, ids=[row[0] for row in NONSTAFF_CASES]
)
def test_nonstaff_full_access_table(monkeypatch, bits, expected):
    enabled, authenticated, active = (bit == "1" for bit in bits)
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE", enabled
    )
    user = SimpleNamespace(
        is_superuser=False,
        is_staff=False,
        is_authenticated=authenticated,
        is_active=active,
    )
    assert HelpdeskUser(user).has_full_access() is expected


@pytest.mark.parametrize("decision", [False, True])
def test_callable_staff_access_policy(monkeypatch, decision):
    user = SimpleNamespace(is_superuser=False, is_staff=False)
    monkeypatch.setattr(
        helpdesk_settings,
        "HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE",
        lambda candidate: decision if candidate is user else False,
    )
    assert HelpdeskUser(user).has_full_access() is decision


@pytest.mark.parametrize("grant,assignment", list(product((False, True), repeat=2)))
def test_scoped_access_by_queue_or_assignment(
    make_ticket, staff, monkeypatch, grant, assignment
):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    ticket = make_ticket(assigned_to=staff if assignment else None)
    unrelated = make_ticket()
    foreign = Queue.objects.create(title="Foreign", slug="foreign")
    unrelated.queue = foreign
    unrelated.save()
    if grant:
        permission = Permission.objects.get(
            codename=ticket.queue.permission_name.split(".")[1]
        )
        staff.user_permissions.add(permission)
    helper = HelpdeskUser(staff)
    expected_ids = {ticket.pk} if grant or assignment else set()
    assert set(helper.accessible_tickets().values_list("pk", flat=True)) == expected_ids
    assert helper.can_access_ticket(ticket) is bool(expected_ids)
    assert helper.can_access_ticket(unrelated) is False


@pytest.mark.parametrize("actor", ["staff", "submitter", "unknown"])
def test_ticket_update_lookup_selects_only_authorized_record(
    rf, make_ticket, staff, actor
):
    ticket = make_ticket()
    other = make_ticket()
    request = rf.post(
        "/",
        {
            "mail": ticket.submitter_email,
            "key": (ticket.secret_key if actor == "submitter" else ""),
        },
    )
    request.user = staff if actor == "staff" else AnonymousUser()
    if actor == "unknown":
        with pytest.raises(PermissionDenied):
            get_ticket_from_request_with_authorisation(request, ticket.pk, public=False)
    else:
        assert (
            get_ticket_from_request_with_authorisation(
                request, ticket.pk, public=False
            ).pk
            == ticket.pk
        )
    assert not ticket.followup_set.exists()
    assert not other.followup_set.exists()
