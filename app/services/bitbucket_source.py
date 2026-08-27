"""Adapter for the Bitbucket Cloud REST API — backs the Release Tracker's "current
version at main" snapshot (docs/superpowers/specs/2026-08-27-release-tracker-design.md).

Only ever reads from ONE fixed repo (settings.bitbucket_workspace/bitbucket_repo_slug,
"SCT/shopfloor-suite" by default) and ONE fixed branch (bitbucket_branch, "main") — this
is deliberately not a per-client lookup (confirmed with the user: clients are
differentiated by DeploymentRequest, not by any Bitbucket branch/repo mapping).
"""

from dataclasses import dataclass

import httpx

from app.config import Settings


@dataclass
class BitbucketMainStatusInfo:
    version: str | None
    pr_number: int | None


class BitbucketCloudProvider:
    """Talks to api.bitbucket.org/2.0.

    Auth: a single Repository or Workspace Access Token, sent as
    `Authorization: Bearer <token>` on every call — unlike InHouseTaskSourceProvider
    (app/services/task_source.py), there's no login step; the token is static and
    doesn't expire mid-run the way the CRM's does, so there's no 401-retry logic
    here either.
    """

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.bitbucket_api_token:
            raise RuntimeError(
                "bitbucket_api_token is not configured. Set it in .env (see .env.example)."
            )
        self._workspace = settings.bitbucket_workspace
        self._repo_slug = settings.bitbucket_repo_slug
        self._path = settings.bitbucket_release_path
        self._branch = settings.bitbucket_branch
        self._token = settings.bitbucket_api_token
        self._client = client or httpx.Client(base_url="https://api.bitbucket.org/2.0", timeout=10.0)

    def _auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def get_main_branch_status(self) -> BitbucketMainStatusInfo:
        return BitbucketMainStatusInfo(
            version=self._fetch_release_version(),
            pr_number=self._fetch_latest_merged_pr_number(),
        )

    def _fetch_release_version(self) -> str | None:
        path = f"/repositories/{self._workspace}/{self._repo_slug}/src/{self._branch}/{self._path}"
        response = self._client.get(path, headers=self._auth_header())
        response.raise_for_status()
        return response.json().get("release")

    def _fetch_latest_merged_pr_number(self) -> int | None:
        # Most recently merged PR into `main` overall, regardless of what it
        # touched — confirmed with the user, not specifically the PR that last
        # changed release.json.
        path = f"/repositories/{self._workspace}/{self._repo_slug}/pullrequests"
        params = {
            "state": "MERGED",
            "q": f'destination.branch.name="{self._branch}"',
            "sort": "-updated_on",
        }
        response = self._client.get(path, headers=self._auth_header(), params=params)
        response.raise_for_status()
        values = response.json().get("values", [])
        return values[0]["id"] if values else None
