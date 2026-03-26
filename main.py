import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse, os, json, warnings
warnings.filterwarnings("ignore")
import pandas as pd
from dotenv import load_dotenv
import anthropic

load_dotenv()
os.environ.setdefault("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", ""))

# RDKit tool implementations
def compute_properties(smiles: str) -> dict:
    """Compute MW, LogP, TPSA, HBD, HBA, RotBonds for a SMILES string."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, rdMolDescriptors
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return {"error": f"Invalid SMILES: {smiles}"}
        return {
            "smiles": smiles,
            "mw": round(Descriptors.MolWt(mol), 2),
            "logp": round(Descriptors.MolLogP(mol), 2),
            "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 2),
            "hbd": rdMolDescriptors.CalcNumHBD(mol),
            "hba": rdMolDescriptors.CalcNumHBA(mol),
            "rotbonds": rdMolDescriptors.CalcNumRotatableBonds(mol),
        }
    except Exception as e:
        return {"error": str(e)}


def check_lipinski(smiles: str) -> dict:
    """Check Lipinski RO5 compliance for a SMILES string."""
    props = compute_properties(smiles)
    if "error" in props:
        return props
    violations = []
    if props["mw"] > 500:
        violations.append(f"MW={props['mw']} > 500")
    if props["logp"] > 5:
        violations.append(f"LogP={props['logp']} > 5")
    if props["hbd"] > 5:
        violations.append(f"HBD={props['hbd']} > 5")
    if props["hba"] > 10:
        violations.append(f"HBA={props['hba']} > 10")
    return {
        "smiles": smiles,
        "ro5_pass": len(violations) == 0,
        "violations": violations,
        **props,
    }


def tanimoto_similarity(smiles1: str, smiles2: str) -> dict:
    """Compute Tanimoto similarity between two SMILES using ECFP4."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, DataStructs
        mol1 = Chem.MolFromSmiles(smiles1)
        mol2 = Chem.MolFromSmiles(smiles2)
        if mol1 is None or mol2 is None:
            return {"error": "Invalid SMILES"}
        fp1 = AllChem.GetMorganFingerprintAsBitVect(mol1, 2, 2048)
        fp2 = AllChem.GetMorganFingerprintAsBitVect(mol2, 2, 2048)
        sim = DataStructs.TanimotoSimilarity(fp1, fp2)
        return {"smiles1": smiles1, "smiles2": smiles2, "tanimoto": round(sim, 4)}
    except Exception as e:
        return {"error": str(e)}


# Tool definitions for Claude
TOOLS = [
    {
        "name": "compute_properties",
        "description": "Compute molecular properties (MW, LogP, TPSA, HBD, HBA, RotBonds) for a SMILES string using RDKit.",
        "input_schema": {
            "type": "object",
            "properties": {
                "smiles": {"type": "string", "description": "SMILES string of the molecule"}
            },
            "required": ["smiles"]
        }
    },
    {
        "name": "check_lipinski",
        "description": "Check if a molecule passes Lipinski Rule of Five (RO5). Returns pass/fail and any violations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "smiles": {"type": "string", "description": "SMILES string of the molecule"}
            },
            "required": ["smiles"]
        }
    },
    {
        "name": "tanimoto_similarity",
        "description": "Compute Tanimoto similarity between two molecules using ECFP4 fingerprints.",
        "input_schema": {
            "type": "object",
            "properties": {
                "smiles1": {"type": "string", "description": "SMILES of first molecule"},
                "smiles2": {"type": "string", "description": "SMILES of second molecule"}
            },
            "required": ["smiles1", "smiles2"]
        }
    }
]

TOOL_MAP = {
    "compute_properties": compute_properties,
    "check_lipinski": check_lipinski,
    "tanimoto_similarity": tanimoto_similarity,
}


def run_tool_loop(client, model, user_message, max_turns=10):
    """Run the tool-use loop until Claude gives a final text response."""
    messages = [{"role": "user", "content": user_message}]
    tool_calls_log = []
    total_input = 0
    total_output = 0

    for turn in range(max_turns):
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            tools=TOOLS,
            messages=messages,
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens

        if response.stop_reason == "end_turn":
            final_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text
            return final_text, tool_calls_log, total_input, total_output

        if response.stop_reason == "tool_use":
            # Execute each tool call
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    tool_fn = TOOL_MAP.get(tool_name)
                    if tool_fn:
                        result = tool_fn(**tool_input)
                    else:
                        result = {"error": f"Unknown tool: {tool_name}"}
                    tool_calls_log.append({"tool": tool_name, "input": tool_input, "result": result})
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            # Add assistant response and tool results to messages
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Max turns reached", tool_calls_log, total_input, total_output


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", required=True)
    parser.add_argument("--n", type=int, default=3, help="Number of compounds to analyze")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    df = pd.read_csv(args.input).head(args.n)
    client = anthropic.Anthropic()

    # Build a question that requires multiple tool calls
    compound_list = "\n".join(
        f"- {row['compound_name']}: {row['smiles']}" for _, row in df.iterrows()
    )
    user_message = (
        f"I have {len(df)} compounds. For each compound:\n"
        f"1. Compute their molecular properties\n"
        f"2. Check if they pass Lipinski RO5\n"
        f"3. Compute the Tanimoto similarity between compound 1 and compound 2\n\n"
        f"Here are the compounds:\n{compound_list}\n\n"
        f"Summarize the key findings at the end."
    )

    print(f"\nPhase 57 — RDKit as Claude Tool")
    print(f"Analyzing {len(df)} compounds with {args.model}...")
    print(f"Query: {user_message[:200]}...\n")

    final_text, tool_calls, total_input, total_output = run_tool_loop(
        client, args.model, user_message
    )

    print(f"Tool calls made: {len(tool_calls)}")
    for tc in tool_calls:
        print(f"  -> {tc['tool']}({list(tc['input'].values())[0][:30] if tc['input'] else ''})")
    print(f"\nFinal response:\n{final_text}\n")

    # Save
    output = {
        "model": args.model,
        "n_compounds": len(df),
        "tool_calls": tool_calls,
        "final_response": final_text,
        "input_tokens": total_input,
        "output_tokens": total_output,
    }
    with open(os.path.join(args.output_dir, "tool_results.json"), "w") as f:
        json.dump(output, f, indent=2)

    cost = (total_input / 1e6 * 0.80) + (total_output / 1e6 * 4.0)
    report = (
        f"Phase 57 — RDKit as Claude Tool\n"
        f"{'='*40}\n"
        f"Model:          {args.model}\n"
        f"Compounds:      {len(df)}\n"
        f"Tool calls:     {len(tool_calls)}\n"
        f"Input tokens:   {total_input}\n"
        f"Output tokens:  {total_output}\n"
        f"Est. cost:      ${cost:.4f}\n"
    )
    print(report)
    with open(os.path.join(args.output_dir, "tool_report.txt"), "w") as f:
        f.write(report)
    print(f"Saved: {args.output_dir}/tool_results.json")
    print(f"Saved: {args.output_dir}/tool_report.txt")
    print("\nDone.")


if __name__ == "__main__":
    main()
