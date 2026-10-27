"""Outils KPI panel : 5 fonctions pures que le LLM peut appeler (function calling).

Un outil = un modèle Pydantic d'arguments (source du JSON Schema montré au LLM et de la
validation) + une fonction `(con, args) -> dict` qui exécute un SQL écrit à la main, à
paramètres liés. Le LLM choisit l'outil et remplit les arguments ; il ne calcule rien.

Usage : uv run python -m insight_agent.kpi calculer_penetration '{"marque": "source claire"}'
"""

import difflib
import json
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from insight_agent.data.generate import DB_PATH

Periode = Literal["CAM", "P-1", "YTD"]
Mesure = Literal["valeur", "volume"]

SEUIL_BASE_FAIBLE = 30  # acheteurs panel (non pondérés) sous lesquels un chiffre n'est pas fiable
SEMAINE = timedelta(weeks=1)

# Mesure pondérée, choisie par une clé du Literal : le LLM ne saisit jamais de SQL.
MESURES = {
    "valeur": "a.montant * f.poids",
    "volume": "a.quantite * p.contenance * f.poids",
}
# Filtre optionnel partagé : $id_enseigne NULL = toutes enseignes.
FILTRE_ENSEIGNE = "AND ($id_enseigne IS NULL OR a.id_enseigne = $id_enseigne)"


# --- Erreurs renvoyées au LLM -----------------------------------------------------------


class ErreurOutil(Exception):
    """Argument du LLM qui ne correspond à rien dans les données : renvoyé au LLM comme
    résultat d'outil (code + message + suggestions) pour qu'il corrige son appel.

    Une panne du système (base absente, bug) reste une exception ordinaire : le LLM n'y peut rien.
    """

    def __init__(self, code: str, message: str, suggestions: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.suggestions = suggestions or []

    def en_dict(self) -> dict:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "suggestions": self.suggestions,
        }


# --- Périodes -----------------------------------------------------------------------------


def etendue(con: duckdb.DuckDBPyConnection) -> tuple[date, date]:
    """Premier et dernier jour de données, sur TOUTE la table achats (jamais après un filtre)."""
    return con.execute("SELECT min(date_achat), max(date_achat) FROM achats").fetchone()


def bornes_periode(
    etendue: tuple[date, date], periode: Periode, decalage_semaines: int = 0
) -> tuple[date, date]:
    """Bornes incluses d'une période, en semaines complètes du lundi au dimanche.

    Fin = dernier dimanche ≤ dernier jour de données (une semaine en cours est exclue).
    CAM = les 52 semaines qui finissent là ; P-1 = les 52 semaines précédentes (CAM A-1) ;
    YTD = de la semaine ISO 1 de l'année jusqu'à la fin. `decalage_semaines` recule la période
    (52 = même période un an avant). Lève ErreurOutil si la période sort des données.
    """
    premier, dernier = etendue
    fin = dernier - timedelta(days=(dernier.weekday() + 1) % 7)  # lundi=0 … dimanche=6
    fin_donnees = fin
    if periode == "YTD":
        debut = date.fromisocalendar(fin.isocalendar().year, 1, 1)
    else:
        debut = fin - 52 * SEMAINE + timedelta(days=1)
    if periode == "P-1":
        decalage_semaines += 52
    debut -= decalage_semaines * SEMAINE
    fin -= decalage_semaines * SEMAINE
    lundi_donnees = premier - timedelta(days=premier.weekday())
    if debut < lundi_donnees:
        raise ErreurOutil(
            "PERIODE_HORS_DONNEES",
            f"Données disponibles du {lundi_donnees} au {fin_donnees} : "
            f"la période {debut} → {fin} n'est pas couverte.",
        )
    return debut, fin


# --- Résolution des valeurs saisies par le LLM -------------------------------------------


def _cle(texte: str) -> str:
    """Forme de comparaison : sans accents, casse, espaces superflus ni apostrophe typographique."""
    texte = texte.replace("’", "'")
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join(sans_accents.casefold().split())


