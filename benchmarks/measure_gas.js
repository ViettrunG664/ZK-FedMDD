// Gas Measurement Script - Sepolia Testnet
// Measures gas consumption on Sepolia network
//
// Usage: npx hardhat run benchmarks/measure_gas.js --network sepolia

import fs from 'fs';
import pkg from 'hardhat';
const { ethers } = pkg;

const NUM_RUNS = 5;
const OUTPUT_FILE = 'metrics/gas_results_sepolia.json';

function average(arr) {
  return arr.reduce((a, b) => a + b, 0n) / BigInt(arr.length);
}

function percentile(arr, p) {
  const sorted = [...arr].sort((a, b) => Number(a - b));
  const index = Math.floor(sorted.length * p);
  return sorted[index];
}

function stdDev(arr) {
  const avg = average(arr);
  const squareDiffs = arr.map(value => {
    const diff = Number(value - avg);
    return diff * diff;
  });
  const avgSquareDiff = squareDiffs.reduce((a, b) => a + b) / squareDiffs.length;
  return Math.sqrt(avgSquareDiff);
}

async function measureGas() {
  console.log('Measuring Gas on Sepolia Testnet...\n');

  const iaAddr = fs.readFileSync('contract_address.txt', 'utf8').trim();
  const ia = await ethers.getContractAt('IdentityAccess', iaAddr);
  const [signer] = await ethers.getSigners();

  const balance = await ethers.provider.getBalance(signer.address);
  console.log(`Signer: ${signer.address}`);
  console.log(`Balance: ${ethers.formatEther(balance)} ETH\n`);

  if (balance < ethers.parseEther('0.01')) {
    console.error('ERROR: Need at least 0.01 ETH');
    process.exit(1);
  }

  const results = {};
  const registerGas = [];

  console.log(`Measuring register() - ${NUM_RUNS} runs...\n`);

  for (let i = 0; i < NUM_RUNS; i++) {
    try {
      const hashIdNum = BigInt(Date.now() + i * 1000);

      const hashId = ethers.toBeHex(hashIdNum, 32);

      console.log(`Run ${i + 1}/${NUM_RUNS}: Sending tx...`);

      const tx = await ia.connect(signer).register(hashId, ethers.ZeroHash, false, signer.address);

      console.log(`  Tx: ${tx.hash}`);
      console.log(`  Waiting...`);

      const receipt = await tx.wait();
      registerGas.push(receipt.gasUsed);

      const gasCost = receipt.gasUsed * receipt.gasPrice;
      console.log(`  Gas: ${receipt.gasUsed.toString()}`);
      console.log(`  Price: ${ethers.formatUnits(receipt.gasPrice, 'gwei')} gwei`);
      console.log(`  Cost: ${ethers.formatEther(gasCost)} ETH\n`);
    } catch (e) {
      console.log(`Run ${i + 1}: FAILED - ${e.message.slice(0, 80)}\n`);
    }
  }

  if (registerGas.length > 0) {
    results.register_patient = {
      runs: registerGas.length,
      mean: Number(average(registerGas)),
      median: Number(percentile(registerGas, 0.5)),
      std: stdDev(registerGas),
      p50: Number(percentile(registerGas, 0.50)),
      p95: Number(percentile(registerGas, 0.95)),
      min: Number(registerGas.reduce((a, b) => a < b ? a : b)),
      max: Number(registerGas.reduce((a, b) => a > b ? a : b)),
      raw: registerGas.map(g => Number(g))
    };
  }

  fs.mkdirSync('metrics', { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(results, null, 2));

  console.log('='.repeat(60));
  console.log('Gas Results (Sepolia)');
  console.log('='.repeat(60));

  for (const [fn, data] of Object.entries(results)) {
    console.log(`\n${fn}:`);
    console.log(`  Runs:   ${data.runs}`);
    console.log(`  Mean:   ${data.mean.toLocaleString()} gas`);
    console.log(`  Median: ${data.median.toLocaleString()} gas`);
    console.log(`  p50:    ${data.p50.toLocaleString()} gas`);
    console.log(`  p95:    ${data.p95.toLocaleString()} gas`);
    console.log(`  Range:  ${data.min.toLocaleString()} - ${data.max.toLocaleString()}`);
  }

  console.log(`\nSaved to: ${OUTPUT_FILE}`);
}

measureGas().catch(console.error);
