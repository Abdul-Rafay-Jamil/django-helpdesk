"""Run the four EC3 workloads via the Django live-server and team fixtures."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core import serializers
from django.urls import reverse
from django.utils import timezone

from helpdesk.models import Ticket


def test_ticket_detail_workloads(live_server, client, staff, queue, make_ticket, settings):
    binary = Path(os.environ["SQE_K6_EXE"]).resolve(strict=True)
    output = Path(os.environ["SQE_K6_OUTPUT"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings.DEBUG = False
    ticket = make_ticket(assigned_to=staff, description="Synthetic printer report")
    followup = ticket.followup_set.create(
        date=timezone.now(), title="Ticket opened", comment="Printer report", public=True
    )
    session_name = settings.SESSION_COOKIE_NAME
    client.force_login(staff)
    path = reverse("helpdesk:ticket-detail", args=[ticket.pk])
    readiness = client.get(path)
    assert readiness.status_code == 200
    assert readiness.json()["id"] == ticket.pk
    assert readiness.json()["status"] == Ticket.OPEN_STATUS

    def state():
        for record in (queue, ticket, followup):
            record.refresh_from_db()
        snapshot = serializers.serialize("json", [queue, ticket, followup])
        return {
            "seed_sha256": hashlib.sha256(snapshot.encode()).hexdigest(),
            "tickets": queue.ticket_set.count(),
            "followups": ticket.followup_set.count(),
        }

    seed = state()
    records = []
    script = Path(__file__).with_name("ticket_detail.js").resolve()
    env = {
        **os.environ,
        "SQE_K6_TARGET": live_server.url + path,
        "SQE_K6_TICKET_ID": str(ticket.pk),
        "SQE_K6_TICKET_TITLE": ticket.title,
        "SQE_K6_TICKET_STATUS": str(Ticket.OPEN_STATUS),
        "SQE_K6_SESSION_NAME": session_name,
        "SQE_K6_SESSION": client.cookies[session_name].value,
    }
    for vus in (1, 5):
        for repeat in (1, 2):
            name = f"{vus}vu-run{repeat}"
            before = state()
            assert before == seed
            with (output / f"{name}.txt").open("wb") as log:
                result = subprocess.run(
                    [str(binary), "run", "--quiet", "--no-usage-report", "--out",
                     f"json={output / (name + '.points.json.gz')}", str(script)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env={
                        **env,
                        "SQE_K6_VUS": str(vus),
                        "SQE_K6_SUMMARY": str(output / f"{name}.summary.json"),
                    },
                    timeout=115,
                )
            after = state()
            records.append(
                dict(run=name, vus=vus, exit_code=result.returncode, before=before, after=after)
            )
            (output / "run-manifest.json").write_text(
                json.dumps(records, indent=2) + "\n", encoding="utf-8"
            )
            assert after == seed
    assert [record["exit_code"] for record in records] == [0, 0, 0, 0]
