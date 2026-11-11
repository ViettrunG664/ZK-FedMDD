// Gas as a function of the number of facilities K, from transaction receipts.
//
// Registers 6 benchmark facilities, then for each K = 2..6 runs one task:
//   createTask, (K-1) × joinTask, ROUNDS × [startRound, K × submitUpdate, submitAggregation]
// Every submitUpdate carries a fresh Groth16 proof bound to (taskId, round, weightsHash).
// Facility secrets are random and kept in memory only. Weight CIDs have the
// production length (59-char CIDv1, as returned by Lighthouse).
//
// Usage:
//   npx hardhat run benchmarks/measure_gas_vs_k.js                      (in-process, deploys)
//   npx hardhat run benchmarks/measure_gas_vs_k.js --network sepolia    (uses *_address.txt)
// Env: ROUNDS (default 2), TIP_GWEI (default 0.1, Sepolia only)
// Output: metrics/gas_vs_k_<network>.json

import fs from "fs";
import crypto from "crypto";
import pkg from "hardhat";
import { proveFacility, toField, SNARK_FIELD } from "../utils/zkproof.js";
const { ethers, network } = pkg;

const K_VALUES = [2, 3, 4, 5, 6];
const ROUNDS = Number(process.env.ROUNDS || 2);
const TIP = ethers.parseUnits(process.env.TIP_GWEI || "0.1", "gwei");
const OUT = `metrics/gas_vs_k_${network.name}.json`;

const randHash = () => "0x" + crypto.randomBytes(32).toString("hex");
const randSecret = () => BigInt(randHash()) % SNARK_FIELD;
const fakeCid = () => "bafkrei" + crypto.randomBytes(40).toString("base64url").toLowerCase().replace(/[^a-z2-7]/g, "a").slice(0, 52);

const txs = [];

async function fees() {
  if (network.name === "hardhat") return {};
  const base = (await ethers.provider.getBlock("latest")).baseFeePerGas;
  return { maxPriorityFeePerGas: TIP, maxFeePerGas: base * 2n + TIP };
}

async function send(label, meta, call) {
  const tx = await call(await fees());
  const r = await tx.wait();
  const rec = { fn: label, ...meta, gas: Number(r.gasUsed), calldataBytes: (tx.data.length - 2) / 2,
                tx: r.hash, block: r.blockNumber };
  txs.push(rec);
  console.log(`  ${label.padEnd(17)} ${JSON.stringify(meta).padEnd(22)} gas ${rec.gas}`);
  return r;
}

async function contracts() {
  if (network.name === "hardhat") {
    const v = await (await ethers.getContractFactory("Groth16Verifier")).deploy();
    const ia = await (await ethers.getContractFactory("IdentityAccess")).deploy(await v.getAddress());
    const fl = await (await ethers.getContractFactory("FederatedLearning"))
      .deploy(await ia.getAddress(), await v.getAddress());
    return { ia, fl };
  }
  const read = (f) => fs.readFileSync(f, "utf8").trim();
  return {
    ia: await ethers.getContractAt("IdentityAccess", read("contract_address.txt")),
    fl: await ethers.getContractAt("FederatedLearning", read("fl_contract_address.txt")),
  };
}

const stats = (xs) => ({
  mean: Math.round(xs.reduce((a, b) => a + b, 0) / xs.length),
  min: Math.min(...xs), max: Math.max(...xs), n: xs.length,
});

