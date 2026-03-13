# Legacy Template

The source of truth for the role template now lives in [bobo.py](/home/drandall/bobo/bobo.py) as an f-string renderer.

Use [examples/software_team.json](/home/drandall/bobo/examples/software_team.json) to fill the role placeholders and generate one `AGENTS.md` per role:

```bash
python3 bobo.py render-agents --config examples/software_team.json
```

The generated prompt contract is intentionally lean:

- the planner creates narrow packets
- workers stay inside a one-file, few-function budget
- every agent turn is a single JSON tool call
- the harness executes tools on the agent's behalf
- handoffs go through SQLite via the orchestrator
- test feedback stays compact: pass/fail, command, summary, top stack frame
