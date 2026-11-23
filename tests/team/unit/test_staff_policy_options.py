from itertools import product
from runpy import run_path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.http import HttpResponse, HttpResponseRedirect

from helpdesk import decorators
from helpdesk import settings as helpdesk_settings


@pytest.mark.parametrize(
    "auth,active,allow,staff,hook",
    [
        pytest.param(*bits, id="".join(str(int(bit)) for bit in bits))
        for bits in product((False, True), repeat=5)
    ],
)
def test_configurable_staff_protector_precedence(
    rf, make_ticket, monkeypatch, auth, active, allow, staff, hook
):
    ticket = make_ticket()
    before = ticket.modified
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE", allow
    )
    limiter = Mock(return_value=HttpResponseRedirect("/protected/") if hook else None)
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_STAFF_VIEW_PROTECTOR", limiter)

    # Evaluate the import-time policy without changing decorators used by other tests.
    namespace = run_path(decorators.__file__)

    @namespace["helpdesk_staff_member_required"]
    def change_priority(request):
        ticket.priority = 2
        ticket.save()
        return HttpResponse()

    request = rf.post("/")
    request.user = SimpleNamespace(
        is_authenticated=auth, is_active=active, is_staff=staff
    )
    if not (auth and active and (allow or staff)):
        response = change_priority(request)
        assert response.status_code == 302 and response.url != "/protected/"
        limiter.assert_not_called()
        writes = False
    else:
        response = change_priority(request)
        limiter.assert_called_once_with(request)
        assert response.status_code == (302 if hook else 200)
        if hook:
            assert response.url == "/protected/"
        writes = not hook
    ticket.refresh_from_db()
    assert ticket.priority == (2 if writes else 3)
    if not writes:
        assert ticket.modified == before
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "policy,active,marker,allowed",
    [
        ("boolean", True, False, True),
        ("boolean", False, True, False),
        ("callable", True, False, False),
        ("callable", True, True, True),
    ],
)
def test_configured_policy_binds_ticket_detail_decorator(
    rf, make_ticket, monkeypatch, policy, active, marker, allowed
):
    ticket = make_ticket()
    before = ticket.modified
    predicate = Mock(side_effect=lambda actor: actor.marker)
    monkeypatch.setattr(
        helpdesk_settings,
        "HELPDESK_ALLOW_NON_STAFF_TICKET_UPDATE",
        True if policy == "boolean" else predicate,
    )
    namespace = run_path(decorators.__file__)

    @namespace["helpdesk_staff_member_required"]
    def update(request):
        ticket.priority = 2
        ticket.save()
        return HttpResponse()

    request = rf.post("/")
    request.user = SimpleNamespace(
        is_authenticated=True, is_active=active, is_staff=False, marker=marker
    )
    response = update(request)
    assert response.status_code == (200 if allowed else 302)
    if policy == "callable":
        predicate.assert_called_once_with(request.user)
    ticket.refresh_from_db()
    assert ticket.priority == (2 if allowed else 3)
    if not allowed:
        assert ticket.modified == before
