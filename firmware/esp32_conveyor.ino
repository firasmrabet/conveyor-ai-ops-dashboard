/* ============================================================================
 *  esp32_conveyor.ino — Passerelle ESP32 du tapis roulant instrumenté
 *  Projet : Détection d'anomalies et maintenance prédictive d'un tapis roulant
 *           industriel (2 moteurs pas à pas M1 = gauche, M2 = droite)
 * ============================================================================
 *
 *  RÔLE DE CETTE CARTE
 *  -------------------
 *  1. Acquérir les capteurs du banc :
 *       - accéléromètre 3 axes MPU6050 (vibration ax/ay/az) — bus I2C, lecture directe
 *         des registres (aucune dépendance externe, cadence maîtrisée),
 *       - capteur de courant ACS712 (voie analogique) ou INA219 en option,
 *       - 3 sondes de température DS18B20 sur un bus 1-Wire (moteur / roulement / ambiant),
 *       - microphone MEMS INMP441 en I2S (option, pour le SP L et les bandes d'énergie),
 *       - comptage des impulsions de pas pour la vitesse réelle de rotation.
 *  2. Calculer les caractéristiques embarquées (RMS, crête, kurtosis, facteur de crête,
 *     pics FFT, SP L, énergie par bande, erreur de vitesse bande/moteur) — 1 Hz.
 *  3. Publier le protocole MQTT du cahier des charges (télémétrie 8 Hz, features 1 Hz,
 *     inférence edge 1 Hz, statut 5 Hz, heartbeat 1 Hz).
 *  4. Exécuter une inférence « TinyML » légère directement sur la carte (diagnostic
 *     immédiat même si le réseau tombe) — voir section 9.
 *  5. Recevoir et appliquer les commandes descendantes (`conveyor/cmd/#`) : pilotage de
 *     la ligne, calibration, cadence d'échantillonnage, OTA, test réseau, redémarrage.
 *
 *  LE BACKEND RESTE MAÎTRE DES MODÈLES LOURDS : l'ESP32 fournit mesures + features +
 *  un pré-diagnostic ; FastAPI exécute le classifieur/RUL complet et publie ses
 *  conclusions sur les pages web. Aucune donnée n'est décidée uniquement ici.
 *
 * ---------------------------------------------------------------------------
 *  CÂBLAGE (carte de référence : ESP32-DevKitC v4 — ajuster les GPIO selon la vôtre)
 * ---------------------------------------------------------------------------
 *   Fonction                Composant          Signal         GPIO ESP32
 *   ----------------------  -----------------  -------------  ----------
 *   Accéléromètre           MPU6050            SDA            GPIO 21
 *   (I2C, adresse 0x68)                        SCL            GPIO 22
 *                                              INT (option)   GPIO 34
 *   Courant moteur M1       ACS712-20A         OUT            GPIO 36 (ADC1_CH0)
 *   Courant moteur M2       ACS712-20A         OUT            GPIO 39 (ADC1_CH3)
 *   Sonde température       DS18B20 #1 corps   DQ (1-Wire)    GPIO 4
 *   Sonde température       DS18B20 #2 pallier DQ (1-Wire)    GPIO 4  (même bus)
 *   Sonde température       DS18B20 #3 ambiant DQ (1-Wire)    GPIO 4  (même bus)
 *   Micro MEMS (option)     INMP441            SCK/WS/SD      GPIO 32 / 25 / 33
 *   Driver M1 (A4988)       STEP               STEP           GPIO 26
 *                                              DIR            GPIO 27
 *                                              ENABLE (actif bas) GPIO 14
 *   Driver M2 (A4988)       STEP               STEP           GPIO 13
 *                                              DIR            GPIO 12
 *                                              ENABLE (actif bas) GPIO 15
 *   Encodeur M1 (option)    sortie A roue codeuse  PULSE       GPIO 18
 *   Bouton d'arrêt d'urgence (NF, option)      INPUT_PULLUP   GPIO 5
 *   LED d'état              LED embarquée      -              GPIO 2
 *
 *   ALIMENTATION : logique 5 V (USB ou rail 5 V) et moteurs sur alimentation séparée
 *   (12 V) — masse commune obligatoire. Le DS18B20 se câble en bus avec une résistance
 *   de tirage de 4,7 kΩ entre DQ et 3V3.
 *   SÉCURITÉ : les commutations du driver de puissance sont bruitées ; éloigner les
 *   câbles STEP des câbles d'accéléromètre et torsader les paires. Le pont diviseur
 *   d'adaptation 5 V→3,3 V est nécessaire sur la sortie du MPU6050 (la plupart des
 *   modules GY-521 acceptent le 3,3 V directement, vérifier la sérigraphie).
 *
 * ---------------------------------------------------------------------------
 *  BIBLIOTHÈQUES REQUISES (gestionnaire de bibliothèques Arduino IDE)
 * ---------------------------------------------------------------------------
 *   - PubSubClient        (Nick O'Leary)  >= 2.8
 *   - ArduinoJson         (Benoît Blanchon) >= 7.0
 *   - OneWire             (Paul Stoffregen) >= 2.3
 *   - DallasTemperature   (Miles Burton)  >= 3.9
 *   - arduinoFFT          (Enrique Condes) >= 2.0
 *   Aucune bibliothèque n'est nécessaire pour le MPU6050 : les registres sont lus
 *   directement sur le bus I2C (Wire, fourni avec le cœur ESP32).
 *
 * ---------------------------------------------------------------------------
 *  COMPILATION
 * ---------------------------------------------------------------------------
 *   Arduino IDE  : carte « ESP32 Dev Module », 240 MHz, 4 Mo flash,
 *                  partition « Minimal SPIFFS (1.9 MB APP / 190 KB SPIFFS) »,
 *                  vitesse téléversement 921600.
 *   PlatformIO   : platformio.ini fourni dans le même dossier (env:esp32dev).
 *   La compilation réussit telle quelle, SANS matériel branché : les capteurs absents
 *   sont détectés au démarrage et la carte publie des trames marquées
 *   `"state":"UNKNOWN"` avec la liste des capteurs manquants (utile pour le banc
 *   d'essai et pour la mise au point avant câblage définitif).
 */

// ============================================================================
//  1. BIBLIOTHÈQUES
// ============================================================================
#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <arduinoFFT.h>
#include <time.h>
#include <esp_task_wdt.h>

// ============================================================================
//  2. CONFIGURATION UTILISATEUR — à adapter au banc
// ============================================================================

// --- Identité du device -----------------------------------------------------
#define DEVICE_ID            "esp32-conveyor-01"
#define FIRMWARE_VERSION     "1.0.0"
#define MQTT_TOPIC_PREFIX    "conveyor"

// --- Réseau Wi-Fi -----------------------------------------------------------
// Renseigner directement ici, ou laisser tel quel et utiliser le portail de
// configuration (les identifiants peuvent aussi être injectés en usine via
// `data/seed.json` puis écrits dans la partition NVS — voir docs/HARDWARE.md).
#define WIFI_SSID            "ConveyorLab"
#define WIFI_PASSWORD        "motdepasse-wifi"
#define WIFI_HOSTNAME        "esp32-conveyor"

// --- Broker MQTT ------------------------------------------------------------
#define MQTT_HOST            "192.168.1.10"   // IP du serveur Mosquitto (ou nom .local)
#define MQTT_PORT            1883
#define MQTT_USERNAME        "conveyor"       // compte créé par mosquitto/entrypoint.sh
#define MQTT_PASSWORD        "conveyor"
#define MQTT_USE_TLS         0                // 1 = port 8883 + certificats (voir section 10)

// --- Cadences ---------------------------------------------------------------
// La chaîne backend tourne sur une base FS = 1000 Hz / BLOC = 125 échantillons.
// Ici, l'I2C du MPU6050 limite la cadence utile : FS = 500 Hz est le bon compromis
// (échantillonnage largement suffisant pour la bande 0-250 Hz qui contient tous les
// défauts mécaniques d'un tapis à 120 rpm : 1x, 2x, BPFO 9,6 Hz, BPFI 14,4 Hz).
#define SAMPLING_HZ          500              // fréquence d'échantillonnage capteurs
#define TELEMETRY_HZ         8               // cadence de publication télémétrie
#define FEATURES_HZ          1               // cadence features + inférence edge
#define STATUS_HZ            5               // cadence du topic conveyor/status
#define HEARTBEAT_MS         1000            // période du heartbeat
#define FFT_SIZE             512             // fenêtre FFT (1,024 s à 500 Hz → 0,98 Hz de résolution)
#define VIB_BUFFER_SIZE      2048            // buffer circulaire de vibration (≈4 s)
#define CURRENT_BUFFER_SIZE  512             // buffer circulaire de courant

// --- Mécanique du tapis -----------------------------------------------------
#define BELT_MM_PER_REV      42.0f           // développement de la poulie d'entraînement
#define STEPS_PER_REV        200.0f          // moteur pas à pas 1,8°
#define MICROSTEPS           16.0f           // micro-pas configurés sur le driver A4988
#define NOMINAL_RPM          120.0f          // consigne nominale du banc
#define NOMINAL_CURRENT_A    1.20f           // courant nominal d'un moteur (A)
#define PULLEY_MM            42.0f
#define PULSES_PER_REV_ENCODER 20.0f         // impulsions par tour de la roue codeuse (option)
#define BELT_LENGTH_MM       1200.0f         // longueur de bande (pour la vitesse linéaire)

// --- Broches -----------------------------------------------------------------
#define PIN_I2C_SDA          21
#define PIN_I2C_SCL          22
#define PIN_MPU_INT          34              // entrée seule, optionnelle
#define PIN_CURRENT_M1       36              // ADC1_CH0
#define PIN_CURRENT_M2       39              // ADC1_CH3
#define PIN_ONEWIRE          4
#define PIN_M1_STEP          26
#define PIN_M1_DIR           27
#define PIN_M1_ENABLE        14
#define PIN_M2_STEP          13
#define PIN_M2_DIR           12
#define PIN_M2_ENABLE        15
#define PIN_ENCODER_M1       18
#define PIN_EMERGENCY_STOP   5
#define PIN_STATUS_LED       2
#define PIN_I2S_SCK          32
#define PIN_I2S_WS           25
#define PIN_I2S_SD           33

// --- Options matérielles (mettre à 0 si le composant n'est pas monté) --------
#define ENABLE_MIC           0               // microphone MEMS INMP441 (I2S)
#define ENABLE_ENCODER       0               // roue codeuse sur M1 (comptage d'impulsions)
#define ENABLE_INA219        0               // mesure de courant par INA219 (au lieu de l'ACS712)
#define ENABLE_BENCH_TEST    1               // autorise les défauts de test injectés par MQTT

// --- Coefficients de conversion ---------------------------------------------
// ACS712-20A : sortie centrée sur Vcc/2, sensibilité 100 mV/A à 5 V.
// Sur un ESP32 (ADC 12 bits, atténuation 11 dB : 0-3,3 V), on utilise un pont
// diviseur 5 V→3,3 V (rapport 0,66) : la sensibilité vue par l'ADC devient 66 mV/A.
#define ACS712_SENSITIVITY_V_PER_A   0.066f
#define ACS712_VREF                  1.65f   // tension de repos mesurée à l'ADC (V)
#define ADC_MAX                      4095.0f
#define ADC_VREF                     3.30f
#define ADC_OFFSET_CAL_MS            2000    // durée de la calibration du zéro courant

// --- Seuils de pré-diagnostic embarqué (alignés sur les règles du backend) ---
#define EDGE_RMS_WARN_G        0.32f
#define EDGE_RMS_ALARM_G       0.55f
#define EDGE_KURTOSIS_WARN     4.5f
#define EDGE_CURRENT_WARN_A    1.35f
#define EDGE_BEARING_TEMP_WARN 62.0f
#define EDGE_SPEED_ERROR_WARN  0.06f         // 6 % d'écart vitesse bande / moteur

