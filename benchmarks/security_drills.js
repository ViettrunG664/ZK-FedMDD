// Security Drills - Test Attack Scenarios
//
// Tests:
// 1. Replay attack (reuse old proof)
// 2. Tampered proof
// 3. Wrong public input
//
// Usage: node benchmarks/security_drills.js

import fs from 'fs';
import pkg from 'hardhat';
const { ethers } = pkg;

async function runSecurityTests() {
  console.log('🔒 Running Security Drills...\n');

  const results = {
    timestamp: new Date().toISOString(),
    tests: []
  };

  // Get contract
  const iaAddr = fs.readFileSync('contract_address.txt', 'utf8').trim();
  const ia = await ethers.getContractAt('IdentityAccess', iaAddr);

  // Load proof files
  const proof = JSON.parse(fs.readFileSync('client/proof.json', 'utf8'));
  const pub = JSON.parse(fs.readFileSync('client/public.json', 'utf8'));

  // Test 1: Replay Attack (send same proof twice)
  console.log('Test 1: Replay Attack');
  console.log('Sending the same proof multiple times...');

  let replayResults = { success: 0, failed: 0 };
  for (let i = 0; i < 5; i++) {
    try {
      const ok = await ia.zkLogin(
        [BigInt(proof.pi_a[0]), BigInt(proof.pi_a[1])],
        [
          [BigInt(proof.pi_b[0][1]), BigInt(proof.pi_b[0][0])],
          [BigInt(proof.pi_b[1][1]), BigInt(proof.pi_b[1][0])]
        ],
        [BigInt(proof.pi_c[0]), BigInt(proof.pi_c[1])],
        BigInt(pub[0])
      );

      if (ok) {
        replayResults.success++;
        console.log(`  Attempt ${i + 1}: ✓ Accepted (VULNERABILITY!)`);
      } else {
        replayResults.failed++;
        console.log(`  Attempt ${i + 1}: ✗ Rejected (Expected)`);
      }
    } catch (e) {
      replayResults.failed++;
      console.log(`  Attempt ${i + 1}: ✗ Reverted (${e.message.slice(0, 50)})`);
    }
  }

  results.tests.push({
    name: "replay_attack",
    description: "Submit same proof multiple times",
    success_count: replayResults.success,
    reject_count: replayResults.failed,
    verdict: replayResults.success === 0 ? "PASS" : "FAIL",
    note: replayResults.success === 0 ?
      "All replay attempts rejected" :
      `${replayResults.success} replays accepted - system vulnerable!`
  });

  // Test 2: Tampered Proof (modify pi_a)
  console.log('\nTest 2: Tampered Proof');
  console.log('Modifying proof data...');

  const tamperedProof = JSON.parse(JSON.stringify(proof));
  tamperedProof.pi_a[0] = (BigInt(proof.pi_a[0]) + 1n).toString();

  let tamperResults = { accepted: false, error: null };
  try {
    const ok = await ia.zkLogin(
      [BigInt(tamperedProof.pi_a[0]), BigInt(tamperedProof.pi_a[1])],
      [
        [BigInt(tamperedProof.pi_b[0][1]), BigInt(tamperedProof.pi_b[0][0])],
        [BigInt(tamperedProof.pi_b[1][1]), BigInt(tamperedProof.pi_b[1][0])]
      ],
      [BigInt(tamperedProof.pi_c[0]), BigInt(tamperedProof.pi_c[1])],
      BigInt(pub[0])
    );

    tamperResults.accepted = ok;
    console.log(ok ?
      '  ✗ VULNERABILITY: Tampered proof accepted!' :
      '  ✓ Tampered proof rejected');
  } catch (e) {
    tamperResults.error = e.message;
    console.log(`  ✓ Tampered proof reverted: ${e.message.slice(0, 50)}`);
  }

  results.tests.push({
    name: "tampered_proof",
    description: "Submit proof with modified pi_a",
    accepted: tamperResults.accepted,
    error: tamperResults.error,
    verdict: !tamperResults.accepted ? "PASS" : "FAIL",
    note: !tamperResults.accepted ?
      "Tampered proof correctly rejected" :
      "CRITICAL: Tampered proof was accepted!"
  });

  // Test 3: Wrong Public Input
  console.log('\nTest 3: Wrong Public Input');
  console.log('Using incorrect hashId...');

  const wrongPubInput = BigInt(pub[0]) + 12345n;

  let wrongPubResults = { accepted: false, error: null };
  try {
    const ok = await ia.zkLogin(
      [BigInt(proof.pi_a[0]), BigInt(proof.pi_a[1])],
      [
        [BigInt(proof.pi_b[0][1]), BigInt(proof.pi_b[0][0])],
        [BigInt(proof.pi_b[1][1]), BigInt(proof.pi_b[1][0])]
      ],
      [BigInt(proof.pi_c[0]), BigInt(proof.pi_c[1])],
      wrongPubInput
    );

    wrongPubResults.accepted = ok;
    console.log(ok ?
      '  ✗ VULNERABILITY: Wrong public input accepted!' :
      '  ✓ Wrong public input rejected');
  } catch (e) {
    wrongPubResults.error = e.message;
    console.log(`  ✓ Wrong public input reverted: ${e.message.slice(0, 50)}`);
  }

  results.tests.push({
    name: "wrong_public_input",
    description: "Submit correct proof with wrong hashId",
    accepted: wrongPubResults.accepted,
    error: wrongPubResults.error,
    verdict: !wrongPubResults.accepted ? "PASS" : "FAIL",
    note: !wrongPubResults.accepted ?
      "Mismatch correctly detected" :
      "CRITICAL: Wrong public input accepted!"
  });

  // Save results
  fs.mkdirSync('metrics', { recursive: true });
  fs.writeFileSync('metrics/security_results.json', JSON.stringify(results, null, 2));

  // Print summary
  console.log('\n' + '='.repeat(60));
  console.log('Security Drill Results');
  console.log('='.repeat(60));

  let passed = 0;
  let failed = 0;

  for (const test of results.tests) {
    console.log(`\n${test.name}:`);
    console.log(`  Verdict: ${test.verdict}`);
    console.log(`  Note: ${test.note}`);

    if (test.verdict === "PASS") passed++;
    else failed++;
  }

  console.log(`\n${'='.repeat(60)}`);
  console.log(`Overall: ${passed} passed, ${failed} failed`);
  console.log(`Status: ${failed === 0 ? '✓ ALL TESTS PASSED' : '✗ VULNERABILITIES FOUND'}`);
  console.log(`\n✅ Results saved to: metrics/security_results.json`);
}

runSecurityTests().catch(console.error);
