"""Speed/effort choices must survive Plan snapshots and native route selection."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from backend.schemas.plan import PlanModelRoute
from backend.services.codex_models import supported_codex_efforts, supported_codex_service_tiers
from backend.services.plan_agent_runner import PlanAgentRunner, PLANNER_SCHEMA


@pytest.mark.parametrize('model', ['gpt-6.1-sol', 'gpt-6-astra', 'gpt-6.1-terra', 'gpt-6-sol', 'gpt-6-luna'])
def test_current_native_models_expose_catalog_speed_and_effort(model):
    assert supported_codex_service_tiers(model) == ['default', 'priority']
    assert 'max' in supported_codex_efforts(model)
    assert ('ultra' in supported_codex_efforts(model)) == (model != 'gpt-6-luna')


def test_plan_routes_default_standard_and_reject_unsupported_fast():
    assert PlanModelRoute(provider='codex', model='gpt-6.1-sol').codex_service_tier == 'default'
    route = PlanModelRoute(provider='codex', model='gpt-6.1-sol', effort='max', codex_service_tier='priority')
    assert PlanModelRoute.model_validate(route.model_dump()).codex_service_tier == 'priority'
    for provider, model in [('claude', 'claude-opus-5-5'), ('codex', 'gpt-5.4-mini')]:
        with pytest.raises(ValidationError):
            PlanModelRoute(provider=provider, model=model, codex_service_tier='priority')


@pytest.mark.asyncio
async def test_plan_route_passes_selected_speed_to_account_selection_and_execution(db_factory):
    pool = MagicMock()
    pool.select.return_value = '/codex/one'
    pool.canonical_home.side_effect = lambda home: home
    pool.account_id_for_home.return_value = 'one'
    runner = PlanAgentRunner(db_factory=db_factory, instance_manager=MagicMock(), codex_pool=pool)
    runner._run_fixed_route_with_retry = AsyncMock(return_value=({'plan': 'test'}, 'test'))
    route = PlanModelRoute(provider='codex', model='gpt-6.1-sol', effort='max', codex_service_tier='priority')
    await runner._run_route(task_id=1, route=route, cwd='/tmp', prompt='test', schema=PLANNER_SCHEMA, timeout=10)
    assert pool.select.call_args.kwargs['service_tier'] == 'priority'
    args = runner._run_fixed_route_with_retry.await_args.kwargs
    assert args['codex_service_tier'] == 'priority'
    assert args['effort'] == 'max'


@pytest.mark.asyncio
async def test_plan_creation_freezes_runtime_choices_without_model_override(client, session_factory):
    from backend.models.global_settings import GlobalSettings
    from backend.schemas.plan import default_plan_pipeline_config
    pipeline = default_plan_pipeline_config().model_dump(mode='json')
    pipeline['planner']['primary'] = {'provider': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'medium'}
    pipeline['reviewer']['primary'] = {'provider': 'codex', 'model': 'gpt-6-astra', 'effort': 'high'}
    async with session_factory() as db:
        db.add(GlobalSettings(id=1, plan_pipeline_config=pipeline))
        await db.commit()
    response = await client.post('/api/plans', json={
        'input': 'Prepare a design', 'target_repo': '/tmp',
        'runtime_options': {
            'planner': {'effort': 'max', 'codex_service_tier': 'priority'},
            'reviewer': {'effort': 'medium', 'codex_service_tier': 'default'},
        },
    })
    assert response.status_code == 201, response.text
    frozen = response.json()['pipeline_config']
    assert frozen['planner']['primary']['model'] == 'gpt-6.1-sol'
    assert frozen['planner']['primary']['effort'] == 'max'
    assert frozen['planner']['primary']['codex_service_tier'] == 'priority'
    assert frozen['reviewer']['primary']['effort'] == 'medium'
    # Runtime overrides cannot change providers/models or the fallback policy.
    invalid = await client.post('/api/plans', json={'input': 'test', 'target_repo': '/tmp',
        'runtime_options': {'planner': {'model': 'attacker-model'}}})
    assert invalid.status_code == 422


def test_effort_only_plan_override_preserves_global_speed_and_fallback():
    from backend.schemas.plan import default_plan_pipeline_config, PlanPipelineConfig
    from backend.schemas.plan_resource import PlanRuntimeOptions
    payload = default_plan_pipeline_config().model_dump(mode='json')
    payload['planner']['primary'] = {'provider': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'medium', 'codex_service_tier': 'priority'}
    original = PlanPipelineConfig.model_validate(payload)
    tuned = PlanRuntimeOptions.model_validate({'planner': {'effort': 'high'}}).apply(original)
    assert tuned.planner.primary.codex_service_tier == 'priority'
    assert tuned.planner.primary.effort == 'high'
    assert tuned.planner.fallback == original.planner.fallback
    assert original.planner.primary.effort == 'medium'


@pytest.mark.asyncio
async def test_fast_plan_api_runtime_admission_receives_same_tier(db_factory, monkeypatch):
    from contextlib import asynccontextmanager
    from backend.services import plan_agent_runner as module
    seen = []
    @asynccontextmanager
    async def admit(provider, home, model, *, service_tier='default'):
        seen.append(service_tier)
        assert service_tier == 'priority'
        yield None
    manager = MagicMock()
    manager._cloudrouter_runtime_admission = admit
    runner = PlanAgentRunner(db_factory=db_factory, instance_manager=manager)
    monkeypatch.setattr(module, '_is_cloudrouter_projection', lambda *args: True)
    async with runner._runtime_admission(provider='codex', home='/codex/one', model='gpt-6.1-sol', codex_service_tier='priority') as (_, api_route):
        assert api_route
    assert seen == ['priority']
