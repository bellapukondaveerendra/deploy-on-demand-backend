from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pyngrok import ngrok
import os, git, uuid, subprocess, shutil, socket, psutil, logging, requests, time
from cryptography.fernet import Fernet
from datetime import datetime, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime, UTC
import platform

from database import (
    users_collection,
    deployments_collection,
    subscriptions_collection,
    scheduled_deployments_collection,
    init_indexes,
)
from auth import hash_password, verify_password, create_access_token, get_current_user
from schemas import SignupRequest, LoginRequest, TokenResponse

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Deploy-On-Demand API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    init_indexes()
    logger.info("✅ MongoDB indexes initialized")

# ── Constants ─────────────────────────────────────────────────────────────────
CLONE_DIR = "cloned_repos"
TEMP_ENV_FOLDER = "temp_envs"
FREE_TIER_LIMIT = 3

os.makedirs(CLONE_DIR, exist_ok=True)
os.makedirs(TEMP_ENV_FOLDER, exist_ok=True)

ENCRYPTION_KEY = Fernet.generate_key()
cipher = Fernet(ENCRYPTION_KEY)

# ═════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/signup", response_model=TokenResponse, status_code=201)
def signup(body: SignupRequest):
    # Check duplicates
    if users_collection.find_one({"email": body.email}):
        raise HTTPException(status_code=400, detail="Email already registered")
    if users_collection.find_one({"username": body.username}):
        raise HTTPException(status_code=400, detail="Username already taken")

    user_id = str(uuid.uuid4())
    user_doc = {
        "_id": user_id,
        "username": body.username,
        "email": body.email,
        "hashed_password": hash_password(body.password),
        "phone_number": body.phone_number,
        "is_active": True,
        "created_at": datetime.now(UTC),
    }
    users_collection.insert_one(user_doc)
    logger.info(f"✅ New user signed up: {body.email}")

    access_token = create_access_token({"sub": user_id})
    return TokenResponse(
        access_token=access_token,
        user_id=user_id,
        username=body.username,
        email=body.email,
    )