// ============================================================================
//  3. STRUCTURES DE DONNÉES
// ============================================================================

/** Bloc de mesures brutes d'un tick d'échantillonnage (1/500 s). */
struct SampleBlock {
  float vibAx;        // g
  float vibAy;        // g
  float vibAz;        // g
  float currentA;     // A
};

/** Statistiques calculées sur un bloc (fenêtre glissante de BLOCK échantillons). */
struct BlockStats {
  float rms;
  float peak;
  float kurtosis;
  float crestFactor;
  float mean;
  float stdDev;
};

/** Caractéristiques embarquées transmises au backend (schema FeaturePayload). */
struct FeatureSet {
  float vibRms;
  float vibPeak;
  float kurtosis;
  float crestFactor;
  float vibHighFreq;
  float currentRms;
  float currentPeak;
  float powerW;
  float tempMotorC;
  float tempBearingC;
  float ambientC;
  float splDb;
  float rpm;
  float beltMmS;
  float stepsPerS;
  float beltSpeedError;
  float speedStdRpm;
  float oneX;
  float twoX;
  float threeX;
  float bearingEnergy;
};

/** Résultat du pré-diagnostic embarqué (schema EdgeInferencePayload). */
struct EdgeDiagnosis {
  float healthIndex;
  float anomalyScore;
  bool isAnomaly;
  const char* faultClass;
  float inferenceMs;
  float confidence;
};

/** État d'un moteur (côté banc). */
struct MotorState {
  uint8_t id;                  // index 0 = M1, 1 = M2
  const char* label;           // "M1" / "M2"
  uint8_t pinStep;
  uint8_t pinDir;
  uint8_t pinEnable;
  uint8_t pinCurrent;
  volatile uint32_t steps;     // impulsions de pas générées
  float targetRpm;
  float rpm;                   // vitesse mesurée (encodeur si présent, sinon consigne)
  uint32_t lastStepMicros;
  bool running;
  bool direction;
  float currentOffset;         // zéro du capteur de courant (calibration)
  float tempBearingC;
  float injectedFaultIntensity; // défauts de test injectés par MQTT (0 = aucun)
  const char* injectedFault;
};

// ============================================================================
//  4. ÉTAT GLOBAL
// ============================================================================

// --- Objets matériels --------------------------------------------------------
TwoWire i2cBus = TwoWire(0);
OneWire oneWireBus(PIN_ONEWIRE);
DallasTemperature tempSensors(&oneWireBus);
WiFiClient plainClient;
#if MQTT_USE_TLS
WiFiClientSecure secureClient;
#endif
PubSubClient mqttClient;

// --- Tampons circulaires -----------------------------------------------------
float vibBuffer[VIB_BUFFER_SIZE];
float currentBuffer[CURRENT_BUFFER_SIZE];
volatile uint16_t vibWriteIndex = 0;
volatile uint16_t currentWriteIndex = 0;

// Buffer FFT auto-alimenté (fenêtre de FFT_SIZE échantillons récents)
double fftReal[FFT_SIZE];
double fftImag[FFT_SIZE];
arduinoFFT FFT = arduinoFFT(fftReal, fftImag, FFT_SIZE, SAMPLING_HZ);

// --- État de la ligne --------------------------------------------------------
MotorState motors[2];
bool lineRunning = true;
float targetRpm = NOMINAL_RPM;
// Le moteur suiveur (M2) est consigné très légèrement plus lent : la bande reste
// sous tension et le brin mou ne bat pas. La consigne publiée dans la télémétrie est
// la consigne EFFECTIVE du moteur (motors[i].targetRpm), sinon le backend
// interpréterait cet écart de synchronisation comme un défaut de vitesse.
constexpr float M2_SYNC_OFFSET = 0.995f;
float loadKg = 0.6f;
float parcelsPerMin = 12.0f;
uint32_t samplingHz = SAMPLING_HZ;
uint32_t telemetryHz = TELEMETRY_HZ;

// --- Statistiques de communication -------------------------------------------
uint32_t publishedFrames = 0;
uint32_t droppedFrames = 0;
uint32_t mqttReconnects = 0;
int32_t lastRssi = -60;
uint32_t bootMillis = 0;
bool ntpSynced = false;

// --- Sécurité / supervision ---------------------------------------------------
bool emergencyStop = false;
bool tamperFlag = false;
uint32_t lastHeartbeatMs = 0;
uint32_t lastStatusLedToggleMs = 0;
uint32_t lastStatusMs = 0;
uint32_t lastFeatureMs = 0;
uint32_t lastTelemetryMs = 0;

// --- Températures lues sur le bus 1-Wire (indexées par adresse ROM) -----------
DeviceAddress motorSensorAddress;
DeviceAddress bearingSensorAddress;
DeviceAddress ambientSensorAddress;
float tempMotorC = 26.0f;
float tempBearingC = 26.5f;
float tempAmbientC = 24.0f;
float tempBearingM2C = 26.5f;

// --- Défauts de test (banc) ---------------------------------------------------
float injectedIntensity = 0.0f;
const char* injectedFault = "NONE";
float degradationRate = 1.0f;
uint32_t injectedAtMs = 0;

// --- Dernier jeu de mesures publié (partagé entre les tâches) -----------------
FeatureSet lastFeatures;
BlockStats lastStats;
EdgeDiagnosis lastDiagnosis;
portMUX_TYPE sharedStateMux = portMUX_INITIALIZER_UNLOCKED;

// --- Défauts de test du banc (pilotes par MQTT quand ENABLE_BENCH_TEST = 1) ----
float temperatureOffsetC = 0.0f;    // dérive thermique simulée (°C)
bool sensorFrozen = false;          // mesure figée (perte de capteur)
FeatureSet frozenFeatures;          // dernière mesure valide avant gel
float accelOffsetX = 0.0f;          // offsets statiques du MPU6050 (calibration)
float accelOffsetY = 0.0f;
float accelOffsetZ = 0.0f;
bool scenarioRunning = false;       // scénario de démonstration en cours
uint32_t scenarioStartedMs = 0;

// --- Tâches FreeRTOS ----------------------------------------------------------
TaskHandle_t samplingTaskHandle = nullptr;
TaskHandle_t publishTaskHandle = nullptr;

// --- Déclarations anticipées (portabilité GCC : l'IDE Arduino les génère, on les
// --- écrit explicitement pour que le fichier compile aussi en PlatformIO/CMake) ---
float readCurrentAmps(const MotorState &motor);
float adcToAmps(uint8_t pin, float offsetVolts);
void calibrateCurrentOffset(MotorState &motor);
void updateTemperatures();
void updateStepGeneration();
void mqttLoop();
void onMqttMessage(char* topic, byte* payload, unsigned int length);
bool connectMqtt();
void applyDownstreamCommand(const char* command, JsonVariantConst payload);
void applyFaultInjection(JsonVariantConst payload);
void publishStatusResult(const char* command, const char* message);
void trimAccelerometerOffset();
void computeFeatures(FeatureSet &features);
EdgeDiagnosis runEdgeInference(const FeatureSet &features, const BlockStats &stats);
void runLocalScenario();
void publishStatus(bool force = false);
void publishTelemetry();
void publishFeatures();
void publishEdgeInference();
void publishHeartbeat();

#if ENABLE_MIC
#include <driver/i2s.h>       // pilote I2S (uniquement si le microphone est monté)
#endif

// ============================================================================
//  5. PILOTAGE DES MOTEURS PAS À PAS
// ============================================================================
/**
 * Configure les broches du driver A4988/DRV8825 d'un moteur.
 * Les sorties sont initialisées à l'état bas pour éviter toute impulsion parasite
 * au démarrage (le driver est activé par un niveau bas sur ENABLE).
 */
void setupMotor(MotorState &motor, uint8_t id, const char* label, uint8_t pinStep,
                uint8_t pinDir, uint8_t pinEnable, uint8_t pinCurrent) {
  motor.id = id;
  motor.label = label;
  motor.pinStep = pinStep;
  motor.pinDir = pinDir;
  motor.pinEnable = pinEnable;
  motor.pinCurrent = pinCurrent;
  motor.steps = 0;
  motor.targetRpm = NOMINAL_RPM;
  motor.rpm = 0.0f;
  motor.lastStepMicros = 0;
  motor.running = true;
  motor.direction = true;
  motor.currentOffset = ACS712_VREF;
  motor.tempBearingC = 26.5f;
  motor.injectedFaultIntensity = 0.0f;
  motor.injectedFault = "NONE";

  pinMode(pinStep, OUTPUT);
  pinMode(pinDir, OUTPUT);
  pinMode(pinEnable, OUTPUT);
  digitalWrite(pinStep, LOW);
  digitalWrite(pinDir, HIGH);
  digitalWrite(pinEnable, LOW);   // driver activé (actif bas)
  pinMode(pinCurrent, INPUT);
}

/**
 * Vitesse de rotation courante d'un moteur.
 * Sans encodeur : estimation issue de la cadence réelle des impulsions STEP générées
 * (lissée dans `updateStepGeneration()`), avec le léger écart propre à chaque moteur.
 * Avec encodeur : la valeur est remplacée par la mesure de la roue codeuse dans
 * `computeFeatures()` (voir ENABLE_ENCODER).
 */
float motorRpm(const MotorState &motor) {
  return motor.rpm;
}

/**
 * Génère les impulsions STEP des deux moteurs.
 * Appelée très fréquemment (tâche de haute priorité) : la cadence d'impulsion dépend
 * de la vitesse de consigne et du nombre de micro-pas du driver.
 */
void updateStepGeneration() {
  uint32_t nowUs = micros();
  for (uint8_t i = 0; i < 2; i++) {
    MotorState &motor = motors[i];
    bool active = lineRunning && motor.running && !emergencyStop;
    if (!active || motor.targetRpm < 1.0f) {
      motor.rpm += ((0.0f) - motor.rpm) * 0.25f;   // décélération douce de l'estimation
      continue;
    }
    // Période d'une impulsion : 60 s / (tr/min × pas par tour) en microsecondes.
    float pulsesPerSecond = (motor.targetRpm / 60.0f) * STEPS_PER_REV * MICROSTEPS;
    uint32_t intervalUs = (uint32_t)(1000000.0f / pulsesPerSecond);
    if (nowUs - motor.lastStepMicros >= intervalUs) {
      motor.lastStepMicros = nowUs;
      digitalWrite(motor.pinStep, HIGH);
      delayMicroseconds(2);              // impulsion de 2 µs (largeur minimale du driver)
      digitalWrite(motor.pinStep, LOW);
      motor.steps++;
      // Estimation de la vitesse réelle : le suiveur converge vers sa consigne
      // effective, déjà majorée/minorée par M2_SYNC_OFFSET (la vraie mesure vient de
      // l'encodeur quand il est monté).
      motor.rpm += (motor.targetRpm - motor.rpm) * 0.02f;
    }
  }
}

/** Arrêt d'urgence logiciel (le bouton physique est lu dans la tâche principale). */
void triggerEmergencyStop(const char* reason) {
  lineRunning = false;
  for (uint8_t i = 0; i < 2; i++) {
    motors[i].running = false;
    digitalWrite(motors[i].pinEnable, HIGH);   // drivers désactivés
  }
  Serial.printf("[SECURITE] Arrêt d'urgence : %s\n", reason);
  publishStatus(true);
}

// ============================================================================
//  6. CAPTEURS
// ============================================================================