def _resoudre(valeurs: list[str], saisie: str, code: str, quoi: str) -> str:
    """Valeur canonique du référentiel qui correspond à la saisie, ou ErreurOutil avec suggestions.

    Les DEUX côtés sont normalisés : 'source claire' trouve 'Source Claire'.
    """
    par_cle = {_cle(v): v for v in valeurs}
    if _cle(saisie) in par_cle:
        return par_cle[_cle(saisie)]
    suggestions = [par_cle[c] for c in difflib.get_close_matches(_cle(saisie), par_cle, n=3)]
    # ponytail: liste complète si aucune suggestion ; trop long avec des milliers de marques
    aide = (
        f"Vouliez-vous dire : {', '.join(suggestions)} ?"
        if suggestions
        else f"Valeurs possibles : {', '.join(sorted(valeurs))}."
    )
    raise ErreurOutil(code, f"{quoi} '{saisie}' inconnue. {aide}", suggestions)


def resoudre_marque(con: duckdb.DuckDBPyConnection, saisie: str) -> tuple[str, str]:
    """(marque canonique, sa catégorie). Catégorie déduite : le LLM ne peut pas se tromper."""
    marques = [r[0] for r in con.execute("SELECT DISTINCT marque FROM produits").fetchall()]
    marque = _resoudre(marques, saisie, "MARQUE_INCONNUE", "Marque")
    categories = con.execute(
        "SELECT DISTINCT categorie FROM produits WHERE marque = $marque", {"marque": marque}
    ).fetchall()
    if len(categories) != 1:  # incohérence du référentiel : panne, pas une erreur du LLM
        raise ValueError(f"La marque {marque} appartient à {len(categories)} catégories.")
    return marque, categories[0][0]


def resoudre_enseigne(
    con: duckdb.DuckDBPyConnection, saisie: str | None
) -> tuple[str | None, int | None]:
    """(nom canonique, id) de l'enseigne, ou (None, None) si aucune n'est demandée."""
    if saisie is None:
        return None, None
    ids = dict(con.execute("SELECT nom, id_enseigne FROM enseignes").fetchall())
    nom = _resoudre(list(ids), saisie, "ENSEIGNE_INCONNUE", "Enseigne")
    return nom, ids[nom]


def _avec_evolution(
    mesurer: Callable[[date, date], dict],
    etendue: tuple[date, date],
    periode: Periode,
    cles: list[str],
) -> dict:
    """Mesure la période et, si les données couvrent la même période un an avant, l'évolution
    des `cles` : calculée ici, jamais par le LLM. Sans données A-1, pas d'évolution (pas d'erreur).
    """
    debut, fin = bornes_periode(etendue, periode)
    resultat = {"periode": periode, "debut": str(debut), "fin": str(fin), **mesurer(debut, fin)}
    try:
        reference = mesurer(*bornes_periode(etendue, periode, decalage_semaines=52))
    except ErreurOutil:
        return resultat
    for cle in cles:
        if resultat[cle] is not None and reference[cle] is not None:
            resultat[f"{cle}_a1"] = reference[cle]
            resultat[f"evolution_{cle}"] = round(resultat[cle] - reference[cle], 2)
    return resultat


def _ratio(numerateur: float, denominateur: float, facteur: float = 1, decimales: int = 2):
    """numérateur / dénominateur arrondi, ou None si le dénominateur est nul (rien à mesurer)."""
    return round(facteur * numerateur / denominateur, decimales) if denominateur else None


# --- Arguments des outils (source du JSON Schema envoyé au LLM) --------------------------


class _Args(BaseModel):
    # un argument inventé par le LLM est refusé, pas ignoré en silence
    model_config = ConfigDict(extra="forbid")


ArgMarque = Annotated[
    str, Field(description="Marque, ex. 'Source Claire'. Casse et accents tolérés.")
]
ArgPeriode = Annotated[
    Periode,
    Field(
        description="CAM = 52 dernières semaines (défaut) ; P-1 = les 52 semaines précédentes ;"
        " YTD = depuis la 1re semaine de l'année."
    ),
]
ArgEnseigne = Annotated[
    str | None,
    Field(description="Optionnel : restreindre à une enseigne, ex. 'MaxiPrix'. Absent = toutes."),
]
ArgMesure = Annotated[
    Mesure, Field(description="valeur = en euros (défaut) ; volume = en litres ou kg.")
]


class ArgsPenetration(_Args):
    marque: ArgMarque
    periode: ArgPeriode = "CAM"
    enseigne: ArgEnseigne = None


class ArgsPartDeMarche(_Args):
    marque: ArgMarque
    periode: ArgPeriode = "CAM"
    mesure: ArgMesure = "valeur"
    enseigne: ArgEnseigne = None


