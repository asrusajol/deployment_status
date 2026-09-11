"""Merging a duplicate client into the one that should survive.

Duplicates happen because `sync_clients()` only adopts a hand-created client when its
name matches the CRM's **exactly**. Someone typing "A.W. Schumacher GmbH" via the request
form's "+ Add new client" before the CRM row arrives — or typing it with any difference at
all — leaves two rows for one customer: one with a `source_system_id`, one without. Both
can accumulate requests, URLs and a Release Tracker row, so the fix is a merge rather than
a delete.

Deliberately a service + CLI command rather than hand-written SQL: this runs against
production, where a half-applied merge leaves requests pointing at a client that no longer
exists. Everything below happens inside the caller's transaction, so it either all lands or
none of it does.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.client import Client
from app.models.client_system_url import ClientSystemUrl
from app.models.client_version_status import ClientVersionStatus
from app.models.deployment_request import DeploymentRequest


class ClientMergeError(Exception):
    """The merge cannot safely proceed — nothing has been changed."""


@dataclass
class ClientMergePlan:
    """What a merge would do. Printed by --dry-run, and returned after a real run."""

    keep_id: int
    keep_name: str
    remove_id: int
    remove_name: str
    requests_moved: int = 0
    urls_moved: int = 0
    urls_dropped_as_duplicate: list[str] = field(default_factory=list)
    version_status: str = "none to move"
    renamed_to: str | None = None

    def describe(self) -> list[str]:
        lines = [
            f"Merge client {self.remove_id} ({self.remove_name!r}) into {self.keep_id} ({self.keep_name!r})",
            f"  deployment_requests repointed : {self.requests_moved}",
            f"  client_system_urls repointed  : {self.urls_moved}",
        ]
        for url in self.urls_dropped_as_duplicate:
            lines.append(f"    dropped (keeper already has it): {url}")
        lines.append(f"  client_version_status         : {self.version_status}")
        if self.renamed_to:
            lines.append(f"  keeper renamed to             : {self.renamed_to!r}")
        lines.append(f"  then deletes client {self.remove_id}")
        return lines


def merge_clients(
    db: Session,
    keep_id: int,
    remove_id: int,
    *,
    rename_to_removed: bool = False,
    force: bool = False,
) -> ClientMergePlan:
    """Move everything owned by `remove_id` onto `keep_id`, then delete the duplicate.

    The caller commits. Raises ClientMergeError before touching anything if the merge
    looks wrong.
    """
    if keep_id == remove_id:
        raise ClientMergeError("keep and remove are the same client.")

    keeper = db.get(Client, keep_id)
    doomed = db.get(Client, remove_id)
    if keeper is None:
        raise ClientMergeError(f"No client with id {keep_id} (the one to keep).")
    if doomed is None:
        raise ClientMergeError(f"No client with id {remove_id} (the one to remove).")

    # The usual reason for a duplicate is a hand-created row shadowing the CRM-synced one,
    # so keeping the row WITHOUT a source_system_id is almost always the wrong way round —
    # it discards the link the sync uses and the duplicate simply comes back next sync.
    if not keeper.source_system_id and doomed.source_system_id and not force:
        raise ClientMergeError(
            f"Client {keep_id} has no source_system_id but {remove_id} has "
            f"({doomed.source_system_id!r}). Keeping the unsynced one would discard the CRM "
            f"link and the duplicate would reappear on the next sync. Swap --keep/--remove, "
            f"or pass --force if this really is what you want."
        )

    plan = ClientMergePlan(
        keep_id=keep_id, keep_name=keeper.name, remove_id=remove_id, remove_name=doomed.name
    )

    plan.requests_moved = (
        db.query(DeploymentRequest).filter(DeploymentRequest.client_id == remove_id).count()
    )
    for request in db.query(DeploymentRequest).filter(DeploymentRequest.client_id == remove_id):
        request.client_id = keep_id

    # A URL the keeper already has for the same system would otherwise be listed twice on
    # the Dashboard's stacked URL cell, so drop rather than move those.
    # Moved through the relationship collections, NOT by assigning client_id: Client
    # <-> ClientSystemUrl is bidirectional, so a row left in doomed.system_urls has its
    # FK nulled out when the parent is deleted — silently undoing a plain client_id
    # assignment and orphaning the URL.
    existing = {(u.environment, u.url) for u in keeper.system_urls}
    for url_row in list(doomed.system_urls):
        doomed.system_urls.remove(url_row)
        if (url_row.environment, url_row.url) in existing:
            plan.urls_dropped_as_duplicate.append(url_row.url)
            db.delete(url_row)
        else:
            keeper.system_urls.append(url_row)
            plan.urls_moved += 1

    # client_version_status.client_id is UNIQUE, so repointing a second row onto the keeper
    # violates the constraint. The keeper's own row is the one to trust — it belongs to the
    # client that is surviving — so the duplicate's is discarded, not merged field by field.
    keeper_status = db.query(ClientVersionStatus).filter_by(client_id=keep_id).one_or_none()
    doomed_status = db.query(ClientVersionStatus).filter_by(client_id=remove_id).one_or_none()
    if doomed_status is not None and keeper_status is None:
        doomed_status.client_id = keep_id
        plan.version_status = "moved to the keeper"
    elif doomed_status is not None:
        db.delete(doomed_status)
        plan.version_status = f"keeper already had one — discarded the duplicate's (id {doomed_status.id})"

    db.delete(doomed)

    # Only possible once the duplicate is gone: clients.name is UNIQUE, so the keeper
    # cannot take the other's name while both rows exist.
    if rename_to_removed:
        db.flush()
        keeper.name = plan.remove_name
        plan.renamed_to = plan.remove_name

    return plan
