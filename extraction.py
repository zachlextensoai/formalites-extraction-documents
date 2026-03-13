import json
from openai import OpenAI
from models import FieldDefinition, ExtractionResult


DEFAULT_INSTRUCTIONS = """Tu es un expert en analyse de documents juridiques français.
Extrais les informations suivantes du document fourni.

Pour chaque champ, fournis :
- La valeur extraite (exactement comme elle apparaît dans le document)
- La référence précise dans le PDF (numéro de page et emplacement)
- Un score de confiance entre 0 et 1

Si une information n'est pas trouvée dans le document, indique "Non trouvé" comme valeur avec une confiance de 0.

Réponds UNIQUEMENT avec un JSON valide au format suivant, sans aucun texte autour :
{
  "extractions": [
    {
      "field_id": "id_du_champ",
      "value": "la valeur extraite",
      "pdf_reference": "Page X, paragraphe/article Y",
      "confidence": 0.95
    }
  ]
}"""


def build_system_prompt(fields: list[FieldDefinition], instructions: str = "") -> str:
    """Build the extraction system prompt from instructions + field definitions."""
    fields_block = "\n".join(
        f'{i+1}. "{f.label}" (id: {f.id}): {f.description}'
        for i, f in enumerate(fields)
    )

    instr = instructions.strip() if instructions else DEFAULT_INSTRUCTIONS

    return f"""{instr}

Champs à extraire :
{fields_block}"""


def extract_fields(
    pdf_text: str,
    fields: list[FieldDefinition],
    api_key: str,
    model: str,
    instructions: str = "",
) -> list[ExtractionResult]:
    """Call the LLM via OpenRouter to extract field values from PDF text."""
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    system_prompt = build_system_prompt(fields, instructions)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Voici le texte du document :\n\n{pdf_text}"},
        ],
        temperature=0.1,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
        return [ExtractionResult(**item) for item in data.get("extractions", [])]
    except json.JSONDecodeError as e:
        raise ValueError(f"Réponse LLM invalide (JSON) : {e}\nRéponse brute : {raw[:500]}")
    except Exception as e:
        raise ValueError(f"Erreur de parsing : {e}\nRéponse brute : {raw[:500]}")
