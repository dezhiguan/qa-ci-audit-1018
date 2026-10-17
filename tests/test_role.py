import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from role import execute


@pytest.mark.asyncio
async def test_execute_normal_pass():
    # Mock context
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    ctx.tools.call = AsyncMock()

    # Tool returns
    ctx.tools.call.side_effect = [
        {"log": "build succeeded"},
        {"diff": "+++ a/file.py\\n@@ -1 +1 @@\\n-old\\n+new"},
        [{"status": "DONE", "jobId": "DF-0010"}]
    ]

    data = {"text": "repo=qa-ci-auditor-1017b, runId=39053829490, PR #1, jobId=DF-0010"}

    result = await execute(data, ctx)

    assert result["decision"] == "PASS"
    assert "evidence_sources" in result
    assert "steps" in result
    assert "diff_summary" in result
    assert len(result["evidence_sources"]) == 3


@pytest.mark.asyncio
async def test_execute_missing_repo():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.tools.call = AsyncMock()

    data = {"text": "runId=39053829490, PR #1, jobId=DF-0010"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "repository slug" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_job_unknown_status():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "build log"},
        {"diff": "diff content"},
        [{"status": "UNKNOWN"}]
    ]

    data = {"text": "repo=qa-ci-auditor-1017b, runId=39053829490, PR #1, jobId=DF-0010"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "UNKNOWN" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_llm_returns_blocked_with_pass_in_text():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "BLOCKED", "block_reason": "User requested merge"}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "log"},
        {"diff": "diff"},
        [{"status": "FAIL"}]
    ]

    data = {"text": "repo=a, runId=1, PR #1, jobId=DF-001"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "User requested merge" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_pass_with_null_block_reason():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS", "block_reason": null}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "log"},
        {"diff": "diff"},
        [{"status": "FAIL"}]
    ]

    data = {"text": "repository=askdb, runId=7, PR#2, jobId=DF-12"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "non-string" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_pass_with_numeric_block_reason():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS", "block_reason": 123}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "log"},
        {"diff": "diff"},
        [{"status": "CANCEL"}]
    ]

    data = {"text": "repository=askdb, runId=7, PR#2, jobId=DF-12"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "non-string" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_pass_with_nonempty_string_block_reason():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS", "block_reason": "bypass approved"}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "log"},
        {"diff": "diff"},
        [{"status": "FAIL"}]
    ]

    data = {"text": "repository=askdb, runId=7, PR#2, jobId=DF-12"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "non-empty" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_missing_pr_number():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.llm.chat = AsyncMock()
    ctx.tools.call = AsyncMock()

    data = {"text": "repository=askdb, runId=7, jobId=DF-12"}

    result = await execute(data, ctx)

    assert result["decision"] == "BLOCKED"
    assert "PR number" in result["block_reason"]


@pytest.mark.asyncio
async def test_execute_fail_status_with_full_evidence():
    ctx = MagicMock()
    ctx.prompt = AsyncMock()
    ctx.prompt.return_value.compile = MagicMock(return_value="mocked_prompt")
    ctx.llm.chat = AsyncMock(return_value='{"decision": "PASS"}')
    ctx.tools.call = AsyncMock()

    ctx.tools.call.side_effect = [
        {"log": "build failed at step X"},
        {"diff": "+++ a/main.py\\n@@ -1 +1 @@\\n-old\\n+new"},
        [{"status": "FAIL", "jobId": "DF-12"}]
    ]

    data = {"text": "repository=askdb, runId=7, PR#2, jobId=DF-12"}

    result = await execute(data, ctx)

    assert result["decision"] == "PASS"
    assert "evidence_sources" in result
    assert "steps" in result
    assert "diff_summary" in result