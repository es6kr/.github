import os
import yaml
import pytest

WORKFLOW_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github",
    "workflows",
    "reusable-ci.yml",
)


@pytest.fixture
def workflow_yaml():
    assert os.path.exists(WORKFLOW_PATH), f"Workflow file not found at {WORKFLOW_PATH}"
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_workflow_has_workflow_call_trigger(workflow_yaml):
    assert "on" in workflow_yaml
    assert "workflow_call" in workflow_yaml["on"]
    inputs = workflow_yaml["on"]["workflow_call"].get("inputs", {})
    assert "image-name" in inputs
    assert inputs["image-name"]["required"] is True
    assert "test-command" in inputs
    assert "docker-context" in inputs
    assert "runner-label" in inputs
    assert inputs["runner-label"]["default"] == "es6kr-local-linux"


def test_workflow_defines_test_and_build_jobs(workflow_yaml):
    jobs = workflow_yaml.get("jobs", {})
    assert "test" in jobs
    assert "build-and-push" in jobs
    assert jobs["build-and-push"].get("needs") == ["test"]


def test_jobs_contain_security_pre_check_step(workflow_yaml):
    for job_name in ["test", "build-and-push"]:
        job = workflow_yaml["jobs"][job_name]
        first_step = job["steps"][0]
        assert "Validate self-hosted runner route" in first_step["name"]
        assert 'HEAD_REPO' in first_step.get("env", {})
        assert 'BASE_REPO' in first_step.get("env", {})
        assert 'exit 1' in first_step["run"]


def test_runner_routing_expression(workflow_yaml):
    for job_name in ["test", "build-and-push"]:
        job = workflow_yaml["jobs"][job_name]
        runs_on = job.get("runs-on", "")
        assert "github.event_name == 'push'" in runs_on
        assert "inputs.runner-label" in runs_on
        assert "es6kr-local-linux" in str(workflow_yaml["on"]["workflow_call"]["inputs"]["runner-label"])
