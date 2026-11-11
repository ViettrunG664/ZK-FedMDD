// scripts/03_register.js
// UNIFIED VERSION - No spec/pending check
// node scripts/03_register.js --hashId "xxx" --role doctor
// node scripts/03_register.js --hashId "xxx" --role patient
// Only hashId goes on-chain; h = sha256(id‖salt) is the ZK witness and stays off-chain.
// The signer must be the IdentityAccess registrar (the deployer) and becomes
// the sponsoring (relayer) wallet of this hashId.

import pkg from "hardhat";
const { ethers } = pkg;
import args from "minimist";
import fs from "fs";

const argv = args(process.argv.slice(2), { string: ["hashId", "role"] });
const { hashId, role } = argv;

if (!hashId || !role) {
  throw new Error("Usage: --hashId <id> --role <doctor|patient>");
}

if (!['doctor', 'patient'].includes(role)) {
  throw new Error("Role must be 'doctor' or 'patient'");
}

async function main() {
  const iaAddr = fs.readFileSync("contract_address.txt", "utf8").trim();
  const ia = await ethers.getContractAt("IdentityAccess", iaAddr);
  const [signer] = await ethers.getSigners();

  // Convert hashId to hex
  const hashIdBigInt = BigInt(hashId);
  const hashIdHex = ethers.toBeHex(hashIdBigInt, 32);

  // Determine if doctor
  const isDoctor = (role === 'doctor');

  // Register with ZeroHash as spec (unified approach)
  const tx = await ia.connect(signer).register(
    hashIdHex,
    ethers.ZeroHash,  // No spec for both doctor and patient
    isDoctor,
    signer.address
  );
  await tx.wait();

  console.log(`✓ ${isDoctor ? "Doctor" : "Patient"} registered successfully`);
  console.log(`  Transaction: ${tx.hash}`);
  console.log(`  Signer: ${signer.address}`);
}

main().catch(console.error);
