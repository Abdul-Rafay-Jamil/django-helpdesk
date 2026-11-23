import pytest
from django.urls import reverse

from helpdesk.forms import PublicTicketForm
from helpdesk.models import CustomField, FollowUp, Ticket
from helpdesk.serializers import TicketSerializer
from helpdesk.views.staff import UpdateTicketView

FIELD_CASES = [
    pytest.param(
        "decimal",
        {"max_length": 5, "decimal_places": 2},
        "991.25",
        "99.123",
        "991.25",
        id="decimal-scale",
    ),
    pytest.param(
        "decimal",
        {"max_length": 5, "decimal_places": 2},
        "999.99",
        "1000.00",
        "999.99",
        id="decimal-precision",
    ),
    pytest.param(
        "list",
        {"list_values": "Paper\nToner", "empty_selection_list": True},
        "Toner",
        "Staples",
        "Toner",
        id="list",
    ),
    pytest.param("integer", {}, "12", "1.5", "12", id="integer"),
    pytest.param("date", {}, "2028-02-29", "2027-02-29", "2028-02-29", id="date"),
    pytest.param(
        "datetime",
        {},
        "2027-02-03T10:40",
        "2027-02-30T10:40",
        "2027-02-03T10:40",
        id="datetime",
    ),
    pytest.param("time", {}, "09:15:00", "25:15:00", "09:15:00", id="time"),
    pytest.param(
        "ipaddress", {}, "2001:db8::1", "300.0.0.1", "2001:db8::1", id="ipaddress"
    ),
    pytest.param(
        "slug", {}, "printer-room-2", "printer room", "printer-room-2", id="slug"
    ),
    pytest.param("text", {"max_length": 4}, "note", "notes", "note", id="text"),
    pytest.param(
        "url",
        {},
        "https://example.test/printer",
        "no url",
        "https://example.test/printer",
        id="url",
    ),
    pytest.param(
        "email", {}, "owner@example.test", "owner@", "owner@example.test", id="email"
    ),
]


@pytest.mark.parametrize("kind,options,valid,invalid,stored", FIELD_CASES)
def test_custom_field_creation_and_api_rejection_preserve_persisted_value(
    queue, kind, options, valid, invalid, stored
):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    field = CustomField.objects.create(
        name="metadata",
        label="Metadata",
        data_type=kind,
        required=True,
        **options,
    )
    payload = {
        "queue": queue.pk,
        "priority": 3,
        "title": "Printer",
        "body": "Information for support",
        "submitter_email": "reporter@example.test",
    }
    bad_form = PublicTicketForm(data={**payload, "custom_metadata": invalid})
    assert not bad_form.is_valid()
    assert "custom_metadata" in bad_form.errors
    assert Ticket.objects.count() == FollowUp.objects.count() == 0
    good_form = PublicTicketForm(data={**payload, "custom_metadata": valid})
    assert good_form.is_valid(), good_form.errors
    ticket = Ticket.objects.get(pk=good_form.save(user=None).pk)
    assert ticket.status == Ticket.OPEN_STATUS
    metadata = ticket.ticketcustomfieldvalue_set.get(field=field)
    assert metadata.value == stored
    history = ticket.followup_set.get()
    modified = ticket.modified

    rejected = TicketSerializer(ticket, data={"custom_metadata": invalid}, partial=True)
    assert not rejected.is_valid()
    assert set(rejected.errors) == {"custom_metadata"}
    ticket.refresh_from_db()
    assert ticket.modified == modified and ticket.followup_set.get() == history
    assert ticket.ticketcustomfieldvalue_set.get(field=field).value == stored


def test_api_custom_boolean_and_decimal_update_reuses_existing_records(make_ticket):
    ticket = make_ticket()
    boolean = CustomField.objects.create(
        name="verified", label="Verified", data_type="boolean"
    )
    decimal = CustomField.objects.create(
        name="estimate",
        label="Estimate",
        data_type="decimal",
        max_length=5,
        decimal_places=2,
    )
    for update in (
        {"custom_verified": True, "custom_estimate": "123.45"},
        {"custom_verified": False, "custom_estimate": "123.46"},
    ):
        serializer = TicketSerializer(ticket, data=update, partial=True)
        assert serializer.is_valid(), serializer.errors
        serializer.save()
    ticket.refresh_from_db()
    assert ticket.ticketcustomfieldvalue_set.get(field=boolean).value == "False"
    assert ticket.ticketcustomfieldvalue_set.get(field=decimal).value == "123.46"
    assert ticket.ticketcustomfieldvalue_set.count() == 2
    assert not ticket.followup_set.exists()


def test_staff_update_rejects_invalid_due_date_without_partial_write(
    rf, make_ticket, staff
):
    ticket = make_ticket()
    before = ticket.title, ticket.due_date, ticket.modified
    request = rf.post(
        reverse("helpdesk:update", args=[ticket.pk]),
        {
            "queue": ticket.queue_id,
            "title": "Would be changed",
            "priority": ticket.priority,
            "submitter_email": ticket.submitter_email,
            "due_date": "2027-02-30",
        },
    )
    request.user = staff
    response = UpdateTicketView.as_view()(request, ticket_id=ticket.pk)
    assert "due_date" in response.context_data["form"].errors
    ticket.refresh_from_db()
    assert (ticket.title, ticket.due_date, ticket.modified) == before
    assert not ticket.followup_set.exists()


@pytest.mark.parametrize(
    "blank_choice", [False, True], ids=["no-placeholder", "placeholder"]
)
def test_required_list_rejects_blank_with_or_without_placeholder(queue, blank_choice):
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    CustomField.objects.create(
        name="supply",
        label="Supply",
        data_type="list",
        required=True,
        list_values="Paper\nToner",
        empty_selection_list=blank_choice,
    )
    form = PublicTicketForm(
        data={
            "queue": queue.pk,
            "priority": 3,
            "title": "Printer",
            "body": "Running low",
            "submitter_email": "reporter@example.test",
            "custom_supply": "",
        }
    )
    assert [key for key, _ in form.fields["custom_supply"].choices] == (
        ["", "Paper", "Toner"] if blank_choice else ["Paper", "Toner"]
    )
    assert not form.is_valid()
    assert form.errors.as_data()["custom_supply"][0].code == "required"
    assert not Ticket.objects.exists() and not FollowUp.objects.exists()
