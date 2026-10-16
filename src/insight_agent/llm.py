"""Client LLM minimal : OpenRouter (API compatible OpenAI), sortie structurée, log des tokens.

Variables d'environnement (fichier .env, chargé par `uv run --env-file .env`) :
- OPENROUTER_API_KEY : obligatoire
- LLM_MODEL : identifiant OpenRouter du modèle (défaut : MODELE_DEFAUT)
"""

import logging
import os

import openai
from openai import OpenAI
from pydantic import BaseModel

log = logging.getLogger(__name__)

MODELE_DEFAUT = "anthropic/claude-sonnet-5.5"


def appeler[T: BaseModel](system: str, user: str, schema: type[T], temperature: float = 0.0) -> T:
    """Envoie un prompt et renvoie la réponse validée par `schema` (lève une erreur sinon)."""
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"]
    )
    modele = os.environ.get("LLM_MODEL", MODELE_DEFAUT)
    try:
        reponse = client.chat.completions.parse(
            model=modele,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format=schema,
            temperature=temperature,
            extra_body={"usage": {"include": True}},  # OpenRouter renvoie alors le coût réel
        )
    except openai.ContentFilterFinishReasonError as e:
        # refus du fournisseur (filtre de sécurité) : ce n'est PAS une erreur de SQL,
        # donc repondre() ne doit pas relancer
        message = e.completion.choices[0].message
        raise RuntimeError(f"Requête refusée par {modele} : {message.refusal}") from e
    u = reponse.usage
    log.info(
        "%s : %s tokens entrée, %s sortie, %s $",
        modele,
        u.prompt_tokens,
        u.completion_tokens,
        getattr(u, "cost", "?"),
    )
    resultat = reponse.choices[0].message.parsed
    if resultat is None:  # refus du modèle ou sortie non conforme : on échoue bruyamment
        raise ValueError(f"Réponse LLM inexploitable : {reponse.choices[0].message.content!r}")
    return resultat
