"""Granular action catalog (RBAC-06, audit §7.1).

`PERMISSIONS` is the full universe of actions a role template may compose
from (and what admin/superadmin implicitly hold in full). It is a strict
superset of the original 5-flag set (`documents.upload`, `documents.delete`,
`workspace.configure`, `analytics.view`, `chat.use`) -- nothing is ever
removed here, so every persisted `RoleTemplate` row stays valid.

`DEFAULT_USER_PERMISSIONS` is the fallback for any "user"-tier account with
no custom role assigned. RBAC-04 (this change) removed the legacy destructive
trio (`documents.upload`, `documents.delete`, `chat.use`) from this set,
leaving only the non-destructive read floor; existing users retain those
powers via the "Contributor" role seeded and assigned to every existing
`role="user"` account by the preceding forward migration, so nobody's
capability regressed when the narrowing landed.
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

# RBAC-04 (deny-by-default): the non-destructive read floor a "user"-tier
# account with no custom role receives. The legacy destructive trio
# (documents.upload, documents.delete, chat.use) was REMOVED here -- those
# are now explicit-grant-only, carried for existing users by the "Contributor"
# role the preceding forward migration seeded and assigned.
DEFAULT_USER_PERMISSIONS = frozenset({
    "workspace.read", "documents.list", "documents.content.read",
    "search.execute", "chat.read", "chat.generate",
})
