import time
import re
import sys
import json, hashlib, subprocess, argparse, hmac, os
from pathlib import Path
from dotenv import load_dotenv
from poseidon_wrapper import poseidon_hash  as poseidon

load_dotenv()
FIELD_P = 21888242871839275222246405745257275088548364400416034343698204186575808495617
IDENTITY_RE = re.compile(r"^BV\.[A-Z0-9]+:[A-Z0-9]{8}$")

BASE   = Path(__file__).resolve().parents[1]

NODE   = os.environ.get('NODE_PATH', 'node')
WITGEN = BASE / "circuits/build/hashlogin_js/generate_witness.cjs"
WASM   = BASE / "circuits/build/hashlogin_js/hashlogin.wasm"
ZKEY   = BASE / "circuits/build/hashlogin_final.zkey"
ROOT   = BASE / "client"
NPX    = os.environ.get('NPX_PATH', 'npx')

ap = argparse.ArgumentParser()
ap.add_argument("--identity", required=True,
                help='Composite practitioner identity, e.g. "BV.HOSPA:A3X7K2PQ"')
ap.add_argument("--salt", required=True)
ap.add_argument("--task-id", default="0")
ap.add_argument("--round", default="0")
ap.add_argument("--payload", default="0",
                help="weightsHash (0x-hex) for submitUpdate; reduced mod p like the contract")
args = ap.parse_args()

if not IDENTITY_RE.match(args.identity):
    sys.exit("Invalid identity format. Expected BV.<NAME>:<8-alnum>, got: "
             + args.identity)

identity = args.identity
salt     = args.salt


h_cccd = hashlib.sha256((identity + salt).encode()).hexdigest()
secret = int(h_cccd, 16) % FIELD_P

hash_id = poseidon([secret])
hash_id_dec = str(hash_id)

inp_path = ROOT / "input.json"
inp_path.write_text(json.dumps({
    "secret":  str(secret),
    "taskId":  str(int(args.task_id, 0)),
    "round":   str(int(args.round, 0)),
    "payload": str(int(args.payload, 0) % FIELD_P),
}))

subprocess.check_call(
    f'"{NODE}" "{WITGEN}" "{WASM}" "{inp_path}" "{ROOT/"witness.wtns"}"',
    shell=True
)

proof_file  = ROOT / f"proof.json"
public_file = ROOT / f"public.json"

subprocess.check_call([
    NPX,"snarkjs","groth16","prove", str(ZKEY),
    str(ROOT/"witness.wtns"), str(proof_file), str(public_file)
], shell=True)


