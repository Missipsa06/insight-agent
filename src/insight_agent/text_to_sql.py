"""Text-to-SQL : question métier → SQL validé en lecture seule → résultat → commentaire.

Usage : uv run --env-file .env python -m insight_agent.text_to_sql "Ma question"
"""

import logging
import sys
from pathlib import Path
from typing import get_args

import duckdb
from pydantic import BaseModel

from insight_agent.data.generate import DB_PATH
from insight_agent.llm import appeler
from insight_agent.schemas import Categorie, Format, Region, Unite

log = logging.getLogger(__name__)

MAX_LIGNES = 50  # lignes envoyées au LLM commentateur
MAX_TENTATIVES = 2  # 1 essai + 1 self-repair


# --- Les types échangés ---------------------------------------------------------------


class RequeteSQL(BaseModel):
    """Sortie structurée du LLM : l'explication (CoT) AVANT le SQL."""

    explication: str
    sql: str


class Resultat(BaseModel):
    colonnes: list[str]
    lignes: list[tuple]
    total: int  # nombre total de lignes du résultat, AVANT troncature


class Commentaire(BaseModel):
    commentaire: str


# --- 1. Validation (sans LLM) ---------------------------------------------------------


def valider_sql(sql: str) -> str:
    """Renvoie le SQL s'il est sûr, lève ValueError sinon."""
    try:
        statements = duckdb.connect().extract_statements(sql)
    except duckdb.ParserException as e:
        raise ValueError(f"SQL invalide : {e}") from e
    if len(statements) != 1:
        raise ValueError("Le SQL doit contenir exactement une instruction.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise ValueError(f"Seul un SELECT est autorisé (reçu : {statements[0].type.name})")
    return sql


# --- 2. Exécution (sans LLM) ----------------------------------------------------------


def executer(sql: str, chemin: Path = DB_PATH, max_lignes: int = MAX_LIGNES) -> Resultat:
    """Exécute en lecture seule, sans accès aux fichiers, et renvoie au plus max_lignes lignes."""
    sql = sql.strip().rstrip(";")  # sinon "(… ;)" casse la sous-requête de comptage
    config = {"enable_external_access": False}  # bloque read_csv('C:/…'), COPY, httpfs…
    with duckdb.connect(str(chemin), read_only=True, config=config) as con:
        resultat = con.execute(sql)
        colonnes = [desc[0] for desc in resultat.description]
        lignes = resultat.fetchmany(max_lignes)
        # le comptage se fait DANS DuckDB : un seul nombre revient en Python
        # ponytail: requête exécutée 2 fois (lignes + comptage) ;
        # passer à count(*) OVER () si ça devient lent
        total = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
    return Resultat(colonnes=colonnes, lignes=lignes, total=total)


# --- 3. Prompt (sans LLM) -------------------------------------------------------------

ROLE = """Tu es un analyste panel consommateurs senior (achats des foyers, produits de grande consommation).
Tu traduis une question métier en UNE requête SQL DuckDB, en expliquant d'abord la démarche."""

REGLES = """Règles métier :
- Toujours pondérer : chaque foyer compte pour foyers.poids. Jamais COUNT(*) ni COUNT(DISTINCT id_foyer) pour un KPI.
- Pénétration = Σ poids des foyers acheteurs / Σ poids de tous les foyers. Pondérer chaque foyer UNE seule fois :
  WHERE id_foyer IN (SELECT DISTINCT id_foyer ...), jamais SUM(poids) après une jointure sur achats.
- PDM valeur = Σ (montant × poids) de la marque / Σ (montant × poids) de sa catégorie (le marché de référence).
- Volume = quantite × contenance (jointure produits). Une PDM volume se calcule dans une seule catégorie.
- Panier moyen = Σ (montant × poids) / Σ poids, sur les actes d'achat concernés (une ligne d'achats = un acte).
- Période non précisée : les 52 dernières semaines des données. Annonce-le dans l'explication.
Règles de forme :
- Une seule instruction SELECT (ou WITH ... SELECT), en SQL DuckDB.
- N'utilise que les tables, colonnes et valeurs listées. N'invente jamais une valeur.
- Question ambiguë (pas de catégorie pour une PDM, indicateur inconnu) ou hors du panel : ne devine pas,
  renvoie SELECT 'CLARIFICATION: <la question à poser>' AS message."""

SCHEMA = """foyers(id_foyer, region, taille_foyer, poids)
  -- poids : coefficient d'extrapolation ; un foyer représente `poids` foyers français (Σ poids ≈ 30 M)
achats(id_foyer, id_produit, id_enseigne, date_achat, quantite, montant)
  -- une ligne = un acte d'achat ; quantite = nombre d'unités ; montant = euros payés
produits(id_produit, libelle, marque, categorie, contenance, unite)
  -- contenance = taille d'UNE unité, exprimée en `unite` ; le prix n'est pas ici
enseignes(id_enseigne, nom, format)"""

EXEMPLES = """Question : Pénétration de Source Claire sur les 52 dernières semaines ?
explication : pénétration = Σ poids des foyers ayant acheté la marque / Σ poids de tous les foyers.
Chaque foyer est pondéré une seule fois (IN + DISTINCT). Période : 2024-12-30 → 2025-12-28.
SQL :
SELECT SUM(f.poids) / (SELECT SUM(poids) FROM foyers) AS penetration
FROM foyers f
WHERE f.id_foyer IN (
    SELECT DISTINCT a.id_foyer
    FROM achats a JOIN produits p USING (id_produit)
    WHERE p.marque = 'Source Claire'
      AND a.date_achat BETWEEN DATE '2024-12-30' AND DATE '2025-12-28'
)

Question : PDM valeur de Grand Arome dans le café en 2025 ?
explication : PDM valeur = Σ (montant × poids) de la marque / la même somme sur la catégorie cafe.
Le poids vient de foyers (jointure). Période : année 2025.
SQL :
SELECT SUM(CASE WHEN p.marque = 'Grand Arome' THEN a.montant * f.poids END)
     / SUM(a.montant * f.poids) AS pdm_valeur
FROM achats a
JOIN produits p USING (id_produit)
JOIN foyers f USING (id_foyer)
WHERE p.categorie = 'cafe'
  AND a.date_achat BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'

Question : PDM volume de Laiterie du Val dans l'ultra-frais, région Ouest ?
explication : PDM volume = Σ (quantite × contenance × poids) de la marque / la même somme sur ultra_frais.
Filtre région sur foyers. Période non précisée : 52 dernières semaines (2024-12-30 → 2025-12-28).
SQL :
SELECT SUM(CASE WHEN p.marque = 'Laiterie du Val' THEN a.quantite * p.contenance * f.poids END)
     / SUM(a.quantite * p.contenance * f.poids) AS pdm_volume
FROM achats a
JOIN produits p USING (id_produit)
JOIN foyers f USING (id_foyer)
WHERE p.categorie = 'ultra_frais'
  AND f.region = 'Ouest'
  AND a.date_achat BETWEEN DATE '2024-12-30' AND DATE '2025-12-28'

Question : Panier moyen par acte dans les enseignes discount ?
explication : panier moyen = Σ (montant × poids) / Σ poids sur les actes concernés.
Filtre format sur enseignes. Période non précisée : 52 dernières semaines.
SQL :
SELECT SUM(a.montant * f.poids) / SUM(f.poids) AS panier_moyen
FROM achats a
JOIN foyers f USING (id_foyer)
JOIN enseignes e USING (id_enseigne)
WHERE e.format = 'discount'
  AND a.date_achat BETWEEN DATE '2024-12-30' AND DATE '2025-12-28'

Question : Quelle est la meilleure marque ?
explication : « meilleure » est ambigu (quel indicateur ?) et aucune catégorie n'est donnée. Je ne devine pas.
SQL :
SELECT 'CLARIFICATION: Sur quelle catégorie et selon quel indicateur (PDM valeur, PDM volume, pénétration) ?' AS message"""


def construire_prompt(question: str) -> tuple[str, str]:
    """Renvoie (system, user) : le constant d'abord (cache), le rappel et la question à la fin."""
    marques = executer(
        "SELECT categorie, string_agg(DISTINCT marque, ', ' ORDER BY marque) "
        "FROM produits GROUP BY 1 ORDER BY 1"
    ).lignes
    ((debut, fin),) = executer("SELECT min(date_achat), max(date_achat) FROM achats").lignes
    liste_marques = "\n".join(f"- {cat} : {mq}" for cat, mq in marques)

    system = f"""<role>
{ROLE}
</role>
<regles>
{REGLES}
</regles>
<schema>
{SCHEMA}
</schema>
<valeurs>
region : {", ".join(get_args(Region))}
categorie : {", ".join(get_args(Categorie))}
format : {", ".join(get_args(Format))}
unite : {", ".join(get_args(Unite))}
marques par catégorie :
{liste_marques}
</valeurs>
<exemples>
{EXEMPLES}
</exemples>"""

    user = f"""<rappel>
Les données vont du {debut} au {fin}. Pondère toujours par foyers.poids.
N'utilise que les valeurs listées ; sinon, demande une clarification.
</rappel>
<question>{question}</question>"""
    return system, user


# --- 4. Appels LLM --------------------------------------------------------------------


def generer_sql(question: str, erreur: str | None = None) -> RequeteSQL:
    """Appelle le LLM. Si `erreur` est fourni (self-repair), l'ajoute au prompt."""
    system, user = construire_prompt(question)
    if erreur:
        user += f"\n<<erreur_precedente>Ta requête précédente a échoué : {erreur}. Corrige-la.</erreur_precedente>"
    return appeler(system, user, RequeteSQL)


REGLES_COMMENTAIRE = """Tu es un analyste panel consommateurs senior. Tu commentes un résultat de requête pour un client.
Règles :
- N'utilise QUE les chiffres présents dans <resultat>. Ne cite aucun chiffre qui n'y figure pas, n'en calcule pas de nouveaux.
- Les colonnes penetration, pdm_valeur et pdm_volume sont des ratios entre 0 et 1 : exprime-les en pourcentage
  avec une décimale (0.391 → 39,1 %). Les montants sont en euros.
- Rappelle toujours la période et le périmètre (marque, catégorie, filtres), tels qu'indiqués dans <explication>.
- 2 à 4 phrases, en français, sans jargon SQL.
- Si le résultat est un message de CLARIFICATION, reformule simplement la question à poser à l'utilisateur.
- Si le résultat est vide, dis-le et propose une cause probable (valeur ou période absente des données), sans inventer de chiffre."""


def commenter(question: str, requete: RequeteSQL, resultat: Resultat) -> str:
    """2e appel LLM : commente le résultat, en signalant la troncature éventuelle."""
    lignes = "\n".join(str(ligne) for ligne in resultat.lignes)
    troncature = ""
    if resultat.total > len(resultat.lignes):
        troncature = (
            f"\nATTENTION : résultat tronqué, {len(resultat.lignes)} lignes affichées sur "
            f"{resultat.total}. Ne conclus rien sur l'ensemble à partir de ces seules lignes."
        )
    user = f"""<question>{question}</question>
<explication>{requete.explication}</explication>
<resultat>
colonnes : {resultat.colonnes}
{lignes}{troncature}
</resultat>"""
    return appeler(REGLES_COMMENTAIRE, user, Commentaire).commentaire


# --- 5. La chaîne complète ------------------------------------------------------------


def repondre(question: str) -> tuple[RequeteSQL, Resultat, str]:
    """question → SQL validé → résultat → commentaire, avec au plus MAX_TENTATIVES essais."""
    erreur = None
    for tentative in range(1, MAX_TENTATIVES + 1):
        requete = generer_sql(question, erreur)
        try:
            resultat = executer(valider_sql(requete.sql))
        except (ValueError, duckdb.Error) as e:
            # le SQL fautif accompagne l'erreur : le LLM voit ce qu'il a écrit ET pourquoi c'est faux
            erreur = f"{e}\nRequête refusée :\n{requete.sql}"
            log.warning("Tentative %d/%d échouée : %s", tentative, MAX_TENTATIVES, e)
            continue
        return requete, resultat, commenter(question, requete, resultat)
    raise RuntimeError(f"Échec après {MAX_TENTATIVES} tentatives. Dernière erreur : {erreur}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    requete, resultat, commentaire = repondre(" ".join(sys.argv[1:]))
    print(f"\nexplication : {requete.explication}\n\nSQL :\n{requete.sql}\n")
    print(f"Résultat ({len(resultat.lignes)}/{resultat.total} lignes) : {resultat.colonnes}")
    for ligne in resultat.lignes[:10]:
        print("  ", ligne)
    print(f"\nCommentaire : {commentaire}")
