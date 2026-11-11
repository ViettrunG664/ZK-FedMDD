// scripts/01_gen_secret.js
// Hospital-bound practitioner identity.
//   node scripts/01_gen_secret.js --hospital BV.HOSPA --doctor-id A3X7K2PQ --role doctor
//   node scripts/01_gen_secret.js --hospital BV.HOSPB   --doctor-id M8V2LJ5T --role patient

import dotenv from "dotenv";
import { buildPoseidon } from "circomlibjs";
import crypto from "crypto";
import args from "minimist";

dotenv.config();
const MASTER = Buffer.from(process.env.MASTER_SALT, "hex"); // 32 B

/* ─── 1. CLI ─────────────────────────────────────────────────────── */
const argv = args(process.argv.slice(2), { string: ["hospital", "doctor-id", "role"] });
const hospital = argv.hospital;
const doctorId = argv["doctor-id"];
const role     = argv.role || "doctor";

if (!hospital || !/^BV\.[A-Z0-9]+$/.test(hospital)) {
  console.error("Hospital code must match BV.<NAME> (e.g. BV.HOSPA)");
  process.exit(1);
}
if (!doctorId || !/^[A-Z0-9]{8}$/.test(doctorId)) {
  console.error("Doctor ID must be exactly 8 uppercase alphanumeric chars");
  process.exit(1);
}
if (!['doctor', 'patient'].includes(role)) {
  console.error("Role must be 'doctor' or 'patient'");
  process.exit(1);
}

// Composite practitioner identity string (never stored on-chain)
const identity = `${hospital}:${doctorId}`;

/* ─── 2. Generate derived salt ─────────────────────────────────────────────────────── */
const derivedSalt = crypto.createHmac("sha256", MASTER)
  .update(identity, "utf8")
  .digest("hex")
  .slice(0, 16); // 16 byte, 32 hex chars

/* ─── 3. Hash identity commitment ─────────────────────────────────────────────────────── */
// h_cccd is the legacy on-chain storage field name; it now holds SHA256(identity||salt).
const h_cccd = crypto.createHash("sha256")
  .update(identity + derivedSalt, "utf8")
  .digest("hex"); // 64 hex chars

/* ─── 4. Generate Poseidon hash ─────────────────────────────────────────────────────── */
const poseidon = await buildPoseidon();
const secret = poseidon([BigInt("0x" + h_cccd)]);
const hashId = poseidon.F.toString(secret);

/* ─── 5. Return result ─────────────────────────────────────────────────────── */
const result = {
  hashId,
  h_cccd: "0x" + h_cccd,
  derivedSalt,
  hospital,
  doctorId,
  identity,
  role
};

console.log(JSON.stringify(result));