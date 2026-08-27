from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — registers all models on Base.metadata
from app.database import Base
from app.models.bitbucket_main_branch_status import BitbucketMainBranchStatus
from app.models.deployable_task import DeployableTask
from app.models.team import Team
from app.models.user import User, UserRole
from app.services.sync import (
    sync_bitbucket_main_status,
    sync_deployable_tasks,
    sync_team_leads,
    sync_teams,
    sync_user_contacts,
    sync_users,
)
from app.services.task_source import DeployableTaskInfo, TeamInfo, TeamLeadInfo, UserContactInfo, UserInfo


class FakeBitbucketProvider:
    def __init__(self, version, pr_number):
        self._version = version
        self._pr_number = pr_number

    def get_main_branch_status(self):
        from app.services.bitbucket_source import BitbucketMainStatusInfo
        return BitbucketMainStatusInfo(version=self._version, pr_number=self._pr_number)


class FakeProvider:
    def __init__(self, users=(), teams=(), team_leads=(), user_contacts=(), deployable_tasks=()):
        self._users = users
        self._teams = teams
        self._team_leads = team_leads
        self._user_contacts = user_contacts
        self._deployable_tasks = deployable_tasks

    def list_users(self):
        return self._users

    def list_teams(self):
        return self._teams

    def list_team_leads(self):
        return self._team_leads

    def list_user_contacts(self):
        return self._user_contacts

    def list_deployable_tasks(self):
        return self._deployable_tasks

    def get_task(self, task_id):
        raise NotImplementedError

    def search_tasks(self, query):
        raise NotImplementedError

    def list_clients(self):
        raise NotImplementedError


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_sync_users_creates_new_users_with_developer_role_default(db_session):
    provider = FakeProvider(
        [
            UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=4),
            UserInfo(source_system_id="101099", name="Farhan Ahmed", machine_group_id=None),
        ]
    )

    result = sync_users(db_session, provider)

    assert (result.created, result.updated) == (2, 0)
    users = db_session.query(User).order_by(User.source_system_id).all()
    assert [u.source_system_id for u in users] == ["101088", "101099"]
    assert users[0].name == "Rajib Ahamad"
    assert users[0].machine_group_id == 4
    assert users[0].role == UserRole.developer
    assert users[0].last_synced_at is not None
    assert users[1].machine_group_id is None


def test_sync_users_updates_existing_without_creating_duplicates(db_session):
    sync_users(db_session, FakeProvider([UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=4)]))

    result = sync_users(
        db_session, FakeProvider([UserInfo(source_system_id="101088", name="Rajib A.", machine_group_id=9)])
    )

    assert (result.created, result.updated) == (0, 1)
    users = db_session.query(User).all()
    assert len(users) == 1
    assert users[0].name == "Rajib A."
    assert users[0].machine_group_id == 9


def test_sync_users_never_overwrites_a_manually_assigned_role(db_session):
    provider = FakeProvider([UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=4)])
    sync_users(db_session, provider)

    user = db_session.query(User).filter_by(source_system_id="101088").one()
    user.role = UserRole.team_lead
    db_session.commit()

    sync_users(db_session, provider)

    user = db_session.query(User).filter_by(source_system_id="101088").one()
    assert user.role == UserRole.team_lead


def test_sync_users_does_not_clobber_an_existing_email(db_session):
    provider = FakeProvider([UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=4)])
    sync_users(db_session, provider)

    user = db_session.query(User).filter_by(source_system_id="101088").one()
    user.email = "rahamad@schertech.com"
    db_session.commit()

    sync_users(db_session, provider)

    user = db_session.query(User).filter_by(source_system_id="101088").one()
    assert user.email == "rahamad@schertech.com"


def test_sync_teams_creates_new_teams_keyed_by_crm_id(db_session):
    provider = FakeProvider(
        teams=[
            TeamInfo(id=1, source_system_id="MG-00001", name="Team QA"),
            TeamInfo(id=3, source_system_id="MG-00003", name="Developer"),
        ]
    )

    result = sync_teams(db_session, provider)

    assert (result.created, result.updated) == (2, 0)
    teams = db_session.query(Team).order_by(Team.id).all()
    assert [(t.id, t.source_system_id, t.name) for t in teams] == [
        (1, "MG-00001", "Team QA"),
        (3, "MG-00003", "Developer"),
    ]
    assert teams[0].last_synced_at is not None


def test_sync_teams_updates_existing_without_creating_duplicates(db_session):
    sync_teams(db_session, FakeProvider(teams=[TeamInfo(id=1, source_system_id="MG-00001", name="Team QA")]))

    result = sync_teams(db_session, FakeProvider(teams=[TeamInfo(id=1, source_system_id="MG-00001", name="Team QA Renamed")]))

    assert (result.created, result.updated) == (0, 1)
    teams = db_session.query(Team).all()
    assert len(teams) == 1
    assert teams[0].name == "Team QA Renamed"


