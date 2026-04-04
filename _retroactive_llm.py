"""Retroactive LLM validation for legacy approved rules missing approval trail."""
import sys
import json
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, ".deps314")
from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

from alpha.llm_mechanism_validator import LLMMechanismValidator

config = json.loads(Path("alpha/output/promoter_config.json").read_text(encoding="utf-8"))
validator = LLMMechanismValidator(config=config)

rules_path = Path("alpha/output/approved_rules.json")
rules = json.loads(rules_path.read_text(encoding="utf-8"))

updated = 0
for r in rules:
    if r.get("approved_by") and r.get("approved_by") != "UNKNOWN":
        continue
    rid = r.get("id", "?")[:40]
    print(f"Validating: {rid}")
    try:
        result = validator.validate(r)
        r["llm_validated"] = True
        r["llm_result"] = result.to_dict()
        r["approved_by"] = "llm_retroactive"
        r["llm_validated_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        updated += 1
        print(f"  confidence: {result.confidence}")
        print(f"  mechanism: {result.mechanism_type}")
        print(f"  display: {result.mechanism_display_name}")
        print(f"  valid: {result.is_valid}")
        essence = getattr(result, "physics_essence", "")
        if essence:
            print(f"  essence: {essence[:80]}")
    except Exception as e:
        print(f"  ERROR: {e}")

rules_path.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nDone: {updated} rules updated")
