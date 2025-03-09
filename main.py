from fastapi import FastAPI, HTTPException,File, UploadFile, Form
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
from cryptography.fernet import Fernet
import time

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
app = FastAPI()
ngrok.set_auth_token("2t8s9yJ3vJsfNADlpRZfYn2IwnO_6wCav8TxywmmzT6pAWTpk")
# Enable CORS (Allow frontend to talk to backend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","*"],  # Allow only frontend URL
    allow_credentials=True,
    allow_methods=["*"],  # Allow all HTTP methods
    allow_headers=["*"],  # Allow all headers
)

# Directory where repos will be cloned
CLONE_DIR = "cloned_repos"
TEMP_ENV_FOLDER = "/home/ubuntu/deploy-on-demand/temp_envs"

# Ensure the directory exists
os.makedirs(CLONE_DIR, exist_ok=True)
os.makedirs(TEMP_ENV_FOLDER, exist_ok=True)

# Generate a key for encryption
ENCRYPTION_KEY = Fernet.generate_key()
cipher = Fernet(ENCRYPTION_KEY)

class RepoRequest(BaseModel):
    repo_url: str
    is_env_given: bool = False

def encrypt_env(repo_id, env_data):
    """Encrypts the .env file contents and stores it temporarily"""
    # encrypted_data = cipher.encrypt(env_data.encode())
    encrypted_data = env_data
    env_path = os.path.join(TEMP_ENV_FOLDER, f"{repo_id}.env")

    with open(env_path, "wb") as f:
        f.write(encrypted_data)
    
    return env_path


@app.get("/")
def home():
    return {"message": "FastAPI is running successfully!"}


@app.post("/deploy")
def deploy_repo(
    repo_url: str = Form(...),
    is_env_given: bool = Form(False),
    env_file: UploadFile = File(None)
):
    """Clones a GitHub repo and serves static files for an HTML project."""

    # Validate URL
    if not repo_url.startswith("https://github.com/"):
        repo_url = repo_url.replace("https://github.com/", "git@github.com:")

    # Generate unique deployment ID
    repo_id = str(uuid.uuid4())[:8]
    repo_path = os.path.join(CLONE_DIR, repo_id)
    public_url = ""
    try:
        # Clone the repo
        git.Repo.clone_from(repo_url, repo_path, branch='master') #, branch='master_v1'

        # Handle .env file if provided
        env_path = None
        if is_env_given and env_file:
            env_data = env_file.read()
            env_path = encrypt_env(repo_id, env_data)

        # Check if index.html exists
        if os.path.exists(os.path.join(repo_path, "index.html")):
            # Move static files to a public directory
            deploy_path = os.path.join("public_html", repo_id)
            shutil.copytree(repo_path, deploy_path)
            aws_ip = get_public_ip()
            public_url = f"http://{aws_ip}/deployments/{repo_id}/index.html"
        
        elif os.path.exists(os.path.join(repo_path, "app.py")):
            public_url = deploy_flask_in_docker(repo_path, repo_id, env_path)

        return {"message": "Deployment successful", "deploy_id": repo_id, "public_url": public_url}

    except Exception as e:
        logger.debug(f"deploy_repo Error is,", e)
        raise HTTPException(status_code=500, detail=str(e))

def deploy_flask_in_docker(repo_path, repo_id, env_path):
    try:
        """Generates a Dockerfile and deploys Flask inside a container, then configures Nginx."""
        
        # Ensure requirements.txt exists
        requirements_path = os.path.join(repo_path, "requirements.txt")
        if not os.path.exists(requirements_path):
            raise HTTPException(status_code=400, detail="Missing requirements.txt. Please add it.")

        # Generate a Dockerfile inside the repo folder
        dockerfile_content = f"""FROM python:3.11
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
        try:
            logger.debug("🔥 Starting Docker Build with Real-Time Logs...")  # Debug Info
            # 🔥 Fix: Run Docker Build & Run Commands Inside `repo_path`
            logger.debug("🔥 Debug: Running Docker Build Command")  # Debug Info
            subprocess.run(["docker", "build", "-t", container_name, "."], cwd=repo_path, check=True)  # ✅ Corrected
            logger.debug(f"🔥 Debug: Running Docker Run Command docker run -d -p {port}:5000 --name ${container_name} ${container_name}")  # Debug Info

            docker_cmd = ["docker", "run", "-d", "-p", f"{port}:5000", "--name", container_name]
            if env_path:
                print("env_pathenv_pathenv_pathenv_path",env_path)
                docker_cmd.extend(["--env-file", env_path])
            docker_cmd.append(container_name)

            # ✅ Real-Time Logs for Docker Build
            process = subprocess.Popen(
                ["docker", "build", "-t", container_name, "."],
                cwd=repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            # Display Build-Time Logs in Console (Optional - for Debugging)
            for line in iter(process.stdout.readline, ''):
                logger.debug(f"[BUILD-LOG] {line.strip()}")
            
            return_code = process.wait()
            if return_code != 0:
                raise HTTPException(status_code=500, detail="❌ Docker Build Failed. Please check the logs.")

            print("Running Docker Command after Appending ::",docker_cmd)
            logger.debug(f"🔥 Debug: Running Docker Run Command {docker_cmd}")
            subprocess.run(docker_cmd, check=True)

            # Delete temp env file
            # if env_path:
                # os.remove(env_path)
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
        subprocess.run(["powershell", "Start-Process", "C:/Users/veeru/nginx/nginx.exe", "-Verb", "runAs"], check=True)
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
    # nginx_conf_path = "/etc/nginx/conf.d/deployments_locations.conf"
    nginx_conf_path = "C:/Users/veeru/nginx/conf/deployments.conf"

    # Append new deployment rule
    with open(nginx_conf_path, "a") as f:
        f.write(nginx_config + "\n")

    # ✅ Restart Nginx properly on Linux
    try:
        NGINX_PATH = r"C:/Users/veeru/nginx"
        logger.debug("🔥 Restarting Nginx...")
        # subprocess.run(["sudo", "nginx", "-t"], check=True)  # Validate config
        # subprocess.run(["sudo", "systemctl", "restart", "nginx"], check=True)
        # subprocess.run(["taskkill", "/IM", "nginx.exe", "/F"], check=True, shell=True)

        for proc in psutil.process_iter(['pid', 'name', 'exe']):
            try:
                if proc.info['name'] == "nginx.exe" and proc.info['exe'] == NGINX_PATH:
                    logger.debug(f"🛑 Stopping Nginx (PID: {proc.info['pid']})...")
                    subprocess.run(["taskkill", "/PID", str(proc.info['pid']), "/F"], check=True, shell=True)
                    time.sleep(2)  # Ensure process fully stops
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        
        logger.debug("🚀 Stopping any existing Nginx instances...")
        # subprocess.run([rf"{NGINX_PATH}\nginx.exe", "-s", "stop"],
        #                check=True,
        #     shell=True,
        #     cwd=rf"{NGINX_PATH}"
        # )
        time.sleep(2)  # Wait 2 seconds to ensure the process stops
        # ✅ Start Nginx again
        logger.debug("🚀 Starting Nginx...")
        subprocess.run(
            [rf"{NGINX_PATH}\nginx.exe", "-c", rf"{NGINX_PATH}\conf\nginx.conf"],
            check=True,
            shell=True,
            cwd=rf"{NGINX_PATH}"  # 🔹 This ensures Nginx runs from its correct directory
        )
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
