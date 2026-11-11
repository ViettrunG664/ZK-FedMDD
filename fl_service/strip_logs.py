"""
Remove per-participant labels and scores from the run logs before release.

The per-facility test labels and predicted probabilities in results/*.jsonl derive
from D-vlog, which may not be redistributed. This script replaces them with the two
descriptive ROC operating points that paper_tables_r1.py reports, so every table can
still be regenerated from the released logs. Confusion-matrix counts are kept.

    python strip_logs.py results/r1_runs.jsonl released/r1_runs.jsonl
"""
import json
import sys

import numpy as np
from sklearn.metrics import roc_curve

DROP = ("labels", "probs")


def roc_operating_points(labels, probs):
    """Specificity at sensitivity >= 0.80 and sensitivity at specificity >= 0.60."""
    fpr, tpr, _ = roc_curve(labels, probs)
    return float(1 - fpr[np.argmax(tpr >= 0.80)]), float(tpr[fpr <= 0.40].max())


def strip_site(site):
    if "probs" in site:
        site["spec_at_sens80"], site["sens_at_spec60"] = roc_operating_points(site["labels"], site["probs"])
    return {k: v for k, v in site.items() if k not in DROP}


def strip_record(rec):
    rec["PerSite"] = [strip_site(s) for s in rec.get("PerSite", [])]
    return rec


def main(src, dst):
    n = 0
    with open(src, encoding="utf-8") as fin, open(dst, "w", encoding="utf-8", newline="\n") as fout:
        for line in fin:
            if line.strip():
                fout.write(json.dumps(strip_record(json.loads(line))) + "\n")
                n += 1
    print(f"{src} -> {dst}: {n} records")


if __name__ == "__main__":
    main(*sys.argv[1:3])
