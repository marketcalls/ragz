"""Tests for ReembedJob model (DOC-10, Task 6)."""

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ragz.modules.models.models import LOCAL_EMBEDDING_MODEL_ID, Model
from ragz.modules.tenancy.models import Workspace
from ragz.modules.tenancy.reembed_models import ReembedJob


@pytest.fixture
async def workspace(session: AsyncSession, seeded_user) -> Workspace:
    """Create a workspace for testing."""
    ws = Workspace(org_id=seeded_user.org_id, name="ReembedTest")
    session.add(ws)
    await session.commit()
    return ws


@pytest.fixture
async def old_embedding_model(session: AsyncSession) -> Model:
    """Create an old embedding model (non-local) to switch from."""
    model = Model(
        litellm_model_name="old-embedding-model",
        display_name="Old Embedding Model",
        provider_kind="tei",
        enabled=True,
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def new_embedding_model() -> None:
    """Use the local embedding model (bootstrapped in conftest) as the new model."""
    # LOCAL_EMBEDDING_MODEL_ID is already seeded by conftest's engine fixture
    pass


async def test_reembed_job_round_trips(
    session: AsyncSession, workspace: Workspace, old_embedding_model: Model
) -> None:
    """Test that a ReembedJob can be created and fetched from the database."""
    job = ReembedJob(
        workspace_id=workspace.id,
        old_embedding_model_id=old_embedding_model.id,
        new_embedding_model_id=LOCAL_EMBEDDING_MODEL_ID,
        documents_total=3,
    )
    session.add(job)
    await session.commit()

    fetched = await session.get(ReembedJob, job.id)
    assert fetched is not None
    assert fetched.workspace_id == workspace.id
    assert fetched.old_embedding_model_id == old_embedding_model.id
    assert fetched.new_embedding_model_id == LOCAL_EMBEDDING_MODEL_ID
    assert fetched.documents_total == 3
    assert fetched.documents_done == 0
    assert fetched.error is None
    assert fetched.started_at is None
    assert fetched.finished_at is None


async def test_reembed_job_progress_updates(
    session: AsyncSession, workspace: Workspace, old_embedding_model: Model
) -> None:
    """Test that a ReembedJob's progress fields can be updated."""
    job = ReembedJob(
        workspace_id=workspace.id,
        old_embedding_model_id=old_embedding_model.id,
        new_embedding_model_id=LOCAL_EMBEDDING_MODEL_ID,
        documents_total=10,
    )
    session.add(job)
    await session.commit()

    job.documents_done = 5
    job.started_at = datetime.now()
    await session.commit()

    fetched = await session.get(ReembedJob, job.id)
    assert fetched is not None
    assert fetched.documents_done == 5
    assert fetched.started_at is not None


async def test_reembed_job_error_and_finished(
    session: AsyncSession, workspace: Workspace, old_embedding_model: Model
) -> None:
    """Test that a ReembedJob can record errors and completion."""
    job = ReembedJob(
        workspace_id=workspace.id,
        old_embedding_model_id=old_embedding_model.id,
        new_embedding_model_id=LOCAL_EMBEDDING_MODEL_ID,
        documents_total=5,
    )
    session.add(job)
    await session.commit()

    job.documents_done = 5
    job.error = None
    job.finished_at = datetime.now()
    await session.commit()

    fetched = await session.get(ReembedJob, job.id)
    assert fetched is not None
    assert fetched.documents_done == 5
    assert fetched.error is None
    assert fetched.finished_at is not None


async def test_reembed_job_cascade_delete_on_workspace(
    session: AsyncSession, workspace: Workspace, old_embedding_model: Model
) -> None:
    """Test that deleting a workspace cascades to delete its ReembedJob."""
    job = ReembedJob(
        workspace_id=workspace.id,
        old_embedding_model_id=old_embedding_model.id,
        new_embedding_model_id=LOCAL_EMBEDDING_MODEL_ID,
        documents_total=3,
    )
    session.add(job)
    await session.commit()

    job_id = job.id
    fetched = await session.get(ReembedJob, job_id)
    assert fetched is not None

    # Delete the workspace
    await session.delete(workspace)
    await session.commit()

    # Expire the session to clear identity map so we query the DB fresh
    session.expunge_all()

    # The job should be gone (cascade delete)
    fetched = await session.get(ReembedJob, job_id)
    assert fetched is None
