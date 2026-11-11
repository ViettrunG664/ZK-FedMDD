// scripts/cancel_pending_tx.js
// Force cancel pending transaction by sending 0 ETH to self with same nonce but higher gas

import pkg from "hardhat";
const { ethers } = pkg;

async function main() {
  console.log("🔄 Canceling pending transaction...");

  const [signer] = await ethers.getSigners();
  const address = await signer.getAddress();

  console.log("👤 Wallet:", address);

  // Get pending nonce
  const pendingNonce = await ethers.provider.getTransactionCount(address, 'pending');
  const latestNonce = await ethers.provider.getTransactionCount(address, 'latest');

  console.log("📊 Latest nonce:", latestNonce);
  console.log("📊 Pending nonce:", pendingNonce);

  // Get current gas price and increase by 900% (10x total) to FORCE outbid stuck tx
  const feeData = await ethers.provider.getFeeData();
  const maxPriorityFeePerGas = feeData.maxPriorityFeePerGas * 1000n / 100n; // 10x
  const maxFeePerGas = feeData.maxFeePerGas * 1000n / 100n; // 10x

  console.log("💰 Using higher gas:");
  console.log("   Max Priority Fee:", ethers.formatUnits(maxPriorityFeePerGas, "gwei"), "gwei");
  console.log("   Max Fee:", ethers.formatUnits(maxFeePerGas, "gwei"), "gwei");

  for (let nonce = latestNonce; nonce < pendingNonce; nonce++) {
    console.log(`\n🚫 Canceling transaction with nonce ${nonce}...`);

    // Send 0 ETH to self with higher gas to replace stuck tx
    const tx = await signer.sendTransaction({
      to: address,
      value: 0,
      nonce: nonce,
      maxPriorityFeePerGas: maxPriorityFeePerGas,
      maxFeePerGas: maxFeePerGas,
      gasLimit: 21000 // Basic transfer gas limit
    });

    console.log("📤 Replacement tx sent:", tx.hash);
    console.log("⏳ Waiting for confirmation...");

    const receipt = await tx.wait();
    console.log("✅ Confirmed in block:", receipt.blockNumber);
  }

  console.log("\n🎉 All stuck transactions cleared!");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("❌ Error:", error);
    process.exit(1);
  });
