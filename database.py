from pymongo import MongoClient
from pymongo.collection import Collection
import os
from dotenv import load_dotenv
load_dotenv()
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "deploy_on_demand")

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

# Collections
users_collection: Collection = db["users"]
deployments_collection: Collection = db["deployments"]
subscriptions_collection: Collection = db["subscriptions"]
scheduled_deployments_collection: Collection = db["scheduled_deployments"]

# Indexes — run once on startup to keep queries fast
def init_indexes():
    users_collection.create_index("email", unique=True)
    users_collection.create_index("username", unique=True)
    deployments_collection.create_index("user_id")
    deployments_collection.create_index("repo_id", unique=True)
    subscriptions_collection.create_index("user_id", unique=True)
    scheduled_deployments_collection.create_index("user_id")
    scheduled_deployments_collection.create_index("scheduled_time")