@app.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = users_collection.find_one({"email": body.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect password")

    access_token = create_access_token({"sub": user["_id"]})
    logger.info(f"✅ User logged in: {body.email}")
    return TokenResponse(
        access_token=access_token,
        user_id=user["_id"],
        username=user["username"],
        email=user["email"],
    )


@app.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "user_id": current_user["_id"],
        "username": current_user["username"],
        "email": current_user["email"],
        "phone_number": current_user.get("phone_number"),
        "created_at": current_user["created_at"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# DEPLOYMENT ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

def _check_deployment_limit(user_id: str) -> bool:
    """Returns True if the user has hit the free-tier limit."""
    sub = subscriptions_collection.find_one({"user_id": user_id, "is_active": True})
    if sub:
        return False  # premium — no limit
    count = deployments_collection.count_documents(
        {"user_id": user_id, "status": {"$in": ["SUCCESS", "RUNNING"]}}
    )
    return count >= FREE_TIER_LIMIT


@app.post("/deploy")
def deploy_repo(
    repo_url: str = Form(...),
    deployment_name: str = Form(...),
    is_env_given: bool = Form(False),
    is_backend_service: bool = Form(False),
    env_file: UploadFile = File(None),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]

    if _check_deployment_limit(user_id):
        raise HTTPException(
            status_code=403,
            detail="Free tier limit reached. Upgrade to Premium for unlimited deployments.",
        )

    if not repo_url.startswith("https://github.com/"):
        repo_url = repo_url.replace("git@github.com:", "https://github.com/")

    repo_id = str(uuid.uuid4())[:8]
    repo_path = os.path.join(CLONE_DIR, repo_id)
    public_url = ""

    # Save deployment record immediately as PENDING
    deployment_doc = {
        "_id": repo_id,
        "repo_id": repo_id,
        "user_id": user_id,
        "deployment_name": deployment_name,
        "repo_url": repo_url,
        "public_url": "",
        "status": "PENDING",
        "is_backend_service": is_backend_service,
        "is_env_given": is_env_given,
        "timestamp": datetime.now(UTC),
    }
    deployments_collection.insert_one(deployment_doc)

    try:
        git.Repo.clone_from(repo_url, repo_path, branch="main")

        env_path = None
        if is_env_given and env_file:
            env_data = env_file.file.read()
            env_path = encrypt_env(repo_id, env_data)

        if os.path.exists(os.path.join(repo_path, "index.html")):
            deploy_path = os.path.join("public_html", repo_id)
            shutil.copytree(repo_path, deploy_path)
            aws_ip = get_public_ip()
            public_url = f"http://{aws_ip}/deployments/{repo_id}/index.html"

        elif os.path.exists(os.path.join(repo_path, "app.py")):
            public_url = deploy_flask_in_docker(repo_path, repo_id, env_path)

        elif os.path.exists(os.path.join(repo_path, "main.py")):
            public_url = deploy_flask_in_docker(repo_path, repo_id, env_path)

        elif os.path.exists(os.path.join(repo_path, "package.json")):
            public_url = deploy_node_in_docker(repo_path, repo_id, env_path)

        # Update record to SUCCESS
        deployments_collection.update_one(
            {"_id": repo_id},
            {"$set": {"status": "SUCCESS", "public_url": public_url}},
        )
        logger.info(f"✅ Deployment {repo_id} succeeded — {public_url}")
        return {"message": "Deployment successful", "deploy_id": repo_id, "public_url": public_url}

    except Exception as e:
        deployments_collection.update_one(
            {"_id": repo_id},
            {"$set": {"status": "FAILED"}},
        )
        logger.error(f"❌ Deployment {repo_id} failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/deployment_history")
def deployment_history(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    docs = list(
        deployments_collection.find({"user_id": user_id}).sort("timestamp", -1)
    )

    deployments = []
    for d in docs:
        deployments.append({
            "repo_id": d["repo_id"],
            "deployment_name": d.get("deployment_name", "Unnamed"),
            "repo_url": d["repo_url"],
            "public_url": d.get("public_url", ""),
            "status": d.get("status", "UNKNOWN"),
            "is_backend_service": d.get("is_backend_service", False),
            "is_env_given": d.get("is_env_given", False),
            "timestamp": d["timestamp"].isoformat(),
        })

    reached_limit = _check_deployment_limit(user_id)
    return {"deployments": deployments, "reached_limit": reached_limit, "total": len(deployments)}


@app.delete("/delete_deployment/{repo_id}")
def delete_deployment(repo_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    doc = deployments_collection.find_one({"repo_id": repo_id, "user_id": user_id})

    if not doc:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Stop and remove Docker container if it exists
    container_name = f"deploy_{repo_id}"
    try:
        subprocess.run(["docker", "stop", container_name], capture_output=True)
        subprocess.run(["docker", "rm", container_name], capture_output=True)
        logger.info(f"🗑 Docker container {container_name} removed")
    except Exception as e:
        logger.warning(f"Could not remove container {container_name}: {e}")

    # Remove cloned repo directory
    repo_path = os.path.join(CLONE_DIR, repo_id)
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path, ignore_errors=True)

    # Remove static files if applicable
    static_path = os.path.join("public_html", repo_id)
    if os.path.exists(static_path):
        shutil.rmtree(static_path, ignore_errors=True)

    # Remove temp env file
    env_path = os.path.join(TEMP_ENV_FOLDER, f"{repo_id}.env")
    if os.path.exists(env_path):
        os.remove(env_path)

    # Delete from DB
    deployments_collection.delete_one({"repo_id": repo_id})
    logger.info(f"✅ Deployment {repo_id} deleted by user {user_id}")
    return {"message": "Deployment deleted successfully"}


@app.get("/docker-logs/{repo_id}")
def get_docker_logs(repo_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    doc = deployments_collection.find_one({"repo_id": repo_id, "user_id": user_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Deployment not found")

    container_name = f"deploy_{repo_id}"
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "200", container_name],
            capture_output=True,
            text=True,
        )
        raw = result.stdout + result.stderr
        logs = [line for line in raw.splitlines() if line.strip()]
        return {"logs": logs or ["No logs available."]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch logs: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# SCHEDULE ENDPOINT
# ═════════════════════════════════════════════════════════════════════════════

@app.post("/schedule")
def schedule_deployment(
    repo_url: str = Form(...),
    deployment_name: str = Form(...),
    is_env_given: bool = Form(False),
    is_backend_service: bool = Form(False),
    scheduled_date: str = Form(...),
    scheduled_time: str = Form(...),
    env_file: UploadFile = File(None),
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["_id"]

    if _check_deployment_limit(user_id):
        raise HTTPException(status_code=403, detail="Free tier limit reached.")

    try:
        scheduled_dt = datetime.fromisoformat(scheduled_time.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scheduled_time format. Use ISO 8601.")

    if scheduled_dt <= datetime.now(UTC) + timedelta(minutes=29):
        raise HTTPException(status_code=400, detail="Scheduled time must be at least 30 minutes from now.")

    schedule_id = str(uuid.uuid4())
    env_path = None
    if is_env_given and env_file:
        env_data = env_file.file.read()
        env_path = encrypt_env(schedule_id, env_data)

    scheduled_doc = {
        "_id": schedule_id,
        "user_id": user_id,
        "repo_url": repo_url,
        "deployment_name": deployment_name,
        "is_env_given": is_env_given,
        "is_backend_service": is_backend_service,
        "env_path": env_path,
        "scheduled_time": scheduled_dt,
        "scheduled_date": scheduled_date,
        "status": "SCHEDULED",
        "created_at": datetime.now(UTC),
    }
    scheduled_deployments_collection.insert_one(scheduled_doc)
    logger.info(f"📅 Deployment scheduled for {scheduled_dt} by user {user_id}")
    return {"message": "Deployment scheduled successfully", "schedule_id": schedule_id, "scheduled_time": scheduled_dt.isoformat()}


# ═════════════════════════════════════════════════════════════════════════════
# SUBSCRIPTION / PAYPAL ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@app.get("/check-subscription")
def check_subscription(current_user: dict = Depends(get_current_user)):
    user_id = current_user["_id"]
    sub = subscriptions_collection.find_one({"user_id": user_id, "is_active": True})
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription")
    return {
        "plan": sub["plan"],
        "start_date": sub["start_date"].strftime("%Y-%m-%d"),
        "expiry_date": sub["expiry_date"].strftime("%Y-%m-%d"),
        "is_active": sub["is_active"],
    }


@app.post("/create-order")
def create_paypal_order(body: dict = {}, current_user: dict = Depends(get_current_user)):
    """
    Stub — replace with real PayPal SDK call.
    Returns a fake order_id for wiring up the flow.
    """
    price = body.get("price", "25")
    order_id = f"FAKE-ORDER-{uuid.uuid4()}"
    logger.info(f"💳 PayPal order created: {order_id} for ${price}")
    return {"order_id": order_id, "price": price}


@app.post("/capture-payment/{order_id}")
def capture_paypal_payment(order_id: str, current_user: dict = Depends(get_current_user)):
    """
    Stub — replace with real PayPal capture call.
    On success, writes a subscription record to MongoDB.
    """
    user_id = current_user["_id"]

    # Upsert subscription record
    now = datetime.now(UTC)
    subscriptions_collection.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "plan": "Premium",
                "paypal_order_id": order_id,
                "start_date": now,
                "expiry_date": now + timedelta(days=30),
                "is_active": True,
                "updated_at": now,
            }
        },
        upsert=True,
    )
    logger.info(f"✅ Subscription activated for user {user_id}")
    return {"message": "Payment captured. Subscription activated.", "order_id": order_id}


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS  (unchanged from original)
# ═════════════════════════════════════════════════════════════════════════════

def encrypt_env(repo_id: str, env_data: bytes) -> str:
    env_path = os.path.join(TEMP_ENV_FOLDER, f"{repo_id}.env")
    with open(env_path, "wb") as f:
        f.write(env_data)
    return env_path


def find_available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def get_public_ip() -> str:
    try:
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2,
        ).text
        return requests.get(
            "http://169.254.169.254/latest/meta-data/public-ipv4",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=2,
        ).text
    except requests.RequestException:
        return "localhost"


def deploy_flask_in_docker(repo_path: str, repo_id: str, env_path: Optional[str]) -> str:
    requirements_path = os.path.join(repo_path, "requirements.txt")
    if not os.path.exists(requirements_path):
        raise HTTPException(status_code=400, detail="Missing requirements.txt")

    dockerfile_content = """FROM python:3.11

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

ENTRYPOINT ["sh","-c"]
CMD ["if [ -f main.py ]; then uvicorn main:app --host 0.0.0.0 --port 5000; elif [ -f app.py ]; then flask run --host=0.0.0.0 --port=5000; else python main.py; fi"]
"""
    with open(os.path.join(repo_path, "Dockerfile"), "w") as f:
        f.write(dockerfile_content)

    port = find_available_port()
    container_name = f"deploy_{repo_id}"

    subprocess.run(["docker", "build", "-t", container_name, "."], cwd=repo_path, check=True)

    docker_cmd = ["docker", "run", "-d", "-p", f"{port}:5000", "--name", container_name]
    if env_path:
        docker_cmd.extend(["--env-file", env_path])
    docker_cmd.append(container_name)
    subprocess.run(docker_cmd, check=True)

    configure_nginx_for_docker(repo_id, port)
    aws_ip = get_public_ip()
    return f"http://{aws_ip}/deployments/{repo_id}/"


def deploy_node_in_docker(repo_path: str, repo_id: str, env_path: Optional[str]) -> str:
    dockerfile_content = """FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
EXPOSE 3000
CMD ["npm", "start"]
"""
    with open(os.path.join(repo_path, "Dockerfile"), "w") as f:
        f.write(dockerfile_content)

    port = find_available_port()
    container_name = f"deploy_{repo_id}"

    subprocess.run(["docker", "build", "-t", container_name, "."], cwd=repo_path, check=True)

    docker_cmd = ["docker", "run", "-d", "-p", f"{port}:3000", "--name", container_name]
    if env_path:
        docker_cmd.extend(["--env-file", env_path])
    docker_cmd.append(container_name)
    subprocess.run(docker_cmd, check=True)

    configure_nginx_for_docker(repo_id, port)
    aws_ip = get_public_ip()
    return f"http://{aws_ip}/deployments/{repo_id}/"
    


def get_nginx_conf_path(repo_id: str) -> str:

    # Allow override using environment variable
    env_path = os.getenv("NGINX_DEPLOY_PATH")
    if env_path:
        os.makedirs(env_path, exist_ok=True)
        return os.path.join(env_path, f"{repo_id}.conf")

    system = platform.system()

    if system == "Darwin":  # Mac (brew nginx)
        base_path = "/opt/homebrew/etc/nginx/deployments"

    elif system == "Windows":
        base_path = "C:/nginx/conf/deployments"

    else:  # Linux / AWS
        base_path = "/etc/nginx/conf.d"

    os.makedirs(base_path, exist_ok=True)

    return os.path.join(base_path, f"{repo_id}.conf")

def configure_nginx_for_docker(repo_id: str, port: int):

    nginx_conf_path = get_nginx_conf_path(repo_id)

    
    nginx_config = f"""
server {{
    listen 8080;

    location /deployments/{repo_id}/ {{
        proxy_pass http://localhost:{port}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }}
}}
"""

    try:

        with open(nginx_conf_path, "w") as f:
            f.write(nginx_config)

        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["nginx", "-s", "reload"], check=True)

        logger.info(f"✅ Nginx configured for {repo_id} → port {port}")

    except Exception as e:
        logger.warning(f"⚠️ Nginx reload skipped: {e}")

@app.get("/")
def root():
    return {"message": "Deploy-On-Demand API is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9000)