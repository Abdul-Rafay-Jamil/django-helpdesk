from itertools import product
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils import timezone

from helpdesk import settings as helpdesk_settings
from helpdesk.decorators import protect_view
from helpdesk.models import CustomField, Queue, Ticket, TicketDependency
from helpdesk.serializers import TicketSerializer
from helpdesk.views import public
from helpdesk.views import staff as staff_views
from helpdesk.views.api import (
    FollowUpViewSet,
    TicketViewSet,
    UserTicketViewSet,
    restrict_relation,
)
from helpdesk.views.public import ViewTicket, get_ticket_for_submitter


@pytest.fixture
def scoped_tickets(make_ticket, staff, monkeypatch):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_ENABLE_PER_QUEUE_STAFF_PERMISSION", True
    )
    opened = make_ticket()
    resolved = make_ticket(status=Ticket.RESOLVED_STATUS)
    hidden = make_ticket(status=Ticket.RESOLVED_STATUS)
    hidden.queue = Queue.objects.create(title="Other support", slug="other-support")
    hidden.save(update_fields=["queue"])
    group = Group.objects.create(name="Support readers")
    group.permissions.add(
        Permission.objects.get(codename=opened.queue.permission_name.split(".")[1])
    )
    staff.groups.add(group)
    return opened, resolved, hidden


@pytest.mark.parametrize(
    "query,expected",
    [
        pytest.param(None, (0, 1), id="omitted"),
        pytest.param("999", (0, 1), id="unknown"),
        pytest.param(str(Ticket.RESOLVED_STATUS), (1,), id="matched"),
        pytest.param(f"999,{Ticket.OPEN_STATUS}", (0,), id="mixed"),
    ],
)
def test_api_status_filter_keeps_queue_scope(scoped_tickets, staff, query, expected):
    request = SimpleNamespace(
        user=staff, query_params={} if query is None else {"status": query}
    )
    view = TicketViewSet()
    view.request = request
    assert {item.pk for item in view.get_queryset()} == {
        scoped_tickets[i].pk for i in expected
    }


def test_scoped_followup_listing_and_ticket_relation(scoped_tickets, staff):
    opened, _, hidden = scoped_tickets
    visible_note = opened.followup_set.create(title="Intake", date=timezone.now())
    hidden.followup_set.create(title="Other note", date=timezone.now())
    view = FollowUpViewSet()
    view.request = SimpleNamespace(user=staff)
    view.format_kwarg = None
    assert set(view.get_queryset().values_list("pk", flat=True)) == {visible_note.pk}
    serializer = view.get_serializer(data={"ticket": hidden.pk, "title": "Intake"})
    assert not serializer.is_valid()
    assert "ticket" in serializer.errors
    assert hidden.followup_set.count() == 1
    assert opened.followup_set.get() == visible_note


def test_scoped_ticket_serializer_allows_queue_move_only_to_visible_queue(
    scoped_tickets, staff
):
    opened, _, hidden = scoped_tickets
    destination = Queue.objects.create(
        title="Second visible queue", slug="second-visible"
    )
    staff.user_permissions.add(
        Permission.objects.get(codename=destination.permission_name.split(".")[1])
    )
    view = TicketViewSet()
    view.request = SimpleNamespace(user=staff)
    view.format_kwarg = None
    before = opened.modified
    denied = view.get_serializer(opened, data={"queue": hidden.queue_id}, partial=True)
    assert not denied.is_valid()
    assert "queue" in denied.errors
    opened.refresh_from_db()
    assert opened.modified == before
    assert opened.queue_id != hidden.queue_id
    approved = view.get_serializer(opened, data={"queue": destination.pk}, partial=True)
    assert approved.is_valid(), approved.errors
    approved.save()
    opened.refresh_from_db()
    hidden.refresh_from_db()
    assert opened.queue_id == destination.pk
    assert hidden.queue.slug == "other-support"
    assert not opened.followup_set.exists()


@pytest.mark.parametrize("field", ["missing", "title"])
def test_relation_filter_fails_closed_on_misconfiguration(queue, field):
    with pytest.raises(ImproperlyConfigured, match=field):
        restrict_relation(TicketSerializer(), field, Queue.objects.filter(pk=queue.pk))


def test_relation_filter_applies_to_list_serializer(queue):
    serializer = TicketSerializer(many=True)
    allowed = Queue.objects.filter(pk=queue.pk)
    restrict_relation(serializer, "queue", allowed)
    assert list(serializer.child.fields["queue"].queryset) == [queue]


