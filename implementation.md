# Phase 57 — RDKit as Claude Tool (SMILES → Properties on Demand)

**Version:** 1.1 | **Tier:** Standard | **Date:** 2026-03-26

## Goal
Demonstrate Claude using RDKit functions as tools in a tool-use loop.
Claude decides when to call tools, executes them, and synthesizes results into a final answer.

CLI: `python main.py --input data/compounds.csv --n 3 --model claude-haiku-4-5-20251001`

Outputs: tool_results.json, tool_report.txt

## Logic
- Define 3 RDKit tools: `compute_properties`, `check_lipinski`, `tanimoto_similarity`
- Build a multi-part question requiring all three tools
- Run tool-use loop: send tools → Claude decides which to call → execute → loop until end_turn
- Collect tool_calls_log and final response

## Key Concepts
- Tool definitions: `name`, `description`, `input_schema` (JSON Schema)
- Claude returns `stop_reason="tool_use"` when it wants to call a tool
- Tool results sent back as `{"type": "tool_result", "tool_use_id": id, "content": json_string}`
- Loop continues until `stop_reason="end_turn"`
- Each iteration adds assistant response + tool results to messages list

## Verification Checklist
- [x] 3 tool definitions accepted by Claude (compute_properties, check_lipinski, tanimoto_similarity)
- [x] Claude autonomously chose which tools to call (7 calls across 3 compounds)
- [x] Tool-use loop terminates correctly on `stop_reason="end_turn"`
- [x] All tool results are valid JSON with correct RDKit outputs
- [x] Final synthesis response references all tool results

## Risks (resolved)
- Infinite loop risk if Claude never returns end_turn — mitigated by `max_turns=10` cap
- Claude may call tools with invalid SMILES — RDKit gracefully returns error dict
- Token cost grows per loop iteration (context accumulates) — manageable for 3 compounds

## Results
| Metric | Value |
|--------|-------|
| Compounds analyzed | 3 |
| Tool calls made | 7 |
| Input tokens | 3105 |
| Output tokens | 1187 |
| Est. cost | $0.0072 |

Tool call breakdown: 3× compute_properties, 3× check_lipinski, 1× tanimoto_similarity.
All 3 compounds passed RO5; Tanimoto benz_001_F vs benz_002_Cl = 0.66.
