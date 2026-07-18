"""OIDC SSO (AUTH-2). Constants shared by admin config routes and the login
flow. The flow itself lands with the /auth/oidc endpoints."""

OIDC_ISSUER_KEY = "oidc_issuer"
OIDC_CLIENT_ID_KEY = "oidc_client_id"
OIDC_SECRET_NAME = "oidc:client_secret"  # noqa: S105 - a secret NAME, not a secret
