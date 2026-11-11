// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./IdentityAccess.sol";
import "./Verifier.sol";

/**
 * @title FederatedLearning (v2 - ZKP facility identity)
 * @notice Participants are identified by facility hashId (bytes32), not wallet address.
 *         One wallet (relayer) can submit proofs on behalf of multiple facilities.
 *
 * Identity model:
 *   - `msg.sender` = relayer wallet (pays gas, owns admin rights over tasks it created)
 *   - `facilityHashId` = Poseidon(secret) — proven via Groth16 ZK proof
 *   - `users[hashId].isDoctor` in IdentityAccess = "authorized facility" flag (semantic reuse)
 *
 * Security:
 *   - Proof verifier: Groth16 (Verifier.sol), public signals [hashId, taskId, round, payload]
 *   - Context binding: createTask (newTaskId, 0, initialWeightsHash mod p),
 *     joinTask (taskId, 0, 0), submitUpdate (taskId, currentRound, weightsHash mod p)
 *   - Sponsor binding: `isHashIdOfWallet[msg.sender][hashId]` (set by the registrar)
 *   - Participant uniqueness: `isParticipant[taskId][hashId]` bytes32-keyed
 */
contract FederatedLearning {
    /* ═════════ Enums ═════════ */
    enum TaskStatus { RECRUITING, TRAINING, AGGREGATING, COMPLETED, CANCELLED }

    /* ═════════ Structs ═════════ */
    struct FLTask {
        uint256    id;
        address    creator;           // Relayer wallet paying gas / controlling admin
        bytes32    creatorFacility;   // Facility hashId of task owner
        bytes32    modelHash;
        string     description;
        uint256    totalRounds;
        uint256    currentRound;
        uint256    minParticipants;
        TaskStatus status;
        string     globalModelCID;
        bytes32    globalWeightsHash;
        uint256    createdAt;
    }

    struct ModelUpdate {
        bytes32   facilityHashId;    // Submitting facility
        bytes32   weightsHash;
        string    weightsCID;
        uint256   accuracy;           // accuracy * 10000
        uint256   dataSize;
        uint256   round;
        uint256   submittedAt;
    }

    // Bundle ZK proof fields to avoid "Stack too deep" errors
    struct ZKProof {
        uint256[2]    a;
        uint256[2][2] b;
        uint256[2]    c;
    }

    // Bundle task creation params (same reason)
    struct TaskParams {
        bytes32 modelHash;
        string  description;
        uint256 totalRounds;
        uint256 minParticipants;
        string  initialModelCID;
        bytes32 initialWeightsHash;
    }

    /* ═════════ Events ═════════ */
    event TaskCreated(uint256 indexed taskId, address creator, bytes32 indexed creatorFacility, string description);
    event ParticipantJoined(uint256 indexed taskId, bytes32 indexed facilityHashId);
    event RoundStarted(uint256 indexed taskId, uint256 round);
    event UpdateSubmitted(uint256 indexed taskId, uint256 round, bytes32 indexed facilityHashId);
    event AggregationSubmitted(uint256 indexed taskId, uint256 round, string globalModelCID);
    event TaskCompleted(uint256 indexed taskId, string finalModelCID);
    event TaskCancelled(uint256 indexed taskId);

    /* ═════════ Storage ═════════ */
    IdentityAccess public identityAccess;
    Groth16Verifier public verifier;

    uint256 public taskCount;
    mapping(uint256 => FLTask) public tasks;

    mapping(uint256 => bytes32[]) public taskParticipants;
    mapping(uint256 => mapping(bytes32 => bool)) public isParticipant;

    mapping(uint256 => mapping(uint256 => ModelUpdate[])) public roundUpdates;
    mapping(uint256 => mapping(uint256 => mapping(bytes32 => bool))) public hasSubmitted;

    /* ═════════ Modifiers ═════════ */
    modifier taskExists(uint256 _taskId) {
        require(_taskId > 0 && _taskId <= taskCount, "FL: task not found");
        _;
    }

    /* ═════════ Constructor ═════════ */
    constructor(address _identityAccess, address _verifier) {
        identityAccess = IdentityAccess(_identityAccess);
        verifier = Groth16Verifier(_verifier);
    }

    /* ═════════ Internal: ZK facility verification ═════════ */
    uint256 internal constant SNARK_FIELD =
        21888242871839275222246405745257275088548364400416034343698204186575808495617;

    function _verifyFacility(
        ZKProof calldata proof,
        bytes32 facilityHashId,
        uint256 taskId,
        uint256 round,
        bytes32 payload
    ) internal view {
        // (a) Proof chứng minh biết secret sao cho Poseidon(secret) == hashId,
        //     gắn với đúng (taskId, round, payload) — không dùng lại ở chỗ khác được
        require(
            verifier.verifyProof(
                proof.a, proof.b, proof.c,
                [uint256(facilityHashId), taskId, round, uint256(payload) % SNARK_FIELD]
            ),
            "FL: invalid ZK proof"
        );

        // (b) hashId thuộc về relayer wallet đang ký tx (chống impersonate)
        require(
            identityAccess.isHashIdOfWallet(msg.sender, facilityHashId),
            "FL: facility not owned by caller"
        );

        // (c) Facility phải được đánh dấu authorized (isDoctor==true trong IdentityAccess)
        (bool isFacility, , bytes32 storedHashId) = identityAccess.users(facilityHashId);
        require(storedHashId != bytes32(0), "FL: facility not registered");
        require(isFacility, "FL: hashId not an authorized facility");
    }

    /* ═════════ 1) Create Task ═════════ */
    function createTask(
        TaskParams calldata params,
        ZKProof calldata proof,
        bytes32 facilityHashId
    ) external returns (uint256) {
        _verifyFacility(proof, facilityHashId, taskCount + 1, 0, params.initialWeightsHash);

        require(params.totalRounds > 0, "FL: rounds must be > 0");
        require(params.minParticipants >= 2, "FL: need at least 2 participants");

        taskCount++;
        tasks[taskCount] = FLTask({
            id: taskCount,
            creator: msg.sender,
            creatorFacility: facilityHashId,
            modelHash: params.modelHash,
            description: params.description,
            totalRounds: params.totalRounds,
            currentRound: 0,
            minParticipants: params.minParticipants,
            status: TaskStatus.RECRUITING,
            globalModelCID: params.initialModelCID,
            globalWeightsHash: params.initialWeightsHash,
            createdAt: block.timestamp
        });

        // Creator facility auto-joins
        taskParticipants[taskCount].push(facilityHashId);
        isParticipant[taskCount][facilityHashId] = true;

        emit TaskCreated(taskCount, msg.sender, facilityHashId, params.description);
        emit ParticipantJoined(taskCount, facilityHashId);

        return taskCount;
    }

    /* ═════════ 2) Join Task ═════════ */
    function joinTask(
        uint256 _taskId,
        ZKProof calldata proof,
        bytes32 facilityHashId
    ) external taskExists(_taskId) {
        _verifyFacility(proof, facilityHashId, _taskId, 0, bytes32(0));

        FLTask storage task = tasks[_taskId];
        require(task.status == TaskStatus.RECRUITING, "FL: not recruiting");
        require(!isParticipant[_taskId][facilityHashId], "FL: already joined");

        taskParticipants[_taskId].push(facilityHashId);
        isParticipant[_taskId][facilityHashId] = true;

        emit ParticipantJoined(_taskId, facilityHashId);
    }

    /* ═════════ 3) Start Round (address-gated admin) ═════════ */
    function startRound(uint256 _taskId) external taskExists(_taskId) {
        FLTask storage task = tasks[_taskId];
        require(msg.sender == task.creator, "FL: only creator");
        require(
            task.status == TaskStatus.RECRUITING || task.status == TaskStatus.AGGREGATING,
            "FL: cannot start round"
        );
        require(
            taskParticipants[_taskId].length >= task.minParticipants,
            "FL: not enough participants"
        );

        task.currentRound++;
        task.status = TaskStatus.TRAINING;

        emit RoundStarted(_taskId, task.currentRound);
    }

    /* ═════════ 4) Submit Local Update ═════════ */
    function submitUpdate(
        uint256 _taskId,
        bytes32 _weightsHash,
        string calldata _weightsCID,
        uint256 _accuracy,
        uint256 _dataSize,
        ZKProof calldata proof,
        bytes32 facilityHashId
    ) external taskExists(_taskId) {
        FLTask storage task = tasks[_taskId];
        _verifyFacility(proof, facilityHashId, _taskId, task.currentRound, _weightsHash);

        require(task.status == TaskStatus.TRAINING, "FL: not in training");
        require(isParticipant[_taskId][facilityHashId], "FL: not a participant");
        require(
            !hasSubmitted[_taskId][task.currentRound][facilityHashId],
            "FL: already submitted this round"
        );

        roundUpdates[_taskId][task.currentRound].push(ModelUpdate({
            facilityHashId: facilityHashId,
            weightsHash: _weightsHash,
            weightsCID: _weightsCID,
            accuracy: _accuracy,
            dataSize: _dataSize,
            round: task.currentRound,
            submittedAt: block.timestamp
        }));

        hasSubmitted[_taskId][task.currentRound][facilityHashId] = true;

        emit UpdateSubmitted(_taskId, task.currentRound, facilityHashId);
    }

    /* ═════════ 5) Submit Aggregation (address-gated admin) ═════════ */
    function submitAggregation(
        uint256 _taskId,
        string calldata _globalModelCID,
        bytes32 _globalWeightsHash
    ) external taskExists(_taskId) {
        FLTask storage task = tasks[_taskId];
        require(msg.sender == task.creator, "FL: only creator");
        require(task.status == TaskStatus.TRAINING, "FL: not in training");

        uint256 submissions = roundUpdates[_taskId][task.currentRound].length;
        require(submissions >= task.minParticipants, "FL: not enough submissions");

        task.globalModelCID = _globalModelCID;
        task.globalWeightsHash = _globalWeightsHash;

        emit AggregationSubmitted(_taskId, task.currentRound, _globalModelCID);

        if (task.currentRound >= task.totalRounds) {
            task.status = TaskStatus.COMPLETED;
            emit TaskCompleted(_taskId, _globalModelCID);
        } else {
            task.status = TaskStatus.AGGREGATING;
        }
    }

    /* ═════════ 6) Cancel Task (address-gated admin) ═════════ */
    function cancelTask(uint256 _taskId) external taskExists(_taskId) {
        FLTask storage task = tasks[_taskId];
        require(msg.sender == task.creator, "FL: only creator");
        require(task.status != TaskStatus.COMPLETED, "FL: already completed");

        task.status = TaskStatus.CANCELLED;
        emit TaskCancelled(_taskId);
    }

    /* ═════════ View Functions ═════════ */

    function getTaskInfo(uint256 _taskId) external view taskExists(_taskId) returns (
        address creator,
        string memory description,
        TaskStatus status,
        uint256 totalRounds,
        uint256 currentRound,
        uint256 minParticipants
    ) {
        FLTask storage t = tasks[_taskId];
        return (t.creator, t.description, t.status, t.totalRounds, t.currentRound, t.minParticipants);
    }

    function getTaskModel(uint256 _taskId) external view taskExists(_taskId) returns (
        bytes32 modelHash,
        string memory globalModelCID,
        bytes32 globalWeightsHash,
        bytes32 creatorFacility
    ) {
        FLTask storage t = tasks[_taskId];
        return (t.modelHash, t.globalModelCID, t.globalWeightsHash, t.creatorFacility);
    }

    function getTaskParticipantCount(uint256 _taskId) external view taskExists(_taskId)
        returns (uint256)
    {
        return taskParticipants[_taskId].length;
    }

    function getParticipants(uint256 _taskId) external view taskExists(_taskId)
        returns (bytes32[] memory)
    {
        return taskParticipants[_taskId];
    }

    function getRoundUpdateCount(uint256 _taskId, uint256 _round) external view
        returns (uint256)
    {
        return roundUpdates[_taskId][_round].length;
    }

    function getRoundUpdate(uint256 _taskId, uint256 _round, uint256 _index) external view
        returns (
            bytes32 facilityHashId,
            bytes32 weightsHash,
            string memory weightsCID,
            uint256 accuracy,
            uint256 dataSize,
            uint256 submittedAt
        )
    {
        ModelUpdate storage u = roundUpdates[_taskId][_round][_index];
        return (u.facilityHashId, u.weightsHash, u.weightsCID, u.accuracy, u.dataSize, u.submittedAt);
    }
}