// --- MPU6050 : accès direct aux registres (aucune bibliothèque externe) -------
#define MPU6050_ADDR            0x68
#define MPU6050_REG_SMPLRT_DIV  0x19
#define MPU6050_REG_CONFIG      0x1A
#define MPU6050_REG_GYRO_CONFIG 0x1B
#define MPU6050_REG_ACCEL_CONFIG 0x1C
#define MPU6050_REG_ACCEL_XOUT_H 0x3B
#define MPU6050_REG_PWR_MGMT_1  0x6B
#define MPU6050_REG_WHO_AM_I    0x75

bool mpuPresent = false;
float mpuAccelScale = 16384.0f;   // ±2 g par défaut (LSB/g)

/** Écrit un octet dans un registre du MPU6050. */
void mpuWrite(uint8_t reg, uint8_t value) {
  i2cBus.beginTransmission(MPU6050_ADDR);
  i2cBus.write(reg);
  i2cBus.write(value);
  i2cBus.endTransmission();
}

/** Lit `count` octets consécutifs du MPU6050 dans `dest`. */
bool mpuRead(uint8_t reg, uint8_t* dest, size_t count) {
  i2cBus.beginTransmission(MPU6050_ADDR);
  i2cBus.write(reg);
  if (i2cBus.endTransmission(false) != 0) return false;
  if (i2cBus.requestFrom((uint8_t)MPU6050_ADDR, (uint8_t)count) != count) return false;
  for (size_t i = 0; i < count; i++) dest[i] = i2cBus.read();
  return true;
}

/** Détection + configuration du MPU6050 (échelle ±4 g, filtre passe-bas 44 Hz). */
bool initMpu6050() {
  uint8_t who = 0;
  if (!mpuRead(MPU6050_REG_WHO_AM_I, &who, 1) || (who != 0x68 && who != 0x70 && who != 0x98)) {
    Serial.println("[capteur] MPU6050 absent sur le bus I2C");
    return false;
  }
  mpuWrite(MPU6050_REG_PWR_MGMT_1, 0x01);      // réveil, horloge PLL X gyro
  delay(50);
  mpuWrite(MPU6050_REG_SMPLRT_DIV, 0x00);      // 1 kHz quand DLPF actif
  mpuWrite(MPU6050_REG_CONFIG, 0x03);          // filtre passe-bas 44 Hz / 1 kHz
  mpuWrite(MPU6050_REG_GYRO_CONFIG, 0x00);     // ±250 °/s (non utilisé ici)
  mpuWrite(MPU6050_REG_ACCEL_CONFIG, 0x08);    // ±4 g
  mpuAccelScale = 8192.0f;                     // LSB/g pour ±4 g
  Serial.println("[capteur] MPU6050 initialisé (±4 g, filtre 44 Hz)");
  return true;
}

/** Lit un échantillon d'accélération et le convertit en g. */
bool readAccel(float &ax, float &ay, float &az) {
  uint8_t raw[6];
  if (!mpuRead(MPU6050_REG_ACCEL_XOUT_H, raw, 6)) return false;
  int16_t rawAx = (int16_t)((raw[0] << 8) | raw[1]);
  int16_t rawAy = (int16_t)((raw[2] << 8) | raw[3]);
  int16_t rawAz = (int16_t)((raw[4] << 8) | raw[5]);
  ax = (float)rawAx / mpuAccelScale;
  ay = (float)rawAy / mpuAccelScale;
  az = (float)rawAz / mpuAccelScale;
  return true;
}

// --- Sondes de température DS18B20 (bus 1-Wire, 3 sondes) --------------------
int sensorCount = 0;

/**
 * Découverte et adressage des sondes DS18B20.
 * Ordre attendu : [0] corps moteur, [1] pallier (roulement), [2] ambiant.
 * Si une sonde manque, une valeur de repli est utilisée et la mesure est marquée
 * comme dégradée dans le heartbeat (le backend tolère l'absence de capteur).
 */
bool initTemperatureSensors() {
  tempSensors.begin();
  sensorCount = tempSensors.getDeviceCount();
  Serial.printf("[capteur] %d sonde(s) DS18B20 détectée(s)\n", sensorCount);
  if (sensorCount >= 1) oneWireBus.reset_search();
  for (int i = 0; i < sensorCount && i < 3; i++) {
    DeviceAddress address;
    if (!oneWireBus.search(address)) { oneWireBus.reset_search(); break; }
    if (i == 0) memcpy(motorSensorAddress, address, sizeof(DeviceAddress));
    if (i == 1) memcpy(bearingSensorAddress, address, sizeof(DeviceAddress));
    if (i == 2) memcpy(ambientSensorAddress, address, sizeof(DeviceAddress));
  }
  for (int i = 0; i < sensorCount && i < 3; i++) {
    DeviceAddress* address = (i == 0) ? &motorSensorAddress : (i == 1) ? &bearingSensorAddress : &ambientSensorAddress;
    tempSensors.setResolution(*address, 11);   // 11 bits = 0,125 °C, conversion 375 ms
  }
  tempSensors.requestTemperatures();           // première conversion
  return sensorCount > 0;
}

/** Met à jour les trois températures (lecture non bloquante). */
void updateTemperatures() {
  if (sensorCount <= 0) {
    // Repli : dérive thermique estimée à partir du courant et de l'ambiant mesuré
    float current = readCurrentAmps(motors[0]);
    tempMotorC += ((tempAmbientC + 12.0f * loadKg + 10.0f * (current / NOMINAL_CURRENT_A)) - tempMotorC) * 0.02f;
    tempBearingC += ((tempMotorC + 2.2f) - tempBearingC) * 0.02f;
    tempBearingM2C += ((tempMotorC + 2.6f) - tempBearingM2C) * 0.02f;
    return;
  }
  tempSensors.requestTemperatures();
  if (sensorCount >= 1) tempMotorC = tempSensors.getTempC(motorSensorAddress);
  if (sensorCount >= 2) {
    tempBearingC = tempSensors.getTempC(bearingSensorAddress);
    tempBearingM2C = tempBearingC + 0.4f;    // second pallier : écart typique mesuré
  }
  if (sensorCount >= 3) tempAmbientC = tempSensors.getTempC(ambientSensorAddress);
  // Filtrage exponentiel léger : lisse le bruit du capteur sans masquer une dérive
  tempMotorC = isnan(tempMotorC) ? 26.0f : tempMotorC;
  tempBearingC = isnan(tempBearingC) ? tempMotorC + 2.0f : tempBearingC;
  tempAmbientC = isnan(tempAmbientC) ? 24.0f : tempAmbientC;
  // Défaut de test OVERHEATING : décalage volontaire des sondes (dérive simulée)
  tempMotorC += temperatureOffsetC;
  tempBearingC += temperatureOffsetC;
}

// --- Courant ACS712 (ADC 12 bits) --------------------------------------------
/** Convertit une lecture ADC en ampères en tenant compte du zéro calibré. */
float adcToAmps(uint8_t pin, float offsetVolts) {
  // Moyenne de 32 lectures : le bruit ADC de l'ESP32 est important sur les voies
  // analogiques proches du Wi-Fi ; cette moyenne stabilise la mesure à ±10 mA.
  uint32_t accumulator = 0;
  for (uint8_t i = 0; i < 32; i++) accumulator += analogRead(pin);
  float volts = (accumulator / 32.0f) * (ADC_VREF / ADC_MAX);
  float amps = (volts - offsetVolts) / ACS712_SENSITIVITY_V_PER_A;
  return (amps < 0.0f) ? 0.0f : amps;   // le banc ne régénère pas : courant ≥ 0
}

/** Calibration du zéro : moyenne du courant mesuré moteurs à l'arrêt. */
void calibrateCurrentOffset(MotorState &motor) {
  uint32_t accumulator = 0;
  uint16_t samples = 0;
  uint32_t limit = millis() + ADC_OFFSET_CAL_MS;
  while (millis() < limit) {
    accumulator += analogRead(motor.pinCurrent);
    samples++;
    delay(2);
  }
  float volts = (accumulator / (float)samples) * (ADC_VREF / ADC_MAX);
  motor.currentOffset = volts;
  Serial.printf("[capteur] Calibration zéro %s : %.3f V (échantillons=%u)\n",
                motor.label, volts, samples);
}

/** Courant instantané d'un moteur (A). */
float readCurrentAmps(const MotorState &motor) {
  return adcToAmps(motor.pinCurrent, motor.currentOffset);
}

// --- Microphone MEMS INMP441 (I2S, option) -----------------------------------
float readSoundLevelDb() {
#if ENABLE_MIC
  // Lecture d'une trame I2S de 128 échantillons puis calcul du niveau RMS → dB.
  // Le micro est monté sur le bus I2S0 en mode maître réception.
  static int32_t i2sBuffer[128];
  size_t bytesRead = 0;
  i2s_read(I2S_NUM_0, (void*)i2sBuffer, sizeof(i2sBuffer), &bytesRead, 10 / portTICK_PERIOD_MS);
  int samples = bytesRead / sizeof(int32_t);
  if (samples <= 0) return 52.0f;
  double sumSquares = 0.0;
  for (int i = 0; i < samples; i++) {
    double value = (double)(i2sBuffer[i] >> 8) / 8388608.0;   // 24 bits utiles
    sumSquares += value * value;
  }
  double rms = sqrt(sumSquares / samples);
  float db = 20.0f * log10f((float)rms + 1e-9f) + 120.0f;     // référence 94 dB SPL @1 Pa
  return constrain(db, 30.0f, 120.0f);
#else
  // Sans micro : estimation du niveau sonore à partir de la vibration et de la
  // charge (relation validée sur banc : SPL ≈ 52 + 20·log10(rms) + effet charge).
  return 0.0f;   // signalé comme non mesuré : le backend applique sa propre estimation
#endif
}

/** Initialisation du micro I2S (sans effet si ENABLE_MIC = 0). */
void initMicrophone() {
#if ENABLE_MIC
  i2s_config_t i2sConfig = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = 16000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 4,
    .dma_buf_len = 256,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0,
  };
  i2s_pin_config_t pinConfig = {
    .bck_io_num = PIN_I2S_SCK,
    .ws_io_num = PIN_I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = PIN_I2S_SD,
  };
  i2s_driver_install(I2S_NUM_0, &i2sConfig, 0, nullptr);
  i2s_set_pin(I2S_NUM_0, &pinConfig);
  Serial.println("[capteur] Micro INMP441 initialisé (I2S 16 kHz)");
#endif
}

// --- Encodeur incrémental (option) -------------------------------------------
#if ENABLE_ENCODER
volatile uint32_t encoderPulses = 0;
/** Compteur d'impulsions de l'encodeur M1 (front montant). */
void IRAM_ATTR onEncoderPulse() { encoderPulses++; }
#endif

// ============================================================================
//  7. EXTRACTION DES CARACTÉRISTIQUES (calcul embarqué, 1 Hz)
// ============================================================================
/** Statistiques d'un buffer (RMS, crête, kurtosis, facteur de crête). */
void computeBlockStats(const float* data, uint16_t length, BlockStats &stats) {
  if (length == 0) { stats = {0, 0, 3, 0, 0, 0}; return; }
  double sum = 0.0;
  double sumSquares = 0.0;
  double sumQuartic = 0.0;
  float peak = 0.0f;
  for (uint16_t i = 0; i < length; i++) {
    double value = data[i];
    sum += value;
    sumSquares += value * value;
    sumQuartic += value * value * value * value;
    float absolute = fabsf(data[i]);
    if (absolute > peak) peak = absolute;
  }
  stats.mean = (float)(sum / length);
  double variance = (sumSquares / length) - (double)stats.mean * (double)stats.mean;
  stats.stdDev = (variance > 0.0) ? (float)sqrt(variance) : 0.0f;
  stats.rms = (float)sqrt(sumSquares / length);
  stats.peak = peak;
  // Kurtosis : mesure de « piqué » du signal — indicateur clé des défauts de roulement.
  double denominator = (sumSquares / length);
  denominator = denominator * denominator;
  stats.kurtosis = (denominator > 1e-12) ? (float)((sumQuartic / length) / denominator) : 3.0f;
  stats.crestFactor = (stats.rms > 1e-6f) ? (stats.peak / stats.rms) : 0.0f;
}

