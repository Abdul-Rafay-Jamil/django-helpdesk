from datetime import timedelta

import pytest
from django.utils import timezone

from helpdesk.forms import EditTicketCustomFieldForm, PublicTicketForm
from helpdesk.models import CustomField, FollowUp, Queue, Ticket
from helpdesk.update_ticket import update_ticket


@pytest.mark.parametrize("priority", [1, 5, 6])
def test_public_creation_with_configured_owner(queue, staff, priority):
    queue.allow_public_submission = True
    queue.default_owner = staff
    queue.save()
    form = PublicTicketForm(
        data={
            "queue": queue.pk,
            "title": "Tray jam",
            "body": "Paper stuck in tray",
            "priority": priority,
            "submitter_email": "reporter@example.test",
        }
    )
    if priority == 6:
        assert not form.is_valid()
        assert "priority" in form.errors
        assert not Ticket.objects.exists()
        assert not FollowUp.objects.exists()
    else:
        assert form.is_valid(), form.errors
        ticket = Ticket.objects.get(pk=form.save(user=None).pk)
        assert (ticket.priority, ticket.status, ticket.assigned_to_id) == (
            priority,
            Ticket.OPEN_STATUS,
            staff.pk,
        )
        assert ticket.followup_set.get().comment == "Paper stuck in tray"


def test_update_records_metadata_custom_field_and_checklist_changes(make_ticket, staff):
    ticket = make_ticket()
    other = make_ticket()
    destination = Queue.objects.create(title="Hardware", slug="hardware")
    custom = CustomField.objects.create(
        name="asset", label="Asset", data_type="varchar"
    )
    form = EditTicketCustomFieldForm({"custom_asset": "Printer A"}, instance=ticket)
    assert form.is_valid(), form.errors
    checklist = ticket.checklists.create(name="Triage")
    now = timezone.now()
    tasks = [
        checklist.tasks.create(
            description=f"Step {i}", position=i, completion_date=now if i % 2 else None
        )
        for i in range(4)
    ]
    untouched = ticket.checklists.create(name="Later").tasks.create(
        description="Inventory",
        position=0,
        completion_date=now,
    )
    due = now + timedelta(days=2)
    follow_up = update_ticket(
        staff,
        ticket,
        title="Printer tray jam",
        priority=1,
        queue=destination.pk,
        due_date=due,
        customfields_form=form,
        new_checklists={checklist.pk: {tasks[0].pk, tasks[3].pk}},
    )
    ticket.refresh_from_db()
    assert (ticket.title, ticket.priority, ticket.queue_id, ticket.due_date) == (
        "Printer tray jam",
        1,
        destination.pk,
        due,
    )
    assert ticket.ticketcustomfieldvalue_set.get(field=custom).value == "Printer A"
    for task in [*tasks, untouched]:
        task.refresh_from_db()
    assert tasks[0].completion_date is not None
    assert tasks[1].completion_date is None
    assert tasks[2].completion_date is None
    assert tasks[3].completion_date == untouched.completion_date == now
    assert set(follow_up.ticketchange_set.values_list("field", flat=True)) == {
        "Title",
        "Priority",
        "Queue",
        "Due on",
        "asset",
        "[Triage] Step 0",
        "[Triage] Step 1",
    }
    other.refresh_from_db()
    assert other.queue_id != destination.pk
    assert other.priority == 3
    assert not other.followup_set.exists()