async function main() {
  const [relayer] = await ethers.getSigners();
  const { ia, fl } = await contracts();
  const balance0 = await ethers.provider.getBalance(relayer.address);
  console.log(`Network ${network.name}, relayer ${relayer.address}, balance ${ethers.formatEther(balance0)} ETH`);
  console.log(`IdentityAccess ${await ia.getAddress()}\nFederatedLearning ${await fl.getAddress()}\n`);

  console.log("Registering 6 benchmark facilities...");
  const facilities = [];
  for (let i = 0; i < Math.max(...K_VALUES); i++) {
    const secret = randSecret();
    const { hashId } = await proveFacility(secret);
    await send("register", { i }, (o) => ia.register(hashId, ethers.ZeroHash, true, relayer.address, o));
    facilities.push({ secret, hashId });
  }

  for (const K of K_VALUES) {
    console.log(`\nK = ${K}`);
    const fs_ = facilities.slice(0, K);
    const taskId = (await fl.taskCount()) + 1n;
    const initialWeightsHash = randHash();
    const create = await proveFacility(fs_[0].secret, { taskId, payload: toField(initialWeightsHash) });
    await send("createTask", { K }, (o) => fl.createTask(
      { modelHash: randHash(), description: `gas-vs-K benchmark, K=${K}`, totalRounds: ROUNDS,
        minParticipants: K, initialModelCID: fakeCid(), initialWeightsHash },
      create.proof, fs_[0].hashId, o));

    for (const f of fs_.slice(1)) {
      const { proof } = await proveFacility(f.secret, { taskId });
      await send("joinTask", { K }, (o) => fl.joinTask(taskId, proof, f.hashId, o));
    }

    for (let round = 1; round <= ROUNDS; round++) {
      await send("startRound", { K, round }, (o) => fl.startRound(taskId, o));
      for (const f of fs_) {
        const wh = randHash();
        const { proof } = await proveFacility(f.secret, { taskId, round: BigInt(round), payload: toField(wh) });
        const acc = 6500 + Math.floor(Math.random() * 1000);
        const n = 140 + Math.floor(Math.random() * 40);
        await send("submitUpdate", { K, round }, (o) =>
          fl.submitUpdate(taskId, wh, fakeCid(), acc, n, proof, f.hashId, o));
      }
      await send("submitAggregation", { K, round }, (o) => fl.submitAggregation(taskId, fakeCid(), randHash(), o));
    }
  }

  const pick = (fn, K, round) => txs.filter((t) => t.fn === fn && (K === undefined || t.K === K)
    && (round === undefined || t.round === round)).map((t) => t.gas);
  const perK = {};
  for (const K of K_VALUES) {
    const rounds = [];
    for (let r = 1; r <= ROUNDS; r++) {
      rounds.push(pick("startRound", K, r)[0] + pick("submitUpdate", K, r).reduce((a, b) => a + b, 0)
        + pick("submitAggregation", K, r)[0]);
    }
    perK[K] = {
      createTask: pick("createTask", K)[0],
      joinTask: K > 1 ? stats(pick("joinTask", K)) : null,
      startRound: stats(pick("startRound", K)),
      submitUpdate: stats(pick("submitUpdate", K)),
      submitAggregation: stats(pick("submitAggregation", K)),
      perRound: stats(rounds),   // startRound + K × submitUpdate + submitAggregation
      setup: pick("createTask", K)[0] + pick("joinTask", K).reduce((a, b) => a + b, 0),
    };
  }
  const balance1 = await ethers.provider.getBalance(relayer.address);
  const submitTx = txs.find((t) => t.fn === "submitUpdate");

  fs.mkdirSync("metrics", { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify({
    network: network.name,
    // Sepolia repriced state creation on 2026-10-06 (benchmarks/check_gas_schedule.js);
    // the in-process network keeps the earlier schedule of this hardfork.
    hardfork: network.config.hardfork ?? null,
    date: new Date().toISOString(),
    relayer: relayer.address,
    identityAccess: await ia.getAddress(),
    federatedLearning: await fl.getAddress(),
    rounds: ROUNDS,
    register: stats(pick("register")),
    perK,
    submitUpdateCalldataBytes: submitTx.calldataBytes,
    proofCalldataBytes: 8 * 32,
    spentEth: ethers.formatEther(balance0 - balance1),
    txs,
  }, null, 2));

  console.log("\n K | submitUpdate | aggregation | per round");
  for (const K of K_VALUES) {
    const p = perK[K];
    console.log(` ${K} | ${p.submitUpdate.mean} | ${p.submitAggregation.mean} | ${p.perRound.mean}`);
  }
  console.log(`\nSpent ${ethers.formatEther(balance0 - balance1)} ETH → ${OUT}`);
  process.exit(0);   // snarkjs leaves curve worker threads alive
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