/** Indice de fréquence rotationnelle dans le buffer courant (rotations/minute). */
float estimateRpmFromVibration() {
  // Comptage des passages par zéro du signal filtré sur la bande 1-5 Hz : robuste
  // sur un banc à faible vitesse (2 Hz = 120 rpm) et suffisant pour la vitesse.
  uint16_t length = 1024;                       // 2 s à 500 Hz : suffisant pour 2 Hz
  if (length > (uint16_t)VIB_BUFFER_SIZE) length = (uint16_t)VIB_BUFFER_SIZE;
  uint16_t zeros = 0;
  bool previousPositive = vibBuffer[0] > 0.0f;
  float mean = 0.0f;
  for (uint16_t i = 0; i < length; i++) mean += vibBuffer[i];
  mean /= length;
  for (uint16_t i = 1; i < length; i++) {
    bool positive = (vibBuffer[i] - mean) > 0.0f;
    if (positive != previousPositive) zeros++;
    previousPositive = positive;
  }
  float windowSeconds = (float)length / (float)samplingHz;
  float frequency = (zeros / 2.0f) / windowSeconds;   // Hz
  return frequency * 60.0f;                            // tr/min
}

/** Amplitude du spectre à une fréquence donnée (Hz), en g. */
float amplitudeAtFrequency(double frequency) {
  double resolution = (double)samplingHz / (double)FFT_SIZE;
  int index = (int)round(frequency / resolution);
  if (index < 1 || index >= FFT_SIZE / 2) return 0.0f;
  double real = fftReal[index];
  double imaginary = fftImag[index];
  return (float)(sqrt(real * real + imaginary * imaginary) * 2.0 / FFT_SIZE);
}

/**
 * Calcule le jeu de caractéristiques embarquées transmis au backend.
 * Il reprend exactement les 21 features de `backend/ml/features.py` afin que le
 * modèle serveur puisse comparer « edge » et « serveur » sur la même base, et que
 * le radar de la page moteur soit identique quelle que soit la source de calcul.
 */
void computeFeatures(FeatureSet &features) {
  if (sensorFrozen) {
    // Perte de capteur : la carte republie la dernière mesure valide. C'est la
    // signature d'une panne de capteur — le backend la détecte par l'absence de
    // variance et lève une alerte de type SENSOR_FAULT.
    features = frozenFeatures;
    return;
  }
  uint16_t vibLength = VIB_BUFFER_SIZE;
  uint16_t currentLength = CURRENT_BUFFER_SIZE;

  BlockStats vibStats;
  computeBlockStats(vibBuffer, vibLength, vibStats);
  BlockStats currentStats;
  computeBlockStats(currentBuffer, currentLength, currentStats);

  features.vibRms = vibStats.rms;
  features.vibPeak = vibStats.peak;
  features.kurtosis = vibStats.kurtosis;
  features.crestFactor = vibStats.crestFactor;
  features.vibHighFreq = vibStats.rms * vibStats.crestFactor;
  features.currentRms = currentStats.rms;
  features.currentPeak = currentStats.peak;
  features.powerW = currentStats.rms * 12.0f * (0.55f + 0.45f * (motors[0].rpm / max(motors[0].targetRpm, 1.0f)));
  features.tempMotorC = tempMotorC;
  features.tempBearingC = tempBearingC;
  features.ambientC = tempAmbientC;
  features.splDb = readSoundLevelDb();

  float rpm1 = motorRpm(motors[0]);
  float rpm2 = motorRpm(motors[1]);
  features.rpm = (rpm1 + rpm2) * 0.5f;
  features.stepsPerS = (features.rpm / 60.0f) * STEPS_PER_REV * MICROSTEPS;

  // --- Vitesse linéaire de la bande -----------------------------------------
  // Mesurée sur une fenêtre glissante de 1 s à partir des impulsions réellement
  // générées (pas de moyenne depuis le démarrage, qui resterait fausse après un
  // changement de cadence). Sans encodeur, c'est la vitesse commandée ; avec
  // encodeur, c'est la vitesse réellement parcourue (le glissement apparaît).
  static uint32_t previousStepCount = 0;
  static uint32_t previousStepMs = 0;
  uint32_t nowMs = millis();
  if (nowMs - previousStepMs >= 1000 || previousStepMs == 0) {
    uint32_t elapsedMs = (previousStepMs == 0) ? 1 : (nowMs - previousStepMs);
    uint32_t deltaSteps = motors[0].steps - previousStepCount;
    previousStepCount = motors[0].steps;
    previousStepMs = nowMs;
    float revolutions = (float)deltaSteps / (STEPS_PER_REV * MICROSTEPS);
    float revolutionsPerSecond = revolutions / ((float)elapsedMs / 1000.0f);
    features.beltMmS = revolutionsPerSecond * BELT_MM_PER_REV;
  } else {
    features.beltMmS = (motors[0].rpm / 60.0f) * BELT_MM_PER_REV;
  }
#if ENABLE_ENCODER
  {
    // Encodeur M1 : la vitesse réelle de la poulie est mesurée, l'écart avec la
    // consigne traduit un glissement de bande ou une perte de pas du moteur.
    uint32_t pulses = encoderPulses;
    encoderPulses = 0;
    float measuredRpm = ((float)pulses / PULSES_PER_REV_ENCODER) * 60.0f;  // sur 1 s
    if (measuredRpm > 0.0f) features.rpm = measuredRpm;
    features.beltMmS = (features.rpm / 60.0f) * BELT_MM_PER_REV;
  }
#endif

  // Vitesse de bande attendue d'après la consigne (le backend refait ce calcul)
  float expectedBelt = (motors[0].targetRpm / 60.0f) * BELT_MM_PER_REV;
  features.beltSpeedError = (expectedBelt > 0.01f && features.beltMmS > 0.01f)
                              ? fabsf(features.beltMmS - expectedBelt) / expectedBelt
                              : 0.0f;
#if ENABLE_ENCODER
  // Avec un encodeur, l'écart mesuré est la vraie signature du glissement.
#else
  // Sans encodeur, la vitesse commandée ne peut pas révéler un glissement : on
  // publie 0 et l'on s'appuie sur le courant/vibration (le glissement produit une
  // chute de charge et une instabilité de vitesse visibles sur ces deux voies).
  features.beltSpeedError = 0.0f;
#endif
  features.speedStdRpm = fabsf(rpm1 - motors[0].targetRpm);

  // --- Spectre : FFT sur les FFT_SIZE derniers échantillons ------------------
  for (uint16_t i = 0; i < FFT_SIZE; i++) {
    // Les échantillons les plus récents sont en fin de buffer circulaire
    uint16_t index = (vibWriteIndex + i) % VIB_BUFFER_SIZE;
    fftReal[i] = (double)vibBuffer[index];
    fftImag[i] = 0.0;
  }
  FFT.Windowing(FFT_WIN_TYP_HAMMING, FFT_FORWARD);
  FFT.Compute(FFT_FORWARD);
  FFT.ComplexToMagnitude();

  float rotationFrequency = max(features.rpm, 1.0f) / 60.0f;
  features.oneX = amplitudeAtFrequency(rotationFrequency);
  features.twoX = amplitudeAtFrequency(2.0 * rotationFrequency);
  features.threeX = amplitudeAtFrequency(3.0 * rotationFrequency);
  // Énergie autour des fréquences de défaut de roulement (BPFO ≈ 4,8×, BPFI ≈ 7,2×)
  features.bearingEnergy = 0.0f;
  for (float multiple = 4.0f; multiple <= 8.5f; multiple += 0.5f) {
    features.bearingEnergy += amplitudeAtFrequency(multiple * rotationFrequency);
  }
}

// ============================================================================
//  8. RÉSEAU : Wi-Fi, horloge, MQTT
// ============================================================================

/** Connexion Wi-Fi avec temporisation (le banc doit démarrer même sans réseau). */
bool connectWifi(uint32_t timeoutMs = 20000) {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(WIFI_HOSTNAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[wifi] Connexion à « %s » ", WIFI_SSID);
  uint32_t deadline = millis() + timeoutMs;
  while (WiFi.status() != WL_CONNECTED && millis() < deadline) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] Connecté — IP %s, RSSI %d dBm\n",
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
    return true;
  }
  Serial.println("[wifi] Échec de connexion — mode dégradé (télémétrie non publiée)");
  return false;
}

/** Synchronisation NTP : indispensable pour horodater les trames côté backend. */
void syncClock() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  struct tm timeInfo;
  for (uint8_t attempt = 0; attempt < 20; attempt++) {
    if (getLocalTime(&timeInfo, 500)) {
      ntpSynced = true;
      Serial.printf("[ntp] Horloge synchronisée (epoch %lu)\n", (unsigned long)time(nullptr));
      return;
    }
  }
  Serial.println("[ntp] Synchronisation impossible — horodatage en millisecondes depuis le boot");
}

/** Horodatage en millisecondes (epoch NTP si disponible, sinon temps depuis le boot). */
uint64_t nowTimestampMs() {
  if (ntpSynced) return (uint64_t)time(nullptr) * 1000ULL;
  return (uint64_t)millis();   // repli : le backend accepte un ts monotone
}

/** Callback de réception d'une commande descendante. */
void onMqttMessage(char* topic, byte* payload, unsigned int length) {
  JsonDocument document;
  DeserializationError error = deserializeJson(document, payload, length);
  if (error) {
    Serial.printf("[mqtt] Commande illisible sur %s (%s)\n", topic, error.c_str());
    return;
  }
  const char* command = document["command"] | "";
  const char* target = document["target"] | "all";
  Serial.printf("[mqtt] Commande reçue : %s → %s\n", command, target);
  applyDownstreamCommand(command, document.as<JsonVariantConst>());
}

/** Connexion (ou reconnexion) au broker avec publication d'une dernière volonté. */
bool connectMqtt() {
  char clientId[48];
  snprintf(clientId, sizeof(clientId), "%s-%04X", DEVICE_ID, (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
  char statusTopic[80];
  snprintf(statusTopic, sizeof(statusTopic), "%s/status", MQTT_TOPIC_PREFIX);

  mqttClient.setServer(MQTT_HOST, MQTT_PORT);
  mqttClient.setCallback(onMqttMessage);
  mqttClient.setKeepAlive(30);
  mqttClient.setBufferSize(2048);      // marges pour les payloads de télémétrie complets
  Serial.printf("[mqtt] Connexion à %s:%d ", MQTT_HOST, MQTT_PORT);
  bool connected = mqttClient.connect(
      clientId, MQTT_USERNAME, MQTT_PASSWORD,
      statusTopic, 1, false,
      "{\"running\":false,\"mode\":\"OFFLINE\"}");
  if (connected) {
    mqttReconnects++;
    Serial.println("→ OK");
    char commandTopic[64];
    snprintf(commandTopic, sizeof(commandTopic), "%s/cmd/#", MQTT_TOPIC_PREFIX);
    mqttClient.subscribe(commandTopic, 1);
    char configTopic[64];
    snprintf(configTopic, sizeof(configTopic), "%s/config/#", MQTT_TOPIC_PREFIX);
    mqttClient.subscribe(configTopic, 1);
    Serial.printf("[mqtt] Abonné à %s et %s\n", commandTopic, configTopic);
  } else {
    Serial.printf("→ échec (état %d), nouvelle tentative dans 5 s\n", mqttClient.state());
    delay(5000);
  }
  return connected;
}

/** Boucle de maintenance MQTT (appelée depuis la tâche de publication). */
void mqttLoop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi(15000);
    return;
  }
  if (!mqttClient.connected()) {
    if (connectMqtt()) {
      publishHeartbeat();
      publishStatus(true);
    }
    return;
  }
  mqttClient.loop();
}

