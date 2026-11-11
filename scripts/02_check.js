// node scripts/02_check.js
// import dotenvVault from "dotenv-vault";
import dotenv from "dotenv";
import crypto from "crypto";
import fs from "fs";
import pkg from "hardhat";
const { ethers } = pkg;
import { buildPoseidon } from "circomlibjs";
import { getBytes, toBeHex } from "ethers";
import { keccak256, toUtf8Bytes } from "ethers";

const t0 = Date.now();
// dotenvVault.config();
dotenv.config();
const MASTER = Buffer.from(process.env.MASTER_SALT, "hex");
const pending = JSON.parse(fs.readFileSync("pending.json", "utf8"));
const db = JSON.parse(fs.readFileSync("doctors_db.json", "utf8"));

for (const row of pending) {
  if (row.status === "approved") continue;

  let matchedDoctor = null;

  for (const doctor of db) {
    const derivedSalt = crypto.createHmac("sha256", MASTER)
                              .update(doctor.cccd, "utf8")
                              .digest("hex")
                              .slice(0, 16);

    const h_cccd = crypto.createHash("sha256")
                         .update(doctor.cccd + derivedSalt, "utf8")
                         .digest("hex");
    console.log("hcccd:", h_cccd);
                         
    const specCommit = keccak256(toUtf8Bytes(doctor.department));
    console.log("specCommit:", specCommit);

    console.log("row.cccd_hash", row.cccd_hash);
    console.log("row.specCommit ", row.specCommit);
    if ("0x" + h_cccd === row.cccd_hash && specCommit === row.specCommit) {
      matchedDoctor = doctor;
      break;
    }
  }

  if (!matchedDoctor) {
    console.log(" Can not find the original citizenID:", row.cccd_hash);
    continue;
  }

  row.status = "approved";
  row.wallet = matchedDoctor.wallet || null;
  row.approvedAt = new Date().toISOString();
  console.log(" approved :", row.hashId);
}

fs.writeFileSync("pending.json", JSON.stringify(pending, null, 2));