class ArgsPanierMoyen(_Args):
    marque: Annotated[
        str | None,
        Field(description="Optionnel : actes contenant cette marque. Absent = tous produits."),
    ] = None
    periode: ArgPeriode = "CAM"
    enseigne: ArgEnseigne = None


class ArgsRecrutement(_Args):
    marque: ArgMarque
    periode: Annotated[
        Literal["CAM", "YTD"],
        Field(description="Période courante, comparée à la même période un an avant (défaut CAM)."),
    ] = "CAM"
    enseigne: ArgEnseigne = None


class ArgsFidelite(_Args):
    marque: ArgMarque
    periode: ArgPeriode = "CAM"
    mesure: ArgMesure = "valeur"
    enseigne: ArgEnseigne = None


# --- Les 5 outils ---------------------------------------------------------------------------


def calculer_penetration(con: duckdb.DuckDBPyConnection, args: ArgsPenetration) -> dict:
    """Pénétration = Σ poids des foyers acheteurs de la marque / Σ poids de tous les foyers.

    Chaque foyer acheteur est compté une fois (sous-requête IN), quel que soit son nombre d'achats.
    """
    marque, categorie = resoudre_marque(con, args.marque)
    enseigne, id_enseigne = resoudre_enseigne(con, args.enseigne)
    base = con.execute("SELECT SUM(poids) FROM foyers").fetchone()[0]

    def mesurer(debut: date, fin: date) -> dict:
        acheteurs, n = con.execute(
            f"""
            SELECT coalesce(SUM(f.poids), 0), count(*) FROM foyers f
            WHERE f.id_foyer IN (
                SELECT a.id_foyer FROM achats a JOIN produits p USING (id_produit)
                WHERE p.marque = $marque AND a.date_achat BETWEEN $debut AND $fin
                {FILTRE_ENSEIGNE})
            """,
            {"marque": marque, "debut": debut, "fin": fin, "id_enseigne": id_enseigne},
        ).fetchone()
        return {
            "penetration_pct": _ratio(acheteurs, base, 100),
            "acheteurs": round(acheteurs),  # foyers extrapolés à la France
            "acheteurs_panel": n,
            "base_faible": n < SEUIL_BASE_FAIBLE,
        }

    perimetre = {"marque": marque, "categorie": categorie, "enseigne": enseigne}
    return perimetre | _avec_evolution(mesurer, etendue(con), args.periode, ["penetration_pct"])


def calculer_part_de_marche(con: duckdb.DuckDBPyConnection, args: ArgsPartDeMarche) -> dict:
    """PDM = ventes de la marque / ventes de SA catégorie, sur le même périmètre (période,
    enseigne) : un seul WHERE pour les deux, le filtre marque est dans le CASE du numérateur.
    """
    marque, categorie = resoudre_marque(con, args.marque)
    enseigne, id_enseigne = resoudre_enseigne(con, args.enseigne)
    mesure = MESURES[args.mesure]

    def mesurer(debut: date, fin: date) -> dict:
        ventes_marque, ventes_categorie, unite, n = con.execute(
            f"""
            SELECT coalesce(SUM(CASE WHEN p.marque = $marque THEN {mesure} END), 0),
                   coalesce(SUM({mesure}), 0),
                   any_value(p.unite),
                   count(DISTINCT a.id_foyer) FILTER (WHERE p.marque = $marque)
            FROM achats a JOIN produits p USING (id_produit) JOIN foyers f USING (id_foyer)
            WHERE p.categorie = $categorie AND a.date_achat BETWEEN $debut AND $fin
            {FILTRE_ENSEIGNE}
            """,
            {
                "marque": marque,
                "categorie": categorie,
                "debut": debut,
                "fin": fin,
                "id_enseigne": id_enseigne,
            },
        ).fetchone()
        return {
            "pdm_pct": _ratio(ventes_marque, ventes_categorie, 100),
            "ventes_marque": round(ventes_marque),
            "ventes_categorie": round(ventes_categorie),
            "unite": "€" if args.mesure == "valeur" else unite,
            "acheteurs_panel": n,
            "base_faible": n < SEUIL_BASE_FAIBLE,
        }

    perimetre = {"marque": marque, "categorie": categorie, "enseigne": enseigne}
    perimetre["mesure"] = args.mesure
    return perimetre | _avec_evolution(mesurer, etendue(con), args.periode, ["pdm_pct"])