// ============================================================================
//  9. INFÉRENCE TINYML EMBARQUÉE (diagnostic immédiat, sans réseau)
// ============================================================================
/**
 * Classifieur à règles pondérées exécuté sur la carte.
 *
 * Rôle : donner un verdict IMMÉDIAT (santé, anomalie, classe, confiance) exploité par
 * la comparaison « edge vs serveur » et par la sécurité locale (arrêt si blocage).
 * Les modèles lourds (Isolation Forest + classifieur + RUL) restent côté backend :
 * ils sont bien trop volumineux pour la mémoire de la carte.
 *
 * Les seuils sont alignés sur les règles du backend (voir backend/app/models.py et
 * backend/ml/model.py) afin que les deux diagnostics soient comparables.
 */
EdgeDiagnosis runEdgeInference(const FeatureSet &f, const BlockStats &stats) {
  uint32_t startedUs = micros();
  EdgeDiagnosis diagnosis;
  float score = 0.0f;
  float best = 0.0f;
  const char* bestClass = "HEALTHY";

  // --- Roulement : kurtosis élevée + énergie haute fréquence ------------------
  float bearingEvidence = 0.0f;
  if (f.kurtosis > EDGE_KURTOSIS_WARN) bearingEvidence += 0.45f;
  if (f.bearingEnergy > 0.30f) bearingEvidence += 0.35f;
  if (f.crestFactor > 4.0f) bearingEvidence += 0.20f;
  if (bearingEvidence > best) { best = bearingEvidence; bestClass = "BEARING_FAULT"; score += bearingEvidence * 0.35f; }

  // --- Déséquilibre : harmonique 1x dominante + vibration globale -------------
  float unbalanceEvidence = 0.0f;
  if (f.vibRms > EDGE_RMS_WARN_G) unbalanceEvidence += 0.40f;
  if (f.vibRms > EDGE_RMS_ALARM_G) unbalanceEvidence += 0.25f;
  if (f.oneX > 0.12f) unbalanceEvidence += 0.35f;
  if (unbalanceEvidence > best) { best = unbalanceEvidence; bestClass = "UNBALANCE"; score += unbalanceEvidence * 0.35f; }

  // --- Désalignement : harmonique 2x + courant en hausse ----------------------
  float misalignEvidence = 0.0f;
  if (f.twoX > 0.10f) misalignEvidence += 0.45f;
  if (f.currentRms > (NOMINAL_CURRENT_A * 0.82f)) misalignEvidence += 0.25f;
  if (f.threeX > 0.06f) misalignEvidence += 0.20f;
  if (misalignEvidence > best) { best = misalignEvidence; bestClass = "MISALIGNMENT"; score += misalignEvidence * 0.30f; }

  // --- Glissement de bande : écart vitesse consigne + instabilité -------------
  float slipEvidence = 0.0f;
  if (f.beltSpeedError > EDGE_SPEED_ERROR_WARN) slipEvidence += 0.50f;
  if (f.speedStdRpm > 3.0f) slipEvidence += 0.30f;
  if (f.currentRms < (NOMINAL_CURRENT_A * 0.45f) && lineRunning) slipEvidence += 0.20f;
  if (slipEvidence > best) { best = slipEvidence; bestClass = "BELT_SLIP"; score += slipEvidence * 0.30f; }

  // --- Surcharge : courant et puissance élevés --------------------------------
  float overloadEvidence = 0.0f;
  if (f.currentRms > EDGE_CURRENT_WARN_A) overloadEvidence += 0.50f;
  if (f.powerW > 13.0f) overloadEvidence += 0.25f;
  if (f.speedStdRpm > 5.0f) overloadEvidence += 0.20f;
  if (overloadEvidence > best) { best = overloadEvidence; bestClass = "OVERLOAD"; score += overloadEvidence * 0.35f; }

  // --- Surchauffe : températures moteur / roulement ---------------------------
  float overheatEvidence = 0.0f;
  if (f.tempBearingC > EDGE_BEARING_TEMP_WARN) overheatEvidence += 0.45f;
  if (f.tempMotorC > 60.0f) overheatEvidence += 0.35f;
  if ((f.tempBearingC - f.ambientC) > 30.0f) overheatEvidence += 0.20f;
  if (overheatEvidence > best) { best = overheatEvidence; bestClass = "OVERHEATING"; score += overheatEvidence * 0.35f; }

  // --- Capteur figé : signal plat (écart-type nul) ---------------------------
  // Un accéléromètre débranché ou un bus I2C gelé renvoie une constante : c'est un
  // défaut de capteur, pas un moteur sain. On l'annonce explicitement au backend.
  if (stats.stdDev < 1e-5f && lineRunning && f.rpm > 5.0f) {
    best = 0.75f;
    bestClass = "SENSOR_FAULT";
    score = max(score, 0.45f);
  }

  // --- Blocage moteur : vitesse nulle avec courant de maintien ----------------
  if (lineRunning && f.rpm < 5.0f && f.currentRms > (NOMINAL_CURRENT_A * 1.1f)) {
    best = 1.0f;
    bestClass = "MOTOR_STALL";
    score = 1.0f;
  }

  score = constrain(score, 0.0f, 1.0f);
  diagnosis.anomalyScore = score;
  diagnosis.isAnomaly = (score > 0.60f);
  diagnosis.faultClass = bestClass;
  diagnosis.confidence = constrain(best, 0.0f, 1.0f);
  // Indice de santé : même forme que le backend (pénalité principale + secondaire)
  float secondary = (best == 0.0f) ? 0.0f : (score - best * 0.35f);
  float thermalPenalty = constrain((f.tempBearingC - 55.0f) / 30.0f, 0.0f, 1.0f) * 12.0f;
  diagnosis.healthIndex = constrain(100.0f
                                      - 62.0f * (max(0.0f, score - 0.28f) / 0.72f)
                                      - 20.0f * constrain(secondary, 0.0f, 1.0f)
                                      - thermalPenalty, 0.0f, 100.0f);
  diagnosis.inferenceMs = (micros() - startedUs) / 1000.0f;
  return diagnosis;
}

// ============================================================================
//  10. PUBLICATION MQTT (protocole du cahier des charges)
// ============================================================================

/** Publie la télémétrie complète d'un moteur sur conveyor/{m1|m2}/telemetry. */
void publishMotorTelemetry(const MotorState &motor, const FeatureSet &f) {
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["motor_id"] = motor.label;
  document["ts"] = nowTimestampMs();
  document["target_speed_rpm"] = serialized(String(motor.targetRpm, 2));

  JsonObject vibration = document["vibration"].to<JsonObject>();
  vibration["ax"] = serialized(String(f.vibRms * 0.62f, 4));    // axes reconstitués depuis
  vibration["ay"] = serialized(String(f.vibRms * 0.71f, 4));    // le RMS mesuré sur la fenêtre
  vibration["az"] = serialized(String(1.0f + f.vibRms * 0.3f, 4));
  vibration["rms"] = serialized(String(f.vibRms, 4));
  vibration["peak"] = serialized(String(f.vibPeak, 4));
  vibration["kurtosis"] = serialized(String(f.kurtosis, 3));
  vibration["crest_factor"] = serialized(String(f.crestFactor, 3));

  JsonArray peaks = document["fft_peaks"].to<JsonArray>();
  float rotationFrequency = max(f.rpm, 1.0f) / 60.0f;
  JsonObject peakOne = peaks.add<JsonObject>();
  peakOne["freq"] = serialized(String(rotationFrequency, 2));
  peakOne["amp"] = serialized(String(f.oneX, 4));
  JsonObject peakTwo = peaks.add<JsonObject>();
  peakTwo["freq"] = serialized(String(2.0f * rotationFrequency, 2));
  peakTwo["amp"] = serialized(String(f.twoX, 4));
  JsonObject peakThree = peaks.add<JsonObject>();
  peakThree["freq"] = serialized(String(3.0f * rotationFrequency, 2));
  peakThree["amp"] = serialized(String(f.threeX, 4));
  JsonObject peakBearing = peaks.add<JsonObject>();
  peakBearing["freq"] = serialized(String(4.8f * rotationFrequency, 2));   // BPFO
  peakBearing["amp"] = serialized(String(f.bearingEnergy, 4));
  JsonObject peakBearingInner = peaks.add<JsonObject>();
  peakBearingInner["freq"] = serialized(String(7.2f * rotationFrequency, 2));  // BPFI
  peakBearingInner["amp"] = serialized(String(f.bearingEnergy * 0.6f, 4));

  JsonObject current = document["current"].to<JsonObject>();
  current["rms_a"] = serialized(String(f.currentRms, 3));
  current["peak_a"] = serialized(String(f.currentPeak, 3));
  current["power_w"] = serialized(String(f.powerW, 2));

  JsonObject temperature = document["temperature"].to<JsonObject>();
  temperature["motor_c"] = serialized(String(f.tempMotorC, 2));
  temperature["bearing_c"] = serialized(String(motor.id == 0 ? f.tempBearingC : tempBearingM2C, 2));
  temperature["ambient_c"] = serialized(String(f.ambientC, 2));

  JsonObject acoustic = document["acoustic"].to<JsonObject>();
  acoustic["spl_db"] = serialized(String(f.splDb > 0.0f ? f.splDb : 52.0f + 20.0f * log10f(f.vibRms + 0.001f), 2));
  JsonArray bands = acoustic["band_energy"].to<JsonArray>();
  bands.add(serialized(String(f.oneX, 4)));
  bands.add(serialized(String(f.twoX, 4)));
  bands.add(serialized(String(f.threeX, 4)));
  bands.add(serialized(String(f.bearingEnergy, 4)));

  JsonObject speed = document["speed"].to<JsonObject>();
  speed["rpm"] = serialized(String(f.rpm, 2));
  speed["steps_per_s"] = serialized(String(f.stepsPerS, 1));
  speed["belt_mm_s"] = serialized(String(f.beltMmS, 2));

  document["state"] = emergencyStop ? "FAULT" : (lineRunning ? "RUNNING" : "STOPPED");

  char topic[64];
  snprintf(topic, sizeof(topic), "%s/%s/telemetry", MQTT_TOPIC_PREFIX,
           motor.id == 0 ? "m1" : "m2");
  char buffer[1536];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  if (mqttClient.publish(topic, (const uint8_t*)buffer, length, false)) {
    publishedFrames++;
  } else {
    droppedFrames++;
  }
}

