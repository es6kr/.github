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


# Source contracts only; these do not attest actual Buildx or registry execution.
def test_platforms_input_is_optional_and_preserves_native_default(workflow_yaml):
    platform = workflow_yaml["on"]["workflow_call"]["inputs"].get("platforms")
    assert isinstance(platform, dict), "CI_PLATFORM_INPUT_MISSING"
    assert platform.get("type") == "string"
    assert platform.get("required", False) is False
    assert platform.get("default", "") == ""


def test_build_action_receives_the_explicit_platform_option(workflow_yaml):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    producers = [step for step in steps if step.get("uses") == "docker/build-push-action@v6"]
    assert len(producers) == 1
    assert producers[0].get("with", {}).get("platforms") == "${{ inputs.platforms }}"


def test_workflow_exports_builder_digest_and_image_id_separately(workflow_yaml):
    job = workflow_yaml["jobs"]["build-and-push"]
    producers = [step for step in job["steps"] if step.get("uses") == "docker/build-push-action@v6"]
    assert len(producers) == 1
    identifier = producers[0].get("id")
    assert isinstance(identifier, str) and identifier, "CI_DIGEST_PRODUCER_ID_MISSING"
    for output, field in [("image-digest", "digest"), ("image-id", "imageid")]:
        assert job.get("outputs", {}).get(output) == "${{ steps." + identifier + ".outputs." + field + " }}"
        public = workflow_yaml["on"]["workflow_call"].get("outputs", {})
        assert public.get(output, {}).get("value") == "${{ jobs.build-and-push.outputs." + output + " }}"


def test_explicit_platform_build_records_identifiers_without_claiming_registry_acceptance(workflow_yaml):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    receipts = [step for step in steps if step.get("name") == "Record build identifiers"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert "inputs.platforms != ''" in receipt["if"]
    assert "steps.build.outcome == 'success'" in receipt["if"]
    assert receipt["env"]["IMAGE_DIGEST"] == "${{ steps.build.outputs.digest }}"
    assert receipt["env"]["IMAGE_ID"] == "${{ steps.build.outputs.imageid }}"
    assert "git rev-parse HEAD" in receipt["run"]
    assert "CI_BUILD_RECEIPT=" in receipt["run"]
    assert '"oci_subject_kind":"unverified"' in receipt["run"]
    assert '"registry_verified":false' in receipt["run"]


def test_nonpublishing_oci_artifact_is_opt_in_with_compatible_default(workflow_yaml):
    setting = workflow_yaml["on"]["workflow_call"]["inputs"].get("oci-artifact-name")
    assert isinstance(setting, dict), "OCI_ARTIFACT_OPT_IN_MISSING"
    assert setting["type"] == "string"
    assert setting.get("required", False) is False
    assert setting.get("default") == ""


def test_named_oci_export_is_prepared_before_the_builder(workflow_yaml):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    prepare = [s for s in steps if s.get("name") == "Prepare optional OCI export"]
    assert len(prepare) == 1, "OCI_EXPORT_PREFLIGHT_MISSING"
    build = next(s for s in steps if s.get("id") == "build")
    assert steps.index(prepare[0]) < steps.index(build)
    assert "inputs.oci-artifact-name != ''" in prepare[0]["if"]
    for value in ["pull_request", "PUSH_IMAGE", "OCI_ARTIFACT_NAME", "mkdir"]:
        assert value in prepare[0]["run"]
    output = build["with"].get("outputs", "")
    assert "inputs.oci-artifact-name != ''" in output
    assert "type=oci,dest=" in output and "image.oci.tar" in output
    assert "runner.temp" in output and "github.run_attempt" in output
    assert "|| ''" in output
    assert build["with"]["push"] == "${{ inputs.push-image || github.event_name == 'push' }}"


def test_oci_archive_and_receipt_are_uploaded_without_overwrite(workflow_yaml):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    uploads = [s for s in steps if s.get("name") == "Upload optional OCI build evidence"]
    assert len(uploads) == 1, "OCI_ARCHIVE_UPLOAD_MISSING"
    upload = uploads[0]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert "inputs.oci-artifact-name != ''" in upload["if"]
    assert "steps.build.outcome == 'success'" in upload["if"]
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is False
    assert upload["with"]["compression-level"] == 0
    assert upload["with"]["retention-days"] == 3
    paths = upload["with"]["path"].splitlines()
    assert len(paths) == 2
    assert paths[0].endswith("/image.oci.tar")
    assert paths[1].endswith("/build-receipt.json")
    receipt = next(s for s in steps if s.get("name") == "Record build identifiers")
    assert steps.index(receipt) < steps.index(upload)
    for value in ["sha256sum", "archive_sha256", "archive_bytes", "2000000000", "build-receipt.json"]:
        assert value in receipt["run"]
    assert '"oci_subject_kind":"unverified"' in receipt["run"]
    assert '"registry_verified":false' in receipt["run"]
