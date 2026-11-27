from datetime import UTC, datetime, timedelta
from itertools import product

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from helpdesk import settings as helpdesk_settings
from helpdesk.models import Queue, Ticket


def at(day, hour, minute=0, second=0, microsecond=0):
    return datetime(2026, 11, day, hour, minute, second, microsecond, tzinfo=UTC)


def start_ticket(make_ticket, created):
    ticket = make_ticket()
    ticket.created = created
    ticket.save(update_fields=["created"])
    return ticket


@pytest.fixture
def auto_time(monkeypatch):
    for key, value in (
        ("FOLLOWUP_TIME_SPENT_AUTO", True),
        ("FOLLOWUP_TIME_SPENT_OPENING_HOURS", {}),
        ("FOLLOWUP_TIME_SPENT_EXCLUDE_HOLIDAYS", ()),
        ("FOLLOWUP_TIME_SPENT_EXCLUDE_STATUSES", ()),
        ("FOLLOWUP_TIME_SPENT_EXCLUDE_QUEUES", ()),
    ):
        monkeypatch.setattr(helpdesk_settings, key, value)
    return monkeypatch


@pytest.mark.parametrize(
    "enabled,explicit",
    [
        pytest.param(e, t, id=f"{int(e)}{int(t)}")
        for e, t in product((False, True), repeat=2)
    ],
)
def test_auto_duration_only_fills_missing_time(
    make_ticket, auto_time, enabled, explicit
):
    auto_time.setattr(helpdesk_settings, "FOLLOWUP_TIME_SPENT_AUTO", enabled)
    ticket = start_ticket(make_ticket, at(23, 10))
    manual = timedelta(minutes=15)
    followup = ticket.followup_set.create(
        title="Repair", date=at(23, 11), time_spent=manual if explicit else None
    )
    followup.refresh_from_db()
    expected = manual if explicit else timedelta(hours=1) if enabled else None
    assert followup.time_spent == expected
    assert Ticket.objects.get(pk=ticket.pk).time_spent == expected


def test_auto_time_aggregates_open_days_and_preserves_existing_interval(
    make_ticket, auto_time
):
    auto_time.setattr(
        helpdesk_settings,
        "FOLLOWUP_TIME_SPENT_OPENING_HOURS",
        {name: (9, 17) for name in ("monday", "tuesday", "wednesday")},
    )
    auto_time.setattr(
        helpdesk_settings, "FOLLOWUP_TIME_SPENT_EXCLUDE_HOLIDAYS", ("2026-11-24",)
    )
    ticket = start_ticket(make_ticket, at(23, 8))
    first = ticket.followup_set.create(title="Triage", date=at(25, 11))
    second = ticket.followup_set.create(title="Repair", date=at(25, 15))
    first.time_spent = None
    first.save()
    assert list(ticket.followup_set.values_list("pk", "time_spent")) == [
        (first.pk, timedelta(hours=10)),
        (second.pk, timedelta(hours=4)),
    ]
    assert ticket.time_spent == timedelta(hours=14)


@pytest.mark.parametrize(
    "status_match,queue_match",
    [
        pytest.param(s, q, id=f"{int(s)}{int(q)}")
        for s, q in product((False, True), repeat=2)
    ],
)
@pytest.mark.parametrize("prior", ["ticket", "history"])
def test_time_exclusions_use_prior_status_and_queue(
    make_ticket, auto_time, prior, status_match, queue_match
):
    ticket = start_ticket(make_ticket, at(23, 9))
    ticket.status = Ticket.RESOLVED_STATUS if prior == "ticket" else Ticket.OPEN_STATUS
    ticket.save(update_fields=["status"])
    prior_queue = ticket.queue
    if prior == "history":
        prior_queue = Queue.objects.create(
            title="Previous queue", slug="previous-queue"
        )
        history = ticket.followup_set.create(
            title="Move",
            date=at(23, 10),
            new_status=Ticket.RESOLVED_STATUS,
            time_spent=timedelta(minutes=5),
        )
        history.ticketchange_set.create(
            field="Queue", old_value=ticket.queue_id, new_value=prior_queue.pk
        )
    auto_time.setattr(
        helpdesk_settings,
        "FOLLOWUP_TIME_SPENT_EXCLUDE_STATUSES",
        (Ticket.RESOLVED_STATUS,) if status_match else (Ticket.CLOSED_STATUS,),
    )
    auto_time.setattr(
        helpdesk_settings,
        "FOLLOWUP_TIME_SPENT_EXCLUDE_QUEUES",
        (prior_queue.slug,) if queue_match else ("unrelated",),
    )
    followup = ticket.followup_set.create(title="Review", date=at(23, 11))
    followup.refresh_from_db()
    expected = (
        timedelta()
        if status_match or queue_match
        else timedelta(hours=2 if prior == "ticket" else 1)
    )
    assert followup.time_spent == expected
    assert ticket.time_spent == expected + (
        timedelta(minutes=5) if prior == "history" else timedelta()
    )
    assert ticket.followup_set.count() == (2 if prior == "history" else 1)


