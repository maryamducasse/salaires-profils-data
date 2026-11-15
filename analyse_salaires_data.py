# Analyse des salaires des profils data
# Données : Stack Overflow Developer Survey 2025, fichier data/results.csv

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# réglages
FICHIER = Path("data/results.csv")
IMAGES = Path("images")
RESULTATS = Path("resultats")
IMAGES.mkdir(exist_ok=True)
RESULTATS.mkdir(exist_ok=True)

# seuils pour ne pas conclure sur trop peu de personnes
N_PAYS = 30   # salaires minimum pour calculer la médiane d'un pays
N_MIN = 30    # personnes minimum pour comparer une technologie
N_CASE = 15   # personnes minimum par groupe

# on enlève les salaires bizarres (en dollars par an)
SALAIRE_MIN, SALAIRE_MAX = 5_000, 500_000

# les métiers que je considère comme "data"
METIERS_DATA = ["Data scientist", "Data engineer",
                "Data or business analyst", "AI/ML engineer"]

# les plateformes que je garde
PLATEFORMES = ["Amazon Web Services (AWS)", "Microsoft Azure", "Google Cloud",
               "Docker", "Kubernetes", "Terraform"]

TRANCHES = ["0-2 ans", "3-5 ans", "6-10 ans", "11-15 ans", "16 ans et +"]

# 1. chargement des données
colonnes = ["ResponseId", "Employment", "Country", "DevType", "WorkExp",
            "ConvertedCompYearly", "LanguageHaveWorkedWith",
            "DatabaseHaveWorkedWith", "PlatformHaveWorkedWith"]
df = pd.read_csv(FICHIER, usecols=colonnes, low_memory=False)
print("Réponses dans le fichier :", len(df))

# 2. nettoyage, je note le nombre de personnes à chaque étape
df = df[df["Employment"] == "Employed"]
print("Personnes salariées :", len(df))

df = df.dropna(subset=["ConvertedCompYearly"])
print("Avec un salaire renseigné :", len(df))

df = df[df["ConvertedCompYearly"].between(SALAIRE_MIN, SALAIRE_MAX)]
print("Avec un salaire crédible :", len(df))

# noms de colonnes plus simples
df = df.rename(columns={"ResponseId": "response_id", "Country": "pays",
                        "WorkExp": "exp_annees",
                        "ConvertedCompYearly": "salaire_usd"})

# noms de pays en français
df["pays"] = df["pays"].replace({
    "United States of America": "États-Unis",
    "United Kingdom of Great Britain and Northern Ireland": "Royaume-Uni",
    "Germany": "Allemagne", "India": "Inde", "Netherlands": "Pays-Bas",
    "Poland": "Pologne", "Spain": "Espagne", "Italy": "Italie",
    "Brazil": "Brésil",
})

# vaut 1 si le métier de la personne est un métier data
def est_profil_data(texte):
    metiers = [m.strip() for m in str(texte).split(";")]
    return int(any(m in METIERS_DATA for m in metiers))

df["profil_data"] = df["DevType"].apply(est_profil_data)

# tranches d'expérience
df["tranche_exp"] = pd.cut(df["exp_annees"], bins=[-1, 2, 5, 10, 15, 200],
                           labels=TRANCHES).astype(object)

# salaire relatif = salaire divisé par la médiane de son pays
# 1,0 veut dire que la personne gagne la médiane de son pays
# ça permet de comparer des pays avec des niveaux de salaire très différents
mediane_pays = df.groupby("pays")["salaire_usd"].transform("median")
effectif_pays = df["pays"].map(df["pays"].value_counts())
df["salaire_rel"] = np.where(effectif_pays >= N_PAYS,
                             df["salaire_usd"] / mediane_pays, np.nan)

print("Profils data dans la base :", int(df["profil_data"].sum()))

# 3. tables avec une ligne par compétence ou par métier
def exploser(colonne, type_nom, garder=None):
    # transforme une réponse comme "A;B;C" en une ligne par valeur
    t = df[["response_id", colonne]].dropna()
    t = t.assign(nom=t[colonne].str.split(";")).explode("nom")
    t["nom"] = t["nom"].str.strip()
    t["type"] = type_nom
    if garder is not None:
        t = t[t["nom"].isin(garder)]
    return t[["response_id", "type", "nom"]]

competences = pd.concat([
    exploser("LanguageHaveWorkedWith", "Langage"),
    exploser("DatabaseHaveWorkedWith", "Base de données"),
    exploser("PlatformHaveWorkedWith", "Plateforme", garder=PLATEFORMES),
])
metiers = exploser("DevType", "Métier")[["response_id", "nom"]]

repondants = df[["response_id", "pays", "salaire_usd", "salaire_rel",
                 "exp_annees", "tranche_exp", "profil_data"]]

# 4. base SQL (SQLite) et requêtes
conn = sqlite3.connect(":memory:")
repondants.to_sql("repondants", conn, index=False)
competences.to_sql("competences", conn, index=False)
metiers.to_sql("metiers", conn, index=False)

