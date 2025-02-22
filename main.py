from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import git
import uuid
import subprocess
from pyngrok import ngrok
import shutil
from fastapi.staticfiles import StaticFiles
import socket
import psutil
import logging
import requests

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI()
ngrok.set_auth_token("2t8s9yJ3vJsfNADlpRZfYn2IwnO_6wCav8TxywmmzT6pAWTpk")
# Enable CORS (Allow frontend to talk to backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Allow only frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

# Directory where repos will be cloned
CLONE_DIR = "cloned_repos"

# Ensure the directory exists
os.makedirs(CLONE_DIR, exist_ok=True)

class RepoRequest(BaseModel):
    repo_url: str


@app.get("/")
def home():
    return {"message": "FastAPI is running successfully!"}


@app.post("/deploy")
async def deploy_repo(request: RepoRequest):
    """Clones a GitHub repo and serves static files for an HTML project."""
    repo_url = request.repo_url

    # Validate URL
    if not repo_url.startswith("https://github.com/"):
        repo_url = repo_url.replace("https://github.com/", "git@github.com:")

    # Generate unique deployment ID
    repo_id = str(uuid.uuid4())[:8]
    repo_path = os.path.join(CLONE_DIR, repo_id)
    public_url = ""
    try:
        # Clone the repo
        git.Repo.clone_from(repo_url, repo_path)

        # Check if index.html exists
        if os.path.exists(os.path.join(repo_path, "index.html")):
            # Move static files to a public directory
            deploy_path = os.path.join("public_html", repo_id)
            shutil.copytree(repo_path, deploy_path)
            aws_ip = get_public_ip()
            public_url = f"http://{aws_ip}/deployments/{repo_id}/index.html"
        
        elif os.path.exists(os.path.join(repo_path, "app.py")):
            public_url = deploy_flask_in_docker(repo_path, repo_id)

        return {"message": "Deployment successful", "deploy_id": repo_id, "public_url": public_url}

    except Exception as e:
        logger.debug(f"deploy_repo Error is,", e)
        raise HTTPException(status_code=500, detail=str(e))

def deploy_flask_in_docker(repo_path, repo_id):
    try:
        """Generates a Dockerfile and deploys Flask inside a container, then configures Nginx."""
        
        # Ensure requirements.txt exists
        requirements_path = os.path.join(repo_path, "requirements.txt")
        if not os.path.exists(requirements_path):
            raise HTTPException(status_code=400, detail="Missing requirements.txt. Please add it.")

        # Generate a Dockerfile inside the repo folder
        dockerfile_content = f"""FROM python:3.10.6
        WORKDIR /app
        COPY . /app
        RUN pip install --no-cache-dir -r requirements.txt
        ENV PORT=5000
        CMD ["flask", "run", "--host", "0.0.0.0"]
        """

        dockerfile_path = os.path.join(repo_path, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(dockerfile_content)

        # Verify if Dockerfile was created
        if not os.path.exists(dockerfile_path):
            raise HTTPException(status_code=500, detail="Dockerfile was not created correctly.")


        # Find an available port
        port = find_available_port()
        logger.debug(f"🔥 Debug: Assigned Port {port}")  # Debug Info

        # Define container name
        container_name = f"deploy_{repo_id}"

        logger.debug(f"repo_path",repo_path)
        try:
            # 🔥 Fix: Run Docker Build & Run Commands Inside `repo_path`
            logger.debug("🔥 Debug: Running Docker Build Command")  # Debug Info
            subprocess.run(["docker", "build", "-t", container_name, "."], cwd=repo_path, check=True)  # ✅ Corrected
            logger.debug(f"🔥 Debug: Running Docker Run Command docker run -d -p {port}:5000 --name ${container_name} ${container_name}")  # Debug Info
            subprocess.run(["docker", "run", "-d", "-p", f"{port}:5000", "--name", container_name, container_name], check=True)
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"Docker build/run failed: {e}")
        # Configure Nginx
        configure_nginx_for_docker(repo_id, port)
        aws_ip = get_public_ip()
        public_url = f"http://{aws_ip}/deployments/{repo_id}/"
        return public_url
    except Exception as e:
        logger.debug("error is ",e)
        raise HTTPException(status_code=500, detail=str(e))




def find_available_port():
    """Finds an available port for running a Flask or Node.js app inside Docker."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))  # Bind to any available port
        return s.getsockname()[1]  # Return the assigned port


def configure_nginx_for_docker_windows(repo_id, port):
    """Adds an Nginx reverse proxy for the Docker container and ensures it restarts properly."""

    nginx_config = f"""
    location ~ ^/deployments/{repo_id}/(.*)$ {{
        proxy_pass http://localhost:{port}/;
        rewrite ^/deployments/{repo_id}/(.*)$ /$1 break;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }}
    """

    # Append to deployments.conf ( local: C:/Users/veeru/nginx-1.26.3/conf/deployments.conf)
    with open("/etc/nginx/conf.d/deployments.conf", "a") as f:
        f.write(nginx_config + "\n")

    # Check if Nginx is running and terminate it
    for proc in psutil.process_iter():
        try:
            if "nginx.exe" in proc.name().lower():
                logger.debug("🔥 Debug: Nginx is running, stopping it first...")
                proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    # 🔥 Fix: Use PowerShell to restart Nginx with Administrator privileges
    try:
        logger.debug("🔥 Restarting Nginx with Admin Privileges...")
        subprocess.run(["powershell", "Start-Process", "C:/Users/veeru/nginx-1.26.3/nginx.exe", "-Verb", "runAs"], check=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Nginx restart failed: {e}")


def configure_nginx_for_docker(repo_id, port):
    """Adds an Nginx reverse proxy for the Docker container and ensures it restarts properly."""

    nginx_config = f"""
    location /deployments/{repo_id}/ {{
        proxy_pass http://localhost:{port}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_redirect off;
    }}
    """


    # ✅ Correct path for Nginx deployments config on Ubuntu
    nginx_conf_path = "/etc/nginx/conf.d/deployments_locations.conf"

    # Append new deployment rule
    with open(nginx_conf_path, "a") as f:
        f.write(nginx_config + "\n")

    # ✅ Restart Nginx properly on Linux
    try:
        logger.debug("🔥 Restarting Nginx...")
        subprocess.run(["sudo", "nginx", "-t"], check=True)  # Validate config
        subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=True)
        logger.debug("✅ Nginx restarted successfully!")
    except Exception as e:
        logger.error(f"❌ Nginx restart failed: {e}")
        raise Exception(f"Nginx restart failed: {e}")

def detect_project_type(repo_path):
    """Detects the type of project based on common files."""
    if os.path.exists(os.path.join(repo_path, "package.json")):
        return "nodejs"
    elif os.path.exists(os.path.join(repo_path, "requirements.txt")):
        return "python"
    elif os.path.exists(os.path.join(repo_path, "index.html")):
        return "static"
    else:
        return "unknown"
    
def expose_port(port):
    """Exposes a running local server to the internet."""
    public_url = ngrok.connect(port).public_url
    return 
    

# Get AWS Public IP dynamically
def get_public_ip():
    """Fetches the current public IP of the EC2 instance."""
    try:
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=2
        ).text
        return requests.get(
            "http://169.254.169.254/latest/meta-data/public-ipv4",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=2
        ).text
    except requests.RequestException:
        return "localhost"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
