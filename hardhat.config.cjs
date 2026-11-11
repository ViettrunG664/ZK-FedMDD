require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

/* ---------- task tuỳ chỉnh ---------- */
task(
  "balance", 
  "Print an account's Sepolia balance",
  async (taskArgs, hre) => {
    const addr =
      taskArgs.account || taskArgs.addr || taskArgs.a || null;

    if (!addr) {
      console.error(
        " Thiếu địa chỉ. Dùng: npx hardhat balance --account <ADDR> --network sepolia"
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
  .addParam("account", "The account address to query", undefined, types.string, false) // --account
  .addOptionalParam("addr", "Alias for --account")                                     // --addr
  .addOptionalParam("a", "Alias for --account");                                       // -a
// ----------------------------------------------
task("block-number", "Prints the current block number", async (_, hre) => {
  const block = await hre.ethers.provider.getBlockNumber();
  console.log("Current block number:", block);
});

const acc = process.env.SEPOLIA_PRIVATE_KEY ? [process.env.SEPOLIA_PRIVATE_KEY] : [];

/** @type import('hardhat/config').HardhatUserConfig */
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