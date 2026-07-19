"""Granular permission flags (RBAC-2).

`PERMISSIONS` is the full universe of flags a role template may compose from
(and what admin/superadmin implicitly hold in full). `DEFAULT_USER_PERMISSIONS`
is the fallback for any "user"-tier account with no custom role assigned --
it mirrors pre-Plan-H user-tier behavior exactly (users could already upload,
delete, and chat), so assigning no template changes nothing and no existing
authz test weakens.
"""

PERMISSIONS = frozenset({
    "documents.upload",
    "documents.delete",
    "workspace.configure",
    "analytics.view",
    "chat.use",
})

DEFAULT_USER_PERMISSIONS = frozenset({"documents.upload", "documents.delete", "chat.use"})
