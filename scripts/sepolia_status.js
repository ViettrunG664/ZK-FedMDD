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
