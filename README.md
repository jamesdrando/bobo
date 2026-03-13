# bobo

bobo orchestrates big operations with agentic A.I.

This repo now includes a minimal orchestration engine in [bobo.py](/home/drandall/bobo/bobo.py) that does three things:

- renders one `AGENTS.md` file per role from a JSON configuration
- defines a JSON-only response contract for agents with no shell access
- stores compact agent handoffs in SQLite through the harness, instead of letting agents touch the database directly

Example flow:

```bash
python3 bobo.py render-agents --config examples/software_team.json
python3 bobo.py init-db --config examples/software_team.json
python3 bobo.py parse-agent-output --config examples/software_team.json --role Implementer --input-file examples/agent_claim_call.json
python3 bobo.py dispatch-agent-output --config examples/software_team.json --role Implementer --input-file examples/agent_claim_call.json
python3 -m unittest discover -v
```

The sample team in [examples/software_team.json](/home/drandall/bobo/examples/software_team.json) is optimized for a single strong planner and inexpensive workers:

- `Planner`: creates narrow, dependency-aware packets
- `Implementer`: changes one file or a few functions
- `Verifier`: returns compact pass/fail feedback
- `Integrator`: assembles finished packets and escalates architectural issues

Agent output is now harness-oriented rather than shell-oriented. Each turn must be exactly one JSON object:

```json
{"tool":"claim_handoff","args":{}}
```

or:

```json
{"tool":"handoff","args":{"run_id":"run-001","task_id":"task-1","to":"Verifier","title":"Done","sum":"Implemented the packet","files":["bobo.py"],"funcs":["render_agent_markdown"],"ok":["Writes one AGENTS.md per role."],"arts":["generated_agents/implementer/AGENTS.md"],"ts":"pass","tc":"python3 -m unittest discover -v","next":"Verify the renderer."}}
```