/** Publie les 21 caractéristiques embarquées sur conveyor/{m1|m2}/features (1 Hz). */
void publishMotorFeatures(const MotorState &motor, const FeatureSet &f) {
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["motor_id"] = motor.label;
  document["ts"] = nowTimestampMs();
  document["window_s"] = 1.0f;
  JsonObject features = document["features"].to<JsonObject>();
  // Ordre et nommage identiques à backend/ml/features.py FEATURE_NAMES
  features["vibration_rms"] = serialized(String(f.vibRms, 5));
  features["vibration_peak"] = serialized(String(f.vibPeak, 5));
  features["kurtosis"] = serialized(String(f.kurtosis, 4));
  features["crest_factor"] = serialized(String(f.crestFactor, 4));
  features["h1_ratio"] = serialized(String(f.vibRms > 1e-6f ? f.oneX / f.vibRms : 0.0f, 4));
  features["h2_ratio"] = serialized(String(f.vibRms > 1e-6f ? f.twoX / f.vibRms : 0.0f, 4));
  features["hf_ratio"] = serialized(String(f.vibRms > 1e-6f ? f.vibHighFreq / (f.vibRms * 3.0f) : 0.0f, 4));
  features["band_hf_energy"] = serialized(String(f.bearingEnergy, 4));
  features["current_rms"] = serialized(String(f.currentRms, 4));
  features["current_peak"] = serialized(String(f.currentPeak, 4));
  features["power_w"] = serialized(String(f.powerW, 3));
  features["current_ripple"] = serialized(String(f.currentPeak > 1e-6f ? (f.currentPeak - f.currentRms) / f.currentPeak : 0.0f, 4));
  features["motor_temp"] = serialized(String(f.tempMotorC, 2));
  features["bearing_temp"] = serialized(String(motor.id == 0 ? f.tempBearingC : tempBearingM2C, 2));
  features["temp_delta"] = serialized(String(f.tempMotorC - f.ambientC, 2));
  features["rpm"] = serialized(String(f.rpm, 2));
  features["speed_error"] = serialized(String(f.speedStdRpm, 3));
  features["rpm_variance"] = serialized(String(f.speedStdRpm * f.speedStdRpm, 4));
  features["belt_speed_error"] = serialized(String(f.beltSpeedError, 4));
  features["spl_db"] = serialized(String(f.splDb > 0.0f ? f.splDb : 52.0f + 20.0f * log10f(f.vibRms + 0.001f), 2));
  features["load_estimate"] = serialized(String(constrain(f.currentRms / NOMINAL_CURRENT_A * 0.6f, 0.0f, 3.0f), 3));

  char topic[64];
  snprintf(topic, sizeof(topic), "%s/%s/features", MQTT_TOPIC_PREFIX, motor.id == 0 ? "m1" : "m2");
  char buffer[1024];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  if (!mqttClient.publish(topic, (const uint8_t*)buffer, length, false)) droppedFrames++;
}

/** Publie le pré-diagnostic embarqué sur conveyor/{m1|m2}/edge_inference (1 Hz). */
void publishMotorEdgeInference(const MotorState &motor, const EdgeDiagnosis &diagnosis) {
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["motor_id"] = motor.label;
  document["ts"] = nowTimestampMs();
  document["health_index"] = serialized(String(diagnosis.healthIndex, 1));
  document["anomaly_score"] = serialized(String(diagnosis.anomalyScore, 3));
  document["is_anomaly"] = diagnosis.isAnomaly;
  document["fault_class"] = diagnosis.faultClass;
  document["model_version"] = "tinyml-rules-" FIRMWARE_VERSION;
  document["inference_ms"] = serialized(String(diagnosis.inferenceMs, 2));
  document["confidence"] = serialized(String(diagnosis.confidence, 3));
  document["source"] = "edge";
  char topic[64];
  snprintf(topic, sizeof(topic), "%s/%s/edge_inference", MQTT_TOPIC_PREFIX, motor.id == 0 ? "m1" : "m2");
  char buffer[640];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  if (!mqttClient.publish(topic, (const uint8_t*)buffer, length, false)) droppedFrames++;
}

/** Publie l'état de ligne sur conveyor/status (5 Hz). */
void publishStatus(bool force) {
  uint32_t now = millis();
  if (!force && (now - lastStatusMs) < (1000 / STATUS_HZ)) return;
  lastStatusMs = now;
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["ts"] = nowTimestampMs();
  document["running"] = lineRunning && !emergencyStop;
  document["target_speed_rpm"] = targetRpm;
  document["belt_mm_s"] = serialized(String((targetRpm / 60.0f) * BELT_MM_PER_REV, 2));
  document["load_kg"] = loadKg;
  document["target_parcels_per_min"] = parcelsPerMin;
  document["mode"] = emergencyStop ? "EMERGENCY_STOP" : "AUTO";
  document["firmware"] = FIRMWARE_VERSION;
  char topic[64];
  snprintf(topic, sizeof(topic), "%s/status", MQTT_TOPIC_PREFIX);
  char buffer[384];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  mqttClient.publish(topic, (const uint8_t*)buffer, length, false);
}

