"""Explicit local audit entry point. Reuse the browser suite's Django fixtures."""

import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urljoin

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import sync_playwright

from helpdesk.models import Ticket

pytestmark = pytest.mark.skipif(
    not os.environ.get("SQE_ACCESSIBILITY_OUTPUT"),
    reason="Set SQE_ACCESSIBILITY_OUTPUT to opt in to the external audit tools",
)


def test_ticket_screen_audits(browser, live_server, client, people, queue, seeded):
    _, url, _ = browser
    queue.allow_public_submission = True
    queue.save(update_fields=["allow_public_submission"])
    opened = seeded()
    resolved = seeded(status=Ticket.RESOLVED_STATUS, resolution="Roller replaced.")
    staff, customer = people["staff"], people["customer"]
    screens = [
        dict(name="intake", url=url("submit"), button="Create Ticket"),
        dict(
            name="staff-open",
            url=url("view", opened.pk),
            button="Save ticket assignment",
            staff=True,
        ),
        dict(
            name="staff-open-respond",
            url=url("view", opened.pk),
            button="Save ticket assignment",
            staff=True,
            respond=True,
        ),
        dict(
            name="staff-resolved",
            url=url("view", resolved.pk),
            button="Save ticket assignment",
            staff=True,
        ),
        dict(
            name="customer-receipt",
            url=urljoin(url("public_view"), opened.ticket_url),
            selector="#followup-list",
            user=customer,
        ),
    ]
    selected = os.environ.get("SQE_ACCESSIBILITY_SCREEN")
    if selected:
        requested = set(selected.split(","))
        screens = [screen for screen in screens if screen["name"] in requested]
        assert {screen["name"] for screen in screens} == requested
    client.force_login(staff)
    name = settings.SESSION_COOKIE_NAME
    staff_cookie = dict(name=name, value=client.cookies[name].value, url=live_server.url)
    customer_client = Client()
    customer_client.force_login(customer)
    customer_cookie = dict(
        name=name, value=customer_client.cookies[name].value, url=live_server.url
    )
    for screen in screens:
        if screen.pop("staff", False):
            screen["cookie"] = staff_cookie
        elif screen.pop("user", None):
            screen["cookie"] = customer_cookie
    destination = Path(os.environ["SQE_ACCESSIBILITY_OUTPUT"]).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
    subprocess.run(
        ["node", str(Path(__file__).with_name("audit") / "run.mjs")],
        input=json.dumps(
            dict(
                executablePath=executable,
                screens=screens,
                output=str(destination),
            )
        ),
        text=True,
        check=True,
        timeout=420,
    )
