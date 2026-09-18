from datetime import datetime, timezone

from app.models.deployment_execution import DeploymentExecution, ExecutionStatus
from app.models.deployment_request import DeploymentRequest, RequestStatus, RequestType
from app.models.request_return import RequestReturn
from app.models.user import UserRole
from tests.conftest import DEFAULT_TEST_PASSWORD, login_as, make_user


def _deploy_team_user(session, settings_group="MG-00013"):
    """The deploy team is identified by machine_group_id — see
    require_deploy_team_member() in app/auth.py. An admin qualifies too."""
    return make_user(
        session, id=1, name="Zunayed Islam", username="zunayed",
        password=DEFAULT_TEST_PASSWORD, role=UserRole.admin,
    )


def _approved_request(session, *, task_id="PR-RET", request_type=RequestType.standard):
    request = DeploymentRequest(
        task_id=task_id, requested_by=2, status=RequestStatus.approved,
        request_type=request_type, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    return request


def test_deploy_team_can_return_an_approved_request(web):
    client, session = web
    _deploy_team_user(session)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return",
        data={"reason": "branch client/foo was deleted"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.returned
    logged = session.query(RequestReturn).one()
    assert logged.reason == "branch client/foo was deleted"
    assert logged.returned_from == RequestStatus.approved
    assert logged.returned_by == 1


def test_returning_requires_a_reason(web):
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "   "}, follow_redirects=False)

    assert response.status_code == 400
    session.refresh(request)
    assert request.status == RequestStatus.approved
    assert session.query(RequestReturn).count() == 0


def test_a_developer_cannot_return(web):
    client, session = web
    make_user(session, id=1, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "nope"}, follow_redirects=False)

    assert response.status_code == 403
    assert session.query(RequestReturn).count() == 0


def test_returning_an_unfinished_status_is_refused(web):
    """Only a request sitting in the deploy queue can be handed back."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session)
    request.status = RequestStatus.completed
    session.commit()
    login_as(client, "zunayed")

    response = client.post(f"/requests/{request.id}/return", data={"reason": "x"}, follow_redirects=False)

    assert response.status_code == 409


def test_returning_from_in_progress_drops_the_claim(web):
    """DeploymentExecution.request_id is unique on purpose, so a leftover claim
    would make the later Start Deployment insert fail — at the exact moment
    devops picks the request back up. The claim is still recorded as
    returned_from."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request = _approved_request(session, task_id="PR-INPROG")
    request.status = RequestStatus.in_progress
    session.add(
        DeploymentExecution(
            request_id=request.id, executed_by=1,
            claimed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            status=ExecutionStatus.claimed,
        )
    )
    session.commit()
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request.id}/return", data={"reason": "migration error on live"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0
    assert session.query(RequestReturn).one().returned_from == RequestStatus.in_progress


