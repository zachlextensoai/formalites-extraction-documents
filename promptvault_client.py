import json
import requests
from models import FieldDefinition


class PromptVaultClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self):
        return {"x-api-key": self.api_key, "Content-Type": "application/json"}

    def get_prompt_data(self, slug: str, project_slug: str) -> dict | None:
        """Fetch full prompt data (instructions + fields) from PromptManager. Returns None on failure."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/prompts/{slug}",
                headers=self._headers(),
                params={"projectSlug": project_slug},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            content = json.loads(data["content"])
            return content
        except Exception:
            return None

    def get_fields(self, slug: str, project_slug: str) -> list[FieldDefinition] | None:
        """Fetch field definitions from PromptManager. Returns None on failure."""
        content = self.get_prompt_data(slug, project_slug)
        if content is None:
            return None
        return [FieldDefinition(**f) for f in content.get("fields", [])]

    def get_instructions(self, project_slug: str) -> str:
        """Fetch extraction instructions from the shared 'extraction-instructions' prompt."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/prompts/extraction-instructions",
                headers=self._headers(),
                params={"projectSlug": project_slug},
                timeout=15,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            return data.get("content", "")
        except Exception:
            return ""

    def save_instructions(
        self,
        project_slug: str,
        instructions: str,
        message: str = "Mis à jour depuis l'interface Extracteur",
    ) -> bool:
        """Save extraction instructions to the shared 'extraction-instructions' prompt."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/prompts/extraction-instructions",
                headers=self._headers(),
                json={
                    "content": instructions,
                    "message": message,
                    "projectSlug": project_slug,
                },
                timeout=15,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def save_fields(
        self,
        slug: str,
        project_slug: str,
        fields: list[FieldDefinition],
        doc_type_label: str = "",
        instructions: str = "",
        message: str = "Mis à jour depuis l'interface Extracteur",
    ) -> bool:
        """Save field definitions to PromptManager. Instructions are saved separately."""
        payload = {
            "version": "1.0",
            "doc_type": slug,
            "doc_type_label": doc_type_label,
            "fields": [f.model_dump() for f in fields],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        # Also save instructions to shared prompt if provided
        if instructions:
            self.save_instructions(project_slug, instructions, message)
        try:
            resp = requests.post(
                f"{self.base_url}/api/prompts/{slug}",
                headers=self._headers(),
                json={"content": content, "message": message, "projectSlug": project_slug},
                timeout=15,
            )
            return resp.status_code in (200, 201)
        except Exception:
            return False

    def ensure_project(self, name: str, slug: str) -> bool:
        """Create project if it doesn't exist. Returns True on success or already exists."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/projects",
                headers=self._headers(),
                json={"name": name, "slug": slug},
                timeout=15,
            )
            return resp.status_code in (200, 201, 409)
        except Exception:
            return False