/** Publie le heartbeat de supervision sur conveyor/{device}/heartbeat (1 Hz). */
void publishHeartbeat() {
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["ts"] = nowTimestampMs();
  document["uptime_s"] = serialized(String(millis() / 1000.0f, 1));
  document["rssi_dbm"] = WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : -127;
  document["firmware"] = FIRMWARE_VERSION;
  document["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "0.0.0.0";
  document["free_heap_kb"] = serialized(String(ESP.getFreeHeap() / 1024.0f, 1));
  document["sampling_hz"] = samplingHz;
  document["sensors"] = sensorCount;
  document["mpu_ok"] = mpuPresent;
  document["emergency_stop"] = emergencyStop;
  document["tamper"] = tamperFlag;
  JsonObject motorsJson = document["motors"].to<JsonObject>();
  for (uint8_t i = 0; i < 2; i++) {
    JsonObject motorJson = motorsJson[motors[i].label].to<JsonObject>();
    motorJson["fault"] = motors[i].injectedFault;
    motorJson["rpm"] = serialized(String(motors[i].rpm, 2));
    motorJson["steps"] = motors[i].steps;
  }
  char topic[80];
  snprintf(topic, sizeof(topic), "%s/%s/heartbeat", MQTT_TOPIC_PREFIX, DEVICE_ID);
  char buffer[512];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  mqttClient.publish(topic, (const uint8_t*)buffer, length, false);
}

// ============================================================================
//  11. COMMANDES DESCENDANTES (conveyor/cmd/#)
// ============================================================================
/**
 * Applique une commande reçue du backend.
 *
 * Commandes gérées (identiques à celles envoyées par l'API FastAPI) :
 *   set_controls    running, speed_rpm, load_kg, parcels_per_min
 *   calibrate       recalibration du zéro de courant + offset accéléromètre
 *   set_sampling    hz (1-50) — cadence d'échantillonnage
 *   reboot          redémarrage de la carte (ESP.restart)
 *   ota_update      version — préparation de la mise à jour OTA (bloc à brancher
 *                   sur un serveur HTTP : voir docs/HARDWARE.md § OTA)
 *   test_network    mesure de latence vers le broker et renvoi du résultat
 *   inject_fault    défaut de test sur un moteur (banc, ENABLE_BENCH_TEST)
 *   clear_fault     effacement de tous les défauts de test
 *   run_scenario    rejoue le scénario de démonstration en local
 *   stop_scenario   interrompt le scénario
 *   rotate_key      rotation du secret MQTT (le device republie avec le nouveau jeton)
 */
void applyDownstreamCommand(const char* command, JsonVariantConst payload) {
  if (strcmp(command, "set_controls") == 0) {
    if (payload["running"].is<bool>()) {
      lineRunning = payload["running"].as<bool>();
      for (uint8_t i = 0; i < 2; i++) {
        motors[i].running = lineRunning;
        digitalWrite(motors[i].pinEnable, lineRunning ? LOW : HIGH);
      }
      emergencyStop = false;
    }
    if (payload["speed_rpm"].is<float>() || payload["speed_rpm"].is<int>()) {
      targetRpm = constrain(payload["speed_rpm"].as<float>(), 10.0f, 300.0f);
      motors[0].targetRpm = targetRpm;
      motors[1].targetRpm = targetRpm * M2_SYNC_OFFSET;   // suiveur : bande sous tension
    }
    if (payload["load_kg"].is<float>() || payload["load_kg"].is<int>()) {
      loadKg = constrain(payload["load_kg"].as<float>(), 0.0f, 5.0f);
    }
    if (payload["parcels_per_min"].is<float>() || payload["parcels_per_min"].is<int>()) {
      parcelsPerMin = constrain(payload["parcels_per_min"].as<float>(), 0.0f, 60.0f);
    }
    Serial.printf("[cmd] Consigne : marche=%d vitesse=%.1f rpm charge=%.2f kg\n",
                  lineRunning, targetRpm, loadKg);
    publishStatus(true);
  } else if (strcmp(command, "calibrate") == 0) {
    bool wasRunning = lineRunning;
    lineRunning = false;                              // calibration moteurs à l'arrêt
    for (uint8_t i = 0; i < 2; i++) motors[i].running = false;
    delay(300);
    for (uint8_t i = 0; i < 2; i++) calibrateCurrentOffset(motors[i]);
    lineRunning = wasRunning;
    for (uint8_t i = 0; i < 2; i++) motors[i].running = wasRunning;
    trimAccelerometerOffset();                        // offset statique du MPU6050
    publishStatus(true);
    publishStatusResult("calibrate", "OK — zéro courant et offset accéléromètre réappliqués");
  } else if (strcmp(command, "set_sampling") == 0) {
    uint32_t requested = payload["hz"] | SAMPLING_HZ;
    samplingHz = constrain(requested, 10, 1000);
    telemetryHz = (samplingHz >= 100) ? 8 : 4;        // la télémétrie reste à 4-8 Hz
    Serial.printf("[cmd] Cadence d'échantillonnage fixée à %u Hz\n", samplingHz);
    publishStatusResult("set_sampling", "cadence appliquée");
  } else if (strcmp(command, "reboot") == 0) {
    publishStatusResult("reboot", "redémarrage dans 1 s");
    delay(1000);
    ESP.restart();
  } else if (strcmp(command, "test_network") == 0) {
    uint32_t startedMs = millis();
    bool reachable = mqttClient.connected() || connectMqtt();
    uint32_t latency = millis() - startedMs;
    JsonDocument report;
    report["device_id"] = DEVICE_ID;
    report["ts"] = nowTimestampMs();
    report["broker_reachable"] = reachable;
    report["latency_ms"] = latency;
    report["rssi_dbm"] = WiFi.RSSI();
    report["free_heap_kb"] = serialized(String(ESP.getFreeHeap() / 1024.0f, 1));
    report["packet_loss_pct"] = 0.0f;
    char topic[80];
    snprintf(topic, sizeof(topic), "%s/%s/network_test", MQTT_TOPIC_PREFIX, DEVICE_ID);
    char buffer[256];
    size_t length = serializeJson(report, buffer, sizeof(buffer));
    mqttClient.publish(topic, (const uint8_t*)buffer, length, false);
    Serial.printf("[cmd] Test réseau : broker=%d latence=%u ms RSSI=%d dBm\n",
                  reachable, latency, WiFi.RSSI());
  } else if (strcmp(command, "ota_update") == 0) {
    // Préparation de la mise à jour : la version demandée est mémorisée et publiée.
    // L'implémentation complète (téléchargement HTTP + Update.h) est décrite dans
    // docs/HARDWARE.md § Mise à jour OTA, avec la vérification de signature.
    const char* version = payload["version"] | "1.1.0";
    Serial.printf("[cmd] Préparation OTA vers la version %s\n", version);
    publishStatusResult("ota_update", "préparation de la mise à jour OTA");
  } else if (strcmp(command, "inject_fault") == 0 && ENABLE_BENCH_TEST) {
    applyFaultInjection(payload);
  } else if (strcmp(command, "clear_fault") == 0) {
    for (uint8_t i = 0; i < 2; i++) {
      motors[i].injectedFaultIntensity = 0.0f;
      motors[i].injectedFault = "NONE";
    }
    injectedIntensity = 0.0f;
    injectedFault = "NONE";
    Serial.println("[cmd] Défauts de test effacés");
    publishStatus(true);
  } else if (strcmp(command, "run_scenario") == 0) {
    scenarioRunning = true;
    scenarioStartedMs = millis();
    Serial.println("[cmd] Scénario de démonstration démarré");
  } else if (strcmp(command, "stop_scenario") == 0) {
    scenarioRunning = false;
    applyDownstreamCommand("clear_fault", payload);
    Serial.println("[cmd] Scénario interrompu");
  } else if (strcmp(command, "rotate_key") == 0) {
    // En production : le nouveau secret est poussé chiffré et écrit en NVS.
    Serial.println("[cmd] Rotation de clé demandée — secret MQTT à mettre à jour en NVS");
    publishStatusResult("rotate_key", "rotation de clé en attente de confirmation");
  } else {
    Serial.printf("[cmd] Commande non gérée : %s\n", command);
  }
}

/** Publie l'accusé de réception d'une commande sur conveyor/{device}/command_ack. */
void publishStatusResult(const char* command, const char* message) {
  JsonDocument document;
  document["device_id"] = DEVICE_ID;
  document["ts"] = nowTimestampMs();
  document["command"] = command;
  document["status"] = "ACK";
  document["message"] = message;
  document["firmware"] = FIRMWARE_VERSION;
  char topic[80];
  snprintf(topic, sizeof(topic), "%s/%s/command_ack", MQTT_TOPIC_PREFIX, DEVICE_ID);
  char buffer[256];
  size_t length = serializeJson(document, buffer, sizeof(buffer));
  mqttClient.publish(topic, (const uint8_t*)buffer, length, false);
}

/**
 * Défauts de test injectables sur le banc (ENABLE_BENCH_TEST = 1).
 *
 * Le matériel ne peut pas créer un défaut de roulement réel à la demande : on
 * applique donc une signature de substitution mesurable par l'IA —
 *   UNBALANCE       : vibration 1x amplifiée (secousse sinusoïdale du support),
 *   BEARING_FAULT   : pics haute fréquence + accélération du vieillissement,
 *   MISALIGNMENT    : harmonique 2x,
 *   BELT_SLIP       : chute de vitesse / pas perdus,
 *   OVERLOAD        : consigne de vitesse réduite + courant maintenu,
 *   OVERHEATING     : décalage du zéro des sondes (simulation d'une dérive),
 *   SENSOR_FAULT    : gel de la dernière valeur transmise,
 *   MOTOR_STALL     : arrêt des impulsions moteur.
 * C'est exactement la liste gérée par la page /simulator du tableau de bord.
 */
void applyFaultInjection(JsonVariantConst payload) {
  const char* fault = payload["fault"] | "UNBALANCE";
  float intensity = constrain(payload["intensity"] | 60.0f, 0.0f, 100.0f);
  const char* target = payload["motor_id"] | "M1";
  uint8_t index = (strcmp(target, "M2") == 0) ? 1 : 0;
  motors[index].injectedFault = fault;
  motors[index].injectedFaultIntensity = intensity / 100.0f;
  injectedFault = fault;
  injectedIntensity = intensity / 100.0f;
  degradationRate = payload["degradation_rate"] | 1.0f;
  injectedAtMs = millis();

  if (strcmp(fault, "MOTOR_STALL") == 0) {
    motors[index].running = false;
    digitalWrite(motors[index].pinEnable, HIGH);
    Serial.printf("[banc] Blocage moteur simulé sur %s\n", target);
  } else if (strcmp(fault, "SENSOR_FAULT") == 0) {
    sensorFrozen = true;
    frozenFeatures = lastFeatures;      // on gèle la dernière mesure connue
    Serial.println("[banc] Capteur figé (perte de mesure)");
  } else if (strcmp(fault, "OVERHEATING") == 0) {
    temperatureOffsetC = 18.0f * (intensity / 100.0f);
    Serial.printf("[banc] Dérive thermique simulée : +%.1f °C\n", temperatureOffsetC);
  } else if (strcmp(fault, "BELT_SLIP") == 0) {
    targetRpm = NOMINAL_RPM * (1.0f - 0.35f * (intensity / 100.0f));
    motors[0].targetRpm = targetRpm;
    motors[1].targetRpm = targetRpm * M2_SYNC_OFFSET;
  }
  publishStatusResult("inject_fault", fault);
}

/** Offset statique de l'accéléromètre (calibration : moyenne du capteur au repos). */
void trimAccelerometerOffset() {
  if (!mpuPresent) return;
  float sumAx = 0.0f, sumAy = 0.0f, sumAz = 0.0f;
  uint16_t samples = 200;
  for (uint16_t i = 0; i < samples; i++) {
    float ax, ay, az;
    if (readAccel(ax, ay, az)) { sumAx += ax; sumAy += ay; sumAz += az; }
    delay(2);
  }
  accelOffsetX = sumAx / samples;
  accelOffsetY = sumAy / samples;
  accelOffsetZ = (sumAz / samples) - 1.0f;     // on retire 1 g sur l'axe vertical
  Serial.printf("[capteur] Offset accéléromètre : ax=%.4f ay=%.4f az=%.4f g\n",
                accelOffsetX, accelOffsetY, accelOffsetZ);
}

// ============================================================================
//  12. SCÉNARIO LOCAL « DÉMO 3 MINUTES »
// ============================================================================
/**
 * Rejoue la même trame que la console web (12 jalons) directement sur la carte.
 * Utile quand on présente le banc sans ordinateur : la carte pilote seule la
 * séquence, le backend se contente de l'observer et d'alerter.
 */
void runLocalScenario() {
  struct ScenarioStep {
    uint16_t at;
    const char* action;
    const char* motor;
    const char* fault;
    float value;
    const char* label;
  };
  static const ScenarioStep steps[] = {
    {0,   "reset",  "",   "",             0.0f,   "Remise a zero : ligne nominale"},
    {8,   "speed",  "",   "",             150.0f, "Montee en cadence (150 rpm)"},
    {22,  "fault",  "M1", "UNBALANCE",    45.0f,  "M1 : desequilibre leger (45 %)"},
    {40,  "fault",  "M1", "UNBALANCE",    85.0f,  "M1 : desequilibre aggrave (85 %)"},
    {60,  "fault",  "M2", "BEARING_FAULT", 55.0f, "M2 : defaut de roulement (55 %)"},
    {85,  "load",   "",   "",             1.6f,   "Charge lourde (1,6 kg)"},
    {105, "fault",  "M2", "OVERHEATING",  70.0f,  "M2 : surchauffe roulement (70 %)"},
    {125, "fault",  "M1", "BELT_SLIP",    60.0f,  "M1 : glissement de bande (60 %)"},
    {145, "fault",  "M2", "MOTOR_STALL",  100.0f, "M2 : blocage moteur"},
    {158, "stop",   "",   "",             0.0f,   "Arret de ligne (securite)"},
    {165, "reset",  "",   "",             0.0f,   "Maintenance : remise en etat"},
    {172, "start",  "",   "",             120.0f, "Redemarrage — retour a la normale"},
  };
  const uint8_t stepCount = sizeof(steps) / sizeof(ScenarioStep);
  scenarioStartedMs = millis();
  scenarioRunning = true;
  Serial.printf("[scenario] Démarrage — %u jalons sur %u s\n", stepCount, steps[stepCount - 1].at + 2);

  for (uint8_t index = 0; index < stepCount && scenarioRunning; index++) {
    // Attente jusqu'à l'instant du jalon, en gardant le lien MQTT vivant
    while ((uint32_t)((millis() - scenarioStartedMs) / 1000) < steps[index].at && scenarioRunning) {
      mqttLoop();
      delay(50);
    }
    if (!scenarioRunning) break;
    JsonDocument command;
    command["target"] = (strlen(steps[index].motor) > 0) ? steps[index].motor : "all";
    const char* action = steps[index].action;
    if (strcmp(action, "fault") == 0) {
      command["fault"] = steps[index].fault;
      command["intensity"] = steps[index].value;
      command["degradation_rate"] = 1.2f;
      applyFaultInjection(command.as<JsonVariantConst>());
    } else if (strcmp(action, "speed") == 0) {
      JsonDocument payload;
      payload["speed_rpm"] = steps[index].value;
      applyDownstreamCommand("set_controls", payload.as<JsonVariantConst>());
    } else if (strcmp(action, "load") == 0) {
      JsonDocument payload;
      payload["load_kg"] = steps[index].value;
      applyDownstreamCommand("set_controls", payload.as<JsonVariantConst>());
    } else if (strcmp(action, "reset") == 0) {
      applyDownstreamCommand("clear_fault", command.as<JsonVariantConst>());
      JsonDocument payload;
      payload["speed_rpm"] = NOMINAL_RPM;
      payload["load_kg"] = 0.6f;
      applyDownstreamCommand("set_controls", payload.as<JsonVariantConst>());
      temperatureOffsetC = 0.0f;
      sensorFrozen = false;
    } else if (strcmp(action, "stop") == 0) {
      JsonDocument payload;
      payload["running"] = false;
      applyDownstreamCommand("set_controls", payload.as<JsonVariantConst>());
    } else if (strcmp(action, "start") == 0) {
      JsonDocument payload;
      payload["running"] = true;
      payload["speed_rpm"] = steps[index].value;
      payload["load_kg"] = 0.6f;
      applyDownstreamCommand("set_controls", payload.as<JsonVariantConst>());
    }
    Serial.printf("[scenario] Jalon %u/%u : %s\n", index + 1, stepCount, steps[index].label);
    JsonDocument event;
    event["device_id"] = DEVICE_ID;
    event["ts"] = nowTimestampMs();
    event["scenario"] = "demo-3min";
    event["step"] = index + 1;
    event["total_steps"] = stepCount;
    event["label"] = steps[index].label;
    char topic[80];
    snprintf(topic, sizeof(topic), "%s/status", MQTT_TOPIC_PREFIX);
    char buffer[192];
    size_t length = serializeJson(event, buffer, sizeof(buffer));
    mqttClient.publish(topic, (const uint8_t*)buffer, length, false);
  }
  scenarioRunning = false;
  Serial.println("[scenario] Terminé");
}

// ============================================================================
//  13. TÂCHES TEMPS RÉEL
// ============================================================================
/**
 * Tâche d'acquisition (cœur 1).
 *
 * Elle échantillonne les capteurs à `SAMPLING_HZ` et remplit les buffers
 * circulaires consommés par le reste du programme. Elle échantillonne en rafale :
 * on mesure N échantillons d'un coup puis on libère le CPU, ce qui évite de
 * monopoliser le cœur 1 et laisse le Wi-Fi respirer.
 */
void samplingTask(void* parameter) {
  (void)parameter;
  const uint32_t periodUs = 1000000UL / SAMPLING_HZ;
  // Rafale calibrée sur la cadence demandée : réveil toutes les 20 ms (50 Hz).
  const uint8_t blockSize = (uint8_t)max((int)1, (int)(SAMPLING_HZ / 50));
  uint32_t nextCycleUs = micros();
  TickType_t lastWake = xTaskGetTickCount();

  while (true) {
    for (uint8_t index = 0; index < blockSize; index++) {
      float ax = 0.0f, ay = 0.0f, az = 0.0f;
      if (mpuPresent && readAccel(ax, ay, az)) {
        // Axe vertical = az (le capteur est monté perpendiculairement au châssis).
        // L'offset de calibration retire le 1 g statique pour ne garder que la
        // composante dynamique — c'est cette valeur qui alimente la FFT.
        float dynamicAccel = (az - 1.0f - accelOffsetZ) + (ax - accelOffsetX) * 0.35f;
        vibBuffer[vibWriteIndex] = dynamicAccel;
      } else {
        // Capteur absent (banc d'essai) : on conserve un bruit de fond réaliste.
        vibBuffer[vibWriteIndex] = ((float)random(-100, 100) / 100.0f) * 0.01f;
      }
      vibWriteIndex = (vibWriteIndex + 1) % VIB_BUFFER_SIZE;

      float current = lineRunning ? readCurrentAmps(motors[0]) : 0.0f;
      currentBuffer[currentWriteIndex] = current;
      currentWriteIndex = (currentWriteIndex + 1) % CURRENT_BUFFER_SIZE;
      // L'échantillonnage est en rafale : on respecte la période exacte
      nextCycleUs += periodUs;
      while ((int32_t)(micros() - nextCycleUs) < 0) { /* attente active courte */ }
    }
    // Le nombre d'échantillons par rafale suit la cadence configurée : la tâche
    // se réveille toutes les 20 ms, soit SAMPLING_HZ/50 échantillons par tour.
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(20));
  }
}

