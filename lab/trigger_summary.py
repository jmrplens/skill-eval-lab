"""Print the trigger table of a run without writing anything (usable mid-run)."""

import sys

sys.path.insert(0, "/lab")

import report  # noqa: E402
from common import read_json  # noqa: E402

suite = read_json("/inputs/suite.json")
rows = report.triggers("/out", suite, suite["skill"])
for model, v in sorted(rows.items()):
    print(f"{model:14s} recall {v['hit']}/{v['should']}  false {v['false']}/{v['should_not']}  "
          f"T14 {v['soft_loaded']}/{v['soft']}  errors {v['errors']}  misses {v['misses']}  false_on {v['false_ids']}")
    others = sorted(v["other_loads"].items(), key=lambda kv: -kv[1])
    print("   other skills loaded:", ", ".join(f"{k} x{n}" for k, n in others) or "-")