@pytest.mark.parametrize(
    "start,end,valid",
    [
        pytest.param(-1, -2, False, id="001"),
        pytest.param(-1, 24, False, id="010"),
        pytest.param(-1, 17, False, id="011"),
        pytest.param(25, 24, False, id="100"),
        pytest.param(17, 9, False, id="101"),
        pytest.param(9, 24, False, id="110"),
        pytest.param(9, 17, True, id="111"),
    ],
)
def test_opening_hours_order_and_range(make_ticket, auto_time, start, end, valid):
    auto_time.setattr(
        helpdesk_settings, "FOLLOWUP_TIME_SPENT_OPENING_HOURS", {"monday": (start, end)}
    )
    ticket = start_ticket(make_ticket, at(23, 8))
    modified = ticket.modified
    if valid:
        ticket.followup_set.create(title="Review", date=at(23, 18))
        assert ticket.followup_set.get().time_spent == timedelta(hours=8)
    else:
        with (
            pytest.raises(ImproperlyConfigured, match="opening_hours|OPENING_HOURS"),
            transaction.atomic(),
        ):
            ticket.followup_set.create(title="Review", date=at(23, 18))
        ticket.refresh_from_db()
        assert ticket.modified == modified and not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "late,midnight",
    [
        pytest.param(l, m, id=f"{int(l)}{int(m)}")
        for l, m in product((False, True), repeat=2)
    ],
)
def test_midnight_credit_applies_only_at_midnight_close(
    make_ticket, auto_time, late, midnight
):
    auto_time.setattr(
        helpdesk_settings,
        "FOLLOWUP_TIME_SPENT_OPENING_HOURS",
        {"monday": (0, 23.9999 if midnight else 23.5)},
    )
    ticket = start_ticket(make_ticket, at(23, 22))
    until = at(23, 23, 59, 59, 999999) if late else at(23, 23)
    ticket.followup_set.create(title="Night review", date=until)
    minutes = {
        (False, False): 60,
        (False, True): 60,
        (True, False): 90,
        (True, True): 120,
    }
    assert ticket.followup_set.get().time_spent == timedelta(
        minutes=minutes[late, midnight]
    )


@pytest.mark.parametrize(
    "start,end,hours",
    [
        pytest.param(7, 8, 0, id="before-before"),
        pytest.param(8, 10, 1, id="before-inside"),
        pytest.param(8, 18, 8, id="before-after"),
        pytest.param(10, 12, 2, id="inside-inside"),
        pytest.param(10, 18, 7, id="inside-after"),
        pytest.param(18, 19, 0, id="after-after"),
    ],
)
def test_automatic_time_clips_to_business_window(
    make_ticket, auto_time, start, end, hours
):
    auto_time.setattr(
        helpdesk_settings, "FOLLOWUP_TIME_SPENT_OPENING_HOURS", {"monday": (9, 17)}
    )
    ticket = start_ticket(make_ticket, at(23, start))
    ticket.followup_set.create(title="Logged work", date=at(23, end))
    assert ticket.followup_set.get().time_spent == timedelta(hours=hours)
    assert ticket.time_spent == timedelta(hours=hours)
