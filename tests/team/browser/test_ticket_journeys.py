"""Six Ticket Management use cases, each with five Chromium flows."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urljoin, urlsplit

import pytest
from django.db import connections
from django.utils import timezone
from playwright.sync_api import expect

from helpdesk.models import FollowUp, Queue, Ticket, TicketChange, TicketDependency

pytestmark = pytest.mark.django_db(transaction=True)
FLOWS = ("M", "A1", "A2", "E1", "E2")
INTAKE_TITLE = "Printer has a dark stripe"
INTAKE_BODY = "A dark stripe appears on every printed page."
EMAIL = "customer@example.test"


def database(callback, *args):
    def run():
        try:
            return callback(*args)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


def changes(ticket):
    return TicketChange.objects.filter(followup__ticket=ticket)


def state(ticket):
    ticket.refresh_from_db()
    return (
        ticket.status,
        ticket.assigned_to_id,
        ticket.resolution,
        ticket.modified,
        tuple(
            ticket.followup_set.order_by("pk").values_list(
                "pk", "comment", "public", "new_status", "user_id"
            )
        ),
        tuple(
            changes(ticket)
            .order_by("pk")
            .values_list("field", "old_value", "new_value")
        ),
    )


def heading(page, ticket):
    expect(
        page.get_by_role("heading", name=re.compile(re.escape(ticket.title)))
    ).to_be_visible()


def shown_status(page, value):
    row = (
        page.locator("small")
        .filter(has_text=re.compile(r"^Status$"))
        .locator("..")
        .locator("p")
    )
    expect(row).to_have_text(value)


def respond(page):
    page.get_by_role("tab", name="Respond").click()
    form = page.locator("#ticket-respond-form")
    expect(form).to_be_visible()
    return form


def submit_update(form, comment="", status=None, public=True):
    if comment:
        form.get_by_label("Comment / Resolution", exact=True).fill(comment)
    if status:
        form.get_by_role("radio", name=status, exact=True).check()
    if not public:
        form.get_by_label("Is this update public? (Optional)", exact=True).uncheck()
    form.get_by_role("button", name="Save", exact=True).click()


def visible_note(page, note):
    match = page.locator("#followup-list").get_by_text(note, exact=True)
    expect(match).to_have_count(1)
    expect(match).to_be_visible()


def lookup(page, url, ticket_id, email):
    page.goto(url("public_view"))
    expect(page.get_by_role("heading", name="Search your ticket")).to_be_visible()
    page.locator('input[name="ticket"]').fill(str(ticket_id))
    page.locator('input[name="email"]').fill(email)
    page.get_by_role("button", name="Search", exact=True).click()


def ticket_form(page, url):
    page.goto(url("home"))
    page.get_by_role("link", name="Create a ticket", exact=True).click()
    return page.get_by_role("form").filter(
        has=page.get_by_role("button", name="Create Ticket", exact=True)
    )


def keyboard_intake(page, form, queue, capture):
    fields = [
        form.get_by_label(label, exact=True)
        for label in (
            "Queue",
            "Summary of the problem",
            "Description of your issue",
            "Priority",
            "Due on",
            "Attach File",
            "Your E-Mail Address",
        )
    ]
    fields[0].focus()
    expect(fields[0]).to_be_focused()
    page.keyboard.press("Home")
    page.keyboard.press("ArrowDown")
    expect(fields[0]).to_have_value(str(queue.pk))
    for index, field in enumerate(fields[1:], start=1):
        page.keyboard.press("Tab")
        expect(field).to_be_focused()
        if index in {1, 2, 6}:
            page.keyboard.insert_text(
                {1: INTAKE_TITLE, 2: INTAKE_BODY, 6: EMAIL}[index]
            )
    button = form.get_by_role("button", name="Create Ticket", exact=True)
    page.keyboard.press("Tab")
    expect(button).to_be_focused()
    capture(page, "UC01-M-keyboard-submit")
    page.keyboard.press("Enter")


@pytest.mark.parametrize("flow", FLOWS)
def test_uc01_intake(flow, browser, queue, record_property):
    open_page, url, capture = browser
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    selected = queue
    if flow == "A2":
        selected = Queue.objects.create(
            title="Hardware desk", slug="hardware", allow_public_submission=True
        )
    priority = 2 if flow == "A1" else 3
    with open_page() as page:
        form = ticket_form(page, url)
        if flow == "M":
            keyboard_intake(page, form, queue, capture)
        else:
            form.get_by_label("Queue", exact=True).select_option(str(selected.pk))
            form.get_by_label("Your E-Mail Address", exact=True).fill(EMAIL)
            form.get_by_label("Summary of the problem", exact=True).fill(INTAKE_TITLE)
            form.get_by_label("Priority", exact=True).select_option(str(priority))
            description = form.get_by_label("Description of your issue", exact=True)
            button = form.get_by_role("button", name="Create Ticket", exact=True)
            if flow == "E1":
                button.click()
                expect(description).to_be_focused()
                feedback = description.evaluate("field => field.validationMessage")
                assert re.search("fill|enter", feedback, re.IGNORECASE), feedback
                record_property("required_field_feedback", feedback)
                assert description.evaluate("field => field.validity.valueMissing")
                expect(
                    form.get_by_label("Summary of the problem", exact=True)
                ).to_have_value(INTAKE_TITLE)
                expect(page).to_have_url(url("submit"))
                assert database(Ticket.objects.count) == 0
                capture(page, "UC01-E1-correction-focus")
            description.fill(INTAKE_BODY)
            if flow == "E2":
                page.route(
                    url("submit"),
                    lambda route: (
                        route.abort("internetdisconnected")
                        if route.request.method == "POST"
                        else route.continue_()
                    ),
                )
                with page.expect_event(
                    "requestfailed", predicate=lambda request: request.method == "POST"
                ) as failure:
                    button.click(no_wait_after=True)
                assert failure.value.failure == "net::ERR_INTERNET_DISCONNECTED"
                expect(
                    page.get_by_role("heading", name=INTAKE_TITLE, exact=True)
                ).to_have_count(0)
                page.unroute(url("submit"))
                page.goto(url("submit"))
                expect(
                    page.get_by_role("button", name="Create Ticket", exact=True)
                ).to_be_visible()
            else:
                button.click()
        if flow != "E2":
            expect(
                page.get_by_role("heading", name=INTAKE_TITLE, exact=True)
            ).to_be_visible()
            expect(
                page.locator("h2 + p").get_by_text(INTAKE_BODY, exact=True)
            ).to_be_visible()
            shown_status(page, "Open")
            if flow == "E1":
                capture(page, "UC01-E1-corrected-success")
    if flow == "E2":
        assert not Ticket.objects.exists()
        assert not FollowUp.objects.exists()
    else:
        stored = Ticket.objects.get()
        assert (stored.queue_id, stored.priority) == (selected.pk, priority)
        assert (
            stored.title,
            stored.description,
            stored.submitter_email,
            stored.status,
        ) == (INTAKE_TITLE, INTAKE_BODY, EMAIL, Ticket.OPEN_STATUS)
        assert stored.followup_set.get().comment == INTAKE_BODY


@pytest.mark.parametrize("flow", FLOWS)
def test_uc02_assign(flow, browser, seeded, people, record_property):
    open_page, url, _ = browser
    staff, other = people["staff"], people["other"]
    ticket = seeded(assigned_to=staff if flow == "A2" else None)
    old = state(ticket)
    target = staff if flow == "A1" else other
    with open_page(people["customer"] if flow == "E1" else staff) as page:
        page.goto(url("view", ticket.pk))
        assignment = page.locator("form").filter(
            has=page.get_by_role("button", name="Save ticket assignment", exact=True)
        )
        if flow == "E1":
            expect(page).not_to_have_url(url("view", ticket.pk))
            expect(assignment).to_have_count(0)
            expect(
                page.get_by_role("heading", name=re.compile(ticket.title))
            ).to_have_count(0)
        else:
            owner = assignment.locator('select[name="owner"]')
            expect(owner).to_be_visible()
            if flow == "A1":
                assignment.locator('a[href="?take"]').click()
            else:
                owner.select_option(str(target.pk))
                if flow == "E2":
                    database(other.delete)
                button = assignment.get_by_role(
                    "button", name="Save ticket assignment", exact=True
                )
                with page.expect_response(
                    lambda res: (
                        res.request.method == "POST"
                        and res.url == url("update", ticket.pk)
                    )
                ) as result:
                    button.click()
                if flow == "E2":
                    assert database(state, ticket) == old
                    record_property("stale_assignee_http_status", result.value.status)
                    assert result.value.status < 500, (
                        "Stale assignee returns a server error"
                    )
                    expect(
                        page.get_by_text(
                            re.compile(
                                r"valid choice|assignee|owner.*available", re.IGNORECASE
                            )
                        )
                    ).to_be_visible()
            if flow not in {"E1", "E2"}:
                heading(page, ticket)
                expect(owner).to_have_value(str(target.pk))
    if flow in {"E1", "E2"}:
        assert state(ticket) == old
    else:
        ticket.refresh_from_db()
        assert (
            ticket.assigned_to_id == target.pk and ticket.status == Ticket.OPEN_STATUS
        )
        audit = changes(ticket).get(field="Owner")
        assert audit.new_value == target.username
        assert ticket.followup_set.count() == 2


@pytest.mark.parametrize("flow", FLOWS)
def test_uc03_followup(flow, browser, seeded, people):
    open_page, url, _ = browser
    ticket = seeded()
    prior = state(ticket)
    receipt = urljoin(url("public_view"), ticket.ticket_url)
    note = (
        "Replaced the printer roller" if flow != "A1" else "Internal diagnostic detail"
    )
    actor = (
        None
        if flow == "E1"
        else people["customer"]
        if flow == "A2"
        else people["staff"]
    )
    with open_page(actor) as page:
        if flow == "A2":
            page.goto(receipt)
            heading(page, ticket)
            page.get_by_label("Comment / Resolution", exact=True).fill(note)
            page.get_by_role("button", name="Update This Ticket", exact=True).click()
            visible_note(page, note)
        elif flow == "E2":
            response = page.goto(url("view", 99999))
            assert response.status == 404
            expect(
                page.get_by_role("heading", name=re.compile("Not Found", re.IGNORECASE))
            ).to_be_visible()
        else:
            page.goto(url("view", ticket.pk))
            if flow == "E1":
                expect(
                    page.get_by_role("heading", name="Log in to your account")
                ).to_be_visible()
                expect(page.locator("#ticket-respond-form")).to_have_count(0)
            else:
                submit_update(respond(page), note, public=flow != "A1")
                visible_note(page, note)
    if flow in {"E1", "E2"}:
        assert state(ticket) == prior
    else:
        saved = ticket.followup_set.get(comment=note)
        assert saved.public == (flow != "A1")
        assert saved.user_id == (people["staff"].pk if flow != "A2" else None)
        ticket.refresh_from_db()
        assert ticket.status == Ticket.OPEN_STATUS
        assert ticket.followup_set.count() == 2


@pytest.mark.parametrize("flow", FLOWS)
def test_uc04_resolve(flow, browser, seeded, people):
    open_page, url, capture = browser
    start = {"A1": Ticket.REOPENED_STATUS, "A2": Ticket.RESOLVED_STATUS}.get(
        flow, Ticket.OPEN_STATUS
    )
    ticket = seeded(
        status=start, resolution="Earlier resolution" if flow == "A2" else None
    )
    if flow == "E2":
        dependency = seeded()
        TicketDependency.objects.create(ticket=ticket, depends_on=dependency)
    old = state(ticket)
    receipt = urljoin(url("public_view"), ticket.ticket_url)
    actor = people["customer"] if flow == "E1" else people["staff"]
    note = "Verified repaired printer output"
    with open_page(actor) as page:
        if flow == "E1":
            page.goto(receipt)
            heading(page, ticket)
            expect(
                page.get_by_role("radio", name="Resolved", exact=True)
            ).to_have_count(0)
            shown_status(page, "Open")
        else:
            page.goto(url("view", ticket.pk))
            form = respond(page)
            if flow == "E2":
                expect(
                    form.get_by_role("radio", name="Resolved", exact=True)
                ).to_be_disabled()
                expect(
                    form.get_by_text(
                        "This ticket cannot be resolved or closed until the tickets it depends on are resolved."
                    )
                ).to_be_visible()
            else:
                submit_update(form, note, "Resolved")
                shown_status(page, "Resolved")
                expect(page.get_by_role("alert").filter(has_text=note)).to_be_visible()
                visible_note(page, note)
                if flow == "M":
                    capture(page, "UC04-M-resolved-confirmation")
    if flow in {"E1", "E2"}:
        assert state(ticket) == old
    else:
        ticket.refresh_from_db()
        assert ticket.status == Ticket.RESOLVED_STATUS and ticket.resolution == note
        assert ticket.followup_set.get(comment=note).user_id == actor.pk
        assert changes(ticket).filter(field="Status").count() == (
            0 if flow == "A2" else 1
        )
        assert ticket.followup_set.count() == 2


@pytest.mark.parametrize("flow", FLOWS)
def test_uc05_close_reopen(flow, browser, seeded, people):
    open_page, url, _ = browser
    start = Ticket.CLOSED_STATUS if flow in {"A2", "E1"} else Ticket.RESOLVED_STATUS
    ticket = seeded(status=start, resolution="Original resolution")
    before = state(ticket)
    with open_page(people["staff"]) as page:
        page.goto(url("view", ticket.pk))
        if flow == "M":
            page.get_by_role("link", name="Accept and Close").click()
            shown_status(page, "Closed")
        elif flow == "E1":
            form = respond(page)
            expect(
                form.get_by_role("radio", name="Resolved", exact=True)
            ).to_have_count(0)
            expect(
                form.get_by_role("radio", name="Reopened", exact=True)
            ).to_be_enabled()
            shown_status(page, "Closed")
        elif flow == "E2":
            stale = respond(page)
            expect(
                stale.get_by_role("radio", name="Resolved", exact=True)
            ).to_be_checked()
            second = page.context.new_page()
            second.goto(url("view", ticket.pk))
            second.get_by_role("link", name="Accept and Close").click()
            shown_status(second, "Closed")
            before = database(state, ticket)
            submit_update(stale, "Stale resolution edit")
            expect(
                page.get_by_text(
                    "This ticket status change is not allowed.", exact=True
                )
            ).to_be_visible()
        else:
            submit_update(respond(page), "Fault returned", "Reopened")
            shown_status(page, "Reopened")
            visible_note(page, "Fault returned")
    if flow in {"E1", "E2"}:
        assert state(ticket) == before
    else:
        ticket.refresh_from_db()
        expected = Ticket.CLOSED_STATUS if flow == "M" else Ticket.REOPENED_STATUS
        assert ticket.status == expected
        assert ticket.resolution == "Original resolution"
        assert ticket.followup_set.count() == 2
        assert changes(ticket).get(field="Status").new_value == (
            "Closed" if flow == "M" else "Reopened"
        )


@pytest.mark.parametrize("flow", FLOWS)
def test_uc06_review(flow, browser, seeded, people, settings, record_property):
    open_page, url, _ = browser
    start, label = {
        "A1": (Ticket.RESOLVED_STATUS, "Resolved"),
        "A2": (Ticket.CLOSED_STATUS, "Closed"),
    }.get(flow, (Ticket.OPEN_STATUS, "Open"))
    resolution = "Roller and drum replaced" if flow in {"A1", "A2"} else None
    ticket = seeded(status=start, resolution=resolution)
    ticket.followup_set.create(
        title="Internal",
        date=timezone.now(),
        public=False,
        comment="Staff only maintenance note",
    )
    before = state(ticket)
    actor = people["outsider"] if flow == "E2" else people["customer"]
    receipt = urljoin(url("public_view"), ticket.ticket_url)
    if flow != "M":
        settings.HELPDESK_VIEW_A_TICKET_PUBLIC = True
    with open_page(actor) as page:
        if flow == "M":
            page.goto(receipt)
        else:
            lookup(page, url, 99999 if flow == "E1" else ticket.pk, actor.email)
        if flow in {"E1", "E2"}:
            assert database(state, ticket) == before
            expect(
                page.get_by_role("heading", name="Search your ticket")
            ).to_be_visible()
            expect(
                page.get_by_role("heading", name=re.compile(ticket.title))
            ).to_have_count(0)
            record_property("lookup_denied_with_database_unchanged", True)
            expect(
                page.get_by_text(
                    "Invalid ticket ID or e-mail address. Please try again.",
                    exact=False,
                )
            ).to_be_visible()
        else:
            heading(page, ticket)
            shown_status(page, label)
            assert urlsplit(page.url).path == urlsplit(url("public_view")).path
            query_ticket = parse_qs(urlsplit(page.url).query)["ticket"][0]
            assert Ticket.queue_and_id_from_query(query_ticket)[1] == str(ticket.pk)
            visible_note(page, "Customer reported stripe")
            expect(
                page.get_by_text("Staff only maintenance note", exact=True)
            ).to_have_count(0)
            if resolution:
                expect(
                    page.get_by_role("alert").filter(has_text=resolution)
                ).to_be_visible()
    assert state(ticket) == before
