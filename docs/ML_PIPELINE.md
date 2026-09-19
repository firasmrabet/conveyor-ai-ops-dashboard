# Pipeline IA — Conveyor AI Ops

Objectif : transformer 8 à 10 trames de télémétrie par seconde en **trois réponses
exploitables** pour un responsable de maintenance — *est-ce anormal ? de quel défaut s'agit-il ?
combien d'heures reste-t-il avant l'intervention ?* — et en un **indice de santé 0-100** unique
affiché sur le tableau de bord.

---

## 1. Données d'entrée

L'ESP32 publie la télémétrie à 8 Hz et les **21 caractéristiques** à 1 Hz (`conveyor/{m}/features`).
Le backend les utilise telles quelles en `MODE=HARDWARE` ; en `MODE=SIMULATION` il les calcule
lui-même à partir des mêmes formules (`backend/ml/features.py`) : les deux chemins produisent
donc des vecteurs comparables, ce qui rend la comparaison *edge vs serveur* pertinente.

| # | Caractéristique | Signification physique | Défaut qu'elle révèle |
|---|---|---|---|
| 1 | `vibration_rms` | Énergie vibratoire globale (g) | Déséquilibre, usure générale |
| 2 | `vibration_peak` | Amplitude crête (g) | Chocs mécaniques |
| 3 | `kurtosis` | « Piqué » du signal (3 = gaussien sain) | **Roulement** |
| 4 | `crest_factor` | Crête / RMS | Chocs localisés |
| 5 | `h1_ratio` | Harmonique 1× / RMS | **Déséquilibre** |
| 6 | `h2_ratio` | Harmonique 2× / RMS | **Désalignement**, accouplement |
| 7 | `hf_ratio` | Part haute fréquence | Roulement (début d'usure) |
| 8 | `band_hf_energy` | Énergie autour de BPFO/BPFI | Roulement (stade avancé) |
| 9 | `current_rms` | Courant efficace (A) | **Surcharge**, frottement |
| 10 | `current_peak` | Courant crête | Blocage, à-coups |
| 11 | `power_w` | Puissance absorbée | Charge réelle transportée |
| 12 | `current_ripple` | Ondulation du courant | Désalignement, pas perdus |
| 13 | `motor_temp` | Température corps moteur (°C) | **Surchauffe** |
| 14 | `bearing_temp` | Température pallier (°C) | Roulement, graissage |
| 15 | `temp_delta` | Écart moteur/ambiant | Charge thermique réelle |
| 16 | `rpm` | Vitesse de rotation | — |
| 17 | `speed_error` | Écart à la consigne | Pas perdus, glissement |
| 18 | `rpm_variance` | Stabilité de vitesse | **Glissement de bande** |
| 19 | `belt_speed_error` | Écart vitesse bande/moteur | Glissement (avec encodeur) |
| 20 | `spl_db` | Niveau acoustique | Frottement, résonance |
| 21 | `load_estimate` | Charge estimée depuis le courant | Surcharge |

Baseline « moteur sain » de référence : RMS 0,068 g · kurtosis 2,30 · courant 0,73 A ·
puissance 8,8 W · moteur 42,2 °C · pallier 43,8 °C · 120 rpm (M1 ; valeurs légèrement plus
basses pour M2, mesures sur banc).

---

## 2. Chaîne de traitement

```
features (21) ──► standardisation (StandardScaler ajusté à l'entraînement)
                        │
        ┌───────────────┼──────────────────────────┬───────────────────────────┐
        ▼               ▼                          ▼                           ▼
  Isolation Forest   RandomForest              Régression RUL            XAI (contributions)
  400 arbres         (7 classes, 300 arbres)   (dégradation)             |Δ normalisé| × poids
        │               │                          │                           │
  score 0-1          probabilités             heures restantes            top 4 features
        └───────────────┴──────────────┬───────────┘                           │
                                       ▼                                       │
                            FUSION → INDICE DE SANTÉ                            │
                     100 − 62·max(0, anomalie−0,28)/0,72                       │
                        − 20·(pire composant) − 12·(thermique) − 8·(usure)     │
                                       │                                       │
                                       └───────────────► payload d'inférence ◄──┘
```

### 2.1 Détection d'anomalie (Isolation Forest)

* 400 arbres, contamination calibrée sur le jeu d'entraînement.
* Le score brut est normalisé en 0-1 avec un décalage et une échelle mesurés à l'entraînement
  (`iso_offset = 0,4719`, `iso_scale = 0,0379`) : `anomalie = clip(z / 6,0 + 0,05, 0, 1)`.
* La **porte de sécurité** `ANOMALY_GATE = 0,32` empêche le classifieur de statuer sur du bruit :
  en dessous, le diagnostic reste `HEALTHY` quel que soit l'argmax du classifieur.
* `is_anomaly` passe à `true` au-delà de `0,6` (observé : sain 0,00-0,35 ; BELT_SLIP 0,65).

### 2.2 Classification (RandomForest)

7 classes : `HEALTHY`, `BEARING_FAULT`, `UNBALANCE`, `MISALIGNMENT`, `BELT_SLIP`, `OVERLOAD`,
`OVERHEATING` — plus deux classes **détectées côté edge uniquement** (`SENSOR_FAULT`,
`MOTOR_STALL`), qui relèvent de la logique embarquée (valeur figée, vitesse nulle).

Métriques de l'artefact actif `conveyor-synth v1.4.0` (1 050 échantillons, graine 7) :

| Métrique | Valeur |
|---|---|
| Exactitude | **0,9125** |
| F1 macro | **0,9136** |
| ROC AUC (macro) | **0,9712** |
| Erreur absolue RUL | **338 h** (sur un horizon de 6 000 h) |

### 2.3 RUL (durée de vie restante)

1. Base de Weibull : échelle 6 000 h, « santé critique » à 40/100.
2. Modulation par la **pente de dégradation** calculée sur la médiane de la fenêtre initiale
   comparée au dernier tiers de l'historique de santé.
3. Garde-fou anti-bruit : si la chute dépasse 3 points **et** que la vitesse de dégradation
   dépasse 20 points/h, un facteur `clip(1 − 0,0015 × vitesse, 0,45, 1)` est appliqué.
   Sans ce garde-fou, une fluctuation de quelques minutes faisait varier le RUL de 1 100 à
   3 400 h ; désormais l'écart-type sur banc nominal est de **41 h**.
4. Intervalle de confiance : étalement 0,18-0,55 selon la stabilité des dernières mesures.

### 2.4 Fusion, lissage et stabilité des alertes

* **Fusion santé** : `100 − 62·pénalité d'anomalie − 20·pénalité du pire composant
  − 12·pénalité thermique − 8·pénalité d'usure`.
* **Lissage EMA** (α = 0,30) sur l'anomalie, la santé et le RUL → courbes stables et lisibles.
* **Verrouillage de classe 2 coups** : une classe ne change qu'après deux inférences consécutives
  concordantes (évite le clignotement de l'étiquette).
* **Anti-flapping des alertes** : 3 inférences concordantes + temporisation (90 s pour l'IA,
  120 s pour les anomalies), 3 alertes/90 s maximum côté IA.
* **Score de confiance publié** : la probabilité de la classe retenue ; en dessous de
  `CLASS_CONFIDENCE_THRESHOLD = 0,25`, la confiance est signalée comme faible dans le contexte
  de l'alerte (le seuil apparaît dans le journal d'alertes).

---

## 3. Exemple de sortie complète

```json
{
  "motor_id": "M1", "ts": 1789583096069,
  "health_index": 54.69, "anomaly_score": 0.5731, "is_anomaly": false,
  "fault_class": "UNBALANCE",
  "fault_probabilities": {"HEALTHY": 0.031, "UNBALANCE": 0.812, "MISALIGNMENT": 0.094,
                          "BEARING_FAULT": 0.041, "BELT_SLIP": 0.014,
                          "OVERLOAD": 0.008, "OVERHEATING": 0.000},
  "rul_hours": 209.0, "rul_confidence": {"low": 118.0, "high": 298.0},
  "explanation": [
    {"feature": "vibration_rms", "contribution": 0.42, "label": "Vibration globale", "deviation": 6.8},
    {"feature": "h1_ratio",      "contribution": 0.31, "label": "Harmonique 1x",      "deviation": 4.1},
    {"feature": "current_rms",   "contribution": 0.14, "label": "Courant moteur",     "deviation": 2.2},
    {"feature": "bearing_temp",  "contribution": 0.08, "label": "Température pallier","deviation": 1.4}
  ],
  "model_version": "v1.4.0", "inference_ms": 41.8
}
```

Lecture opérationnelle : *« Déséquilibre confirmé (81 %), vibration 6,8 écarts-types au-dessus de
la baseline, 209 h de vie restante estimée — rééquilibrer la poulie sous 7 jours. »*
C'est exactement la prescription que l'alerting crée automatiquement.

---

## 4. Entraînement et validation

```bash
cd backend

# Entraînement complet (jeu synthétique physique, 7 classes)
python -m ml.train --samples 150 --out ./artifacts --version v1.4.0 --seed 7

# Jeu réduit (intégration continue, quelques secondes)
python -m ml.train --quick

# Validation de l'artefact (utilisé par le build Docker : code 1 ⇒ ré-entraînement automatique)
python -m ml.validate --artifacts ./artifacts
#   [validate] nominal  : HEALTHY / santé 98.6 / anomalie 0.00
#   [validate] roulement: BEARING_FAULT / santé 66.7 / RUL 792 h

# Auto-test des 7 scénarios de défaut (moteur réel du projet)
python -m ml.inference --selftest
```

Résultat de l'auto-test (attendu : 7/7) :

| Scénario | Santé | Anomalie | Classe | RUL |
|---|---|---|---|---|
| Nominal | 97,2 | 0,000 | HEALTHY | 3 166 h |
| Roulement | 62,9 | 0,480 | BEARING_FAULT | 667 h |
| Déséquilibre | 59,2 | 0,521 | UNBALANCE | 564 h |
| Désalignement | 60,1 | 0,511 | MISALIGNMENT | 588 h |
| Glissement | 48,2 | 0,650 | BELT_SLIP | 343 h |
| Surcharge | 54,6 | 0,575 | OVERLOAD | 457 h |
| Surchauffe | 81,8 | 0,159 | OVERHEATING | 1 572 h |

Le moteur **heuristique de secours** (`HeuristicModel`, seuils physiques) obtient également 7/7 :
si un artefact est corrompu ou incompatible, le backend continue de diagnostiquer au lieu de
s'arrêter — c'est la garantie d'une démonstration qui ne tombe jamais.

---

## 5. Génération des données synthétiques

`backend/ml/synthetic.py` construit un jeu de données **physiquement réaliste** plutôt que
purement statistique :

* modèle mécanique (`ml/physics.py`) : composantes 1× / 2× / 3× dépendant de la vitesse, bruit
  large bande, impulsions de roulement périodiques, frottement de glissement, saturation de
  courant, inertie thermique (deux constantes de temps pour le corps et le pallier) ;
* **usure cumulée** qui dérive lentement la signature, et défaut de roulement résiduel pour une
  fraction des échantillons sains ;
* **diversité du glissement de bande** sur 45 % des échantillons sains (0,1-1,4 rpm d'écart) afin
  que la classe `HEALTHY` ne soit pas stéréotypée — correctif appliqué après avoir constaté que
  le moteur M2, monté avec un léger décalage de vitesse, était classé anormal ;
* fenêtre d'analyse de 2 048 échantillons à 1 000 Hz (résolution FFT 0,49 Hz), ce qui place BPFO
  (9,6 Hz) et BPFI (14,4 Hz) nettement au-dessus de la résolution.

C'est ce même jeu qui alimente `scripts/seed_db.py` pour l'historique de 24 h du tableau de bord :
les courbes affichées le premier jour sont donc cohérentes avec ce que les modèles savent voir.

---

## 6. Surveillance de la dérive et MLOps

| Élément | Mise en œuvre |
|---|---|
| **Dérive entraînement ↔ production** | `GET /api/models/metrics` compare les distributions (écarts normalisés par caractéristique) ; la page `/ml` affiche l'histogramme correspondant. |
| **Cache de calcul** | Les métriques (ROC/PR, dérive) sont mises en cache 30 s : le tableau de bord peut les rafraîchir sans recalculer les courbes. |
| **Versionnage** | Chaque artefact porte ses métriques dans `.metrics.json` et dans la table `model_version` (exactitude, F1, précisision, rappel, ROC AUC, RUL MAE, taille, latence). |
| **Rollback / hot-swap** | `POST /api/models/rollback` recharge une version antérieure sans redémarrer le service ; `POST /api/models/reload` recharge l'artefact actif après ré-entraînement. |
| **Comparaison edge vs serveur** | `GET /api/models/edge-comparison` met en regard le pré-diagnostic embarqué et le diagnostic serveur sur la même fenêtre (latence, mémoire, accord). |
| **Auto-réparation au démarrage** | Le `docker-entrypoint.sh` du backend exécute `ml.validate` et **ré-entraîne automatiquement** si l'artefact est absent ou non conforme. |

---

## 7. Brancher un modèle entraîné sur données réelles

1. **Constituer un relevé étiqueté** : faire tourner le banc, injecter chaque défaut, relever
   `conveyor/{m}/features` (1 Hz) et étiqueter les fenêtres.
2. **Ajuster la baseline** dans `ml/features.py` (`BASELINE`, `TOLERANCE`) aux valeurs mesurées :
   sans cela, les z-scores et le radar restent calés sur le banc d'origine.
3. **Entraîner** vos modèles (scikit-learn, XGBoost, TensorFlow, PyTorch) et les envelopper dans
   l'adaptateur du projet :

```python
from ml.model import ConveyorAIEngine
import joblib

engine = ConveyorAIEngine(
    anomaly_model=my_isolation_forest,      # expose .score_samples() ou .predict()
    classifier=my_classifier,               # expose .predict_proba()
    rul_model=my_regressor,                 # expose .predict()
    scaler=my_scaler,                       # ajusté à l'entraînement
    metadata={"accuracy": 0.94, "f1_macro": 0.93, "rul_mae_hours": 180.0,
              "trained_at": 1789583096069, "dataset_size": 12000},
)
joblib.dump(engine, "artifacts/conveyor-reel.joblib")
```

4. **Déclarer et activer** :

```bash
echo "ML_ACTIVE_BUNDLE=conveyor-reel" >> backend/.env
echo "ML_MODEL_VERSION=v2.0.0" >> backend/.env
curl -X POST localhost:8000/api/models/reload
python ../scripts/seed_db.py --check        # contrôle des nouvelles valeurs de référence
```

5. **Recalibrer les seuils** : `ANOMALY_GATE`, le seuil `is_anomaly`, les seuils de classe et
   `ml/validate.py` (les valeurs actuelles sont celles du banc simulé).

---

## 8. Limites connues et honnêteté scientifique

* Les modèles livrés sont entraînés sur **données synthétiques physiques** : ils démontrent la
  chaîne complète et les ordres de grandeur, mais doivent être ré-entraînés sur les relevés du
  banc réel avant toute décision de maintenance engageante.
* Les classes `SENSOR_FAULT` et `MOTOR_STALL` sont volontairement traitées **côté edge** : une
  valeur figée ou une vitesse nulle sont des constats de capteurs, pas des diagnostics statistiques.
* L'estimation thermique sans sonde (mode dégradé) repose sur un modèle courant → température :
  elle est précise à ±3 °C sur le banc, à revérifier sur une autre mécanique.
* La RUL reste l'indicateur le plus incertain (erreur moyenne 338 h sur 6 000 h) : elle est
  toujours présentée avec sa bande de confiance et n'est jamais utilisée seule pour décider.
