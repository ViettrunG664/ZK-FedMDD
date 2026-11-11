// Is a gas difference due to our code or to the network's gas schedule?
// Estimates (no transaction sent) the PRE-REVISION IdentityAccess.register()
// on the contract still deployed at its old address, for comparison with the
// 121,367 gas recorded on Sepolia in metrics/gas_results_sepolia.json (Nov 2025).
// Usage: npx hardhat run benchmarks/check_gas_schedule.js --network sepolia
import pkg from "hardhat";
const { ethers } = pkg;

const OLD_IDENTITY_ACCESS = "0x2Ae8C31ed17b544d3071Ccb2bcAf74899c5d0d32";
const OLD_REGISTER_GAS_NOV2025 = 121367;

const [signer] = await ethers.getSigners();
const old = new ethers.Contract(
  OLD_IDENTITY_ACCESS, ["function register(bytes32,bytes32,bytes32,bool)"], signer);
const rnd = () => ethers.hexlify(ethers.randomBytes(32));

const now = await old.register.estimateGas(rnd(), rnd(), ethers.ZeroHash, false);
console.log("old register(), Nov 2025 receipt:", OLD_REGISTER_GAS_NOV2025);
console.log("old register(), estimate today:  ", now.toString(),
  `(×${(Number(now) / OLD_REGISTER_GAS_NOV2025).toFixed(2)})`);

let cfg;
try { cfg = await ethers.provider.send("eth_config", []); }
catch (e) { cfg = `eth_config unavailable: ${e.message.slice(0, 80)}`; }
console.log("eth_config:", JSON.stringify(cfg, null, 1).slice(0, 1500));

const b = await ethers.provider.send("eth_getBlockByNumber", ["latest", false]);
console.log("block", BigInt(b.number), "gasLimit", BigInt(b.gasLimit), "header fields:", Object.keys(b).join(","));
