---
name: jour
description: Lance une journée du plan de formation Insight Agent (J1 à J10) — cours en français sur les leçons du jour, puis travail guidé sur le livrable. Usage : /jour 3
---

# /jour N — une journée de formation + projet

Argument : le numéro du jour ($ARGUMENTS). S'il manque, lire `PROGRESSION.md` et proposer le premier jour non terminé.

## 0. Cadrage (2 min)
1. Lire le plan dans `CLAUDE.md` et la ligne du jour N : thème, leçons, livrable. Le livrable détaillé figure aussi dans le Carnet (bloc « Livrable » en tête de la section `JN`).
2. Lire `PROGRESSION.md` : ce qui est fait, les points à revoir de la veille.
3. Annoncer en 3 lignes : le thème du jour, les leçons, le livrable attendu ce soir.
4. S'il reste un point à revoir de la veille, poser d'abord 1 question de rappel dessus.

## 1. Cours détaillé (environ 1h30 par leçon)

**Source principale : `Carnet_AI_Engineering.html`** (racine du repo). C'est la traduction française complète des leçons, rangée par jour, avec les encadrés « Appliqué à Insight Agent », les quiz, les exercices et le vocabulaire. Ne jamais le lire en entier (424 Ko) : extraire la leçon dans le scratchpad puis lire le fichier extrait **en entier**.

```bash
uv run python .claude/skills/jour/carnet.py X.YY > <scratchpad>/lecon-X.YY.md   # une leçon
uv run python .claude/skills/jour/carnet.py JN                                   # tout le jour
```

Repli si le Carnet est absent ou si la leçon n'y est pas : `../ai-engineering-from-scratch/phases/<XX>-*/<YY>-*/docs/en.md`. Dans tous les cas, regarder `code/` dans ce dossier pour donner la commande à lancer.

Pour chaque leçon du jour :
1. **Enseigner par blocs, dans l'ordre des sections `###` du Carnet.** Un message = 2 ou 3 sections, puis s'arrêter : « Dis *suite* pour continuer, ou pose ta question. » Ne pas résumer la leçon en un seul message.
2. **Dans chaque bloc** :
   - Rester fidèle au Carnet : garder les tableaux, les exemples de code et les chiffres. Expliquer au lieu de condenser.
   - Ajouter un exemple tiré du panel d'achats (foyers, achats, KPI) dès que l'exemple de la leçon est générique.
   - Placer chaque encadré « Appliqué à Insight Agent » (`>>> ENCADRÉ` dans l'extrait) à l'endroit où il apparaît, et le relier au code déjà écrit dans le repo.
   - Signaler toute affirmation datée ou fausse (API obsolète, modèle remplacé, prefill non supporté…) avec un encadré « ⚠️ Correction du cours ».
3. **« Construire pas à pas »** : une étape par bloc, avec le code commenté en français. Me faire lancer la commande moi-même (`uv run python ../ai-engineering-from-scratch/phases/.../code/<fichier>.py`, car `python` n'est pas dans le PATH). Ne pas la lancer à ma place.
4. **Fin de leçon** :
   - **Vocabulaire** : reprendre le tableau du Carnet, en une ligne par terme.
   - **Quiz du Carnet** (`## Quiz` dans l'extrait, en JSON : `q` question, `o` options, `c` indice de la bonne réponse, `e` explication). Poser les questions **sans donner les réponses**, puis corriger avec `e`.
   - **3 questions de vérification** : une de compréhension, une d'application au projet, une piège. Attendre mes réponses et corriger.
   - **Lien direct** avec le livrable du jour : ce que je vais réutiliser cet après-midi.
   - Les **exercices du cours** sont optionnels : les citer en une ligne et proposer celui qui sert le projet.
5. Noter dans les points à revoir chaque réponse fausse ou partielle.
6. Ne pas passer à la leçon suivante tant que je n'ai pas répondu.

## 2. Projet (4 à 5 h)
1. Proposer un plan court du livrable (fichiers, fonctions, tests) et attendre ma validation.
2. Appliquer les règles du mode tuteur de `CLAUDE.md` : je code le cœur, tu écris la périphérie, aide graduée (question → indice → squelette → solution si demandée).
3. Après chaque bloc que j'écris : code review directe, puis tests.
4. Signaler toute décision qui mérite une entrée dans `docs/decisions.md`.

## 3. Bilan (30 min)
1. Vérifier que le livrable est atteint : tests verts, commande de démo qui tourne. Ne pas le déclarer atteint sans l'avoir vérifié.
2. Me poser 3 questions d'entretien sur ce que j'ai construit aujourd'hui (« pourquoi ce choix ? », « que se passe-t-il si… ? »).
3. Mettre à jour `PROGRESSION.md` : statut du jour, notions acquises, points à revoir.
4. Proposer le message de commit et rappeler le thème de demain en une ligne.
