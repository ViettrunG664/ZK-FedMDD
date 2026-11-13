import fs from "fs";
import crypto from "crypto";
import { execSync } from "child_process";
import * as snarkjs from "snarkjs";
import pkg from "hardhat";
const { ethers } = pkg;

const B     = "circuits/build";
const R1CS  = `${B}/hashlogin.r1cs`;
const PTAU  = "circuits/pot/powersOfTau28_hez_final_18.ptau";
const Z0    = `${B}/hashlogin_0000.zkey`;
const Z1    = `${B}/hashlogin_0001.zkey`;
const ZF    = `${B}/hashlogin_final.zkey`;
const BEACON_ITER_EXP = 10;

const log = { info: (...a) => console.log(...a), warn: console.warn, error: console.error, debug: () => {} };
const hex = (u8) => Buffer.from(u8).toString("hex");

console.log("① groth16 setup (phase-2 start)");
await snarkjs.zKey.newZKey(R1CS, PTAU, Z0, log);

console.log("② contribution");
const entropy = crypto.randomBytes(64).toString("hex");
const contribHash = await snarkjs.zKey.contribute(Z0, Z1, "ZK-FedMDD contribution 1", entropy, log);
const contribDone = await ethers.provider.getBlockNumber();

console.log("③ waiting for the next Sepolia block as beacon...");
let beaconBlock;
do {
  await new Promise((r) => setTimeout(r, 4000));
  beaconBlock = await ethers.provider.getBlock("latest");
} while (beaconBlock.number <= contribDone);
const beaconHex = beaconBlock.hash.slice(2);
await snarkjs.zKey.beacon(Z1, ZF, "Sepolia block beacon", beaconHex, BEACON_ITER_EXP, log);

console.log("④ verify final zkey against r1cs + ptau");
const ok = await snarkjs.zKey.verifyFromR1cs(R1CS, PTAU, ZF, log);
if (!ok) throw new Error("zkey verification FAILED");

console.log("⑤ export verification key + Verifier.sol");
const vk = await snarkjs.zKey.exportVerificationKey(ZF, log);
fs.writeFileSync(`${B}/verification_key.json`, JSON.stringify(vk, null, 1));
execSync(`npx snarkjs zkey export solidityverifier ${ZF} contracts/Verifier.sol`, { stdio: "inherit" });

const sha256 = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
const transcript = {
  date: new Date().toISOString(),
  circuit: "circuits/hashlogin.circom",
  nPublic: vk.nPublic,
  phase1: { file: PTAU, source: "Hermez powersOfTau28_hez_final_18 (public ceremony)" },
  phase2: {
    contributions: [{ name: "ZK-FedMDD contribution 1", hash: hex(contribHash) }],
    beacon: {
      source: "Ethereum Sepolia block hash",
      contributionFinishedAtBlock: contribDone,
      block: beaconBlock.number,
      hash: beaconBlock.hash,
      iterationsExp: BEACON_ITER_EXP,
    },
  },
  verified: ok,
  sha256: { r1cs: sha256(R1CS), finalZkey: sha256(ZF), verifier: sha256("contracts/Verifier.sol") },
};
fs.writeFileSync(`${B}/setup_transcript.json`, JSON.stringify(transcript, null, 2));
fs.rmSync(Z1);
console.log("Done →", `${B}/setup_transcript.json`);
process.exit(0);
