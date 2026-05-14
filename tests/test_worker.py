"""Celery Worker test

**Why**: validates Celery app configuration, task registration, and queue routing.
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def test_celery_app_configured():
    """Test that Celery app instance is created and correctly configured."""
    from ai_werewolf_core.worker import celery_app

    assert celery_app is not None
    assert celery_app.main == "werewolf_tasks"
    # Verify broker is configured
    assert celery_app.conf.broker_url is not None
    # Verify serialization config
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    # Verify reliability config
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_task_routing_configured():
    """Test task routing config -- game/agent/eval routes to different queues."""
    from ai_werewolf_core.worker import celery_app

    routes = celery_app.conf.task_routes
    assert routes is not None

    # Check three queue routing patterns
    route_keys = list(routes.keys()) if routes else []
    has_game = any("tasks.game" in k for k in route_keys)
    has_agent = any("tasks.agent" in k for k in route_keys)
    has_eval = any("tasks.eval" in k for k in route_keys)
    assert has_game, "game queue routing not configured"
    assert has_agent, "agent queue routing not configured"
    assert has_eval, "eval queue routing not configured"


def test_game_tasks_registered():
    """Test that game lifecycle tasks are imported and registered."""
    # Trigger task registration
    import ai_werewolf_core.tasks.game  # noqa: F401

    from ai_werewolf_core.worker import celery_app

    task_names = celery_app.tasks.keys()
    assert "tasks.game.resolve_night" in task_names
    assert "tasks.game.resolve_vote" in task_names
    assert "tasks.game.advance_phase" in task_names
    assert "tasks.game.evaluate_winner" in task_names


def test_agent_tasks_registered():
    """Test that agent inference tasks are registered (placeholder)."""
    import ai_werewolf_core.tasks.agent_tasks  # noqa: F401

    from ai_werewolf_core.worker import celery_app

    task_names = celery_app.tasks.keys()
    assert "agents.run_agent_decision" in task_names
    assert "agents.submit_action" in task_names


def test_eval_tasks_registered():
    """Test that evaluation tasks are registered (placeholder)."""
    import ai_werewolf_core.tasks.eval  # noqa: F401

    from ai_werewolf_core.worker import celery_app

    task_names = celery_app.tasks.keys()
    assert "tasks.eval.evaluate_game" in task_names


def test_retry_configuration():
    """Test that retry strategy config is correct."""
    from ai_werewolf_core.worker import celery_app

    assert celery_app.conf.task_default_retry_delay == 5
    assert celery_app.conf.task_max_retries == 3


def test_result_expires_configuration():
    """Test that result expiration config is correct."""
    from ai_werewolf_core.worker import celery_app

    assert celery_app.conf.result_expires == 3600