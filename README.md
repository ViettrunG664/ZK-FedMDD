# ZK-FedMDD

Code and artifacts for the manuscript **"ZK-FedMDD: Zero-Knowledge Facility
Authentication for Blockchain-Coordinated Federated Depression Detection"**
(under review).

ZK-FedMDD coordinates federated training of a compact audio-visual BiLSTM
depression detector through Ethereum smart contracts. Each facility proves in
zero knowledge that it holds the secret behind a registered pseudonym, bound
to the task, round and hash of the update it submits, so that a relayer
wallet can post the update without the facility's identity appearing on
chain. The layer provides pseudonymity, not anonymity or transaction
unlinkability; the aggregator is trusted. See the paper for the threat model.

This is a research prototype. It is not a medical device and has not been
evaluated for clinical use.

## Contents

| Path | What it is |
|---|---|
| `circuits/hashlogin.circom` | Groth16 circuit: Poseidon pre-image with context binding (418 constraints, 4 public signals) |
| `circuits/build/` | R1CS, witness generator (WASM), final proving key, verification key, setup transcript |
| `contracts/` | `Verifier.sol` (generated), `IdentityAccess.sol` (registrar, zkLogin), `FederatedLearning.sol` (tasks, rounds, updates, aggregation) |
| `test/zk_fedmdd.test.js` | Ten contract tests: replay, double submission, tampered proof, swapped weights hash, cross-task reuse, sponsor binding, registrar-only registration |
| `benchmarks/` | Gas versus number of facilities, gas-schedule check, prover latency and memory, security drills |
| `metrics/` | Measured results, including the transaction hash of every Sepolia figure in the paper |
| `fl_service/` | Data loading, model, centralized / FedAvg / FedProx training, experiment drivers, statistics and table generation |
| `fl_service/results/` | Per-run logs from which every federated-learning result table is regenerated (see [Released logs](#released-logs)) |
| `client/`, `fast_prove/`, `server*.js`, `FE/`, `utils/` | End-to-end prototype: facility prover, proof service, relayer and registration servers, web front end |
| `storage/docker-compose.yml` | Optional IPFS / Filecoin nodes for off-chain weights (not exercised in the paper's experiments) |

## Deployed contracts (Ethereum Sepolia)

| Contract | Address |
|---|---|
| `Groth16Verifier` | `0x3E9F299f4063560FA2e86C34836A9a30AADd1e05` |
| `IdentityAccess` | `0x071ff5512Db7a05a5cded1d8e979049418d64A43` |
| `FederatedLearning` | `0x7AD9baB5c0F4c9d47f812710f265f60cF2E9AE11` |

Transaction hashes for the gas figures are in `metrics/gas_vs_k_sepolia.json`.

## Requirements

- Node.js 20 or later (we used 24.7.0) and npm
- Python 3.12 with the packages in `fl_service/requirements.txt` (PyTorch with CUDA for training)
- Only to rebuild the circuit: circom 2.2.1 and a copy of [circomlib](https://github.com/iden3/circomlib) in `./circomlib`

```bash
npm ci
cp .env.example .env    # only needed for Sepolia, servers or setup
```

Always pass `--network` to Hardhat: `.env` may set `HARDHAT_NETWORK=sepolia`.

## Reproducing the results

### 1. Contracts and proofs (no dataset needed)

```bash
npx hardhat test test/zk_fedmdd.test.js --network hardhat     # 10 passing
```

### 2. Trusted setup

Phase 1 is the public Hermez ceremony (`powersOfTau28_hez_final_18.ptau`,
302 MB, not stored here). Download it into `circuits/pot/` from the list
of Hermez files in the [snarkjs README](https://github.com/iden3/snarkjs).
Our copy has BLAKE2b-512
`7e6a9c2e5f05179ddfc923f38f917c9e6831d16922a902b0b4758b8e79c2ab8a81bb5f29952e16ee6c5067ed044d7857b5de120a90704c1d3b637fd94b95b13e`.

Phase 2 has one contribution followed by a random beacon taken from Sepolia
block 12048950; `circuits/build/setup_transcript.json` records both. A
single-party phase 2 is a limitation discussed in the paper. To check the key
against the circuit and phase 1:

```bash
npx snarkjs zkey verify circuits/build/hashlogin.r1cs \
    circuits/pot/powersOfTau28_hez_final_18.ptau circuits/build/hashlogin_final.zkey
```

The SHA-256 of `hashlogin_final.zkey` is
`046e2336bc02e78abd66d7210b521c192ed91fde9ffaf07ea7c25ef6d1529794`, as in
the transcript. Running `npm run build:circuits` and `npm run setup` again
produces a different key and verifier, which would then need redeploying.

### 3. Gas and prover cost

```bash
npx hardhat run benchmarks/measure_gas_vs_k.js --network hardhat   # -> metrics/gas_vs_k_hardhat.json
npx hardhat run benchmarks/measure_gas_vs_k.js --network sepolia   # uses *_address.txt and .env
python benchmarks/benchmark_prover.py --runs 50 --warmup 2 --output metrics/prover_results.json
```

The prover benchmark uses synthetic identities (`BV.BENCH:<n>`) and random
salts, never a facility secret.

### 4. Federated learning experiments

The experiments use **D-vlog** (Yoon et al., 2022), which may not be
redistributed. Request it from its authors and place the release (with
`labels.csv`) under `fl_service/data/dvlog-dataset/`. We use the dataset's
own train / validation / test folds. Facility shards are drawn from the
training fold per seed; the seeds and settings are in
`fl_service/results/r1_meta.json`.

```bash
cd fl_service
pip install -r requirements.txt
python benchmark_seeds.py --exp main fedprox noniid shift   # -> results/r1_runs.jsonl
python ablation_split.py                                    # -> results/ablation_r1.jsonl
python analyze_r1.py                                        # -> results/r1_stats.json
python paper_tables_r1.py > results/paper_tables_r1.txt    # LaTeX rows of the result tables
python paper_fig_curves_r1.py                               # learning-curve figure
```

Both drivers append one JSON line per run and skip runs already in the output
file, so an interrupted run resumes where it stopped. `--smoke` runs a short
configuration to check the setup.

### Released logs

The per-facility test labels and predicted probabilities in the run logs
derive from D-vlog and have been removed with `fl_service/strip_logs.py`.
Each facility entry instead carries the two ROC operating points the paper
reports (`spec_at_sens80`, `sens_at_spec60`); confusion-matrix counts,
thresholds, metrics and per-round histories are kept. Running `analyze_r1.py`
and `paper_tables_r1.py` on the released logs reproduces `r1_stats.json` and
`paper_tables_r1.txt` exactly, so the last three commands above work without
the dataset.

## Secrets

Nothing in this repository is a live secret. `.env` (wallet key, registrar
master key `MASTER_SALT`, facility salts, storage tokens) is never committed;
`.env.example` lists the variables. Identities in examples (`BV.HOSPA`,
`BV.HOSPB`) are fictitious.
