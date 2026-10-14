# Progression — Insight Agent

| Jour | Thème | Statut | Notions acquises | Points à revoir |
|---|---|---|---|---|
| J1 | Données + setup | Fait (CI écrite, pas encore exécutée : pas de remote) | Défense en profondeur (le prompt ne protège rien, lecture seule côté exécution) ; décodage contraint ; Pydantic (`Field`, `Literal`, alias = source de vérité unique, `get_args`) ; schéma en étoile ; génération contrainte par référentiel ; modèle NBD Gamma-Poisson ; poids d'extrapolation ; numpy vectorisé (`np.repeat`, `np.where`, fancy indexing) ; validation 2 niveaux (Pydantic échantillon + SQL 100 %) ; tests d'invariants, échec volontaire / mutation testing | Effet réel de la température (reproductibilité, pas anti-hallucination) ; définitions pénétration vs PDM (valeur/volume, marché de référence) ; JSON mode ≠ schéma, forme ≠ vérité ; `=` vs `.append` dans une boucle ; test sans `assert` toujours vert ; argumenter en entretien avec du concret et des mesures. À faire : docstrings métier de `schemas.py` (J2), entrées `docs/decisions.md`, quiz Carnet 11.01/11.03 en révision |
| J2 | Text-to-SQL | À faire | | |
| J3 | Outils KPI | À faire | | |
| J4 | RAG simple | À faire | | |
| J5 | Agent | À faire | | |
| J6 | Golden set | À faire | | |
| J7 | Évals | À faire | | |
| J8 | API + UI | À faire | | |
| J9 | Garde-fous + traces | À faire | | |
| J10 | Packaging | À faire | | |

Statuts : À faire · En cours · Fait · Reporté
