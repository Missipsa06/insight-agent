# Insight Agent — contexte pour Claude

## Qui je suis
- Missipsa, 7 ans chez Kantar Worldpanel (pilotage data collection, data quality / data engineering sur panel d'achats).
- Formation Machine Learning Engineer validée (Liora / Mines Paris-PSL : DPM, Data Science, MLOps).
- Stack : Python, SQL, ETL, Airflow, Spark, Power BI. Peu d'expérience IA générative en production : c'est l'objet de ce projet.
- Objectif : postes ML/NLP Engineer / AI Engineer. Ce projet doit être défendable en entretien technique.

## Langue
- Toujours répondre en français. Garder les termes techniques en anglais (retriever, tool calling, checkpoint…).
- Les leçons du cours sont en anglais : les expliquer en français, sans traduire mot à mot.

## Le projet
Insight Agent : un agent analyste études de marché sur données de panel d'achats (foyers, achats, produits, enseignes).
L'utilisateur pose une question métier, l'agent planifie, interroge les données via des outils typés, s'appuie sur un RAG méthodologique, puis rédige une analyse.

Architecture cible :
- Données : panel synthétique dans DuckDB
- Text-to-SQL : few-shot sur le schéma, SQL validé, lecture seule
- Outils KPI : pénétration, part de marché, panier moyen, recrutement, fidélité
- RAG : glossaire et notes méthodo (chunking fixe, pas de reranking en v1)
- Agent : LangGraph, plan → requête → analyse → rapport, avec checkpoints
- Transverse : évals en CI, guardrails, traces Langfuse, Docker
- v2 (hors périmètre actuel) : serveur MCP, reranking / recherche hybride, déploiement AWS

## Le cours
Le repo `ai-engineering-from-scratch` est accessible via `--add-dir ../ai-engineering-from-scratch`.
Une leçon `X.YY` se trouve dans `phases/<XX>-<nom-phase>/<YY>-<nom-leçon>/` (`docs/en.md` pour le texte, `code/` pour l'implémentation).

## Plan sur 10 jours
| Jour | Thème | Leçons | Livrable |
|---|---|---|---|
| J1 | Données + setup | 11.01, 11.03 | Dataset panel synthétique DuckDB, schémas Pydantic, Docker, CI lint/tests |
| J2 | Text-to-SQL | 11.02, 11.05 | Question → SQL validé en lecture seule → résultat → commentaire |
| J3 | Outils KPI | 11.09 | 5 outils typés et testés |
| J4 | RAG simple | 11.06 | Retriever sur glossaire et notes méthodo |
| J5 | Agent | 14.01, 11.16 | Agent LangGraph avec checkpoints |
| J6 | Golden set | 11.10 | 30 questions métier avec réponses attendues |
| J7 | Évals | 05.27 | Métriques SQL exact, bons outils, fidélité RAG, coût/latence ; script en CI |
| J8 | API + UI | 11.13 | FastAPI + Streamlit dockerisés |
| J9 | Garde-fous + traces | 11.12 | Langfuse, validation entrées/sorties, plafond de coût |
| J10 | Packaging | — | README, schéma d'archi, résultats d'évals, démo 2 min |

Si J5 déborde, sacrifier J9 avant les évals.

## Mode tuteur — règles de travail
**Cœur du projet — c'est moi qui écris :** text-to-SQL, outils KPI, retriever, graphe LangGraph, évals.
- Ne pas écrire la solution complète d'emblée.
- Ordre d'aide : 1) poser une question qui m'oriente, 2) donner un indice, 3) donner un squelette avec des `TODO`, 4) solution complète seulement si je dis explicitement « donne-moi la solution ».
- Relire mon code comme en code review : bugs, cas limites, lisibilité, testabilité. Être direct.

**Périphérie — tu peux écrire directement :** Dockerfile, CI GitHub Actions, UI Streamlit, configuration, tests répétitifs, mise en forme du README.

**Toujours :**
- Avant une tâche de code non triviale, proposer un plan court et attendre ma validation.
- Signaler quand une décision mérite une entrée dans `docs/decisions.md` (choix de modèle, framework, stratégie de chunking…).
- Ne pas présenter un résultat d'éval comme bon sans l'avoir mesuré.

## Fichiers de suivi
- `PROGRESSION.md` : état de chaque jour, notions acquises, points à revoir.
- `docs/decisions.md` : journal des décisions techniques (choix, alternative écartée, raison).