def calculer_panier_moyen(con: duckdb.DuckDBPyConnection, args: ArgsPanierMoyen) -> dict:
    """Décomposition des ventes : dépense = acheteurs × fréquence × panier moyen.

    Un acte d'achat = un passage en caisse = (foyer, date, enseigne) distinct, PAS une ligne
    d'achats (une ligne = un article). Fréquence = actes / acheteurs ; panier moyen = dépense /
    actes ; dépense par acheteur = dépense / acheteurs. Tout est pondéré, chaque acte une fois.
    """
    marque, categorie = resoudre_marque(con, args.marque) if args.marque else (None, None)
    enseigne, id_enseigne = resoudre_enseigne(con, args.enseigne)

    def mesurer(debut: date, fin: date) -> dict:
        depense, actes, acheteurs, n = con.execute(
            f"""
            WITH lignes AS (
                SELECT a.id_foyer, a.date_achat, a.id_enseigne, a.montant, f.poids
                FROM achats a JOIN produits p USING (id_produit) JOIN foyers f USING (id_foyer)
                WHERE ($marque IS NULL OR p.marque = $marque)
                  AND a.date_achat BETWEEN $debut AND $fin
                  {FILTRE_ENSEIGNE}
            ),
            actes AS (SELECT DISTINCT id_foyer, date_achat, id_enseigne, poids FROM lignes),
            acheteurs AS (SELECT DISTINCT id_foyer, poids FROM lignes)
            SELECT coalesce((SELECT SUM(montant * poids) FROM lignes), 0),
                   coalesce((SELECT SUM(poids) FROM actes), 0),
                   coalesce((SELECT SUM(poids) FROM acheteurs), 0),
                   (SELECT count(*) FROM acheteurs)
            """,
            {"marque": marque, "debut": debut, "fin": fin, "id_enseigne": id_enseigne},
        ).fetchone()
        return {
            "depense": round(depense),  # euros extrapolés
            "acheteurs": round(acheteurs),
            "actes": round(actes),
            "frequence": _ratio(actes, acheteurs),
            "panier_moyen": _ratio(depense, actes),
            "depense_par_acheteur": _ratio(depense, acheteurs),
            "acheteurs_panel": n,
            "base_faible": n < SEUIL_BASE_FAIBLE,
        }

    perimetre = {"marque": marque, "categorie": categorie, "enseigne": enseigne}
    cles = ["panier_moyen", "frequence", "depense_par_acheteur"]
    return perimetre | _avec_evolution(mesurer, etendue(con), args.periode, cles)


def calculer_recrutement(con: duckdb.DuckDBPyConnection, args: ArgsRecrutement) -> dict:
    """Recrutés = acheteurs de la période absents un an avant ; perdus = l'inverse ; conservés =
    acheteurs des deux. Acheteurs courants − acheteurs de référence = recrutés − perdus.
    """
    marque, categorie = resoudre_marque(con, args.marque)
    enseigne, id_enseigne = resoudre_enseigne(con, args.enseigne)
    debut, fin = bornes_periode(etendue(con), args.periode)
    # la référence DOIT exister : sans elle, ce KPI n'a pas de sens (erreur, pas valeur nulle)
    debut_ref, fin_ref = bornes_periode(etendue(con), args.periode, decalage_semaines=52)
    # ponytail: panel à poids fixes et sans sorties ; un vrai panel exigerait un échantillon
    # constant (foyers présents sur les deux périodes), sinon un « perdu » peut être un sortant
    recrutes, perdus, conserves, n = con.execute(
        f"""
        WITH statut AS (
            SELECT a.id_foyer,
                   bool_or(a.date_achat BETWEEN $debut AND $fin) AS courant,
                   bool_or(a.date_achat BETWEEN $debut_ref AND $fin_ref) AS reference
            FROM achats a JOIN produits p USING (id_produit)
            WHERE p.marque = $marque
              AND (a.date_achat BETWEEN $debut AND $fin
                   OR a.date_achat BETWEEN $debut_ref AND $fin_ref)
              {FILTRE_ENSEIGNE}
            GROUP BY a.id_foyer
        )
        SELECT coalesce(SUM(f.poids) FILTER (WHERE courant AND NOT reference), 0),
               coalesce(SUM(f.poids) FILTER (WHERE reference AND NOT courant), 0),
               coalesce(SUM(f.poids) FILTER (WHERE courant AND reference), 0),
               count(*) FILTER (WHERE courant)
        FROM statut JOIN foyers f USING (id_foyer)
        """,
        {
            "marque": marque,
            "debut": debut,
            "fin": fin,
            "debut_ref": debut_ref,
            "fin_ref": fin_ref,
            "id_enseigne": id_enseigne,
        },
    ).fetchone()
    acheteurs_courant = recrutes + conserves
    return {
        "marque": marque,
        "categorie": categorie,
        "enseigne": enseigne,
        "periode": args.periode,
        "debut": str(debut),
        "fin": str(fin),
        "debut_reference": str(debut_ref),
        "fin_reference": str(fin_ref),
        "recrutes": round(recrutes),  # foyers extrapolés
        "perdus": round(perdus),
        "conserves": round(conserves),
        "acheteurs_courant": round(acheteurs_courant),
        "acheteurs_reference": round(perdus + conserves),
        "solde_net": round(recrutes - perdus),
        "taux_recrutement_pct": _ratio(recrutes, acheteurs_courant, 100),
        "acheteurs_panel": n,
        "base_faible": n < SEUIL_BASE_FAIBLE,
    }


