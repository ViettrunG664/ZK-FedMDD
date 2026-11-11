// scripts/deploy.js
// Deploy Groth16Verifier + IdentityAccess + FederatedLearning, save the
// addresses and the deployment receipts (metrics/deployment_<network>.json).
// Usage: npx hardhat run scripts/deploy.js --network sepolia
// The deployer becomes the IdentityAccess registrar (admin).

import fs from "fs";
import pkg from "hardhat";
const { ethers, network } = pkg;

// Low priority fee (TIP_GWEI, default 0.1); the ethers default tip is ~1 gwei.
async function fees() {
  if (network.name === "hardhat") return {};
  const tip = ethers.parseUnits(process.env.TIP_GWEI || "0.1", "gwei");
  const base = (await ethers.provider.getBlock("latest")).baseFeePerGas;
  return { maxPriorityFeePerGas: tip, maxFeePerGas: base * 2n + tip };
}

async function deploy(name, ...args) {
  console.log(`Deploying ${name}...`);
  const c = await (await ethers.getContractFactory(name)).deploy(...args, await fees());
  await c.waitForDeployment();
  const receipt = await c.deploymentTransaction().wait();
  const address = await c.getAddress();
  console.log(`  ${name}: ${address}  (gas ${receipt.gasUsed})`);
  return { address, tx: receipt.hash, block: receipt.blockNumber, gas: Number(receipt.gasUsed) };
}

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deployer:", deployer.address);

  const v  = await deploy("Groth16Verifier");
  const ia = await deploy("IdentityAccess", v.address);
  const fl = await deploy("FederatedLearning", ia.address, v.address);

  fs.writeFileSync("verifier_address.txt", v.address);
  fs.writeFileSync("contract_address.txt", ia.address);
  fs.writeFileSync("fl_contract_address.txt", fl.address);
  console.log("Saved to verifier_address.txt, contract_address.txt & fl_contract_address.txt");

  fs.mkdirSync("metrics", { recursive: true });
  const out = `metrics/deployment_${network.name}.json`;
  fs.writeFileSync(out, JSON.stringify({
    network: network.name,
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    date: new Date().toISOString(),
    deployer: deployer.address,
    contracts: { Groth16Verifier: v, IdentityAccess: ia, FederatedLearning: fl },
  }, null, 2));
  console.log("Saved to", out);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
