// Print the relayer address, its balance, gas price and the latest block.
// The latest block hash doubles as the public beacon for the phase-2 setup.
// Usage: npx hardhat run scripts/sepolia_status.js --network sepolia
import pkg from "hardhat";
const { ethers } = pkg;

const [signer] = await ethers.getSigners();
const block = await ethers.provider.getBlock("latest");
const fee = await ethers.provider.getFeeData();

console.log("Relayer:   ", signer.address);
console.log("Balance:   ", ethers.formatEther(await ethers.provider.getBalance(signer.address)), "ETH");
console.log("Gas price: ", ethers.formatUnits(fee.gasPrice, "gwei"), "gwei");
console.log("Block:     ", block.number);
console.log("Block hash:", block.hash);
