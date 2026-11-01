"""Dashboard: the design's overview (greeting, KPI tiles, Needs attention, Request
pipeline, Deployment activity, Recent deployments, Release alignment, Setup health),
with the Current Deployment Status table kept below it.

Numbers are asserted by their data- hooks rather than by layout, so restyling a card
can't quietly change what it counts.
"""

import json
import re
from datetime import datetime, timedelta, timezone

from app.models.checklist import ChecklistItem, ChecklistItemType
from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentEnvironment, DeploymentRequest, RequestStatus, RequestType
from app.models.seeder_command import SeederCommand
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user

NOW = datetime.now(timezone.utc)
LIVE, TEST = DeploymentEnvironment.live, DeploymentEnvironment.test


def _request(session, task, status, *, client_id=1, env=LIVE, hours_ago=1, rtype=RequestType.standard,
             deployed_hours_ago=None, deployer=2):
    r = DeploymentRequest(
        task_id=task, module_name="Interface", client_id=client_id, environment=env, version="V12",
        request_type=rtype, requested_by=1, status=status, created_at=NOW - timedelta(hours=hours_ago),
    )
    session.add(r)
    session.flush()
    if deployed_hours_ago is not None:
        done = NOW - timedelta(hours=deployed_hours_ago)
        session.add(DeploymentExecution(
            request_id=r.id, executed_by=deployer, claimed_at=done, started_at=done, completed_at=done,
            status=ExecutionStatus.completed,
        ))
    return r


def _seed(session):
    session.add_all([
        Client(id=1, name="CRM", is_active=True),
        Client(id=2, name="PlanVisu", is_active=True),
        Client(id=3, name="Old Client", is_active=False),
    ])
    make_user(session, id=1, name="Rajib Ahamad", username="rajib", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=2, name="Zunayed Rabbi", role=UserRole.devops, username="zunayed", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=3, name="Root Admin", role=UserRole.admin, username="root", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=4, name="Synced Only")   # no login access
    session.flush()
    # CRM has both URLs; PlanVisu only Test — one active client missing a Live URL.
    session.add_all([
        ClientSystemUrl(client_id=1, environment=TEST, url="http://crm.test"),
        ClientSystemUrl(client_id=1, environment=LIVE, url="http://crm.live"),
        ClientSystemUrl(client_id=2, environment=TEST, url="http://plan.test"),
    ])
    _request(session, "PR-PEND-OLD", RequestStatus.pending_approval, hours_ago=50)
    _request(session, "PR-PEND-NEW", RequestStatus.approved, hours_ago=2, env=TEST, rtype=RequestType.db_dump_restore)
    _request(session, "PR-PROG", RequestStatus.in_progress, hours_ago=30)
    _request(session, "PR-RET", RequestStatus.returned, hours_ago=5, client_id=2)
    _request(session, "PR-DONE-1", RequestStatus.completed, hours_ago=80, deployed_hours_ago=72, client_id=2)
    _request(session, "PR-DONE-2", RequestStatus.completed, hours_ago=10, deployed_hours_ago=3, env=TEST)
    _request(session, "PR-DONE-OLD", RequestStatus.completed, hours_ago=24 * 40, deployed_hours_ago=24 * 39)
    _request(session, "PR-REJ", RequestStatus.rejected, hours_ago=6)
    session.add_all([
        ClientVersionStatus(client_id=1, test_current_version="2026.1", live_current_version="2026.1"),
        ClientVersionStatus(client_id=2, test_current_version="2026.2", live_current_version="2026.1"),
    ])
    item = ChecklistItem(label="Backup", due="before_start", is_active=True, created_at=NOW)
    item.types = [ChecklistItemType(request_type=RequestType.standard, position=1)]
    session.add(item)
    session.add(SeederCommand(client_id=1, title="Perms", command="php artisan seed", created_by=3, updated_by=3))
    session.commit()


def _page(client):
    return client.get("/dashboard").text


def _kpi(text, key):
    match = re.search(rf'data-kpi="{key}"[^>]*>.*?<div class="kpi-value">\s*(\d+)', text, re.S)
    assert match, f"kpi {key} missing"
    return int(match.group(1))