liste_metiers = ", ".join(f"'{m}'" for m in METIERS_DATA)

# Q1 nombre de personnes par métier data
q1 = f"""
SELECT m.nom AS metier, COUNT(*) AS nb
FROM metiers m
JOIN repondants r ON r.response_id = m.response_id
WHERE r.profil_data = 1 AND m.nom IN ({liste_metiers})
GROUP BY m.nom
ORDER BY nb DESC
"""
par_metier = pd.read_sql(q1, conn)

# Q2 les 5 technologies les plus utilisées dans chaque catégorie
q2 = """
SELECT type, nom, nb, part_pct, rang
FROM (
    SELECT c.type, c.nom, COUNT(*) AS nb,
           ROUND(100.0 * COUNT(*) /
                 (SELECT COUNT(*) FROM repondants WHERE profil_data = 1), 1) AS part_pct,
           RANK() OVER (PARTITION BY c.type ORDER BY COUNT(*) DESC) AS rang
    FROM competences c
    JOIN repondants r ON r.response_id = c.response_id
    WHERE r.profil_data = 1
    GROUP BY c.type, c.nom
)
WHERE rang <= 5
ORDER BY type, rang
"""
usage = pd.read_sql(q2, conn)

# Q3 les pays qui ont le plus de profils data
q3 = """
SELECT pays, COUNT(*) AS nb_profils_data
FROM repondants
WHERE profil_data = 1
GROUP BY pays
HAVING COUNT(*) >= :n
ORDER BY nb_profils_data DESC
LIMIT 8
"""
top_pays = pd.read_sql(q3, conn, params={"n": N_CASE})

# Q4 tous les salaires relatifs (pays avec assez de réponses)
base_rel = pd.read_sql(
    "SELECT * FROM repondants WHERE salaire_rel IS NOT NULL", conn)

# Q5 les technologies de chaque profil data avec son salaire relatif
data_comp = pd.read_sql("""
    SELECT r.response_id, r.salaire_rel, r.exp_annees, c.type, c.nom
    FROM repondants r
    JOIN competences c ON c.response_id = r.response_id
    WHERE r.profil_data = 1 AND r.salaire_rel IS NOT NULL
""", conn)
conn.close()

print("\n--- Profils data par métier ---")
print(par_metier.to_string(index=False))
print("\n--- Technologies les plus utilisées par les profils data ---")
print(usage.to_string(index=False))

# 5. statistiques avec pandas
# A. salaire relatif médian, profils data contre autres développeurs
mediane_globale = base_rel.groupby("profil_data")["salaire_rel"].median()
print("\n--- Salaire relatif médian (1,0 = médiane du pays) ---")
print(mediane_globale.round(2).rename({0: "Autres développeurs",
                                       1: "Profils data"}))

# j'ai regardé l'expérience de chaque groupe pour comprendre ce résultat
print("\n--- Expérience professionnelle médiane (années) ---")
print(base_rel.groupby("profil_data")["exp_annees"].median().rename(
    {0: "Autres développeurs", 1: "Profils data"}))

# B. salaire relatif médian selon l'expérience
exp = (base_rel.dropna(subset=["tranche_exp"])
       .groupby(["tranche_exp", "profil_data"])["salaire_rel"]
       .agg(mediane="median", nb="count").reset_index())
exp.loc[exp["nb"] < N_CASE, "mediane"] = np.nan   # pas assez de monde
courbes = exp.pivot(index="tranche_exp", columns="profil_data",
                    values="mediane").reindex(TRANCHES)
print("\n--- Salaire relatif médian par tranche d'expérience ---")
print(courbes.round(2))

# C. salaire médian en dollars par pays
base_pays = df[df["pays"].isin(top_pays["pays"])]
salaire_pays = (base_pays.groupby(["pays", "profil_data"])["salaire_usd"]
                .median().unstack().reindex(top_pays["pays"]))
print("\n--- Salaire médian en dollars par pays ---")
print(salaire_pays.round(0))

# D. écart de salaire pour chaque technologie, chez les profils data
data_rel = base_rel[base_rel["profil_data"] == 1]
lignes = []
for (type_nom, nom), groupe in data_comp.groupby(["type", "nom"]):
    utilisateurs = groupe["salaire_rel"]
    autres = data_rel.loc[~data_rel["response_id"].isin(groupe["response_id"]),
                          "salaire_rel"]
    if len(utilisateurs) >= N_MIN and len(autres) >= N_MIN:
        lignes.append({
            "type": type_nom, "nom": nom, "nb_utilisateurs": len(utilisateurs),
            "ecart_pct": round(100 * (utilisateurs.median() / autres.median() - 1), 1),
            "exp_mediane": groupe["exp_annees"].median(),
        })
prime = pd.DataFrame(lignes)
if not prime.empty:
    prime = prime.sort_values("ecart_pct", ascending=False)
    print("\n--- Écart de salaire relatif (utilisateurs contre non-utilisateurs) ---")
    print(prime.to_string(index=False))
    prime.to_csv(RESULTATS / "ecart_par_technologie.csv", index=False)
