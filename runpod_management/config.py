import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Configuration
RUNPOD_API_KEY = os.getenv("RUNPOD_API_KEY")

# Local Dashboard Authentication Config
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD")