import os
import subprocess
import sys
from pathlib import Path

import pytest

from project_copilot.knowledge import KnowledgeIndexError, LocalKnowledgeIndex


def test_local_knowledge_query_returns_cited_evidence(tmp_path: Path) -> None:
    (tmp_path / "control.md").write_text(
        "冷冻水供水温度设定值为 7 摄氏度，回水温度通常为 12 摄氏度。",
        encoding="utf-8",
    )
    (tmp_path / "safety.md").write_text(
        "维护前必须关闭机组，并执行锁定挂牌程序。",
        encoding="utf-8",
    )
    index = LocalKnowledgeIndex.from_directory(tmp_path)

    result = index.query("供水温度设定值是多少？")

    assert result.refused is False
    assert "7 摄氏度" in result.answer
    assert result.citations[0].source == "control.md"
    assert "冷冻水供水温度" in result.citations[0].excerpt


def test_local_knowledge_query_refuses_when_no_evidence_exists(tmp_path: Path) -> None:
    (tmp_path / "control.md").write_text(
        "冷冻水供水温度设定值为 7 摄氏度。",
        encoding="utf-8",
    )
    index = LocalKnowledgeIndex.from_directory(tmp_path)

    result = index.query("火星轨道速度是多少？")

    assert result.refused is True
    assert result.citations == ()
    assert "没有找到" in result.answer


def test_knowledge_module_forces_haystack_telemetry_off() -> None:
    environment = os.environ.copy()
    environment["HAYSTACK_TELEMETRY_ENABLED"] = "True"

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; import project_copilot.knowledge; "
                "print(os.environ['HAYSTACK_TELEMETRY_ENABLED'])"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.stdout.strip().endswith("False")


def test_knowledge_index_rejects_oversized_source_file(tmp_path: Path) -> None:
    (tmp_path / "large.md").write_bytes(b"x" * 2_000_001)

    with pytest.raises(KnowledgeIndexError, match="larger than"):
        LocalKnowledgeIndex.from_directory(tmp_path)


def test_knowledge_index_rejects_symlinked_source(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.md"
    outside.write_text("private material", encoding="utf-8")
    linked = tmp_path / "linked.md"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows environment")

    with pytest.raises(KnowledgeIndexError, match="symbolic link"):
        LocalKnowledgeIndex.from_directory(tmp_path)
