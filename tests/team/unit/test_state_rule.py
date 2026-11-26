from itertools import product

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from helpdesk.models import FollowUp, Ticket, TicketDependency
from helpdesk.update_ticket import get_and_set_ticket_status, update_ticket
from helpdesk.views.public import ViewTicket, update_ticket_from_public_view
from tests.team.state_oracle import (
    ALLOWED,
    CLOSED,
    DUPLICATE,
    LABELS,
    OPEN,
    REOPENED,
    RESOLVED,
)


@pytest.mark.parametrize(
    "source,target",
    [
        pytest.param(source, target, id=f"{LABELS[source]}-{LABELS[target]}")
        for source, target in product(ALLOWED, repeat=2)
    ],
)
def test_public_transition_matrix(rf, make_ticket, source, target):
    ticket = make_ticket(status=source)
    before = ticket.modified
    assert tuple(code for code, _ in ticket.get_allowed_status_flow) == ALLOWED[source]

    request = rf.post(
        ticket.ticket_url, {"new_status": target, "comment": "Status checked"}
    )
    request.user = AnonymousUser()

    if target in ALLOWED[source]:
        response = ViewTicket().post(request)
        assert response.status_code == 302
        ticket.refresh_from_db()
        assert ticket.status == target
        follow_up = ticket.followup_set.get()
        assert follow_up.comment == "Status checked"
        assert follow_up.public is True
        assert follow_up.new_status == (target if source != target else None)
        assert follow_up.ticketchange_set.filter(field="Status").count() == int(
            source != target
        )
    else:
        with pytest.raises(PermissionDenied):
            ViewTicket().post(request)
        ticket.refresh_from_db()
        assert ticket.status == source
        assert ticket.modified == before
        assert not ticket.followup_set.exists()


@pytest.mark.parametrize("target,allowed", [(RESOLVED, False), (OPEN, True)])
def test_open_dependency_blocks_only_state_changes(rf, make_ticket, target, allowed):
    ticket = make_ticket(status=OPEN)
    TicketDependency.objects.create(ticket=ticket, depends_on=make_ticket(status=OPEN))
    request = rf.post(ticket.ticket_url, {"new_status": target, "comment": "Waiting"})
    request.user = AnonymousUser()
    if allowed:
        assert ViewTicket().post(request).status_code == 302
    else:
        with pytest.raises(PermissionDenied):
            ViewTicket().post(request)
    ticket.refresh_from_db()
    assert ticket.status == OPEN
    assert ticket.followup_set.count() == int(allowed)


def test_unmapped_status_uses_all_display_choices():
    assert dict(Ticket(status=999).get_allowed_status_flow) == LABELS


@pytest.mark.parametrize("input_status", ["bad", ""])
def test_public_malformed_or_empty_status(rf, make_ticket, input_status):
    ticket = make_ticket(status=OPEN)
    request = rf.post(ticket.ticket_url, {"new_status": input_status})
    request.user = AnonymousUser()
    if input_status:
        with pytest.raises(PermissionDenied):
            ViewTicket().post(request)
    else:
        assert ViewTicket().post(request).status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == OPEN
    assert ticket.followup_set.count() == int(not input_status)


@pytest.mark.django_db
def test_public_status_post_to_missing_ticket_is_rejected(rf):
    request = rf.post(
        "/view/?ticket=sqe-support-999&email=customer@example.test&key=unused",
        {"new_status": RESOLVED},
    )
    request.user = AnonymousUser()
    with pytest.raises(PermissionDenied):
        ViewTicket().post(request)
    assert not FollowUp.objects.exists()


@pytest.mark.parametrize(
    "source,target,blocked,expected",
    [
        (OPEN, RESOLVED, False, OPEN),
        (OPEN, RESOLVED, True, OPEN),
        (REOPENED, RESOLVED, True, REOPENED),
        (REOPENED, RESOLVED, False, RESOLVED),
        (CLOSED, RESOLVED, False, CLOSED),
        (REOPENED, "bad", False, REOPENED),
    ],
)
def test_public_comment_form_transition(
    rf, make_ticket, source, target, blocked, expected
):
    ticket = make_ticket(status=source)
    if blocked:
        TicketDependency.objects.create(
            ticket=ticket, depends_on=make_ticket(status=OPEN)
        )
    request = rf.post(
        f"/tickets/{ticket.pk}/update/",
        {
            "mail": ticket.submitter_email,
            "key": ticket.secret_key,
            "new_status": target,
        },
    )
    request.user = AnonymousUser()
    assert update_ticket_from_public_view(request, ticket.pk).status_code == 302
    ticket.refresh_from_db()
    assert ticket.status == expected
    if source == expected:
        assert not ticket.followup_set.exists()
    else:
        assert ticket.followup_set.get().new_status == expected


# Oracle columns use N/P/T order 000, 001, 010, 011, 100, 101, 110, 111.
# N: no existing resolution; P: previously Reopened; T: nonempty comment.
RESOLUTION_CASES = []
for target, outcomes in [
    (DUPLICATE, "00000000"),
    (RESOLVED, "11111111"),
    (CLOSED, "00011111"),
]:
    for conditions, outcome in zip(
        product((False, True), repeat=3), outcomes, strict=True
    ):
        bits = "".join(str(int(value)) for value in conditions)
        RESOLUTION_CASES.append(
            pytest.param(
                target, *conditions, outcome == "1", id=f"{LABELS[target]}-{bits}"
            )
        )


@pytest.mark.parametrize(
    "target,no_resolution,reopened,comment,replace", RESOLUTION_CASES
)
def test_resolution_decision_mcc(
    make_ticket, staff, target, no_resolution, reopened, comment, replace
):
    old_resolution = None if no_resolution else "Previous resolution"
    note = "New resolution" if comment else ""
    ticket = make_ticket(
        status=REOPENED if reopened else OPEN, resolution=old_resolution
    )

    follow_up = update_ticket(staff, ticket, comment=note, new_status=target)

    ticket.refresh_from_db()
    follow_up.refresh_from_db()
    assert ticket.status == target
    assert ticket.resolution == (note if replace else old_resolution)
    assert follow_up.ticket_id == ticket.pk
    assert follow_up.comment == note


@pytest.mark.parametrize(
    "target,title,comment,expected",
    [
        (OPEN, "", "Details", "Comment"),
        (OPEN, "", "", "Updated"),
        (RESOLVED, "", "", "Resolved"),
        (RESOLVED, "Checked", "", "Checked and Resolved"),
    ],
)
def test_status_update_sets_history_title(
    make_ticket, target, title, comment, expected
):
    ticket = make_ticket(status=OPEN)
    follow_up = FollowUp(
        ticket=ticket, title=title, comment=comment, date=timezone.now()
    )
    assert get_and_set_ticket_status(target, ticket, follow_up) == ("Open", OPEN)
    follow_up.refresh_from_db()
    assert follow_up.title == expected
