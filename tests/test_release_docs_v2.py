from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load_prefetch_script() -> ModuleType:
    script = ROOT / "scripts" / "prefetch_docling_assets.py"
    spec = importlib.util.spec_from_file_location("prefetch_docling_assets", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_docling_prefetch_script_uses_bounded_offline_asset_set(tmp_path: Path) -> None:
    module = _load_prefetch_script()
    calls: dict[str, object] = {}

    def layout_downloader(**kwargs: object) -> None:
        calls["layout_download"] = kwargs

    class Tokenizer:
        @staticmethod
        def from_pretrained(model: str, *, revision: str):  # type: ignore[no-untyped-def]
            calls["model"] = model
            calls["revision"] = revision

            class Loaded:
                @staticmethod
                def save_pretrained(path: Path) -> None:
                    calls["tokenizer_path"] = path

            return Loaded()

    artifacts = tmp_path / "docling"
    tokenizer = tmp_path / "tokenizer"
    module.prefetch_assets(
        artifacts_dir=artifacts,
        tokenizer_dir=tokenizer,
        layout_downloader=layout_downloader,
        tokenizer_factory=Tokenizer,
    )

    assert calls["model"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert calls["revision"] == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    assert calls["tokenizer_path"] == tokenizer.resolve()
    assert calls["layout_download"] == {
        "repo_id": "docling-project/docling-layout-old",
        "revision": "b5b4bd59ad2b69aab715e9b1f1dfd74394c45fd4",
        "local_dir": artifacts.resolve() / "docling-project--docling-layout-old",
        "force": False,
        "progress": False,
    }


def test_documents_ci_uses_one_hash_locked_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    documents_job = workflow.split("\n  documents:\n", 1)[1].split("\n  secrets:\n", 1)[
        0
    ]

    assert "runs-on: windows-latest" in documents_job
    assert "runs-on: ubuntu-latest" not in documents_job
    assert "requirements.documents-ci.lock" in documents_job
    assert "pip install reportlab" not in documents_job
    assert "python -m pip check" in documents_job
    assert "scripts/prefetch_docling_assets.py" in documents_job

    deployment = (ROOT / "docs" / "company-deployment-v2.md").read_text(
        encoding="utf-8"
    )
    assert "--only-binary=:all:" in deployment
    assert "requirements.documents-ci.lock" in deployment
    assert "requirements.documents.lock" in deployment
    assert "Parser wheelhouse contains a non-wheel artifact" in deployment
    assert (
        '$Release = "D:\\ProjectCopilot\\releases\\REPLACE_WITH_COMMIT"' in deployment
    )
    assert (
        "--layout-model-revision b5b4bd59ad2b69aab715e9b1f1dfd74394c45fd4" in deployment
    )
    assert re.search(
        r"Run the\s+manifest block only after every selected optional bundle",
        deployment,
    )
    assert "regenerate `SHA256SUMS.json`" in deployment
    handoff = (ROOT / "docs" / "company-agent-handoff.md").read_text(encoding="utf-8")
    assert "requirements.documents.lock" in handoff
    assert "do not install the CI/test lock" in handoff

    gitleaks = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "useDefault = true" in gitleaks
    assert "1110a243fdf4706b3f48f1d95db1a4f5529b4d41" in gitleaks
    assert "commits =" not in gitleaks


def test_documents_ci_lock_pins_smoke_and_parser_dependencies() -> None:
    lock = (ROOT / "requirements.documents-ci.lock").read_text(encoding="utf-8")

    for requirement in (
        "docling==2.113.0",
        "docling-haystack==1.2.0",
        "pytest==9.0.3",
        "reportlab==5.0.0",
    ):
        assert requirement in lock
    assert lock.count("--hash=sha256:") > 100


def _locked_versions(path: str) -> dict[str, str]:
    content = (ROOT / path).read_text(encoding="utf-8")
    requirement = re.compile(r"^([a-z0-9][a-z0-9._-]*)==([^ \\\r\n]+)", re.MULTILINE)
    return dict(requirement.findall(content))


def test_documents_ci_lock_keeps_every_production_parser_version() -> None:
    runtime_versions = _locked_versions("requirements.runtime.lock")
    production_versions = _locked_versions("requirements.documents.lock")
    ci_versions = _locked_versions("requirements.documents-ci.lock")

    assert production_versions
    assert {
        name: (version, production_versions.get(name))
        for name, version in runtime_versions.items()
        if name in production_versions and production_versions[name] != version
    } == {}
    assert {
        name: (version, ci_versions.get(name))
        for name, version in production_versions.items()
        if ci_versions.get(name) != version
    } == {}
