import os
import hashlib
import json
import csv
import re
from pathlib import Path
import subprocess
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
    identifier = prepare[0].get("id")
    assert isinstance(identifier, str) and identifier, "OCI_EXPORT_OUTPUT_PRODUCER_MISSING"
    assert output == "${{ steps." + identifier + ".outputs.build_output || '' }}"
    assert "runner.temp" in prepare[0]["env"]["OCI_DIRECTORY"]
    assert "github.run_attempt" in prepare[0]["env"]["OCI_DIRECTORY"]
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


def run_workflow_shell(script, directory, **values):
    """Exercise the actual shell recipe, never Buildx or a remote artifact API."""
    bash = ("C:/Program Files/Git/usr/bin/bash.exe" if os.name == "nt" else "/bin/bash")
    assert Path(bash).is_file(), "existing Bash runtime is required"
    env = {key: os.environ[key] for key in (
        "PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP",
    ) if key in os.environ}
    if os.name == "nt":
        # Git Bash needs its own executable paths; native Windows PATH is not shell PATH.
        env["PATH"] = "/mingw64/bin:/usr/bin:/bin"
        for key in ("OCI_DIRECTORY", "GITHUB_OUTPUT"):
            if key in values:
                values[key] = Path(values[key]).as_posix()
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", **values)
    return subprocess.run([bash, "--noprofile", "--norc", "-c", script],
                          cwd=directory, env=env, capture_output=True, text=True, timeout=10)


def test_oci_preflight_recipe_preserves_existing_and_rejects_publishing(workflow_yaml, tmp_path):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    prepare = next(s for s in steps if s.get("name") == "Prepare optional OCI export")
    target = tmp_path / "synthetic-export"
    values = {"OCI_ARTIFACT_NAME": "fixture-oci", "OCI_DIRECTORY": str(target),
              "BUILD_EVENT": "pull_request", "PUSH_IMAGE": "false",
              "GITHUB_OUTPUT": str(tmp_path / "github-output.txt")}
    allowed = run_workflow_shell(prepare["run"], tmp_path, **values)
    assert allowed.returncode == 0, allowed.stderr
    assert target.is_dir()
    marker = target / "preserve.txt"
    marker.write_text("synthetic pre-existing bytes", encoding="utf-8")
    before = marker.read_bytes()
    assert run_workflow_shell(prepare["run"], tmp_path, **values).returncode != 0
    assert marker.read_bytes() == before
    for index, changes in enumerate((
        {"OCI_ARTIFACT_NAME": "../escape"},
        {"BUILD_EVENT": "push"},
        {"PUSH_IMAGE": "true"},
    )):
        refused_target = tmp_path / ("refused-" + str(index))
        actual = {**values, "OCI_DIRECTORY": str(refused_target), **changes}
        refused = run_workflow_shell(prepare["run"], tmp_path, **actual)
        assert refused.returncode != 0
        assert not refused_target.exists()


def test_oci_receipt_recipe_hashes_fixture_bytes_and_refuses_missing_export(workflow_yaml, tmp_path):
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    receipt = next(s for s in steps if s.get("name") == "Record build identifiers")
    # A synthetic byte fixture exercises receipt transport, not OCI validity.
    archive = tmp_path / "image.oci.tar"
    payload = b"synthetic non-OCI receipt fixture\n"
    archive.write_bytes(payload)
    root = Path(WORKFLOW_PATH).resolve().parents[2]
    values = {"OCI_ARTIFACT_NAME": "fixture-oci", "OCI_DIRECTORY": str(tmp_path),
              "IMAGE_ID": "", "IMAGE_DIGEST": "", "BUILD_RUN_ID": "123", "BUILD_RUN_ATTEMPT": "1"}
    accepted = run_workflow_shell(receipt["run"], root, **values)
    assert accepted.returncode == 0, accepted.stderr
    observed = json.loads((tmp_path / "build-receipt.json").read_text(encoding="utf-8"))
    assert observed["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    assert observed["archive_bytes"] == len(payload)
    assert observed["run_id"] == "123" and observed["run_attempt"] == "1"
    assert observed["oci_subject_kind"] == "unverified"
    assert observed["registry_verified"] is False
    missing = tmp_path / "missing-export"
    missing.mkdir()
    refused = run_workflow_shell(receipt["run"], root, **{**values, "OCI_DIRECTORY": str(missing)})
    assert refused.returncode != 0
    assert not (missing / "build-receipt.json").exists()


@pytest.mark.parametrize("directory_name", ["runner", "runner,temp"])
def test_oci_preflight_exports_csv_destination(workflow_yaml, tmp_path, directory_name):
    """Exercise the actual prepare recipe, not GitHub's expression engine or Buildx."""
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    prepare = next(s for s in steps if s.get("name") == "Prepare optional OCI export")
    directory = tmp_path / directory_name
    output_file = tmp_path / "github-output.txt"
    result = run_workflow_shell(
        prepare["run"], tmp_path, OCI_ARTIFACT_NAME="fixture-oci",
        OCI_DIRECTORY=directory.as_posix(), GITHUB_OUTPUT=output_file.as_posix(),
        BUILD_EVENT="pull_request", PUSH_IMAGE="false",
    )
    assert result.returncode == 0, result.stderr
    assert directory.is_dir()
    assert output_file.is_file(), "OCI_EXPORT_OUTPUT_MISSING"
    output_lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(output_lines) == 1
    key, value = output_lines[0].split("=", 1)
    assert key == "build_output"
    assert next(csv.reader([value])) == ["type=oci", f"dest={directory.as_posix()}/image.oci.tar"]


@pytest.mark.parametrize("directory_name,allowed", [
    ('runner,"quoted"', True),
    ("runner\nrejected", False),
    ("runner\rrejected", False),
])
def test_oci_csv_writer_quotes_fields_and_refuses_output_injection(
    workflow_yaml, tmp_path, directory_name, allowed,
):
    """Use the real Bash writer alone: quotes/newlines are not Windows directory names."""
    steps = workflow_yaml["jobs"]["build-and-push"]["steps"]
    prepare = next(s for s in steps if s.get("name") == "Prepare optional OCI export")
    writer = re.search(r"(?ms)^record_oci_export_output\(\) \{\n.*?^\}", prepare["run"])
    assert writer is not None, "OCI_ENCODER_RECIPE_MISSING"
    directory = (tmp_path / directory_name).as_posix()
    output_file = tmp_path / "github-output.txt"
    result = run_workflow_shell(
        "set -euo pipefail\n" + writer.group(0) + "\nrecord_oci_export_output\n",
        tmp_path, OCI_DIRECTORY=directory, GITHUB_OUTPUT=output_file.as_posix(),
    )
    if not allowed:
        assert result.returncode != 0
        assert not output_file.exists()
        return
    assert result.returncode == 0, result.stderr
    output_lines = output_file.read_text(encoding="utf-8").splitlines()
    assert len(output_lines) == 1
    key, value = output_lines[0].split("=", 1)
    assert key == "build_output"
    assert next(csv.reader([value])) == ["type=oci", f"dest={directory}/image.oci.tar"]
