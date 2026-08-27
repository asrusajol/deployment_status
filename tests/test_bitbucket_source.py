import httpx
import pytest

from app.config import Settings
from app.services.bitbucket_source import BitbucketCloudProvider


def _make_provider(handler):
    settings = Settings(
        bitbucket_api_token="fake-token",
        bitbucket_workspace="SCT",
        bitbucket_repo_slug="shopfloor-suite",
        bitbucket_release_path="frontend-sap/src/assets/release.json",
        bitbucket_branch="main",
    )
    client = httpx.Client(
        base_url="https://api.bitbucket.org/2.0",
        transport=httpx.MockTransport(handler),
    )
    return BitbucketCloudProvider(settings, client=client)


def test_get_main_branch_status_parses_release_and_latest_merged_pr():
    def handler(request):
        assert request.headers["authorization"] == "Bearer fake-token"
        if request.url.path == (
            "/2.0/repositories/SCT/shopfloor-suite/src/main/"
            "frontend-sap/src/assets/release.json"
        ):
            return httpx.Response(200, json={"release": "2026.34.34"})
        if request.url.path == "/2.0/repositories/SCT/shopfloor-suite/pullrequests":
            assert request.url.params["state"] == "MERGED"
            assert request.url.params["q"] == 'destination.branch.name="main"'
            assert request.url.params["sort"] == "-updated_on"
            return httpx.Response(200, json={"values": [{"id": 1234}, {"id": 1200}]})
        raise AssertionError(f"unexpected path {request.url.path}")

    provider = _make_provider(handler)
    status = provider.get_main_branch_status()

    assert status.version == "2026.34.34"
    assert status.pr_number == 1234


def test_get_main_branch_status_handles_no_merged_prs_yet():
    def handler(request):
        if "pullrequests" in request.url.path:
            return httpx.Response(200, json={"values": []})
        return httpx.Response(200, json={"release": "2026.34.34"})

    provider = _make_provider(handler)
    status = provider.get_main_branch_status()

    assert status.version == "2026.34.34"
    assert status.pr_number is None


def test_requires_configured_token():
    settings = Settings(bitbucket_api_token=None)
    with pytest.raises(RuntimeError, match="bitbucket_api_token is not configured"):
        BitbucketCloudProvider(settings)