def calculer_fidelite(con: duckdb.DuckDBPyConnection, args: ArgsFidelite) -> dict:
    """Fidélité des acheteurs de la marque, sur la période :
    - taux de nourriture = leurs achats de la marque / leurs achats de la catégorie ;
    - exclusifs = part (pondérée) de ces acheteurs qui n'ont acheté QUE la marque dans la catégorie.
    """
    marque, categorie = resoudre_marque(con, args.marque)
    enseigne, id_enseigne = resoudre_enseigne(con, args.enseigne)
    mesure = MESURES[args.mesure]

    def mesurer(debut: date, fin: date) -> dict:
        v_marque, v_categorie, poids_exclusifs, poids_acheteurs, n = con.execute(
            f"""
            WITH par_foyer AS (
                SELECT a.id_foyer, any_value(f.poids) AS poids,
                       SUM({mesure}) FILTER (WHERE p.marque = $marque) AS v_marque,
                       SUM({mesure}) AS v_categorie,
                       bool_and(p.marque = $marque) AS exclusif
                FROM achats a JOIN produits p USING (id_produit) JOIN foyers f USING (id_foyer)
                WHERE p.categorie = $categorie AND a.date_achat BETWEEN $debut AND $fin
                  {FILTRE_ENSEIGNE}
                GROUP BY a.id_foyer
            )
            SELECT coalesce(SUM(v_marque), 0), coalesce(SUM(v_categorie), 0),
                   coalesce(SUM(poids) FILTER (WHERE exclusif), 0),
                   coalesce(SUM(poids), 0), count(*)
            FROM par_foyer
            WHERE v_marque IS NOT NULL  -- seulement les acheteurs de la marque
            """,
            {
                "marque": marque,
                "categorie": categorie,
                "debut": debut,
                "fin": fin,
                "id_enseigne": id_enseigne,
            },
        ).fetchone()
        return {
            "taux_nourriture_pct": _ratio(v_marque, v_categorie, 100),
            "exclusifs_pct": _ratio(poids_exclusifs, poids_acheteurs, 100),
            "acheteurs_panel": n,
            "base_faible": n < SEUIL_BASE_FAIBLE,
        }

    perimetre = {"marque": marque, "categorie": categorie, "enseigne": enseigne}
    perimetre["mesure"] = args.mesure
    cles = ["taux_nourriture_pct", "exclusifs_pct"]
    return perimetre | _avec_evolution(mesurer, etendue(con), args.periode, cles)


# --- Registre : la liste blanche des outils -------------------------------------------------


@dataclass(frozen=True)
class Outil:
    args: type[_Args]
    fonction: Callable[[duckdb.DuckDBPyConnection, _Args], dict]
    description: str  # le « prompt » du choix d'outil


_COMMUN = (
    " Les évolutions (evolution_*) comparent à la même période un an avant, en points pour les %."
    " base_faible = moins de 30 acheteurs dans le panel : chiffre peu fiable, le signaler."
)

