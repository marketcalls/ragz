"""Granular action catalog (RBAC-06, audit §7.1).

`PERMISSIONS` is the full universe of actions a role template may compose
from (and what admin/superadmin implicitly hold in full). It is a strict
superset of the original 5-flag set (`documents.upload`, `documents.delete`,
`workspace.configure`, `analytics.view`, `chat.use`) -- nothing is ever
removed here, so every persisted `RoleTemplate` row stays valid.

`DEFAULT_USER_PERMISSIONS` is the fallback for any "user"-tier account with
no custom role assigned. Task 1 (this expansion) is a PURE ADDITION: it keeps
every legacy flag a plain user already receives today (upload/delete/chat)
so no existing gate weakens, while also exposing the new non-destructive
read-ish actions those accounts implicitly rely on. RBAC-04 (a later task)
is where the actual deny-by-default narrowing happens -- it removes the
legacy destructive flags from this set once a forward migration has given
every existing `role="user"` account an explicit "Contributor" role
template, so nobody's capability regresses when the narrowing lands.
"""

PERMISSIONS = frozenset({
    # Workspace
    "workspace.read", "workspace.create", "workspace.configure", "workspace.reembed",
    "workspace.members.read", "workspace.members.manage", "workspace.metadata.manage",
    # Documents
    "documents.list", "documents.content.read", "documents.upload",
    "documents.metadata.update", "documents.move", "documents.pin", "documents.delete",
    "documents.acl.manage", "documents.acl.bypass", "documents.approve",
    # Folders
    "folders.read", "folders.create", "folders.update", "folders.delete",
    # Knowledge/chat
    "search.execute", "chat.read", "chat.generate", "chat.update",
    "chat.attachments.create", "chat.delete", "chat.feedback",
    # Users/groups
    "users.read", "users.invite", "users.activate", "users.role.assign",
    "groups.read", "groups.manage",
    # Policy/audit
    "roles.read", "roles.author", "roles.assign", "audit.read", "audit.export",
    # Operations
    "models.read", "models.manage", "secrets.manage", "sso.manage",
    "integrations.manage", "api_keys.manage", "quota.read", "quota.manage",
    "analytics.view", "feedback.review", "evals.read", "evals.manage", "evals.run",
    "platform.health", "platform.orgs.manage", "settings.manage",
    "client_errors.report",
    # Legacy (kept for backward compatibility with persisted RoleTemplate rows;
    # no route checks this flag after Task 6 retires it from new grants)
    "chat.use",
})

# RBAC-06 (this task): purely additive. The narrower non-destructive floor
# (workspace.read, documents.list, documents.content.read, search.execute,
# chat.read, chat.generate) is unioned with -- not substituted for -- the
# three legacy flags a "user"-tier account with no custom role already
# receives today, so this task changes nothing about who-can-do-what.
# RBAC-04 removes the legacy trio below once its forward migration has
# backfilled every existing role="user" account with an explicit
# "Contributor" template.
DEFAULT_USER_PERMISSIONS = frozenset({
    "workspace.read", "documents.list", "documents.content.read",
    "search.execute", "chat.read", "chat.generate",
    # Legacy floor, preserved until RBAC-04's migration lands (see above).
    "documents.upload", "documents.delete", "chat.use",
})