def test_sync_team_leads_promotes_matched_developer_and_backfills_contact_info(db_session):
    sync_users(db_session, FakeProvider(users=[UserInfo(source_system_id="101088", name="Rajib Ahamad")]))

    result = sync_team_leads(
        db_session,
        FakeProvider(
            team_leads=[
                TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad", email="rahamad@schertech.com", username="ahamad")
            ]
        ),
    )

    assert (result.matched, result.promoted) == (1, 1)
    user = db_session.query(User).filter_by(source_system_id="101088").one()
    assert user.role == UserRole.team_lead
    assert user.email == "rahamad@schertech.com"
    assert user.username == "ahamad"


def test_sync_team_leads_sets_team_leader_user_id_from_the_leads_own_machine_group(db_session):
    # Rajib Ahamad's own Machine record puts him in machine_group_id 13 — that's the team
    # he leads, resolved the same way User.team already resolves via machine_group_id.
    sync_users(
        db_session,
        FakeProvider(users=[UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=13)]),
    )
    sync_teams(db_session, FakeProvider(teams=[TeamInfo(id=13, source_system_id="MG-00013", name="Team Rajib")]))

    sync_team_leads(
        db_session,
        FakeProvider(team_leads=[TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad")]),
    )

    user = db_session.query(User).filter_by(source_system_id="101088").one()
    team = db_session.get(Team, 13)
    assert team.leader_user_id == user.id


def test_sync_team_leads_skips_setting_leader_when_team_not_synced_locally_yet(db_session):
    # sync_teams() hasn't run for machine_group_id 13 — must not error, just leave it unset.
    sync_users(
        db_session,
        FakeProvider(users=[UserInfo(source_system_id="101088", name="Rajib Ahamad", machine_group_id=13)]),
    )

    result = sync_team_leads(
        db_session,
        FakeProvider(team_leads=[TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad")]),
    )

    assert (result.matched, result.promoted) == (1, 1)
    assert db_session.query(Team).count() == 0


def test_sync_team_leads_never_demotes_an_existing_higher_role(db_session):
    sync_users(db_session, FakeProvider(users=[UserInfo(source_system_id="101088", name="Rajib Ahamad")]))
    user = db_session.query(User).filter_by(source_system_id="101088").one()
    user.role = UserRole.admin
    db_session.commit()

    provider = FakeProvider(
        team_leads=[TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad", email="rahamad@schertech.com")]
    )
    result = sync_team_leads(db_session, provider)

    assert (result.matched, result.promoted) == (1, 0)
    user = db_session.query(User).filter_by(source_system_id="101088").one()
    assert user.role == UserRole.admin


def test_sync_team_leads_skips_a_supervisor_with_no_matching_machine_user(db_session):
    # e.g. "Theodor Scherer" / non-factory staff in the Users feed who never appeared
    # in the Machines feed at all — must not create a new User row from this alone.
    provider = FakeProvider(team_leads=[TeamLeadInfo(source_system_id="541", name="Theodor Scherer")])

    result = sync_team_leads(db_session, provider)

    assert (result.matched, result.promoted) == (0, 0)
    assert db_session.query(User).count() == 0


def test_sync_team_leads_rerun_does_not_re_promote_or_duplicate(db_session):
    sync_users(db_session, FakeProvider(users=[UserInfo(source_system_id="101088", name="Rajib Ahamad")]))
    provider = FakeProvider(
        team_leads=[TeamLeadInfo(source_system_id="101088", name="Rajib Ahamad", email="rahamad@schertech.com")]
    )

    first = sync_team_leads(db_session, provider)
    second = sync_team_leads(db_session, provider)

    assert (first.matched, first.promoted) == (1, 1)
    assert (second.matched, second.promoted) == (1, 0)  # already team_lead, nothing left to promote
    assert db_session.query(User).count() == 1


def test_sync_user_contacts_backfills_email_and_username_for_a_regular_developer(db_session):
    # e.g. Biprojit Roy — an ordinary developer, not a supervisor, so sync_team_leads()
    # never touches him; this is the only sync step that fills in his contact info.
    sync_users(db_session, FakeProvider(users=[UserInfo(source_system_id="101003", name="Biprojit Roy")]))

    result = sync_user_contacts(
        db_session,
        FakeProvider(
            user_contacts=[
                UserContactInfo(source_system_id="101003", name="Biprojit Roy", email="bproy@schertech.com", username="biprojit")
            ]
        ),
    )

    assert (result.matched, result.backfilled) == (1, 1)
    user = db_session.query(User).filter_by(source_system_id="101003").one()
    assert user.email == "bproy@schertech.com"
    assert user.username == "biprojit"
    assert user.role == UserRole.developer  # unchanged — contact sync never touches role


def test_sync_user_contacts_skips_a_user_with_no_matching_machine_record(db_session):
    provider = FakeProvider(user_contacts=[UserContactInfo(source_system_id="541", name="Theodor Scherer")])

    result = sync_user_contacts(db_session, provider)

    assert (result.matched, result.backfilled) == (0, 0)
    assert db_session.query(User).count() == 0


def test_sync_user_contacts_rerun_reports_zero_backfilled_once_up_to_date(db_session):
    sync_users(db_session, FakeProvider(users=[UserInfo(source_system_id="101003", name="Biprojit Roy")]))
    provider = FakeProvider(
        user_contacts=[
            UserContactInfo(source_system_id="101003", name="Biprojit Roy", email="bproy@schertech.com", username="biprojit")
        ]
    )

    first = sync_user_contacts(db_session, provider)
    second = sync_user_contacts(db_session, provider)

    assert (first.matched, first.backfilled) == (1, 1)
    assert (second.matched, second.backfilled) == (1, 0)  # already up to date, nothing changed


def _deploy_task(operation_id, target_status="PLANNED", **overrides):
    defaults = dict(
        operation_id=operation_id,
        order_id=552,
        task_id="PR-03045",
        order_name="Some order",
        client_name="VolaPlast GmbH & Co. KG",
        item_custom_id="ReportV",
        item_name="ReportVisu",
        pos_id="0040",
        target="test",
        target_status=target_status,
        assigned_developer_custom_id="101088",
        assigned_developer_name="Rajib Ahamad",
        due_date="2026-08-04 00:00:00",
    )
    defaults.update(overrides)
    return DeployableTaskInfo(**defaults)


def test_sync_deployable_tasks_creates_row(db_session):
    info = _deploy_task(2060)

    result = sync_deployable_tasks(db_session, FakeProvider(deployable_tasks=[info]))

    assert (result.created, result.updated) == (1, 0)

    task = db_session.get(DeployableTask, 2060)
    assert task.order_id == 552
    assert task.task_id == "PR-03045"
    assert task.pos_id == "0040"
    assert task.target_status == "PLANNED"
    assert task.due_date == datetime(2026, 8, 4)
    assert task.assigned_developer_name == "Rajib Ahamad"


def test_sync_deployable_tasks_allows_two_orders_sharing_the_same_task_id(db_session):
    # Two distinct orders (different order_id) both numbered "PR-9999" — a real scenario
    # per the user's report, not just a hypothetical. Both must persist as separate rows,
    # disambiguated by order_id, since operation_id (not task_id) is the true unique key.
    provider = FakeProvider(
        deployable_tasks=[
            _deploy_task(3001, order_id=100, task_id="PR-9999"),
            _deploy_task(3002, order_id=200, task_id="PR-9999"),
        ]
    )

    result = sync_deployable_tasks(db_session, provider)

    assert (result.created, result.updated) == (2, 0)
    rows = db_session.query(DeployableTask).filter(DeployableTask.task_id == "PR-9999").order_by(DeployableTask.id).all()
    assert [(r.id, r.order_id) for r in rows] == [(3001, 100), (3002, 200)]


def test_sync_deployable_tasks_rerun_updates_not_duplicates(db_session):
    provider = FakeProvider(deployable_tasks=[_deploy_task(2060)])

    first = sync_deployable_tasks(db_session, provider)
    second = sync_deployable_tasks(db_session, provider)

    assert (first.created, first.updated) == (1, 0)
    assert (second.created, second.updated) == (0, 1)
    assert db_session.query(DeployableTask).count() == 1  # upsert, not a duplicate row


def test_sync_bitbucket_main_status_creates_row_on_first_sync(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))

    status = db_session.get(BitbucketMainBranchStatus, 1)
    assert status is not None
    assert status.version == "2026.34.34"
    assert status.pr_number == 1234
    assert status.last_synced_at is not None


def test_sync_bitbucket_main_status_updates_in_place_not_duplicates(db_session):
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.34", 1234))
    sync_bitbucket_main_status(db_session, FakeBitbucketProvider("2026.34.40", 1300))

    assert db_session.query(BitbucketMainBranchStatus).count() == 1
    status = db_session.get(BitbucketMainBranchStatus, 1)
    assert status.version == "2026.34.40"
    assert status.pr_number == 1300
