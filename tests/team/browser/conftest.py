"""Chromium and Django live-server fixtures for the team scenarios."""

import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright


@pytest.fixture(autouse=True)
def browser_settings(settings, monkeypatch):
    settings.DEBUG = False
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    monkeypatch.setenv("HELPDESK_NEW_TICKET_WEBHOOK_URLS", "")
    monkeypatch.setenv("HELPDESK_FOLLOWUP_WEBHOOK_URLS", "")


@pytest.fixture
def people(staff, transactional_db):
    staff.email = "staff@example.test"
    staff.save(update_fields=["email"])
    model = get_user_model()
    return {
        "staff": staff,
        "other": model.objects.create_user(
            "browser-assignee", email="staff2@example.test", is_staff=True
        ),
        "customer": model.objects.create_user(
            "browser-customer", email="customer@example.test"
        ),
        "outsider": model.objects.create_user(
            "browser-outsider", email="outsider@example.test"
        ),
    }


@pytest.fixture
def seeded(make_ticket):
    def create(**fields):
        ticket = make_ticket(description="Printer output has a dark stripe.", **fields)
        ticket.followup_set.create(
            date=timezone.now(),
            title="Ticket opened",
            comment="Customer reported stripe",
            public=True,
        )
        return ticket

    return create


@pytest.fixture
def browser(client, live_server, request, record_property):
    evidence = os.environ.get("SQE_BROWSER_EVIDENCE")
    artifacts = Path(evidence) if evidence else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)
    case_id = request.node.name.replace("[", "-").removesuffix("]")

    def url(name, *args):
        return live_server.url + reverse(f"helpdesk:{name}", args=args)

    @contextmanager
    def open_page(user=None):
        cookies = []
        if user is not None:
            client.force_login(user)
            name = django_settings.SESSION_COOKIE_NAME
            cookies.append(
                {
                    "name": name,
                    "value": client.cookies[name].value,
                    "url": live_server.url,
                }
            )
        with sync_playwright() as playwright:
            proxy = os.environ.get("SQE_BROWSER_LOCAL_PROXY")
            launch_options = (
                {"proxy": {"server": proxy, "bypass": "<-loopback>"}} if proxy else {}
            )
            chromium = playwright.chromium.launch(**launch_options)
            record_property("chromium", chromium.version)
            context = chromium.new_context(locale="en-US")
            context.add_cookies(cookies)
            if proxy:
                local = urlsplit(live_server.url).netloc

                def local_only(route):
                    if urlsplit(route.request.url).netloc == local:
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", local_only)
            if artifacts:
                context.tracing.start(screenshots=True, snapshots=True)
            page = context.new_page()
            page.set_default_timeout(4500)
            try:
                yield page
            except Exception:
                if artifacts:
                    context.tracing.stop(path=artifacts / f"{case_id}-trace.zip")
                raise
            else:
                if artifacts:
                    context.tracing.stop()
            finally:
                context.close()
                chromium.close()

    def capture(page, label):
        if artifacts:
            page.screenshot(path=artifacts / f"{label}.png", full_page=True)

    return open_page, url, capture