@pytest.mark.parametrize(
    "email,visible", [("", False), ("customer@example.test", True)]
)
def test_customer_api_queryset_requires_a_nonempty_email(make_ticket, email, visible):
    own = make_ticket()
    blank = make_ticket()
    Ticket.objects.filter(pk=blank.pk).update(submitter_email="")
    view = UserTicketViewSet()
    view.request = SimpleNamespace(user=SimpleNamespace(email=email))
    assert set(view.get_queryset().values_list("pk", flat=True)) == (
        {own.pk} if visible else set()
    )


@pytest.mark.parametrize(
    "authenticated,matching,key",
    [
        pytest.param(a, m, k, id=f"{int(a)}{int(m)}{int(k)}")
        for a, m, k in product((False, True), repeat=3)
    ],
)
def test_submitter_proof_table(rf, make_ticket, authenticated, matching, key):
    ticket = make_ticket()
    request = rf.get("/")
    request.user = SimpleNamespace(
        is_authenticated=authenticated,
        email=ticket.submitter_email if matching else "other@example.test",
    )
    arguments = (
        request,
        ticket.pk,
        ticket.submitter_email,
        ticket.secret_key if key else "",
    )
    if (authenticated and matching) or key:
        assert get_ticket_for_submitter(*arguments) == ticket
    else:
        with pytest.raises(Ticket.DoesNotExist):
            get_ticket_for_submitter(*arguments)


def test_public_detail_filters_private_followups_and_changes(rf, make_ticket):
    ticket = make_ticket()
    submitter = get_user_model().objects.create_user(
        "sqm-viewer", email=ticket.submitter_email
    )
    visible = ticket.followup_set.create(
        date=timezone.now(), title="Reply", public=True
    )
    ticket.followup_set.create(date=timezone.now(), title="Internal", public=False)
    CustomField.objects.create(
        name="internal", label="Internal", data_type="varchar", staff_only=True
    )
    visible.ticketchange_set.create(
        field="internal", old_value="old", new_value="private"
    )
    shown = visible.ticketchange_set.create(
        field="Priority", old_value="3", new_value="2"
    )
    request = rf.get(
        "/tickets/?"
        + urlencode({"ticket": ticket.ticket_for_url, "email": ticket.submitter_email})
    )
    request.user = submitter
    response = ViewTicket.as_view()(request)
    followups = list(response.context_data["followups"])
    assert [entry.pk for entry in followups] == [visible.pk]
    assert followups[0].public_changes == [shown]
    assert ticket.followup_set.count() == 2
    assert visible.ticketchange_set.count() == 2


@pytest.mark.parametrize("lookup", ["blank", "id-only", "unknown"])
def test_public_detail_incomplete_lookup_returns_search_without_ticket(
    rf, make_ticket, settings, lookup
):
    settings.HELPDESK_VIEW_A_TICKET_PUBLIC = True
    ticket = make_ticket()
    query = {
        "blank": {},
        "id-only": {"ticket": ticket.ticket_for_url},
        "unknown": {"ticket": "missing-999", "email": ticket.submitter_email},
    }[lookup]
    request = rf.get("/view/?" + urlencode(query))
    request.user = AnonymousUser()
    response = ViewTicket.as_view()(request)
    assert response.status_code == 200
    assert response.context_data["ticket"] is False
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "close,resolved",
    [
        pytest.param(c, r, id=f"{int(c)}{int(r)}")
        for c, r in product((False, True), repeat=2)
    ],
)
def test_public_accept_resolution_decision_and_retry(rf, make_ticket, close, resolved):
    initial = Ticket.RESOLVED_STATUS if resolved else Ticket.OPEN_STATUS
    ticket = make_ticket(status=initial, resolution="Existing note")
    original = ticket.modified
    target = ticket.ticket_url + ("&close=1" if close else "")
    for attempt in range(2):
        request = rf.get(target)
        request.user = AnonymousUser()
        response = ViewTicket.as_view()(request)
        assert response.status_code == (
            302 if close and resolved and attempt == 0 else 200
        )
    ticket.refresh_from_db()
    if close and resolved:
        assert ticket.status == Ticket.CLOSED_STATUS
        followup = ticket.followup_set.get()
        assert followup.public is True and followup.new_status == Ticket.CLOSED_STATUS
        assert followup.ticketchange_set.filter(field="Status").count() == 1
    else:
        assert ticket.status == initial and ticket.modified == original
        assert not ticket.followup_set.exists()
    assert ticket.resolution == "Existing note"
    assert Ticket.objects.count() == 1