def test_returning_from_approved_leaves_other_claims_alone(web):
    """Guards against making the DeploymentExecution delete unconditional. An
    approved request never has its own execution row in practice, so the
    regression this guards against is an unconditional delete wiping out a
    claim that belongs to a different, unrelated in-progress request."""
    client, session = web
    _deploy_team_user(session)
    session.commit()
    request_a = _approved_request(session, task_id="PR-A")
    request_b = _approved_request(session, task_id="PR-B")
    request_b.status = RequestStatus.in_progress
    session.add(
        DeploymentExecution(
            request_id=request_b.id, executed_by=1,
            claimed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            started_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            status=ExecutionStatus.claimed,
        )
    )
    session.commit()
    login_as(client, "zunayed")

    response = client.post(
        f"/requests/{request_a.id}/return", data={"reason": "wrong client"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.query(DeploymentExecution).filter_by(request_id=request_b.id).count() == 1
    assert session.query(RequestReturn).one().returned_from == RequestStatus.approved


def _returned_request(session, *, request_type=RequestType.standard, requester_id=2):
    request = DeploymentRequest(
        task_id="PR-BACK", requested_by=requester_id, status=RequestStatus.returned,
        request_type=request_type, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(request)
    session.commit()
    session.add(
        RequestReturn(
            request_id=request.id, reason="branch deleted", returned_by=1,
            returned_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            returned_from=RequestStatus.approved,
        )
    )
    session.commit()
    return request


def test_resubmitting_a_standard_request_goes_back_for_approval(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    # The branch changed, so the team lead's approval was for something that no
    # longer exists.
    assert request.status == RequestStatus.pending_approval


def test_resubmitting_a_test_local_request_goes_straight_to_the_deploy_queue(web):
    """test_local and db_dump_restore skip the approval gate at creation, so they
    skip it on the way back too."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session, request_type=RequestType.test_local)
    login_as(client, "devone")

    client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    session.refresh(request)
    assert request.status == RequestStatus.approved


def test_someone_elses_request_cannot_be_resubmitted(web):
    client, session = web
    make_user(session, id=3, name="Other Dev", username="other", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "other")

    response = client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)

    assert response.status_code == 403


def test_resubmitting_something_not_returned_is_refused(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    request.status = RequestStatus.approved
    session.commit()
    login_as(client, "devone")

    assert client.post(f"/requests/{request.id}/resubmit", follow_redirects=False).status_code == 409


def test_the_requester_can_withdraw_a_returned_request(web):
    """Withdraw, not delete: the return log has to survive, or the team lead's
    view of why it kept coming back disappears with it."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    response = client.post(f"/requests/{request.id}/withdraw", follow_redirects=False)

    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.withdrawn
    assert session.query(RequestReturn).count() == 1


def test_a_returned_non_standard_request_is_editable(web):
    """request_edit.html is now type-aware (app/templates/request_edit.html branches on
    request_type, mirroring each type's own creation form), so the old blanket "non-
    standard is never editable" rule in can_edit_request() no longer applies. A return is
    exactly the moment a test_local/db_dump_restore request needs to become editable —
    that's the whole point of returning one instead of just leaving it stuck: devops
    flags something (e.g. "branch deleted") and the requester needs a way to fix it
    without deleting and re-raising the request from scratch."""
    from app.auth import can_edit_request

    client, session = web
    requester = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session, request_type=RequestType.test_local)

    assert can_edit_request(requester, request) is True


def test_a_returned_request_is_not_deletable(web):
    """It carries a log now, and DELETABLE_REQUEST_STATUSES' own reasoning is that
    deleting a row with history breaks the audit trail this tool exists for."""
    from app.auth import can_delete_request

    client, session = web
    requester = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)

    assert can_delete_request(requester, request) is False


def test_a_resubmitted_request_is_still_not_deletable(web):
    """`returned` was deliberately kept out of DELETABLE_REQUEST_STATUSES, but a
    returned request doesn't stay returned: resubmit moves it to pending_approval
    (or approved), both of which ARE deletable statuses. A resubmitted request
    still carries its request_returns log, and deleting the row would destroy it
    exactly like a still-returned one would — so it must stay refused even once
    the status has moved on."""
    from app.auth import can_delete_request

    client, session = web
    requester = make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")
    client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)
    session.refresh(request)
    assert request.status == RequestStatus.pending_approval  # sanity: now in a deletable status

    assert can_delete_request(requester, request) is False


def test_deleting_a_resubmitted_request_is_refused_not_a_500(web):
    """Route-level companion to the test above: without the auth fix, delete_request
    clears `approvals` but nothing clears `request_returns`, and the FK (no ondelete,
    no cascade) turns this into an IntegrityError -> 500 instead of a clean 403, and
    the request becomes permanently undeletable."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")
    client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)
    session.refresh(request)

    response = client.post(f"/requests/{request.id}/delete", follow_redirects=False)

    assert response.status_code == 403
    assert session.query(RequestReturn).count() == 1


def test_a_returned_row_shows_the_reason_and_its_actions(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")

    page = client.get("/requests").text

    assert "Returned" in page
    assert "branch deleted" in page
    assert f"/requests/{request.id}/resubmit" in page
    assert f"/requests/{request.id}/withdraw" in page
    # Not deletable — the log has to survive.
    assert f"/requests/{request.id}/delete" not in page


def test_resubmitted_request_still_shows_its_return_history(web):
    """The [i] button used to be gated on `r.status == RequestStatus.returned`, so
    the history disappeared the moment the request moved on — exactly when the
    team lead reviewing it most needs to see why it kept coming back. Fix: gate
    on `r.returns` alone. Uses `withdrawn`, not `pending_approval`, on purpose:
    `pending_approval` is in ACTIVE_REQUEST_STATUSES_FOR_NOTIFICATIONS, so its
    return reason also lands in the page's `active-requests-data` JSON blob for
    the notification feed — asserting on raw substring presence there would pass
    whether or not the table's own gate was fixed (the exact trap noted in this
    repo's CLAUDE.md: a test matching a JSON blob elsewhere on the page instead
    of the table it meant to assert on). `withdrawn` is excluded from that list,
    so the only way "branch deleted" can appear is the request_list.html table
    row itself, via the return-log-reason markup."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    login_as(client, "devone")
    client.post(f"/requests/{request.id}/withdraw", follow_redirects=False)
    session.refresh(request)
    assert request.status == RequestStatus.withdrawn

    page = client.get("/requests").text

    assert 'data-return-log="%d"' % request.id in page
    assert '<p class="return-log-reason">branch deleted</p>' in page


