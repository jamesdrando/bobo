# bobo

bobo orchestrates big operations with agentic A.I.

This repo now includes a package-backed orchestration engine with a compatibility shim in [bobo.py](/home/drandall/bobo/bobo.py). The project now does a few core things:

- renders one `AGENTS.md` file per role from a JSON configuration
- defines a JSON-only response contract for agents with no shell access
- stores compact agent handoffs in SQLite through the harness, instead of letting agents touch the database directly
- executes registered external tools inside the workspace instead of handing raw commands back to an outer harness
- blocks high-impact tools such as dependency changes until the caller explicitly approves them
- can issue provider-agnostic LLM calls through `llm-complete` (including AWS Bedrock and OpenRouter)
- can launch a chat-first terminal interface with persistent per-workspace chat history in `.bobo/chats`
- can launch a planner-first project flow that stores project briefs and review history in `.bobo/projects`

Example flow:

```bash
uv pip install -r requirements.txt
mkdir -p .bobo
python3 bobo.py chat --team-config examples/software_team.json
python3 bobo.py chat --title "Exploration" --provider bedrock
python3 bobo.py chat --title "OpenRouter chat" --provider openrouter --model openai/gpt-4o-mini
python3 bobo.py kill-chat --session latest
python3 bobo.py render-agents --config examples/software_team.json
python3 bobo.py init-db --config examples/software_team.json
python3 bobo.py parse-agent-output --config examples/software_team.json --role Implementer --input-file examples/agent_claim_call.json
python3 bobo.py dispatch-agent-output --config examples/software_team.json --role Implementer --input-file examples/agent_claim_call.json --base-path .
python3 bobo.py llm-complete --provider bedrock --model anthropic.claude-3-5-sonnet-20240620-v1:0 --prompt "Give me a one-line status." --region us-east-1
python3 bobo.py llm-complete --provider openrouter --model openai/gpt-4o-mini --prompt "Summarize this repo."
python3 -m unittest discover -v
```

`requirements.txt` installs the full local stack through `-e .[all]`. I recommend `uv` for day-to-day setup because it is faster and already works well with this `pyproject.toml`, but plain `pip install -r requirements.txt` also works.

`llm-complete` accepts `--prompt` or structured `--messages-json/--messages-file` inputs and returns a normalized JSON result (`provider`, `model`, `message`, token usage, and raw provider response). Supported providers are currently `bedrock` and `openrouter`.

`chat` launches a Textual interface that stores each chat session under `.bobo/chats/<timestamp>_<slug>/` with:

- `session.json` for title and runtime settings
- `messages.jsonl` for chat history
- `events.jsonl` for explicit operational activity such as provider requests and failures
- `runtime.json` for the current in-flight provider process and termination state

When a team config with a `Planner` role is available, the launch screen is planner-first:

- use the arrow keys and `Enter` to choose `Create project`, `Open project`, `Open chat`, or `Quit`
- fill in the project brief fields for scope, architecture, tech stack, allowed dependencies, style, compliance, and intended end result
- click `Ask Planner` to generate the first plan in a dedicated planner session
- revise with `Request Changes`, then `Approve Plan`, then `Proceed`

Project briefs and lifecycle history are stored separately under `.bobo/projects/<timestamp>_<slug>/`.

By default bobo reads workspace settings from `.bobo/config.json`. The first supported keys are:

```json
{
  "chat": {
    "storage_dir": ".bobo/chats",
    "default_provider": "bedrock",
    "default_model": "anthropic.claude-3-5-sonnet-20240620-v1:0"
  },
  "bedrock": {
    "region": "us-east-1",
    "profile": "default"
  },
  "openrouter": {
    "base_url": "https://openrouter.ai/api/v1/chat/completions",
    "api_key_env": "OPENROUTER_API_KEY",
    "site_url": "https://your-site.example",
    "app_name": "bobo"
  }
}
```

`kill-chat --session <id|latest>` marks the session as terminated and force-kills the local provider worker process when one is active. That gives immediate local cancellation for bobo itself. The one thing it cannot guarantee is that a remote provider has already stopped server-side generation; some vendors may continue briefly after the client disconnects, but bobo will stop waiting, stop recording output, and block any future prompts for that session.

The sample team in [examples/software_team.json](/home/drandall/bobo/examples/software_team.json) is optimized for a single strong planner and inexpensive workers:

- `Planner`: creates narrow, dependency-aware packets
- `Implementer`: changes one file or a few functions
- `Verifier`: returns compact pass/fail feedback
- `Integrator`: assembles finished packets and escalates architectural issues

Roles can now declare their own preferred provider and model in `software_team.json`:

```json
{
  "name": "Planner",
  "model_tier": "frontier",
  "llm": {
    "provider": "openrouter",
    "model": "anthropic/claude-3.7-sonnet"
  }
}
```

That `llm` block is parsed into the role config, shown in the generated `AGENTS.md`, and can be merged with workspace defaults through `resolve_role_llm_settings(...)`. Omitted fields still inherit from `.bobo/config.json`.

Agent output is now harness-oriented rather than shell-oriented. Each turn must be exactly one JSON object:

```json
{"tool":"claim_handoff","args":{}}
```

or:

```json
{"tool":"handoff","args":{"run_id":"run-001","task_id":"task-1","to":"Verifier","title":"Done","sum":"Implemented the packet","files":["bobo.py"],"funcs":["render_agent_markdown"],"ok":["Writes one AGENTS.md per role."],"arts":["generated_agents/implementer/AGENTS.md"],"ts":"pass","tc":"python3 -m unittest discover -v","next":"Verify the renderer."}}
```

`dispatch-agent-output` is the execution boundary. It parses the JSON object, runs builtin tools directly, executes supported external tools inside `--base-path`, and returns `execution_status: "approval_required"` instead of executing when the current approval policy blocks the tool.