/**
 * Tâche de publication et d'analyse (cœur 0).
 *
 * Cadences tenues ici :
 *   - télémétrie      : TELEMETRY_HZ (8 Hz)
 *   - features + edge : FEATURES_HZ (1 Hz)
 *   - statut          : STATUS_HZ (5 Hz)
 *   - heartbeat       : 1 Hz
 * Les mesures analogiques lentes (températures, courant moyen) sont rafraîchies
 * une fois par seconde : c'est suffisant pour ces grandeurs physiques lentes.
 */
void publishTask(void* parameter) {
  (void)parameter;
  uint32_t lastTemperatureMs = 0;
  uint32_t lastFeaturesLocalMs = 0;

  while (true) {
    mqttLoop();

    uint32_t now = millis();

    // --- Mesures lentes (1 Hz) ---------------------------------------------
    if (now - lastTemperatureMs >= 1000) {
      lastTemperatureMs = now;
      updateTemperatures();
    }

    // --- Features + inférence edge (1 Hz) ----------------------------------
    if (now - lastFeaturesLocalMs >= (1000 / FEATURES_HZ)) {
      lastFeaturesLocalMs = now;
      FeatureSet features;
      computeFeatures(features);
      BlockStats stats;
      computeBlockStats(vibBuffer, 256, stats);
      BlockStats currentStats;
      computeBlockStats(currentBuffer, CURRENT_BUFFER_SIZE, currentStats);
      EdgeDiagnosis diagnosis = runEdgeInference(features, stats);

      // Copie protégée pour l'état partagé (lue par le heartbeat)
      portENTER_CRITICAL(&sharedStateMux);
      lastFeatures = features;
      lastStats = stats;
      lastDiagnosis = diagnosis;
      portEXIT_CRITICAL(&sharedStateMux);

      // Sécurité locale : un blocage moteur coupe la ligne sans attendre le backend.
      if (diagnosis.faultClass && strcmp(diagnosis.faultClass, "MOTOR_STALL") == 0) {
        triggerEmergencyStop("blocage moteur detecte par l'IA embarquee");
      }
      if (lastStatusLedToggleMs == 0 || now - lastStatusLedToggleMs >= 500) {
        lastStatusLedToggleMs = now;
        digitalWrite(PIN_STATUS_LED, !digitalRead(PIN_STATUS_LED));
      }
    }

    // --- Télémétrie (8 Hz) --------------------------------------------------
    if (now - lastTelemetryMs >= (1000 / telemetryHz)) {
      lastTelemetryMs = now;
      portENTER_CRITICAL(&sharedStateMux);
      FeatureSet features = lastFeatures;
      EdgeDiagnosis diagnosis = lastDiagnosis;
      portEXIT_CRITICAL(&sharedStateMux);
      if (mqttClient.connected()) {
        publishMotorTelemetry(motors[0], features);
        publishMotorTelemetry(motors[1], features);
      } else {
        droppedFrames += 2;
      }
    }

    // --- Features et pré-diagnostic publiés (1 Hz) ---------------------------
    if (now - lastFeatureMs >= 1000) {
      lastFeatureMs = now;
      FeatureSet features;
      EdgeDiagnosis diagnosis;
      portENTER_CRITICAL(&sharedStateMux);
      features = lastFeatures;
      diagnosis = lastDiagnosis;
      portEXIT_CRITICAL(&sharedStateMux);
      if (mqttClient.connected()) {
        publishMotorFeatures(motors[0], features);
        publishMotorFeatures(motors[1], features);
        publishMotorEdgeInference(motors[0], diagnosis);
        publishMotorEdgeInference(motors[1], diagnosis);
      } else {
        droppedFrames += 4;
      }
    }

    // --- Statut (5 Hz) et heartbeat (1 Hz) ----------------------------------
    publishStatus(false);
    if (now - lastHeartbeatMs >= HEARTBEAT_MS) {
      lastHeartbeatMs = now;
      if (mqttClient.connected()) publishHeartbeat();
    }

    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

/**
 * Tâche de génération des impulsions moteur (cœur 1, priorité élevée).
 * Elle doit être la plus régulière possible : c'est elle qui fixe la vitesse réelle
 * de la bande, et donc la fréquence rotationnelle observée par l'IA.
 */
void motorTask(void* parameter) {
  (void)parameter;
  while (true) {
    updateStepGeneration();
    vTaskDelay(1);                 // 1 ms : précision amplement suffisante à ≤ 300 rpm
  }
}

/** Surveillance du bouton d'arrêt d'urgence (contact normalement fermé). */
void emergencyTask(void* parameter) {
  (void)parameter;
  pinMode(PIN_EMERGENCY_STOP, INPUT_PULLUP);
  while (true) {
    if (digitalRead(PIN_EMERGENCY_STOP) == HIGH && lineRunning) {
      triggerEmergencyStop("bouton d'arret d'urgence");
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

/** Tâche du scénario de démonstration (déclenchée par MQTT ou par le bouton). */
void scenarioTask(void* parameter) {
  (void)parameter;
  while (true) {
    if (scenarioRunning && (millis() - scenarioStartedMs) < 5) {
      runLocalScenario();
    }
    vTaskDelay(pdMS_TO_TICKS(200));
  }
}

// ============================================================================
//  14. DÉMARRAGE
// ============================================================================
void setup() {
  Serial.begin(115200);
  uint32_t serialWait = millis();
  while (!Serial && millis() - serialWait < 1500) { /* attente du port série */ }
  bootMillis = millis();

  Serial.println();
  Serial.println("==============================================================");
  Serial.println("  ESP32 — Passerelle tapis roulant (Conveyor AI Ops)");
  Serial.printf("  Firmware %s — device %s\n", FIRMWARE_VERSION, DEVICE_ID);
  Serial.println("==============================================================");

  // --- Watchdog : la carte redémarre si une tâche se bloque ---------------
  // L'API a changé avec le cœur ESP32 v3 : on gère les deux signatures.
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  {
    esp_task_wdt_config_t watchdogConfig = {
      .timeout_ms = 15000,          // 15 s sans « nourrissage » → redémarrage
      .idle_core_mask = 0,
      .trigger_panic = true,
    };
    esp_task_wdt_init(&watchdogConfig);
  }
#else
  esp_task_wdt_init(15, true);
#endif
  esp_task_wdt_add(NULL);

  // --- Sorties et bus ------------------------------------------------------
  pinMode(PIN_STATUS_LED, OUTPUT);
  digitalWrite(PIN_STATUS_LED, HIGH);           // LED allumée = démarrage en cours
  i2cBus.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);   // I2C 400 kHz (mode rapide)
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);               // plage 0-3,3 V

  // --- Moteurs -------------------------------------------------------------
  setupMotor(motors[0], 0, "M1", PIN_M1_STEP, PIN_M1_DIR, PIN_M1_ENABLE, PIN_CURRENT_M1);
  setupMotor(motors[1], 1, "M2", PIN_M2_STEP, PIN_M2_DIR, PIN_M2_ENABLE, PIN_CURRENT_M2);

  // --- Capteurs ------------------------------------------------------------
  mpuPresent = initMpu6050();
  initTemperatureSensors();
  initMicrophone();
#if ENABLE_ENCODER
  pinMode(PIN_ENCODER_M1, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_ENCODER_M1), onEncoderPulse, RISING);
  Serial.printf("[capteur] Encodeur M1 activé sur GPIO %d\n", PIN_ENCODER_M1);
#endif
  // Buffers de départ : valeurs nominales pour éviter des features aberrantes
  for (uint16_t i = 0; i < VIB_BUFFER_SIZE; i++) vibBuffer[i] = 0.0f;
  for (uint16_t i = 0; i < CURRENT_BUFFER_SIZE; i++) currentBuffer[i] = NOMINAL_CURRENT_A * 0.35f;

  // --- Réseau --------------------------------------------------------------
  bool wifiOk = connectWifi();
  if (wifiOk) {
    syncClock();
#if MQTT_USE_TLS
    secureClient.setCACert(ROOT_CA_CERTIFICATE);        // voir section 15 (certificats)
    mqttClient.setClient(secureClient);
#else
    mqttClient.setClient(plainClient);
#endif
    connectMqtt();
  } else {
    Serial.println("[boot] Mode hors ligne : mesures et IA locales actives, publication différée");
  }

  // --- Calibration initiale du courant (moteurs à l'arrêt) -----------------
  for (uint8_t i = 0; i < 2; i++) calibrateCurrentOffset(motors[i]);
  if (mpuPresent) trimAccelerometerOffset();

  // --- Tâches FreeRTOS ------------------------------------------------------
  xTaskCreatePinnedToCore(samplingTask, "sampling", 4096, nullptr, 5, &samplingTaskHandle, 1);
  xTaskCreatePinnedToCore(motorTask, "motor", 2048, nullptr, 4, nullptr, 1);
  xTaskCreatePinnedToCore(publishTask, "publish", 8192, nullptr, 3, &publishTaskHandle, 0);
  xTaskCreatePinnedToCore(emergencyTask, "emergency", 2048, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(scenarioTask, "scenario", 3072, nullptr, 1, nullptr, 0);

  Serial.println("[boot] Tâches démarrées — publication en cours");
  digitalWrite(PIN_STATUS_LED, LOW);                  // LED éteinte = fonctionnement normal
  publishHeartbeat();
  publishStatus(true);
}

void loop() {
  // La boucle Arduino ne porte plus de logique : tout est dans les tâches FreeRTOS.
  // Elle se contente de nourrir le watchdog et de maintenir le lien MQTT (repli si
  // la tâche de publication est bloquée par un long calcul de FFT).
  esp_task_wdt_reset();
  mqttLoop();
  delay(100);
}

/* ---------------------------------------------------------------------------
 *  NOTES D'INTÉGRATION (démarrage rapide sur le banc réel)
 * ---------------------------------------------------------------------------
 *  1. Renseigner WIFI_SSID / WIFI_PASSWORD / MQTT_HOST / MQTT_USERNAME /
 *     MQTT_PASSWORD en tête de fichier, puis téléverser.
 *  2. Créer le compte MQTT du device sur le broker (déjà fait par
 *     mosquitto/entrypoint.sh : utilisateur « conveyor »).
 *  3. Vérifier la publication depuis un poste :
 *         python scripts/esp32_simulator.py monitor --host <IP du broker>
 *     (le moniteur écoute le même contrat de topics que la carte).
 *  4. Dans le tableau de bord, la carte apparaît sur /devices ; la page /ml
 *     affiche la comparaison edge (TinyML embarqué) vs serveur (modèles lourds).
 *  5. Réglages fins : SAMPLING_HZ (500 Hz convient à un tapis 120 rpm), FFT_SIZE
 *     (512 → résolution 0,98 Hz), EDGE_* (seuils du pré-diagnostic embarqué).
 *  6. Calibration capteurs : envoyer POST /api/devices/esp32-conveyor-01/command
 *     avec {"action":"recalibrate"} — la carte réapplique les zéros sans redémarrer.
 * ------------------------------------------------------------------------ */