def test_staff_detail_context_exposes_only_reachable_dependency_links(
    rf, make_ticket, scoped_tickets, staff, monkeypatch
):
    opened, resolved, hidden = scoped_tickets
    related = make_ticket()
    visible_out = TicketDependency.objects.create(ticket=opened, depends_on=resolved)
    visible_in = TicketDependency.objects.create(ticket=related, depends_on=opened)
    TicketDependency.objects.create(ticket=opened, depends_on=hidden)
    TicketDependency.objects.create(ticket=hidden, depends_on=opened)
    monkeypatch.setattr(
        staff_views, "render", lambda request, template, context: context
    )
    request = rf.get("/tickets/")
    request.user = staff
    context = staff_views.view_ticket(request, opened.pk)
    assert context["ticket"].pk == opened.pk
    assert list(context["dependencies"]) == [visible_out]
    assert list(context["resolves"]) == [visible_in]
    assert TicketDependency.objects.count() == 4


def test_staff_detail_subscription_retry_preserves_single_subscriber(
    rf, make_ticket, staff, monkeypatch
):
    ticket = make_ticket()
    before = ticket.modified
    monkeypatch.setattr(
        staff_views, "render", lambda request, template, context: context
    )
    first = rf.get("/tickets/?subscribe=1")
    first.user = staff
    assert staff_views.view_ticket(first, ticket.pk).status_code == 302
    subscriber = ticket.ticketcc_set.get()
    assert subscriber.user_id == staff.pk
    repeated = rf.get("/tickets/?subscribe=1")
    repeated.user = staff
    assert staff_views.view_ticket(repeated, ticket.pk)["SHOW_SUBSCRIBE"] is False
    ticket.refresh_from_db()
    assert ticket.ticketcc_set.get() == subscriber
    assert ticket.modified == before
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "authenticated,redirect,hide",
    [
        pytest.param(a, r, h, id=f"{int(a)}{int(r)}{int(h)}")
        for a, r, h in product((False, True), repeat=3)
    ],
)
def test_public_protection_precedence(rf, monkeypatch, authenticated, redirect, hide):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_REDIRECT_TO_LOGIN_BY_DEFAULT", redirect
    )
    monkeypatch.setattr(helpdesk_settings, "HELPDESK_ANON_ACCESS_RAISES_404", hide)
    seen = []

    def hook(request):
        seen.append(request)
        return HttpResponseRedirect("/protected/")

    monkeypatch.setattr(helpdesk_settings, "HELPDESK_PUBLIC_VIEW_PROTECTOR", hook)
    request = rf.get("/")
    request.user = SimpleNamespace(is_authenticated=authenticated)
    view = protect_view(lambda req: HttpResponse())
    if not authenticated and redirect:
        assert view(request).url != "/protected/"
        assert not seen
    elif not authenticated and hide:
        with pytest.raises(Http404):
            view(request)
        assert not seen
    else:
        assert view(request).url == "/protected/"
        assert seen == [request]


@pytest.mark.parametrize(
    "post,mail,nonstaff",
    [
        pytest.param(p, m, n, id=f"{int(p)}{int(m)}{int(n)}")
        for p, m, n in product((False, True), repeat=3)
    ],
)
def test_staff_update_dispatch_table(rf, monkeypatch, post, mail, nonstaff):
    # A synthetic POST mapping on GET lets the method check be toggled on its own.
    request = rf.post("/", {"mail": "reporter@example.test"} if mail else {})
    if not post:
        request.method = "GET"
    request.user = SimpleNamespace(
        is_authenticated=True, is_active=True, is_staff=not nonstaff
    )
    public_handler = Mock(return_value="public")
    staff_handler = Mock(return_value="staff")
    monkeypatch.setattr(public, "update_ticket_from_public_view", public_handler)
    monkeypatch.setattr(staff_views.UpdateTicketView, "as_view", lambda: staff_handler)
    result = staff_views.update_ticket_view(request, 12)
    if post and mail and nonstaff:
        assert result == "public"
        public_handler.assert_called_once_with(request, 12)
        staff_handler.assert_not_called()
    else:
        assert result == "staff"
        staff_handler.assert_called_once_with(request, ticket_id=12)
        public_handler.assert_not_called()
