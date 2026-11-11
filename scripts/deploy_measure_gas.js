// Deploy Contracts and Measure Gas on Sepolia
// Usage: npx hardhat run scripts/deploy_measure_gas.js --network sepolia

import fs from 'fs';
import pkg from 'hardhat';
const { ethers } = pkg;

async function main() {
  console.log('Deploying to Sepolia to measure gas...\n');

  const [deployer] = await ethers.getSigners();
  console.log(`Deployer: ${deployer.address}`);
  console.log(`Balance: ${ethers.formatEther(await ethers.provider.getBalance(deployer.address))} ETH\n`);

  const results = { timestamp: new Date().toISOString(), deployments: [] };

  // Deploy Verifier
  console.log('Deploying Verifier...');
  const Verifier = await ethers.getContractFactory('Groth16Verifier');
  const verifier = await Verifier.deploy();
  await verifier.waitForDeployment();
  const vAddr = await verifier.getAddress();
  const vReceipt = await ethers.provider.getTransactionReceipt(verifier.deploymentTransaction().hash);
  const vCost = vReceipt.gasUsed * vReceipt.gasPrice;

  console.log(`  Address: ${vAddr}`);
  console.log(`  Gas: ${vReceipt.gasUsed.toString()}`);
  console.log(`  Cost: ${ethers.formatEther(vCost)} ETH\n`);

  results.deployments.push({
    contract: 'Verifier',
    address: vAddr,
    gas: Number(vReceipt.gasUsed),
    cost_eth: parseFloat(ethers.formatEther(vCost))
  });

  // Deploy IdentityAccess
  console.log('Deploying IdentityAccess...');
  const IA = await ethers.getContractFactory('IdentityAccess');
  const ia = await IA.deploy(vAddr);
  await ia.waitForDeployment();
  const iaAddr = await ia.getAddress();
  const iaReceipt = await ethers.provider.getTransactionReceipt(ia.deploymentTransaction().hash);
  const iaCost = iaReceipt.gasUsed * iaReceipt.gasPrice;

  console.log(`  Address: ${iaAddr}`);
  console.log(`  Gas: ${iaReceipt.gasUsed.toString()}`);
  console.log(`  Cost: ${ethers.formatEther(iaCost)} ETH\n`);

  results.deployments.push({
    contract: 'IdentityAccess',
    address: iaAddr,
    gas: Number(iaReceipt.gasUsed),
    cost_eth: parseFloat(ethers.formatEther(iaCost))
  });

  // Summary
  const totalGas = results.deployments.reduce((s, d) => s + d.gas, 0);
  const totalCost = results.deployments.reduce((s, d) => s + d.cost_eth, 0);
  results.summary = { total_gas: totalGas, total_cost_eth: totalCost, total_cost_usd: totalCost * 2000 };

  console.log('='.repeat(60));
  console.log(`Verifier:       ${results.deployments[0].gas.toLocaleString()} gas`);
  console.log(`IdentityAccess: ${results.deployments[1].gas.toLocaleString()} gas`);
  console.log(`Total:          ${totalGas.toLocaleString()} gas`);
  console.log(`Cost:           ${totalCost.toFixed(4)} ETH ($${(totalCost * 2000).toFixed(2)})\n`);

  fs.mkdirSync('metrics', { recursive: true });
  fs.writeFileSync('metrics/deployment_gas.json', JSON.stringify(results, null, 2));
  console.log('Saved to: metrics/deployment_gas.json');
  console.log('\nNOTE: These are NEW contracts. Your app still uses the old ones.');
}

main().catch(console.error);
