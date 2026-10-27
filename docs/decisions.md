# Journal des décisions techniques

Une entrée par choix structurant. C'est la matière à réutiliser en entretien.

## Modèle d'entrée

### AAAA-MM-JJ — Titre de la décision
- **Contexte :** quel problème se posait.
- **Choix :** ce qui a été retenu.
- **Alternative écartée :** quoi, et pourquoi.
- **Conséquence :** ce que ça implique (coût, limite, à revoir en v2).

---

## J1 — Données

### 2026-10-12 — Schéma en étoile plutôt qu'une table plate
- **Contexte :** modéliser un panel d'achats (foyers, produits, enseignes, actes d'achat) que le LLM devra interroger en SQL.
- **Choix :** une table de faits `achats` (identifiants + mesures) et trois dimensions `foyers`, `produits`, `enseignes`.
- **Alternative écartée :** une table dénormalisée avec les noms en clair. Les noms changent (renommage d'enseigne, formats), les doublons faussent les KPI, et le LLM génère plus sûrement des jointures classiques.
- **Conséquence :** chaque KPI demande des jointures ; l'intégrité est contrôlée par des tests (clés orphelines, unicité), DuckDB ne posant pas de contrainte de clé.

### 2026-10-12 — Le prix n'est pas un attribut du produit
- **Contexte :** calculer des montants réalistes et une PDM valeur.
- **Choix :** le prix payé est porté par la transaction (`montant / quantite`) ; le prix de référence ne sert qu'au générateur et n'est jamais stocké.
- **Alternative écartée :** un champ `prix` dans `Produit`. Faux métier : un même produit se vend à des prix différents selon l'enseigne, la date et les promotions.
- **Conséquence :** on ne stocke que ce qu'un panel observe réellement.

### 2026-10-12 — Génération contrainte par référentiel, fréquence d'achat en NBD
- **Contexte :** produire des données synthétiques réalistes, reproductibles et dont la vérité terrain est connue.
- **Choix :** référentiels écrits à la main (`CATALOGUE`, `ENSEIGNES`), aléatoire seulement à l'intérieur ; nombre d'actes par foyer en Gamma-Poisson (modèle NBD d'Ehrenberg) ; poids normalisés à 30 M de foyers ; PDM pilotées par une attractivité de marque ; enseigne principale (70 %) ; niveaux de prix par format ; seed fixe.
- **Alternative écartée :** des tirages uniformes. Tous les KPI auraient été identiques, donc une démo sans intérêt.
- **Conséquence :** PDM observées à 0,3 point des cibles, génération en moins d'une seconde. Limites connues : pas de fidélité marque, pas de saisonnalité, pénétration irréaliste (~89 %) car aucun foyer n'est non-acheteur d'une marque. À revoir au J3.

### 2026-10-12 — Alias `Literal` comme source de vérité unique
- **Contexte :** les valeurs autorisées (régions, catégories, formats, unités) servent à la validation, à la génération, au prompt et aux schémas d'outils.
- **Choix :** un alias par référentiel dans `schemas.py` (`Categorie = Literal[...]`), lu ailleurs avec `get_args`.
- **Alternative écartée :** des listes recopiées dans chaque module. Elles divergent, et une valeur valide mais jamais générée renvoie 0 ligne en silence.
- **Conséquence :** une seule modification se propage partout ; un test vérifie que les valeurs en base appartiennent aux référentiels.

### 2026-10-12 — Validation à deux niveaux : Pydantic sur échantillon, SQL sur 100 %
- **Contexte :** garantir la qualité de ~300 000 lignes générées.
- **Choix :** Pydantic valide 1 000 achats tirés au hasard avant l'écriture (bugs systématiques) ; des tests SQL portent sur toutes les lignes (clés orphelines, montants > 0, somme des poids, marque leader).
- **Alternative écartée :** Pydantic sur toutes les lignes. Il ne voit qu'une ligne à la fois et ne détecte ni les clés orphelines ni les incohérences d'agrégats (et prend 1,2 s pour rien de plus).
- **Conséquence :** chaque outil sert là où il est le moins cher ; les tests vérifient des invariants, pas des valeurs figées, donc restent valides si la seed ou le catalogue changent.

## J2 — Text-to-SQL

### 2026-10-15 — OpenRouter via le SDK OpenAI
- **Contexte :** choisir un fournisseur LLM pour la génération SQL et le commentaire.
- **Choix :** OpenRouter (API compatible OpenAI), modèle `anthropic/claude-sonnet-5.5` configurable par la variable `LLM_MODEL`.
- **Alternative écartée :** le SDK Anthropic direct, qui lie le code à un seul fournisseur.
- **Conséquence :** comparer des modèles au J7 revient à changer une chaîne ; coût réel logué (~0,012 $ par question). Contreparties : un intermédiaire de plus, et le routage vers plusieurs hébergeurs (Azure, Bedrock…) rend le comportement moins déterministe.

### 2026-10-15 — Contexte fixe plutôt qu'assemblage dynamique
- **Contexte :** le prompt text-to-SQL contient rôle, règles, schéma, valeurs et exemples.
- **Choix :** le même prompt pour toutes les questions (~2 800 tokens mesurés), partie constante en tête, rappel des règles critiques et question à la fin (lost-in-the-middle).
- **Alternative écartée :** filtrer schéma et exemples selon la question. Gain négligeable avec 4 tables, perte du prompt caching, et un prompt variable est plus dur à évaluer.
- **Conséquence :** à revoir au-delà d'une dizaine de tables ; le glossaire du J4 sera, lui, récupéré dynamiquement (RAG).

### 2026-10-15 — Validation SQL par liste blanche avec le parseur DuckDB
- **Contexte :** le SQL vient d'un LLM, donc il est non fiable par principe.
- **Choix :** `extract_statements` doit renvoyer exactement une instruction, de type `SELECT` (CTE comprises), sinon `ValueError`.
- **Alternative écartée :** une liste noire de mots-clés (`DROP`, `DELETE`…), qui oublie toujours un cas (`ATTACH`, `COPY`, `PRAGMA`…) ; une regex ; une dépendance de plus (`sqlglot`).
- **Conséquence :** même parseur que celui qui exécute, aucune dépendance ajoutée ; `SELECT 1; DROP TABLE` est rejeté car il compte deux instructions.

### 2026-10-15 — Connexion `read_only` et `enable_external_access=False`
- **Contexte :** la validation est du code et peut avoir un bug ; il faut une garantie au niveau du moteur.
- **Choix :** une connexion en lecture seule, accès externe désactivé, au plus 50 lignes renvoyées avec le total réel (compté par sous-requête).
- **Alternative écartée :** la lecture seule seule. Elle laisse passer `SELECT * FROM read_csv('C:/...')`, un SELECT valide qui lit le disque.
- **Conséquence :** défense en profondeur testée (écriture refusée, lecture de fichier refusée) et vérifiée par une tentative d'injection réelle. Limite : la requête est exécutée deux fois (lignes + comptage).

### 2026-10-16 — Champ `explication` plutôt que `raisonnement` dans la sortie structurée
- **Contexte :** refus systématique (`content_filter`, motif natif `refusal`) sur trois hébergeurs, message évoquant la reproduction des sorties du modèle.
- **Choix :** renommer le champ ; le contenu (définition du KPI avant le SQL) est inchangé.
- **Alternative écartée :** supprimer la sortie structurée, ou changer d'hébergeur (le refus venait des trois).
- **Conséquence :** diagnostic en 4 essais en isolant une variable à la fois (schéma, hébergeur, exemples, nom du champ). Un refus lève désormais une `RuntimeError` explicite, non relancée par la boucle de self-repair. Les filtres de sécurité des fournisseurs font partie de l'environnement de production.
