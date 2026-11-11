// scripts/zklogin.js
// Hospital-bound practitioner login.
// node scripts/zklogin.js --hospital BV.HOSPA --doctor-id A3X7K2PQ --salt <derived-salt> --role doctor

import fs from "fs";
import args from "minimist";
import pkg from "hardhat";
const { ethers } = pkg;
import crypto from "crypto";
import { buildPoseidon } from "circomlibjs";

const t0 = Date.now();

// Read proof
const proof = JSON.parse(fs.readFileSync("client/proof.json", "utf8"));
const pub = JSON.parse(fs.readFileSync("client/public.json", "utf8"));

// Get info from CLI
const argv = args(process.argv.slice(2), { string: ["hospital", "doctor-id", "salt", "role"] });
const hospital      = argv.hospital;
const doctorId      = argv["doctor-id"];
const derivedSalt   = argv.salt;
const requestedRole = argv.role; // Role user is trying to login as

if (!hospital || !/^BV\.[A-Z0-9]+$/.test(hospital)) {
  console.error("Usage: --hospital <BV.NAME> --doctor-id <8-alnum> --salt <salt> --role <doctor|patient>");
  process.exit(1);
}
if (!doctorId || !/^[A-Z0-9]{8}$/.test(doctorId)) {
  console.error("Doctor ID must be exactly 8 uppercase alphanumeric chars");
  process.exit(1);
}
if (!derivedSalt || !requestedRole) {
  console.error("Usage: --hospital <BV.NAME> --doctor-id <8-alnum> --salt <salt> --role <doctor|patient>");
  process.exit(1);
}

if (!['doctor', 'patient'].includes(requestedRole)) {
  console.error("Role must be 'doctor' or 'patient'");
  process.exit(1);
}

// Compose identity string — must match gen_secret / prove.py exactly.
const identity = `${hospital}:${doctorId}`;

// Compute hashId
const poseidon = await buildPoseidon();
// h_cccd retains its legacy variable name; it now holds SHA256(identity||salt).
const h_cccd = crypto.createHash("sha256").update(identity + derivedSalt, "utf8").digest("hex");
const secret = poseidon([BigInt("0x" + h_cccd)]);
const hashId = poseidon.F.toString(secret);

// Call contract
const iaAddr = fs.readFileSync("contract_address.txt", "utf8").trim();
const ia = await ethers.getContractAt("IdentityAccess", iaAddr);
const [signer] = await ethers.getSigners();

// Multi-identity support: Get info by specific hashId
const hashIdHex = ethers.toBeHex(BigInt(hashId), 32);
let userInfo;
try {
  userInfo = await ia.getUserByHashId(hashIdHex);
} catch (e) {
  console.log("❌ HashId not found in wallet (or not registered):", hashId);
  // Fallback to check if wallet has ANY registration (for debugging)
  try {
    const fallbackInfo = await ia.getUserByWallet(signer.address);
    console.log("⚠️  Found OTHER identity for this wallet (first registered):", fallbackInfo);
  } catch (e2) {
    console.log("❌ No identity found for this wallet.");
  }
  process.exit(1);
}

// Extract user info from blockchain
const departmentHash = userInfo[1];
const isDoctor = userInfo[3];

// IMPORTANT: Verify that requested role matches registered role
const registeredRole = isDoctor ? 'doctor' : 'patient';
if (requestedRole !== registeredRole) {
  console.log(`❌ Role mismatch! Registered as '${registeredRole}' but trying to login as '${requestedRole}'`);
  const result = {
    ok: false,
    error: `Role mismatch. This account is registered as ${registeredRole}, not ${requestedRole}.`,
    hashIdHex: "0x0",
    isDoctor: false
  };
  console.log(JSON.stringify(result));
  process.exit(1);
}

// Call zkLogin function to verify
const ok = await ia.zkLogin(
  [BigInt(proof.pi_a[0]), BigInt(proof.pi_a[1])],
  [
    [BigInt(proof.pi_b[0][1]), BigInt(proof.pi_b[0][0])], // <-- swap
    [BigInt(proof.pi_b[1][1]), BigInt(proof.pi_b[1][0])]  // <-- swap
  ],
  [BigInt(proof.pi_c[0]), BigInt(proof.pi_c[1])],
  BigInt(pub[0])
);

console.log("✅ pub[0] from file:", pub[0]);
console.log("✅ hashId recomputed:", hashId);

if (!ok) {
  console.log("❌ Login failed: Invalid ZK proof.");
  const result = {
    ok: false,
    error: "Invalid ZK proof",
    hashIdHex: "0x0",
    isDoctor: false
  };
  console.log(JSON.stringify(result));
  process.exit(1);
} else {
  console.log(`✅ Login successful as ${registeredRole}!`);
  console.log("Hash ID (Decimal):", hashId);

  const result = {
    ok: true,
    hashIdHex,
    departmentHash,
    isDoctor,
    role: registeredRole
  };
  console.log(JSON.stringify(result));
}