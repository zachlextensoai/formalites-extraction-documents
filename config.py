import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
PROMPTVAULT_BASE_URL = os.getenv("PROMPTVAULT_BASE_URL", "https://prompting-manager.replit.app")
PROMPTVAULT_API_KEY = os.getenv("PROMPTVAULT_API_KEY", "")
PROMPTVAULT_PROJECT_SLUG = os.getenv("PROMPTVAULT_PROJECT_SLUG", "extracteur-documents")

AVAILABLE_MODELS = {
    "Gemini 2.5 Flash": "google/gemini-2.5-flash",
    "Claude Sonnet 4.6": "anthropic/claude-sonnet-4-6",
}

DOC_TYPES = {
    "PV Assemblée Générale": "pv-assemblee-generale",
    "Statuts de Société": "statuts-societe",
    "Ordre client": "ordre-client",
}
