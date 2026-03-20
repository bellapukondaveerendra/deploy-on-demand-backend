# Deploy-On-Demand — Backend

FastAPI backend for the Deploy-On-Demand platform. Handles authentication, deployment orchestration, Docker container lifecycle, ngrok tunnel management, and subscription tracking.

---

## Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.110 |
| Database | MongoDB (pymongo) |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| Containerisation | Docker via subprocess |
| Tunneling | pyngrok |
| Git cloning | gitpython |
| Scheduling | APScheduler |

---

## Project Structure

```
backend/
├── main.py                  # App factory + lifespan + router mounts
├── auth.py                  # JWT encode/decode + bcrypt + Bearer dependency
├── database.py              # MongoClient, collections, index setup
├── schemas.py               # Pydantic request/response models
│
├── routers/                 # HTTP layer — request parsing, auth, responses
│   ├── __init__.py
│   ├── auth.py              # POST /signup  POST /login  GET /me
│   ├── deployments.py       # POST /deploy  GET /deployment_history
│   │                        # DELETE /delete_deployment  GET /docker-logs
│   ├── schedule.py          # POST /schedule
│   └── subscription.py      # GET /check-subscription
│                            # POST /create-order  POST /capture-payment
│
├── services/                # Business logic — no FastAPI imports
│   ├── __init__.py
│   ├── docker.py            # Dockerfile generation, build/run/stop/logs
│   ├── git.py               # Repository cloning with branch validation
│   ├── ngrok.py             # Tunnel open/close/registry/shutdown
│   └── storage.py          # .env file saving, on-disk cleanup
│
├── requirements.txt
└── .env.example
```

### Separation of concerns

- **`routers/`** — only touches HTTP. Parses Form/JSON, injects the `current_user` dependency, calls services, returns responses. No Docker or git logic here.
- **`services/`** — pure Python business logic. No `Request`, `Form`, or `Depends` imports. Can be unit-tested without spinning up FastAPI.
- **`main.py`** — app factory only. Registers middleware and routers, manages startup/shutdown. Nothing else.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- Docker running
- MongoDB running on `localhost:27017`
- ngrok account — get a free auth token at [dashboard.ngrok.com](https://dashboard.ngrok.com/)

### 2. Install

```bash
cd backend
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
SECRET_KEY=your-long-random-secret-here
MONGO_URL=mongodb://localhost:27017
DB_NAME=deploy_on_demand
NGROK_AUTHTOKEN=your_ngrok_token_here
```

### 4. Run

```bash
uvicorn main:app --host 0.0.0.0 --port 9000 --reload
```

- API base: `http://localhost:9000`
- Interactive docs (Swagger UI): `http://localhost:9000/docs`
- OpenAPI schema: `http://localhost:9000/openapi.json`

---

## Environment Variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `SECRET_KEY` | insecure default | **Yes** | JWT signing secret. Use a long random string in production. |
| `MONGO_URL` | `mongodb://localhost:27017` | No | MongoDB connection string |
| `DB_NAME` | `deploy_on_demand` | No | MongoDB database name |
| `NGROK_AUTHTOKEN` | — | **Yes** | ngrok auth token. Without this, tunnels will be rate-limited. |
| `FREE_TIER_LIMIT` | `3` | No | Max active deployments for free users |
| `CLONE_DIR` | `cloned_repos` | No | Directory where repos are cloned |
| `TEMP_ENV_FOLDER` | `temp_envs` | No | Directory where uploaded .env files are stored |

---

## API Reference

All protected endpoints require:
```
Authorization: Bearer <access_token>
```

### Auth

#### `POST /signup`
Create a new user account.

**Body (JSON)**
```json
{
  "username": "veerendra",
  "email": "v@example.com",
  "password": "secret123",
  "phone_number": "+1 555 000 0000"
}
```

**Response `201`**
```json
{
  "access_token": "<jwt>",
  "token_type": "bearer",
  "user_id": "uuid",
  "username": "veerendra",
  "email": "v@example.com"
}
```

---

#### `POST /login`
Authenticate and receive a JWT.

**Body (JSON)**
```json
{ "email": "v@example.com", "password": "secret123" }
```

**Response `200`** — same shape as `/signup`

---

#### `GET /me` 🔒
Return the current user's profile.

---

### Deployments

#### `POST /deploy` 🔒
Clone a GitHub repo, build a Docker image, start a container, open an ngrok tunnel.

**Body (multipart/form-data)**

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `repo_url` | string | Yes | — | GitHub HTTPS or SSH URL |
| `deployment_name` | string | Yes | — | Display name |
| `branch` | string | No | `main` | Branch to clone |
| `is_backend_service` | bool | No | `false` | Show Docker logs in UI |
| `is_env_given` | bool | No | `false` | Whether an .env file is attached |
| `entry_file` | string | No | `` | Override auto-detection (e.g. `server.py`) |
| `env_file` | file | No | — | `.env` file upload |

**Project type auto-detection order:**
1. `entry_file` extension provided → use it directly
2. `requirements.txt` exists → Python (scans for `app.py`, `main.py`, `run.py`, `server.py`, `wsgi.py`, `asgi.py`, `manage.py`)
3. `package.json` exists → Node.js (`npm start`)
4. `index.html` exists → Static (nginx)
5. None matched → `400` with instructions

**Response `200`**
```json
{
  "message": "Deployment successful",
  "deploy_id": "1a2b3c4d",
  "public_url": "https://abc123.ngrok.io"
}
```

---

#### `GET /deployment_history` 🔒
List all deployments for the current user, newest first.

**Response `200`**
```json
{
  "deployments": [
    {
      "repo_id": "1a2b3c4d",
      "deployment_name": "my-app",
      "repo_url": "https://github.com/user/repo",
      "branch": "main",
      "entry_file": "app.py",
      "public_url": "https://abc123.ngrok.io",
      "status": "SUCCESS",
      "is_backend_service": true,
      "is_env_given": false,
      "timestamp": "2025-06-01T12:00:00+00:00"
    }
  ],
  "reached_limit": false,
  "total": 1
}
```

**Deployment statuses:** `PENDING` → `SUCCESS` | `FAILED`, `RUNNING`, `SCHEDULED`

---

#### `DELETE /delete_deployment/{repo_id}` 🔒
Stop the container, close the ngrok tunnel, remove all on-disk artefacts, and delete the DB record.

---

#### `GET /docker-logs/{repo_id}` 🔒
Fetch the last 200 lines of Docker container logs.

**Response `200`**
```json
{ "logs": ["Starting server...", "Listening on port 5000"] }
```

---

### Schedule

#### `POST /schedule` 🔒
Queue a deployment to run at a future time (minimum 30 minutes from now).

**Body (multipart/form-data)** — same fields as `/deploy`, plus:

| Field | Type | Required | Description |
|---|---|---|---|
| `scheduled_date` | string | Yes | `YYYY-MM-DD` |
| `scheduled_time` | string | Yes | ISO 8601 datetime e.g. `2025-06-01T14:30:00Z` |

---

### Subscription

#### `GET /check-subscription` 🔒
Returns active subscription details, or `404` if none.

#### `POST /create-order` 🔒
*(Stub)* Creates a PayPal order. Replace body with real PayPal SDK call.

#### `POST /capture-payment/{order_id}` 🔒
*(Stub)* Captures a PayPal payment and activates Premium. Replace with real capture call.

---

### Health

#### `GET /`
```json
{ "status": "ok", "service": "Deploy-On-Demand API", "version": "1.0.0" }
```

#### `GET /health`
```json
{ "status": "healthy", "timestamp": "2025-06-01T12:00:00+00:00" }
```

---

## MongoDB Collections

| Collection | Purpose | Key indexes |
|---|---|---|
| `users` | User accounts | `email` (unique), `username` (unique) |
| `deployments` | Deployment records | `user_id`, `repo_id` (unique), `timestamp` desc |
| `subscriptions` | Active subscriptions | `user_id` (unique) |
| `scheduled_deployments` | Queued deployments | `user_id`, `scheduled_time` |

---

## Adding a New Route

1. Create or open the relevant file in `routers/`
2. Add business logic to the appropriate file in `services/` (or create a new one)
3. Register the router in `main.py` with `app.include_router(...)`

Example — adding `GET /deployment_stats`:

```python
# routers/deployments.py
@router.get("/deployment_stats")
def deployment_stats(current_user: dict = Depends(get_current_user)):
    return deployment_service.get_stats(current_user["_id"])
```

```python
# services/deployment_stats.py  (new file)
from database import deployments_collection

def get_stats(user_id: str) -> dict:
    total   = deployments_collection.count_documents({"user_id": user_id})
    success = deployments_collection.count_documents({"user_id": user_id, "status": "SUCCESS"})
    failed  = deployments_collection.count_documents({"user_id": user_id, "status": "FAILED"})
    return {"total": total, "success": success, "failed": failed}
```

No changes needed in `main.py` since the router is already mounted.