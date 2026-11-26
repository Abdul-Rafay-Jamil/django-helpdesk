"""Summarize a fixed workflow inventory from coverage.py's function report."""

import json
import sys
from pathlib import Path

INVENTORY = {
    "decorators.py": (
        "check_staff_status",
        "protect_view",
        "helpdesk_staff_member_required",
        "apply_staff_view_protector",
    ),
    "forms.py": (
        "AbstractTicketForm",
        "TicketForm",
        "PublicTicketForm",
        "EditTicketForm",
        "EditTicketCustomFieldForm",
        "CustomFieldMixin",
    ),
    "lib.py": ("get_assignable_users",),
    "models.py": (
        "Ticket",
        "FollowUp",
        "TicketDependency",
        "TicketChange",
        "TicketCC",
        "TicketCustomFieldValue",
        "CustomField",
        "Checklist",
        "ChecklistTask",
    ),
    "serializers.py": (
        "BaseTicketSerializer",
        "TicketSerializer",
        "PublicTicketListingSerializer",
        "FollowUpSerializer",
    ),
    "update_ticket.py": ("",),
    "user.py": (
        "HelpdeskUser.get_queues",
        "HelpdeskUser.accessible_queues",
        "HelpdeskUser.accessible_tickets_q",
        "HelpdeskUser.accessible_tickets",
        "HelpdeskUser.has_full_access",
        "HelpdeskUser.can_access_queue",
        "HelpdeskUser.can_access_ticket",
    ),
    "validators.py": ("validate_file_extension",),
    "views/abstract_views.py": ("AbstractCreateTicketMixin",),
    "views/api.py": (
        "restrict_relation",
        "TicketViewSet",
        "UserTicketViewSet",
        "FollowUpViewSet",
    ),
    "views/permissions.py": ("MustBeStaffMixin",),
    "views/public.py": (
        "create_ticket",
        "BaseCreateTicketView",
        "CreateTicketView",
        "SearchForTicketView",
        "ViewTicket",
        "get_ticket_for_submitter",
        "update_ticket_from_public_view",
    ),
    "views/staff.py": (
        "view_ticket",
        "ticket_perm_check",
        "get_user_queues",
        "get_followups_for_ticket",
        "get_attachments_for_ticket",
        "get_ticket_from_request_with_authorisation",
        "get_due_date_from_form_or_ticket",
        "get_time_spent_from_form",
        "update_ticket_view",
        "save_ticket_update",
        "return_to_ticket",
        "CreateTicketView",
        "UpdateTicketView",
    ),
}
COUNTS = ("covered_lines", "num_statements", "covered_branches", "num_branches")


def main():
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    totals = dict.fromkeys(COUNTS, 0)
    selected = {}
    matched = {file: set() for file in INVENTORY}
    for path, data in report["files"].items():
        name = path.replace("\\", "/").removeprefix("src/helpdesk/")
        selectors = INVENTORY.get(name, ())
        for function, item in data.get("functions", {}).items():
            matches = {
                selector
                for selector in selectors
                if function
                and (
                    not selector
                    or function == selector
                    or function.startswith(selector + ".")
                )
            }
            if matches:
                matched[name].update(matches)
                summary = item["summary"]
                selected[f"{name}:{function}"] = {
                    "counts": {key: summary[key] for key in COUNTS},
                    "missing_lines": item["missing_lines"],
                    "missing_branches": item["missing_branches"],
                }
                for key in COUNTS:
                    totals[key] += summary[key]
    for name, selectors in INVENTORY.items():
        missing = set(selectors) - matched[name]
        if missing:
            raise ValueError(f"Unknown selectors in {name}: {sorted(missing)}")
    print(json.dumps({"totals": totals, "functions": selected}, indent=2))


if __name__ == "__main__":
    main()