def _card(text, key):
    match = re.search(rf'<section class="dcard[^"]*" data-card="{key}">.*?</section>', text, re.S)
    assert match, f"card {key} missing"
    return match.group(0)


def test_dashboard_keeps_the_current_deployment_status_table(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client)

    assert "Current Deployment Status" in text
    assert 'class="status-table deployment-table"' in text


def test_greeting_names_the_user_and_counts_open_requests(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client)

    assert 'data-greet-name="Rajib"' in text
    # Open = pending (2) + in progress (1); returned waits on the requester.
    assert "<b>3</b> open requests need action" in text
    assert "2 waiting for approval or deployment" in text


def test_kpi_tiles_count_by_status_group(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client)

    assert _kpi(text, "pending") == 2
    assert _kpi(text, "progress") == 1
    assert _kpi(text, "deployed") == 3
    assert _kpi(text, "returned") == 1
    assert _kpi(text, "clients") == 2
    assert 'data-kpi-of="3"' in text   # active of all clients
    assert "2 in the last 7 days" in text
    assert "1 missing Test/Live URL" in text


def test_needs_attention_lists_open_and_returned_oldest_first_by_stage(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    card = _card(_page(client), "attention")
    order = re.findall(r'data-task="([^"]+)"', card)

    assert order == ["PR-PEND-OLD", "PR-PEND-NEW", "PR-PROG", "PR-RET"]
    assert 'href="/requests?q=PR-PROG"' in card
    assert "4 open items · oldest first" in card


def test_pipeline_counts_every_group_type_and_system(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    card = _card(_page(client), "pipeline")

    assert "8 requests in total" in card
    for group, n in (("pending", 2), ("progress", 1), ("deployed", 3), ("returned", 1), ("stopped", 1)):
        assert f'data-group="{group}" data-n="{n}"' in card
    assert 'data-type="db_dump_restore" data-n="1" data-open="1"' in card
    assert 'data-type="standard" data-n="7" data-open="2"' in card
    assert 'data-system="test" data-n="2"' in card
    assert 'data-system="live" data-n="6"' in card


def test_activity_chart_gets_the_last_fortnights_deployments(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    text = _page(client)
    data = json.loads(re.search(r'<script type="application/json" id="activity-data">(.*?)</script>', text, re.S).group(1))

    assert sorted(d["system"] for d in data["deployments"]) == ["live", "test"]   # the 39-day-old one is out
    assert data["topDeployers"] == [["Zunayed Rabbi", 3]]


def test_recent_deployments_are_the_latest_four(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    card = _card(_page(client), "recent")

    assert re.findall(r'data-task="([^"]+)"', card) == ["PR-DONE-2", "PR-DONE-1", "PR-DONE-OLD"]
    assert "by Zunayed Rabbi" in card


def test_release_alignment_shows_drift(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")

    card = _card(_page(client), "release")

    assert "50%" in card
    assert 'data-aligned="1"' in card and 'data-drift="1"' in card
    assert "PlanVisu" in card and "CRM" not in card.split('class="drift"')[1]


def test_setup_health_rows_and_links_follow_access(web):
    client, session = web
    _seed(session)
    login_as(client, "root")

    card = _card(_page(client), "health")

    assert 'data-health="clients" data-ok="0"' in card
    assert 'data-health="checklist" data-ok="0"' in card     # DB and Test.local have none
    assert 'data-health="users" data-ok="1"' in card
    assert "3 with login access of 4" in card
    assert 'data-health="seeders" data-ok="1"' in card
    assert 'href="/admin/users"' in card and 'href="/seeder-collection"' in card


def test_setup_health_links_only_where_the_viewer_may_go(web):
    client, session = web
    _seed(session)
    login_as(client, "rajib")    # developer: no Users, no Checklist, no Seeder pages

    card = _card(_page(client), "health")

    assert 'href="/admin/users"' not in card
    assert 'href="/management/checklists"' not in card
    assert 'href="/seeder-collection"' not in card
    assert 'href="/clients"' in card
