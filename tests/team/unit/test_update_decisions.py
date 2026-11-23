from itertools import product
from types import SimpleNamespace
from unittest.mock import Mock

from django.http import QueryDict
from django.utils.datastructures import MultiValueDict

from helpdesk import settings as helpdesk_settings
from helpdesk.models import Ticket
from helpdesk.update_ticket import process_email_notifications_for_ticket_update
from helpdesk.views import staff as staff_views


def test_staff_no_change_decision_complete_table(monkeypatch):
    # True means the corresponding input would allow the update to be skipped.
    dispatch = Mock()
    monkeypatch.setattr(staff_views, "update_ticket", dispatch)
    for inputs in product((False, True), repeat=10):
        files, body, checklist, state, title, priority, queue, due, owner, custom = (
            inputs
        )
        fields = QueryDict(mutable=True)
        fields.update(
            {
                "comment": "" if body else "Follow-up",
                "owner": -1 if owner else 0,
                "new_status": Ticket.OPEN_STATUS if state else Ticket.RESOLVED_STATUS,
            }
        )
        if not checklist:
            fields.setlist("checklist-1", ["11"])
        tasks = Mock()
        tasks.completed.return_value.values_list.return_value = []
        ticket = SimpleNamespace(
            status=Ticket.OPEN_STATUS,
            title="Printer",
            priority=3,
            due_date=None,
            queue=SimpleNamespace(id=1),
            assigned_to=SimpleNamespace(id=8),
            checklists=SimpleNamespace(
                all=lambda tasks=tasks: [SimpleNamespace(id=1, tasks=tasks)]
            ),
        )
        form = SimpleNamespace(
            data=fields,
            files=MultiValueDict({} if files else {"attachment": [object()]}),
            cleaned_data={
                "title": "Printer" if title else "Printer jam",
                "priority": 3 if priority else 2,
                "queue": 1 if queue else 2,
            },
        )
        monkeypatch.setattr(
            staff_views,
            "get_due_date_from_form_or_ticket",
            lambda _form, _ticket, due=due: None if due else object(),
        )
        monkeypatch.setattr(
            staff_views,
            "EditTicketCustomFieldForm",
            lambda *_args, custom=custom, **_kwargs: SimpleNamespace(
                has_changed=lambda: not custom
            ),
        )
        dispatch.reset_mock()
        assert staff_views.save_ticket_update(form, ticket, object()) is ticket
        assert dispatch.call_count == (0 if all(inputs) else 1), inputs


def test_submitter_notification_complete_table(monkeypatch):
    monkeypatch.setattr(
        helpdesk_settings, "HELPDESK_PRIVATE_FOLLOWUP_MEANS_NO_EMAILS", False
    )
    # Rows are remark/terminal 00, 01, 10, 11. Key is (notify-all, public).
    expected = {
        (False, False): "0000",
        (False, True): "0111",
        (True, False): "1111",
        (True, True): "1111",
    }
    for (notify_all, public), outcomes in expected.items():
        monkeypatch.setattr(
            helpdesk_settings,
            "HELPDESK_NOTIFY_SUBMITTER_FOR_ALL_TICKET_CHANGES",
            notify_all,
        )
        for (remark, terminal), outcome in zip(
            product((False, True), repeat=2), outcomes, strict=True
        ):
            send = Mock(return_value=set())
            ticket = SimpleNamespace(assigned_to=None, send=send)
            follow_up = SimpleNamespace(
                comment="Resolution note" if remark else "",
                new_status=Ticket.RESOLVED_STATUS if terminal else Ticket.OPEN_STATUS,
            )
            process_email_notifications_for_ticket_update(
                public,
                ticket,
                follow_up,
                {},
                set(),
                [],
            )
            recipients = [next(iter(call.args[0])) for call in send.call_args_list]
            assert recipients == (
                ["submitter", "ticket_cc"] if outcome == "1" else ["ticket_cc"]
            )
