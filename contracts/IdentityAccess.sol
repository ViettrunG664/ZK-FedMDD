// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./Verifier.sol";

contract IdentityAccess {
    /* ═════════ Struct ═════════ */
    // Only the Poseidon commitment is kept on-chain. The digest
    // h = sha256(id‖salt) is the witness of the ZK proof and stays off-chain.
    struct User {
        bool     isDoctor;     // FL: "authorized facility" flag
        bytes32  specCommit;   // hash chuyên khoa (chỉ bác sĩ dùng)
        bytes32  hashId;       // Poseidon(h mod p)
    }

    /* ═════════ Events ═════════ */
    event Registered(bytes32 indexed hashId, address indexed wallet, bool isDoctor);

    /* ═════════ Storage ═════════ */
    mapping(bytes32 => User)           public users;    // key = hashId
    // Multi-identity support: one wallet can own multiple hashIds
    mapping(address => mapping(bytes32 => bool)) public isHashIdOfWallet;
    mapping(address => bytes32[])      public hashIdsOf; // For enumeration
    address public admin;
    Groth16Verifier public verifier;

    constructor(address _verifier) {
        admin    = msg.sender;
        verifier = Groth16Verifier(_verifier);
    }
    modifier onlyAdmin() { require(msg.sender == admin, "only-admin"); _; }

    /* ═════════ 1) Đăng ký — chỉ registrar (admin) ═════════ */
    /// @param _hashId    Poseidon( sha256(id‖salt) mod p )
    /// @param _spec      bytes32 commit chuyên khoa (chỉ khi bác sĩ)
    /// @param _asDoctor  true → bác sĩ / cơ sở được phép tham gia FL
    /// @param _wallet    ví relayer được nộp thay cho hashId này
    function register(
        bytes32 _hashId,
        bytes32 _spec,
        bool    _asDoctor,
        address _wallet
    ) external onlyAdmin {
        require(_hashId != 0, "zero-id");
        require(users[_hashId].hashId == 0, "exists");
        users[_hashId] = User(_asDoctor, _spec, _hashId);
        isHashIdOfWallet[_wallet][_hashId] = true;
        hashIdsOf[_wallet].push(_hashId);
        emit Registered(_hashId, _wallet, _asDoctor);
    }

    /* ═════════ 2) Getter vai trò ═════════ */
    // Note: These return info for the FIRST registered identity only
    function isDoctor(address who) public view returns (bool) {
        if (hashIdsOf[who].length == 0) return false;
        bytes32 h = hashIdsOf[who][0];
        return users[h].isDoctor;
    }
    function isPatient(address who) public view returns (bool) {
        if (hashIdsOf[who].length == 0) return false;
        bytes32 h = hashIdsOf[who][0];
        return !users[h].isDoctor;
    }
    function isAdmin(address who) public view returns (bool) { return who == admin; }
    function specOf(address doctor) external view returns (bytes32) {
        if (hashIdsOf[doctor].length == 0) return bytes32(0);
        return users[hashIdsOf[doctor][0]].specCommit;
    }

    // Returns info for the FIRST registered identity
    function getUserByWallet(address wallet) public view returns (
        bytes32 hashId,
        bytes32 specCommit,
        bool    doctorFlag
    ) {
        require(hashIdsOf[wallet].length > 0, "no registration");
        bytes32 _hashId = hashIdsOf[wallet][0];
        User memory u = users[_hashId];
        return (_hashId, u.specCommit, u.isDoctor);
    }

    // Get user info by specific hashId (for multi-identity support)
    function getUserByHashId(bytes32 _hashId) public view returns (
        bytes32 hashId,
        bytes32 specCommit,
        bool    doctorFlag
    ) {
        require(isHashIdOfWallet[msg.sender][_hashId], "not authorized");
        User memory u = users[_hashId];
        return (_hashId, u.specCommit, u.isDoctor);
    }

    /* ═════════ 3) zk-Login ═════════ */
    // Login proofs use the context (taskId, round, payload) = (0, 0, 0).
    function zkLogin(
        uint256[2] calldata a,
        uint256[2][2] calldata b,
        uint256[2] calldata c,
        uint256        pubHash
    ) external view returns (bool) {
        // Multi-identity support: Check if this hashId belongs to caller's wallet
        if (!isHashIdOfWallet[msg.sender][bytes32(pubHash)]) {
            return false; // This hashId is not registered to this wallet
        }
        return verifier.verifyProof(a, b, c, [pubHash, 0, 0, 0]);
    }
}