def test_the_info_button_counts_repeat_returns(web):
    """"Returned three times" is the fact a team lead is looking for; it should
    not require opening the dialog to discover."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _returned_request(session)
    for day, reason in ((3, "wrong commit"), (4, "migration error on live")):
        session.add(
            RequestReturn(
                request_id=request.id, reason=reason, returned_by=1,
                returned_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                returned_from=RequestStatus.approved,
            )
        )
    session.commit()
    login_as(client, "devone")

    page = client.get("/requests").text

    assert 'class="return-log-count"' in page
    assert ">3<" in page
    assert "migration error on live" in page
    assert "wrong commit" in page


def test_the_deploy_queue_offers_return(web):
    client, session = web
    make_user(session, id=1, name="Zunayed Islam", username="zunayed",
              password=DEFAULT_TEST_PASSWORD, role=UserRole.admin)
    session.commit()
    request = _approved_request(session)
    login_as(client, "zunayed")

    page = client.get("/requests").text

    assert f"/requests/{request.id}/return" in page


def test_the_listing_eager_loads_returns(web):
    """One lazy load per row would be an N+1 across the whole queue — the same
    trap the seeder listing hit. Now there are 2 independent eager-load queries:
    one for the paginated listing, one for the active_requests (needed for the
    notification feed), but neither is an N+1 per row."""
    from sqlalchemy import event

    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    for i in range(3):
        request = _returned_request(session)
        request.task_id = f"PR-{i}"
    session.commit()
    login_as(client, "devone")

    client.get("/requests")  # warm anything lazy

    queries = []

    def record(conn, cursor, statement, *rest):
        if "request_returns" in statement:
            queries.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        client.get("/requests")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(queries) <= 2, f"returns loaded per row, not eagerly: {len(queries)} queries"


def test_returned_requests_reach_the_notification_feed(web):
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _returned_request(session)
    login_as(client, "devone")

    page = client.get("/requests").text
    payload = page.split('id="active-requests-data">')[1].split("</script>")[0]

    assert '"status": "returned"' in payload
    assert "branch deleted" in payload


def test_start_return_resubmit_start_round_trip_does_not_collide_on_the_unique_claim(web):
    """Regression for the exact failure mode return_request()'s comment warns about:
    DeploymentExecution.request_id is unique, so if the execution-row delete in
    return_request() were ever dropped or narrowed, this wouldn't blow up at Return —
    it would blow up later, when devops presses Start Deployment on the resubmitted
    request and the insert dies on the unique constraint. Drives the whole cycle
    through the HTTP routes (Start -> Return -> Resubmit -> Start) rather than the
    model directly, so it exercises what a user actually does.

    Uses a test_local request so resubmitting lands straight back on `approved`
    (test_local/db_dump_restore skip the approval gate) instead of `pending_approval`
    like a standard request would — keeping this test about the unique-claim
    constraint, not the approval workflow.
    """
    client, session = web
    _deploy_team_user(session)
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    request = _approved_request(session, task_id="PR-ROUNDTRIP", request_type=RequestType.test_local)

    # Start Deployment: deploy team claims it, creating the one-and-only execution row.
    login_as(client, "zunayed")
    response = client.post(f"/requests/{request.id}/start", follow_redirects=False)
    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 1

    # Return: devops hands it back with a reason, which must drop the claim.
    response = client.post(
        f"/requests/{request.id}/return", data={"reason": "wrong branch pushed"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.returned
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 0

    # Resubmit: the requester puts it back in the queue. test_local goes straight to
    # `approved`, so it's immediately eligible for Start Deployment again.
    login_as(client, "devone")
    response = client.post(f"/requests/{request.id}/resubmit", follow_redirects=False)
    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.approved

    # Start Deployment again: this is the claim that would die on the unique
    # constraint if the Return route had left the old execution row behind.
    login_as(client, "zunayed")
    response = client.post(f"/requests/{request.id}/start", follow_redirects=False)
    assert response.status_code == 303
    session.refresh(request)
    assert request.status == RequestStatus.in_progress
    assert session.query(DeploymentExecution).filter_by(request_id=request.id).count() == 1


def test_non_requester_does_not_see_returned_notification(web):
    """A returned request appears in the feed, but only the requester gets notified.
    A different logged-in user sees isRequester: false, so the notification does not
    fire for them (isRequester is required in the condition)."""
    client, session = web
    make_user(session, id=2, name="Dev One", username="devone", password=DEFAULT_TEST_PASSWORD)
    make_user(session, id=3, name="Dev Two", username="devtwo", password=DEFAULT_TEST_PASSWORD)
    session.commit()
    _returned_request(session, requester_id=2)  # requester is devone (id=2)
    login_as(client, "devtwo")  # but logged in as devtwo (id=3)

    page = client.get("/requests").text
    payload = page.split('id="active-requests-data">')[1].split("</script>")[0]

    # The returned request is in the feed (everyone sees the queue)
    assert '"status": "returned"' in payload
    # But isRequester is false for this non-requester user
    assert '"isRequester": false' in payload
