from itertools import product
from types import SimpleNamespace

import pytest

from helpdesk.decorators import check_staff_status, is_helpdesk_staff

STAFF_CASES = [
    ("000", False),
    ("001", False),
    ("010", False),
    ("011", False),
    ("100", False),
    ("101", False),
    ("110", False),
    ("111", True),
]


@pytest.mark.parametrize(
    "bits,allowed",
    [pytest.param(bits, allowed, id=bits) for bits, allowed in STAFF_CASES],
)
def test_default_staff_predicate_mcc(bits, allowed):
    authenticated, active, staff = (bit == "1" for bit in bits)
    user = SimpleNamespace(
        is_authenticated=authenticated, is_active=active, is_staff=staff
    )
    assert is_helpdesk_staff(user) is allowed


@pytest.mark.parametrize(
    "authenticated,active,superuser", list(product((False, True), repeat=3))
)
def test_alternate_staff_predicate_modes(authenticated, active, superuser):
    user = SimpleNamespace(
        is_authenticated=authenticated, is_active=active, is_superuser=superuser
    )
    assert check_staff_status(False)(False)(user) is (authenticated and active)
    assert check_staff_status(False)(True)(user) is (
        authenticated and active and superuser
    )