usage.to_csv(RESULTATS / "technologies_les_plus_utilisees.csv", index=False)

# 6. graphiques
# graphique 1, salaire selon l'expérience
fig, ax = plt.subplots(figsize=(9, 5))
if 1 in courbes:
    ax.plot(courbes.index, courbes[1], marker="o", color="steelblue",
            label="Profils data")
if 0 in courbes:
    ax.plot(courbes.index, courbes[0], marker="o", color="gray",
            label="Autres développeurs")
ax.axhline(1, color="lightgray", linestyle="--")
ax.set_title("Salaire relatif selon l'expérience professionnelle")
ax.set_xlabel("Expérience professionnelle")
ax.set_ylabel("Salaire médian relatif (1 = médiane du pays)")
ax.legend()
plt.tight_layout()
plt.savefig(IMAGES / "1_salaire_selon_experience.png", dpi=150)
plt.close()

# graphique 2, salaire par pays
fig, ax = plt.subplots(figsize=(10, 5))
salaire_pays.rename(columns={0: "Autres développeurs", 1: "Profils data"}) \
    [["Profils data", "Autres développeurs"]] \
    .plot(kind="bar", ax=ax, color=["steelblue", "lightgray"])
ax.legend(title="")
ax.set_title("Salaire annuel médian par pays (en dollars)")
ax.set_xlabel("")
ax.set_ylabel("Dollars par an")
plt.xticks(rotation=30, ha="right")
plt.tight_layout()
plt.savefig(IMAGES / "2_salaire_par_pays.png", dpi=150)
plt.close()

# graphique 3, technologies et salaire
if not prime.empty:
    p = prime.sort_values("ecart_pct")
    if len(p) > 16:                      # je garde les extrêmes pour que ce soit lisible
        p = pd.concat([p.head(8), p.tail(8)])
    couleurs = ["steelblue" if v >= 0 else "lightcoral" for v in p["ecart_pct"]]
    fig, ax = plt.subplots(figsize=(9, 0.4 * len(p) + 2))
    ax.barh(p["nom"], p["ecart_pct"], color=couleurs)
    ax.axvline(0, color="gray", linewidth=1)
    ax.set_title("Salaire relatif des profils data, selon la technologie utilisée")
    ax.set_xlabel("Écart par rapport à ceux qui ne l'utilisent pas (%)")
    plt.tight_layout()
    plt.savefig(IMAGES / "3_technologies_et_salaire.png", dpi=150)
    plt.close()

# graphique 4, technologies les plus utilisées
couleur_type = {"Langage": "lightpink", "Base de données": "steelblue",
                "Plateforme": "khaki"}
u = usage.sort_values("part_pct")
fig, ax = plt.subplots(figsize=(9, 7))
ax.barh(u["nom"], u["part_pct"], color=[couleur_type[t] for t in u["type"]])
ax.set_title("Technologies les plus utilisées par les profils data")
ax.set_xlabel("Part des profils data qui l'utilisent (%)")
legende = [plt.Rectangle((0, 0), 1, 1, color=c) for c in couleur_type.values()]
ax.legend(legende, couleur_type.keys(), loc="lower right")
plt.tight_layout()
plt.savefig(IMAGES / "4_technologies_utilisees.png", dpi=150)
plt.close()

# 7. mon analyse perso, que font les profils data débutants ?
# je cherche un premier stage, je veux savoir quoi apprendre en priorité
data_ids = repondants[repondants["profil_data"] == 1]
debutants = data_ids[data_ids["exp_annees"] <= 5]      # 0 à 5 ans d'expérience
confirmes = data_ids[data_ids["exp_annees"] > 5]       # plus de 5 ans

# part des personnes du groupe qui utilisent chaque technologie
def part_utilisateurs(ids):
    c = competences[competences["response_id"].isin(ids["response_id"])]
    return c.groupby("nom")["response_id"].nunique() / len(ids) * 100

comparaison = pd.DataFrame({
    "debutants_pct": part_utilisateurs(debutants),
    "confirmes_pct": part_utilisateurs(confirmes),
}).fillna(0)

top_deb = comparaison.sort_values("debutants_pct", ascending=False).head(10)
print(f"\n--- Technologies des profils data débutants ({len(debutants)} personnes) ---")
print(top_deb.round(1))
top_deb.round(1).to_csv(RESULTATS / "technologies_debutants.csv")

# graphique 5, débutants contre confirmés
fig, ax = plt.subplots(figsize=(9, 6))
top_deb.sort_values("debutants_pct")[["debutants_pct", "confirmes_pct"]].plot(
    kind="barh", ax=ax, color=["steelblue", "lightgray"])
ax.set_title("Technologies des profils data débutants et confirmés")
ax.set_xlabel("Part des personnes qui l'utilisent (%)")
ax.set_ylabel("")
ax.legend(["Débutants (0 à 5 ans)", "Confirmés (plus de 5 ans)"])
plt.tight_layout()
plt.savefig(IMAGES / "5_technologies_debutants.png", dpi=150)
plt.close()

print("\nGraphiques dans images/ et tableaux dans resultats/")
