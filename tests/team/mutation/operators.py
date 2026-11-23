"""Cosmic Ray operators for Ticket Management state rules."""

from cosmic_ray.operators.operator import Example, Operator
from parso import parse


class ReplaceBusinessExpression(Operator):
    """Replace one exact expression in the named production function."""

    function = ""
    original = ""
    replacement = ""

    def mutation_positions(self, node):
        if node.get_code().strip() != self.original:
            return
        parent = node.parent
        while parent is not None and parent.type != "funcdef":
            parent = parent.parent
        if parent is not None and parent.name.value == self.function:
            yield node.start_pos, node.end_pos

    def mutate(self, node, index):
        replacement = parse(self.replacement).children[0]
        replacement.get_first_leaf().prefix = node.get_first_leaf().prefix
        return replacement

    @classmethod
    def examples(cls):
        before = f"def {cls.function}():\n    call({cls.original})\n"
        after = f"def {cls.function}():\n    call({cls.replacement})\n"
        return (Example(before, after),)


class IgnoreConfiguredTransitionFlow(ReplaceBusinessExpression):
    """Make the changed-state denial check never fire (BR04/FR08/FR10)."""

    function = "validate_status_transition"
    original = "new_status not in dict(ticket.get_allowed_status_flow)"
    replacement = "False"


class CloseAsResolved(ReplaceBusinessExpression):
    """Keep a staff Close request Resolved instead of closing it (FR08)."""

    function = "view_ticket"
    original = "Ticket.CLOSED_STATUS"
    replacement = "Ticket.RESOLVED_STATUS"


OPERATORS = {
    cls.__name__: cls for cls in (IgnoreConfiguredTransitionFlow, CloseAsResolved)
}
