require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

task(
  "balance", 
  "Print an account's Sepolia balance",
  async (taskArgs, hre) => {
    const addr =
      taskArgs.account || taskArgs.addr || taskArgs.a || null;

    if (!addr) {
      console.error(
        "Missing address. Usage: npx hardhat balance --account <ADDR> --network sepolia"
      );
      return;
    }

    const balWei = await hre.ethers.provider.getBalance(addr);
    console.log(
      `Balance of ${addr}:`,
      hre.ethers.formatEther(balWei),
      "SEPETH"
    );
  }
)
  .addParam("account", "The account address to query", undefined, types.string, false)
  .addOptionalParam("addr", "Alias for --account")
  .addOptionalParam("a", "Alias for --account");
task("block-number", "Prints the current block number", async (_, hre) => {
  const block = await hre.ethers.provider.getBlockNumber();
  console.log("Current block number:", block);
});

const acc = process.env.SEPOLIA_PRIVATE_KEY ? [process.env.SEPOLIA_PRIVATE_KEY] : [];

module.exports = {
  solidity: "0.8.28",

  networks: {
    localhost: { url: "http://127.0.0.1:8545" },

    sepolia: {
      url: process.env.SEPOLIA_RPC || "https://ethereum-sepolia-rpc.publicnode.com",
      chainId: 11155111,
      accounts: acc,
    },
  },

  etherscan: {
    apiKey: {
      sepolia: process.env.ETHERSCAN_KEY || "",
    },
  },
  
};