
import pkg from "hardhat";
const { ethers } = pkg;

async function main() {
  console.log("🔄 Canceling pending transaction...");

  const [signer] = await ethers.getSigners();
  const address = await signer.getAddress();

  console.log("👤 Wallet:", address);

  const pendingNonce = await ethers.provider.getTransactionCount(address, 'pending');
  const latestNonce = await ethers.provider.getTransactionCount(address, 'latest');

  console.log("📊 Latest nonce:", latestNonce);
  console.log("📊 Pending nonce:", pendingNonce);

  const feeData = await ethers.provider.getFeeData();
  const maxPriorityFeePerGas = feeData.maxPriorityFeePerGas * 1000n / 100n;
  const maxFeePerGas = feeData.maxFeePerGas * 1000n / 100n;

  console.log("💰 Using higher gas:");
  console.log("   Max Priority Fee:", ethers.formatUnits(maxPriorityFeePerGas, "gwei"), "gwei");
  console.log("   Max Fee:", ethers.formatUnits(maxFeePerGas, "gwei"), "gwei");

  for (let nonce = latestNonce; nonce < pendingNonce; nonce++) {
    console.log(`\n🚫 Canceling transaction with nonce ${nonce}...`);

    const tx = await signer.sendTransaction({
      to: address,
      value: 0,
      nonce: nonce,
      maxPriorityFeePerGas: maxPriorityFeePerGas,
      maxFeePerGas: maxFeePerGas,
      gasLimit: 21000
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
