from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess, json, os, sys, pathlib, hashlib, time, re
from fastapi.middleware.cors import CORSMiddleware
import logging

IDENTITY_RE = re.compile(r"^BV\.[A-Z0-9]+:[A-Z0-9]{8}$")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ZK-Prove service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:7000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=['POST', 'GET'],
    allow_headers=['*'],
)

BASE_DIR = pathlib.Path(__file__).resolve().parents[1]
CLIENT = BASE_DIR / "client"

PROOF_CACHE = {}
CACHE_TTL = 3600

class ProveRequest(BaseModel):
    identity: str
    salt: str
    task_id: str = "0"
    round: str = "0"
    payload: str = "0"

def get_cache_key(body: ProveRequest) -> str:
    return hashlib.sha256(
        f"{body.identity}|{body.salt}|{body.task_id}|{body.round}|{body.payload}".encode()
    ).hexdigest()

def get_from_cache(cache_key: str):
    if cache_key in PROOF_CACHE:
        cached_data, timestamp = PROOF_CACHE[cache_key]
        if time.time() - timestamp < CACHE_TTL:
            logger.info(f"✅ Cache HIT for key {cache_key[:8]}...")
            return cached_data
        else:
            logger.info(f"⏰ Cache EXPIRED for key {cache_key[:8]}...")
            del PROOF_CACHE[cache_key]
    return None

def save_to_cache(cache_key: str, data):
    PROOF_CACHE[cache_key] = (data, time.time())
    logger.info(f"💾 Cached proof for key {cache_key[:8]}...")

def run(cmd: list[str], timeout=30):
    try:
        res = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            raise RuntimeError(res.stderr or "Unknown error")
        return res.stdout
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timed out after {timeout} seconds")
    except Exception as e:
        raise RuntimeError(f"Subprocess error: {str(e)}")

@app.post("/prove")
def prove(body: ProveRequest):
    start_time = time.time()

    if not body.identity or not body.salt:
        raise HTTPException(status_code=400, detail="Missing identity or salt")

    if not IDENTITY_RE.match(body.identity):
        raise HTTPException(status_code=400,
                            detail="Invalid identity format (expected BV.<NAME>:<8-alnum>)")

    cache_key = get_cache_key(body)
    cached_result = get_from_cache(cache_key)

    if cached_result:
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"⚡ Returned cached proof in {elapsed:.2f}ms")
        return cached_result

    logger.info(f"🔄 Cache MISS - generating new proof for {body.identity}")

    cmd = [sys.executable, str(CLIENT / "prove.py"),
           "--identity", body.identity, "--salt", body.salt,
           "--task-id", body.task_id, "--round", body.round, "--payload", body.payload]
    
    try:
        run(cmd, timeout=120)
        
        proof_file = CLIENT / "proof.json"
        public_file = CLIENT / "public.json"
        
        if not proof_file.exists() or not public_file.exists():
            raise HTTPException(status_code=500, detail="Proof files not generated")
        
        proof = json.load(open(proof_file))
        pub = json.load(open(public_file))
        
        result = {"proof": proof, "pub": pub}
        
        save_to_cache(cache_key, result)
        
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Generated and cached new proof in {elapsed:.2f}ms")
        
        return result
        
    except subprocess.TimeoutExpired:
        logger.error("❌ Proof generation timeout")
        raise HTTPException(status_code=504, detail="Proof generation timed out")
    except Exception as e:
        logger.error(f"❌ Proof generation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cache/stats")
def cache_stats():
    total_entries = len(PROOF_CACHE)
    expired_count = 0
    current_time = time.time()
    
    for _, (_, timestamp) in PROOF_CACHE.items():
        if current_time - timestamp >= CACHE_TTL:
            expired_count += 1
    
    return {
        "total_entries": total_entries,
        "active_entries": total_entries - expired_count,
        "expired_entries": expired_count,
        "cache_ttl_seconds": CACHE_TTL
    }

@app.delete("/cache/clear")
def clear_cache():
    count = len(PROOF_CACHE)
    PROOF_CACHE.clear()
    logger.info(f"🗑️ Cleared {count} cached proofs")
    return {"message": f"Cleared {count} cached proofs"}

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "ZK-Prove"}
