"""Both processes refuse to start against an invalid template set (8.2)."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import REPO_ROOT, Settings
from app.templates.registry import TemplateLoadError
from tests.test_template_loading import write


@pytest.fixture
def broken_templates(tmp_path):
    write(
        tmp_path,
        "broken_one/sms.yaml",
        """
        slug: broken_one
        channel: sms
        options:
          name: {type: string}
        """,
    )
    write(tmp_path, "broken_one/sms.txt", "Hi {{ name }} {{ undeclared_thing }}")
    # A valid neighbour: the whole set is refused, not just the bad template.
    write(tmp_path, "fine/sms.yaml", "slug: fine\nchannel: sms\noptions: {}\n")
    write(tmp_path, "fine/sms.txt", "hello")
    return tmp_path


def test_api_refuses_to_start(broken_templates, database_url):
    app = create_app(
        Settings(DATABASE_URL=database_url, template_dir=broken_templates)  # type: ignore[call-arg]
    )
    with pytest.raises(TemplateLoadError) as info:
        with TestClient(app):
            pass
    assert "broken_one" in str(info.value) and "undeclared_thing" in str(info.value)


def run(command, template_dir, database_url):
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": database_url, "NOTIF_TEMPLATE_DIR": str(template_dir)},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_api_process_exits_naming_the_template(broken_templates, database_url):
    result = run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "0"],
        broken_templates,
        database_url,
    )
    assert result.returncode != 0
    assert "broken_one" in result.stderr and "undeclared_thing" in result.stderr


def test_worker_process_exits_naming_the_template(broken_templates, database_url):
    result = run([sys.executable, "-m", "app.delivery.worker"], broken_templates, database_url)
    assert result.returncode == 1
    assert "broken_one" in result.stderr and "undeclared_thing" in result.stderr


def test_missing_template_directory_is_refused_by_the_worker(tmp_path, database_url):
    result = run([sys.executable, "-m", "app.delivery.worker"], tmp_path / "nope", database_url)
    assert result.returncode == 1 and "does not exist" in result.stderr
