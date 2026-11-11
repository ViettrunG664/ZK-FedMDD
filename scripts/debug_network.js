
import pkg from "hardhat";
const { ethers } = pkg;
import fs from "fs";

async function main() {
  console.log("🔍 Debugging Network & Contract...");

  const [signer] = await ethers.getSigners();
  console.log("👤 Signer:", signer.address);

  const balance = await ethers.provider.getBalance(signer.address);
  console.log("💰 Balance:", ethers.formatEther(balance), "ETH");

  const iaAddr = fs.readFileSync("contract_address.txt", "utf8").trim();
  console.log("📝 Contract Address:", iaAddr);

  const code = await ethers.provider.getCode(iaAddr);
  if (code === "0x") {
    console.error("❌ Error: No contract code at this address!");
    process.exit(1);
  } else {
    console.log("✅ Contract code found (length: " + code.length + ")");
  }

  const ia = await ethers.getContractAt("IdentityAccess", iaAddr);

  try {
    console.log("🔄 Calling getUserByWallet...");
    const info = await ia.getUserByWallet(signer.address);
    console.log("✅ getUserByWallet success:", info);
  } catch (error) {
    console.error("❌ getUserByWallet failed:", error.message);
  }
}

main().catch(console.error);
