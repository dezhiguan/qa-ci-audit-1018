import json
import re
from typing import Dict, Any, List, Optional


async def execute(data: dict, ctx) -> dict:
    text = data.get("text", "")
    if not isinstance(text, str):
        return {"decision": "BLOCKED", "block_reason": "input.text must be a string"}

    # Extract identifiers with strict ASCII repo slug: [a-z][a-z0-9-]{0,62}
    # Match labels case-insensitively; Chinese chars act as boundaries, not part of slug
    repo_match = re.search(r'(?:repository|repo|仓库)[\s:=：、，;]*([a-z][a-z0-9-]{0,62})', text, re.IGNORECASE)
    if not repo_match:
        repo_match = re.search(r'(?:repository|repo|仓库)([a-z][a-z0-9-]{0,62})', text, re.IGNORECASE)
    runId_match = re.search(r'(?:runId|CI\s+runId|CI\s+运行ID|CI运行)[\s:=：、，;]*(\d+)', text, re.IGNORECASE)
    if not runId_match:
        runId_match = re.search(r'(?:runId|CI\s+runId|CI\s+运行ID|CI运行)(\d+)', text, re.IGNORECASE)
    pr_match = re.search(r'(?:PR|pr|Pull\s+Request|拉取请求)[\s#：、，;]*(\d+)', text, re.IGNORECASE)
    if not pr_match:
        pr_match = re.search(r'(?:PR|pr|Pull\s+Request|拉取请求)#?(\d+)', text, re.IGNORECASE)
    jobId_match = re.search(r'(?:jobId|任务|job|DF-)[\s:=：、，;]*(DF-\d+)', text, re.IGNORECASE)
    if not jobId_match:
        jobId_match = re.search(r'(?:jobId|任务|job|DF-)(DF-\d+)', text, re.IGNORECASE)

    repo = repo_match.group(1).strip() if repo_match else None
    runId = runId_match.group(1).strip() if runId_match else None
    pr_number = int(pr_match.group(1)) if pr_match and pr_match.group(1).isdigit() else None
    jobId = jobId_match.group(1).strip() if jobId_match else None

    # Validate required fields
    missing = []
    if not repo:
        missing.append("repository slug")
    if not runId:
        missing.append("CI runId")
    if not pr_number or pr_number <= 0:
        missing.append("PR number (positive integer)")
    if not jobId:
        missing.append("jobId (e.g., DF-XXXX)")
    if missing:
        return {
            "decision": "BLOCKED",
            "block_reason": f"Missing required identifiers: {', '.join(missing)}"
        }

    # Call tools
    try:
        log_res = await ctx.tools.call("ci.log.fetch", repo=repo, runId=runId)
        if not isinstance(log_res, dict) or "log" not in log_res or not isinstance(log_res["log"], str) or log_res.get("logsUnavailable") == True:
            return {"decision": "BLOCKED", "block_reason": "ci.log.fetch returned no usable log or logsUnavailable=true"}
        if not log_res["log"].strip():
            return {"decision": "BLOCKED", "block_reason": "ci.log.fetch returned empty log text"}

        diff_res = await ctx.tools.call("git.pr.diff", repo=repo, number=pr_number)
        if not isinstance(diff_res, dict) or "diff" not in diff_res or not isinstance(diff_res["diff"], str) or not diff_res["diff"].strip():
            return {"decision": "BLOCKED", "block_reason": "git.pr.diff returned no usable diff"}

        job_res = await ctx.tools.call("devflow.job.read", jobId=jobId)
        if not isinstance(job_res, list):
            return {"decision": "BLOCKED", "block_reason": "devflow.job.read did not return list"}
        if not job_res:
            return {"decision": "BLOCKED", "block_reason": "devflow.job.read returned empty list"}

        # Check for UNKNOWN state in any job record: status/state must be non-empty str, not UNKNOWN
        has_unknown_or_invalid = False
        for record in job_res:
            status = record.get("status")
            state = record.get("state")
            # At least one of status/state must be non-empty string and not UNKNOWN
            if (isinstance(status, str) and status.strip() and status.strip() != "UNKNOWN") or \
               (isinstance(state, str) and state.strip() and state.strip() != "UNKNOWN"):
                continue
            # If neither qualifies, mark invalid
            if not (isinstance(status, str) and status.strip()) and not (isinstance(state, str) and state.strip()):
                has_unknown_or_invalid = True
                break
            if (isinstance(status, str) and status.strip() == "UNKNOWN") or \
               (isinstance(state, str) and state.strip() == "UNKNOWN"):
                has_unknown_or_invalid = True
                break
        if has_unknown_or_invalid:
            return {"decision": "BLOCKED", "block_reason": "job contains UNKNOWN, missing, or blank status/state"}

        # Assemble evidence
        evidence_sources = [
            {"type": "ci.log.fetch", "repo": repo, "runId": runId},
            {"type": "git.pr.diff", "repo": repo, "number": pr_number},
            {"type": "devflow.job.read", "jobId": jobId}
        ]

        steps = [
            "Extracted repo, runId, PR number, and jobId from input text",
            "Fetched CI log",
            "Fetched PR diff",
            "Fetched devflow job records",
            "Validated all evidence is non-empty and job status is not UNKNOWN"
        ]

        # Build diff_summary
        diff_lines = diff_res["diff"].splitlines()
        added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
        diff_summary = f"{len(diff_lines)} lines total; +{added} / -{removed} changes"

        # Compile prompt with field descriptions only (no real data in system)
        prompt = await ctx.prompt("role")
        compiled_prompt = prompt.compile(input="Natural language input containing repo, runId, PR number, jobId", evidence="JSON object with keys: ci_log (str), pr_diff (str), job_records (list)")

        # Send to LLM: real data goes ONLY in user message
        llm_input = [{"role": "system", "content": compiled_prompt}, {"role": "user", "content": json.dumps({"input": text, "evidence": {
            "ci_log": log_res["log"],
            "pr_diff": diff_res["diff"],
            "job_records": job_res
        }}, ensure_ascii=False)}]
        llm_output = await ctx.llm.chat(llm_input, prompt=prompt)

        # Parse decision
        try:
            decision_obj = json.loads(llm_output.strip())
            decision = decision_obj.get("decision")
            if decision not in ["PASS", "BLOCKED"]:
                return {"decision": "BLOCKED", "block_reason": "LLM output decision must be 'PASS' or 'BLOCKED'"}
            # Enforce block_reason typing & presence
            block_reason = decision_obj.get("block_reason")
            if decision == "PASS":
                if block_reason is not None:
                    if not isinstance(block_reason, str) or block_reason.strip():
                        return {"decision": "BLOCKED", "block_reason": "LLM returned PASS with non-string or non-empty block_reason"}
                # Also BLOCKED if block_reason is null (None is already handled above; JSON null becomes None in Python)
                # But per spec: 'PASS' must omit block_reason OR have empty string — no null/123/[]/{}
                # So explicit check for None is redundant (covered), but we *must* reject falsy non-empty-string like 0, [], {}, etc.
                # Already covered by `not isinstance(..., str)` — keep as-is.
            elif decision == "BLOCKED":
                if not isinstance(block_reason, str) or not block_reason.strip():
                    return {"decision": "BLOCKED", "block_reason": "LLM returned BLOCKED without non-empty string block_reason"}
            result = {"decision": decision}
            if decision == "PASS":
                result.update({
                    "evidence_sources": evidence_sources,
                    "steps": steps,
                    "diff_summary": diff_summary
                })
            else:
                result["block_reason"] = block_reason
            return result
        except (json.JSONDecodeError, ValueError, AttributeError):
            return {"decision": "BLOCKED", "block_reason": "LLM output is not valid JSON or lacks 'decision' field"}

    except Exception as e:
        return {"decision": "BLOCKED", "block_reason": f"Tool call or processing error: {str(e)}"}