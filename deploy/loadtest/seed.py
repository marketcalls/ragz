"""Seed the load-test tenant: 1 org, 100 users, 1 workspace, 1 mock model.

Requires the compose stack (postgres/redis/litellm) up and migrations applied.
Run from backend/:  uv run python ../deploy/loadtest/seed.py

Deviation from the D2 brief: seeded emails use ``@loadtest-ragz.com``
rather than ``@loadtest.local``. The ``.local`` TLD is a special-use domain
that pydantic's ``EmailStr`` (email-validator) rejects outright, and
``/api/v1/auth/login`` validates its body against ``LoginRequest.email:
EmailStr`` - a ``.local`` seed account can never log in. Verified directly:
``EmailStr`` accepts ``lt-000@loadtest-ragz.com`` and rejects
``lt-000@loadtest.local`` with "special-use or reserved name".

Review round 1 fix: also mints one long-lived access token per seeded user
and writes them to ``.tokens.json`` next to this script. ``/api/v1/auth/login``
is rate-limited per client IP at 10/60s (``core/ratelimit.py``, a FastAPI
route dependency); a 100-concurrent locust run driving every ``on_start``
login from one test box IP would 429 ~90 of them. Minting tokens here
sidesteps the rate limiter entirely (and the argon2 password hash check)
because it goes straight through the same signing-key + claims path
``modules/auth/service.py`` uses internally
(``core.app_settings.get_or_create_signing_key`` +
``modules.auth.tokens.issue_access_token``) rather than through the rate-
limited HTTP route. ``locustfile.py`` reads this file in ``on_start`` and
only falls back to a live login when it's missing.
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from ragz.core.app_settings import get_or_create_signing_key
from ragz.core.config import get_settings
from ragz.core.db import build_engine, build_session_factory
from ragz.modules.auth.models import User
from ragz.modules.auth.passwords import hash_password
from ragz.modules.auth.tokens import issue_access_token
from ragz.modules.models.models import Model
from ragz.modules.models.sync import sync_models_to_litellm
from ragz.modules.tenancy.models import Organization, Workspace, WorkspaceMember

USERS = 100
PASSWORD = "loadtest-pw-1"  # noqa: S105 - throwaway local tenant
EMAIL_DOMAIN = "loadtest-ragz.com"  # see module docstring: .local is rejected by EmailStr
TOKENS_PATH = Path(__file__).with_name(".tokens.json")
# Long enough to outlast any acceptance run (default access tokens are 15 min);
# this file is a throwaway local-loadtest artifact, never committed.
TOKEN_TTL_SECONDS = 4 * 60 * 60


async def main() -> None:
    settings = get_settings()
    factory = build_session_factory(build_engine(settings.database_url))
    async with factory() as session:
        org = (await session.execute(
            select(Organization).where(Organization.name == "loadtest")
        )).scalar_one_or_none()
        if org is None:
            org = Organization(name="loadtest")
            session.add(org)
            await session.flush()
        model = (await session.execute(
            select(Model).where(Model.litellm_model_name == "loadtest-mock")
        )).scalar_one_or_none()
        if model is None:
            model = Model(
                display_name="Loadtest Mock", litellm_model_name="loadtest-mock",
                provider_kind="openai_compatible", base_url="http://mock.invalid",
                enabled=True,
                mock_response="This is a canned load-test answer. " * 8,
            )
            session.add(model)
            await session.flush()
        ws = (await session.execute(
            select(Workspace).where(Workspace.org_id == org.id)
        )).scalar_one_or_none()
        if ws is None:
            ws = Workspace(org_id=org.id, name="loadtest-ws", default_model_id=model.id)
            session.add(ws)
            await session.flush()
        pw = hash_password(PASSWORD)  # hash once - argon2 is deliberately slow
        existing = set((await session.execute(
            select(User.email).where(User.org_id == org.id)
        )).scalars())
        for i in range(USERS):
            email = f"lt-{i:03d}@{EMAIL_DOMAIN}"
            if email not in existing:
                user = User(org_id=org.id, email=email, password_hash=pw, role="user")
                session.add(user)
                await session.flush()
                session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id))
        await session.commit()
        deployed = await sync_models_to_litellm(session, settings)

        # Mint one long-lived token per seeded user, indexed by the lt-NNN
        # ordinal so locustfile.py's on_start can look one up by
        # `n = next(_counter) % 100` without ever hitting the rate-limited
        # /auth/login route. Query fresh rows rather than reusing the loop
        # above so a re-run over already-seeded users still refreshes tokens.
        signing_key = await get_or_create_signing_key(session)
        users_by_email = {
            u.email: u
            for u in (
                await session.execute(select(User).where(User.org_id == org.id))
            ).scalars()
        }
        tokens: dict[str, str] = {}
        for i in range(USERS):
            user = users_by_email[f"lt-{i:03d}@{EMAIL_DOMAIN}"]
            tokens[str(i)] = issue_access_token(
                user_id=user.id, org_id=user.org_id, role=user.role,
                signing_key=signing_key, ttl_seconds=TOKEN_TTL_SECONDS,
            )
        TOKENS_PATH.write_text(json.dumps({
            "ttl_seconds": TOKEN_TTL_SECONDS,
            "tokens": tokens,
        }, indent=2))

        print(
            f"seeded org=loadtest users={USERS} model=loadtest-mock deployed={deployed} "
            f"tokens={TOKENS_PATH}"
        )


if __name__ == "__main__":
    asyncio.run(main())
