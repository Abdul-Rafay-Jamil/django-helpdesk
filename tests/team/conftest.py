from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from helpdesk.models import Queue, Ticket


@pytest.fixture(autouse=True)
def test_environment(settings):
    settings.LANGUAGE_CODE = "en-us"


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("sqm-staff", is_staff=True)


@pytest.fixture
def queue(db):
    return Queue.objects.create(title="SQE Support", slug="sqe-support")


@pytest.fixture
def make_ticket(queue):
    def create(**fields):
        return Ticket.objects.create(
            queue=queue,
            title="Printer cannot connect",
            submitter_email="customer@example.test",
            created=timezone.now(),
            **fields,
        )

    return create


@pytest.fixture
def ticket_payload(queue, staff):
    return {
        "data": {
            "queue": queue.pk,
            "title": "Printer cannot connect",
            "description": "The test printer is offline.",
            "submitter_email": "customer@example.test",
        },
        "context": {"request": SimpleNamespace(user=staff)},
    }
