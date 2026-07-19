"""Seed the load-test tenant: 1 org, 100 users, 1 workspace, 1 mock model.

Requires the compose stack (postgres/redis/litellm) up and migrations applied.
Run from backend/:  uv run python ../deploy/loadtest/seed.py

Deviation from the D2 brief: seeded emails use ``@loadtest-raghub.com``
rather than ``@loadtest.local``. The ``.local`` TLD is a special-use domain
that pydantic's ``EmailStr`` (email-validator) rejects outright, and
``/api/v1/auth/login`` validates its body against ``LoginRequest.email:
EmailStr`` - a ``.local`` seed account can never log in. Verified directly:
``EmailStr`` accepts ``lt-000@loadtest-raghub.com`` and rejects
``lt-000@loadtest.local`` with "special-use or reserved name".
"""

import asyncio

from sqlalchemy import select

from raghub.core.config import get_settings
from raghub.core.db import build_engine, build_session_factory
from raghub.modules.auth.models import User
from raghub.modules.auth.passwords import hash_password
from raghub.modules.models.models import Model
from raghub.modules.models.sync import sync_models_to_litellm
from raghub.modules.tenancy.models import Organization, Workspace, WorkspaceMember

USERS = 100
PASSWORD = "loadtest-pw-1"  # noqa: S105 - throwaway local tenant
EMAIL_DOMAIN = "loadtest-raghub.com"  # see module docstring: .local is rejected by EmailStr


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
        print(f"seeded org=loadtest users={USERS} model=loadtest-mock deployed={deployed}")


if __name__ == "__main__":
    asyncio.run(main())
