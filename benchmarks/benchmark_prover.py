import os
import re
import sys
import json
import time
import secrets
import argparse
import platform
import subprocess
from pathlib import Path
from statistics import mean, stdev, median

import psutil

BASE_DIR = Path(__file__).resolve().parents[1]
CLIENT_DIR = BASE_DIR / "client"
BUILD_DIR = BASE_DIR / "circuits" / "build"
NPX = os.environ.get("NPX_PATH", "npx")
PROVER_PYTHON = sys.executable


def _tree_rss(proc):
    try:
        procs = [proc] + proc.children(recursive=True)
    except psutil.Error:
        return 0
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except psutil.Error:
            pass
    return total


def measure_single_run(identity: str, salt: str, task_id: int, rnd: int, payload: str):
    cmd = [PROVER_PYTHON, str(CLIENT_DIR / "prove.py"), "--identity", identity, "--salt", salt,
           "--task-id", str(task_id), "--round", str(rnd), "--payload", payload]
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=BASE_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ps, peak = psutil.Process(proc.pid), 0
    while proc.poll() is None:
        peak = max(peak, _tree_rss(ps))
        time.sleep(0.01)
    end = time.perf_counter()
    _, err = proc.communicate()
    if proc.returncode != 0:
        print(f"Error: {err.strip()[-500:]}")
        return None

    proof_file = CLIENT_DIR / "proof.json"
    public_file = CLIENT_DIR / "public.json"
    return {
        "latency_ms": (end - start) * 1000,
        "peak_rss_mb": peak / 1024 / 1024,
        "proof_size_bytes": proof_file.stat().st_size if proof_file.exists() else 0,
        "public_size_bytes": public_file.stat().st_size if public_file.exists() else 0,
    }


def circuit_info():
    out = subprocess.run(f'{NPX} snarkjs r1cs info "{BUILD_DIR / "hashlogin.r1cs"}"', shell=True,
                         capture_output=True, text=True, cwd=BASE_DIR).stdout
    info = {}
    for key, pattern in (("constraints", r"# of Constraints:\s*(\d+)"), ("wires", r"# of Wires:\s*(\d+)"),
                         ("private_inputs", r"# of Private Inputs:\s*(\d+)"),
                         ("public_inputs", r"# of Public Inputs:\s*(\d+)"),
                         ("outputs", r"# of Outputs:\s*(\d+)")):
        m = re.search(pattern, out)
        info[key] = int(m.group(1)) if m else None
    return info


def verify_last():
    out = subprocess.run(
        f'{NPX} snarkjs groth16 verify "{BUILD_DIR / "verification_key.json"}" '
        f'"{CLIENT_DIR / "public.json"}" "{CLIENT_DIR / "proof.json"}"',
        shell=True, capture_output=True, text=True, cwd=BASE_DIR)
    return "OK" in out.stdout


def hardware():
    return {"platform": platform.platform(), "processor": platform.processor(),
            "physical_cores": psutil.cpu_count(logical=False), "logical_cores": psutil.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
            "python": platform.python_version(),
            "node": subprocess.run("node --version", shell=True, capture_output=True, text=True).stdout.strip()}


def pct(sorted_xs, q):
    return sorted_xs[min(len(sorted_xs) - 1, int(len(sorted_xs) * q))]


def run_benchmark(num_runs: int, warmup: int, output_file: str):
    print(f"Running {warmup} warm-up + {num_runs} proof generations (submitUpdate context)...")
    results = []
    for i in range(warmup + num_runs):
        identity = f"BV.BENCH:{i:08d}"
        salt = secrets.token_hex(16)
        payload = "0x" + secrets.token_hex(32)
        tag = "warm-up" if i < warmup else f"Run {i - warmup + 1}/{num_runs}"
        print(f"{tag}...", end=" ", flush=True)
        r = measure_single_run(identity, salt, task_id=1, rnd=1 + i % 30, payload=payload)
        if r is None:
            print("failed")
            continue
        print(f"{r['latency_ms']:.0f} ms, peak {r['peak_rss_mb']:.0f} MB")
        if i >= warmup:
            results.append(r)

    if not results:
        print("No successful runs!")
        return

    lat = sorted(r["latency_ms"] for r in results)
    rss = [r["peak_rss_mb"] for r in results]
    stats = {
        "num_runs": len(results), "num_failed": num_runs - len(results), "warmup": warmup,
        "context": "submitUpdate (taskId=1, round=1..30, random 256-bit payload)",
        "hardware": hardware(),
        "circuit": circuit_info(),
        "last_proof_verifies": verify_last(),
        "latency_ms": {"mean": mean(lat), "median": median(lat), "std": stdev(lat) if len(lat) > 1 else 0.0,
                       "min": lat[0], "max": lat[-1], "p95": pct(lat, 0.95), "p99": pct(lat, 0.99)},
        "peak_rss_mb": {"mean": mean(rss), "max": max(rss)},
        "proof_size": {"proof_json_bytes": results[-1]["proof_size_bytes"],
                       "public_json_bytes": results[-1]["public_size_bytes"],
                       "calldata_bytes": 8 * 32},
        "raw_data": results,
    }

    output_path = BASE_DIR / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    L = stats["latency_ms"]
    print(f"\n{'=' * 50}\nBenchmark Results ({len(results)} runs)\n{'=' * 50}")
    print(f"Latency (ms): mean {L['mean']:.1f}  median {L['median']:.1f}  std {L['std']:.1f}  "
          f"p95 {L['p95']:.1f}  p99 {L['p99']:.1f}")
    print(f"Peak RSS (MB): mean {stats['peak_rss_mb']['mean']:.1f}  max {stats['peak_rss_mb']['max']:.1f}")
    print(f"Circuit: {stats['circuit']}")
    print(f"Proof: {stats['proof_size']}  last proof verifies: {stats['last_proof_verifies']}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark ZK proof generation")
    parser.add_argument("--runs", type=int, default=50, help="Number of measured runs")
    parser.add_argument("--warmup", type=int, default=2, help="Unmeasured warm-up runs")
    parser.add_argument("--output", type=str, default="metrics/prover_results.json", help="Output file")
    parser.add_argument("--prover-python", default=sys.executable,
                        help="Python interpreter that runs client/prove.py (default: this one)")
    args = parser.parse_args()
    PROVER_PYTHON = args.prover_python
    run_benchmark(args.runs, args.warmup, args.output)
