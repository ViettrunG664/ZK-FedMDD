
import fs from 'fs';
import pkg from 'hardhat';
const { ethers } = pkg;

async function estimateZkLoginGas() {
  console.log('Estimating zkLogin() Gas on Sepolia...\n');

  const iaAddr = fs.readFileSync('contract_address.txt', 'utf8').trim();
  const ia = await ethers.getContractAt('IdentityAccess', iaAddr);

  const proof = JSON.parse(fs.readFileSync('client/proof.json', 'utf8'));
  const pub = JSON.parse(fs.readFileSync('client/public.json', 'utf8'));

  console.log('Contract:', iaAddr);
  console.log('Proof loaded from client/proof.json');
  console.log('Public inputs loaded from client/public.json\n');

  console.log('Running estimateGas()...\n');

  try {
    const gasEstimate = await ia.zkLogin.estimateGas(
      [BigInt(proof.pi_a[0]), BigInt(proof.pi_a[1])],
      [
        [BigInt(proof.pi_b[0][1]), BigInt(proof.pi_b[0][0])],
        [BigInt(proof.pi_b[1][1]), BigInt(proof.pi_b[1][0])]
      ],
      [BigInt(proof.pi_c[0]), BigInt(proof.pi_c[1])],
      BigInt(pub[0])
    );

    const gasNum = Number(gasEstimate);

    const gasPrices = [
      { name: '1 gwei (Sepolia low)', gwei: 1 },
      { name: '5 gwei (Sepolia normal)', gwei: 5 },
      { name: '20 gwei (Sepolia high)', gwei: 20 },
      { name: '30 gwei (Mainnet normal)', gwei: 30 },
      { name: '50 gwei (Mainnet high)', gwei: 50 }
    ];

    console.log('='.repeat(60));
    console.log('zkLogin() Gas Estimation Results');
    console.log('='.repeat(60));
    console.log(`\nEstimated Gas: ${gasNum.toLocaleString()} gas\n`);

    console.log('Cost Estimates (ETH = $2000):');
    console.log('-'.repeat(60));

    for (const gp of gasPrices) {
      const costWei = BigInt(gasNum) * ethers.parseUnits(gp.gwei.toString(), 'gwei');
      const costEth = ethers.formatEther(costWei);
      const costUsd = parseFloat(costEth) * 2000;

      console.log(`${gp.name.padEnd(30)} ${costEth.padStart(18)} ETH  $${costUsd.toFixed(2)}`);
    }

    const results = {
      timestamp: new Date().toISOString(),
      network: 'sepolia',
      method: 'estimateGas',
      gas_estimate: gasNum,
      cost_scenarios: gasPrices.map(gp => {
        const costWei = BigInt(gasNum) * ethers.parseUnits(gp.gwei.toString(), 'gwei');
        const costEth = ethers.formatEther(costWei);
        return {
          scenario: gp.name,
          gas_price_gwei: gp.gwei,
          cost_eth: parseFloat(costEth),
          cost_usd_at_2000: parseFloat(costEth) * 2000
        };
      }),
      note: 'This is an estimate. zkLogin is a view function and costs 0 gas when called from frontend.'
    };

    fs.mkdirSync('metrics', { recursive: true });
    fs.writeFileSync('metrics/zklogin_gas_estimate.json', JSON.stringify(results, null, 2));

    console.log('\n' + '='.repeat(60));
    console.log('IMPORTANT NOTE:');
    console.log('='.repeat(60));
    console.log('zkLogin() is a VIEW FUNCTION.');
    console.log('When called from frontend/web3, it costs 0 gas (FREE).');
    console.log('The estimate above is only relevant if zkLogin() is called');
    console.log('inside another transaction (e.g., as part of a complex flow).\n');

    console.log('Results saved to: metrics/zklogin_gas_estimate.json');

  } catch (error) {
    console.error('Error estimating gas:', error.message);
    process.exit(1);
  }
}

estimateZkLoginGas().catch(console.error);