REGISTRE: dict[str, Outil] = {
    "calculer_penetration": Outil(
        ArgsPenetration,
        calculer_penetration,
        "Pénétration d'une marque : part des foyers (pondérés) qui l'ont achetée au moins une fois"
        " sur la période, en %, avec le nombre de foyers acheteurs extrapolé à la France."
        " À utiliser pour : pénétration, combien de foyers achètent, taux d'acheteurs."
        " Ne pas utiliser pour une part des ventes : voir calculer_part_de_marche." + _COMMUN,
    ),
    "calculer_part_de_marche": Outil(
        ArgsPartDeMarche,
        calculer_part_de_marche,
        "Part de marché (PDM) d'une marque dans sa catégorie (déduite de la marque), en valeur"
        " (défaut) ou en volume. À utiliser pour : PDM, part de marché, poids, combien pèse."
        " Ne pas utiliser pour une part de foyers acheteurs : voir calculer_penetration." + _COMMUN,
    ),
    "calculer_panier_moyen": Outil(
        ArgsPanierMoyen,
        calculer_panier_moyen,
        "Panier moyen et décomposition des ventes : dépense = acheteurs × fréquence × panier."
        " Panier moyen = dépense par passage en caisse ; fréquence = passages par acheteur."
        " Pour une marque ou pour tous les produits, éventuellement dans une enseigne."
        " À utiliser pour expliquer une évolution des ventes (plus d'acheteurs ? plus souvent ?"
        " plus cher ?)." + _COMMUN,
    ),
    "calculer_recrutement": Outil(
        ArgsRecrutement,
        calculer_recrutement,
        "Dynamique d'acheteurs d'une marque face à la même période un an avant : recrutés"
        " (nouveaux), perdus, conservés, solde net. À utiliser pour : recrutement, conquête,"
        " perte d'acheteurs, pourquoi le nombre d'acheteurs bouge."
        " base_faible = moins de 30 acheteurs dans le panel : chiffre peu fiable.",
    ),
    "calculer_fidelite": Outil(
        ArgsFidelite,
        calculer_fidelite,
        "Fidélité des acheteurs d'une marque : taux de nourriture (part de la marque dans leurs"
        " achats de la catégorie) et part d'acheteurs exclusifs. À utiliser pour : fidélité,"
        " part de besoin, exclusivité. Une petite marque a normalement une fidélité plus basse"
        " (double jeopardy) : comparer à des marques de taille proche." + _COMMUN,
    ),
}


def definitions_outils() -> list[dict]:
    """Définitions au format OpenAI (paramètre `tools`), générées depuis les modèles Pydantic."""
    return [
        {
            "type": "function",
            "function": {
                "name": nom,
                "description": outil.description,
                "parameters": outil.args.model_json_schema(),
            },
        }
        for nom, outil in REGISTRE.items()
    ]


def executer(con: duckdb.DuckDBPyConnection, nom: str, arguments_json: str) -> dict:
    """Exécute un appel d'outil du LLM. Arguments au format OpenAI : une CHAÎNE JSON.

    Erreur du LLM (outil inconnu, JSON ou argument invalide, valeur absente des données)
    → dict d'erreur qu'il peut corriger. Toute autre exception remonte : c'est une panne.
    """
    if nom not in REGISTRE:
        return ErreurOutil("OUTIL_INCONNU", f"Outil '{nom}' inconnu.", sorted(REGISTRE)).en_dict()
    outil = REGISTRE[nom]
    try:
        args = outil.args.model_validate_json(arguments_json)  # parse + valide en une étape
        return outil.fonction(con, args)
    except ValidationError as e:
        # loc vide = erreur sur tout le document (JSON invalide) : on n'affiche que le message
        details = "; ".join(
            f"{'.'.join(map(str, d['loc']))} : {d['msg']}" if d["loc"] else d["msg"]
            for d in e.errors()
        )
        return ErreurOutil("ARGUMENTS_INVALIDES", f"Arguments invalides : {details}").en_dict()
    except ErreurOutil as e:
        return e.en_dict()


def connecter(chemin: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Connexion en lecture seule, sans accès aux fichiers (même config que le text-to-SQL)."""
    return duckdb.connect(str(chemin), read_only=True, config={"enable_external_access": False})


if __name__ == "__main__":
    with connecter() as con:
        print(json.dumps(executer(con, sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))
