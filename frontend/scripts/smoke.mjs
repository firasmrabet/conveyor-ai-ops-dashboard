#!/usr/bin/env node
/**
 * Test de fumée navigateur — Conveyor AI Ops
 * ==========================================
 *
 * Ouvre les 8 pages de l'interface dans un vrai Chromium (Playwright), vérifie
 * qu'elles s'hydratent sans erreur console, que les composants clés sont présents
 * et que le WebSocket temps réel pousse bien des données — puis enregistre une
 * capture d'écran de chaque page dans `docs/screenshots/` (utilisée par le README
 * et la soutenance).
 *
 * Prérequis :
 *     npm install                     # playwright est une dépendance de développement
 *     npx playwright install chromium # navigateur (une seule fois)
 *     # backend sur :8000 et frontend sur :3000 doivent tourner :
 *     #   backend  : cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000
 *     #   frontend : cd frontend && npm run dev   (ou npm run build && npm run start)
 *
 * Utilisation :
 *     npm run smoke                          # valeurs par défaut
 *     BASE_URL=http://localhost:3000 npm run smoke
 *     npm run smoke -- --no-screens          # sans captures
 */
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { chromium } from 'playwright';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SHOTS = path.resolve(HERE, '../../docs/screenshots');
const BASE_URL = process.env.BASE_URL ?? 'http://127.0.0.1:3000';
const WITH_SCREENS = !process.argv.includes('--no-screens');
const VIEWPORT = { width: 1680, height: 950 };

/** Pages à parcourir : route, nom de capture et repères attendus dans le DOM. */
const PAGES = [
  { route: '/', name: '01-salle-de-controle', media: ['canvas'], checks: ['Salle de contrôle', 'Disponibilité', 'Jumeau numérique 3D', 'Santé de la ligne'] },
  {
    route: '/motors/M1', name: '02-moteur-M1', media: ['canvas'],
    checks: ['Moteur M1', 'Flux temps réel'],
    // La fiche moteur est organisée en onglets : on parcourt chacun d'eux pour
    // vérifier que les panneaux se rendent réellement (et pas seulement la coquille).
    steps: [
      { click: 'Prédiction IA', expect: ['Durée de vie restante', 'Probabilités de défaut', 'Courbe de dégradation'] },
      { click: 'Analyse fréquentielle', expect: ['Spectre', 'BPFO'] },
      { click: 'Explicabilité', expect: ['Explicabilité'] },
      { click: 'Historique', expect: ['Historique'] },
      { click: 'Vue temps réel', expect: ['Flux temps réel'] },
    ],
  },
  { route: '/analytics', name: '03-analyse-historique', media: ['charts'], checks: ['Analyse historique', 'Indicateurs de fiabilité', 'MTBF'] },
  { route: '/alerts', name: '04-alertes-maintenance', media: [], checks: ['Alertes', 'Historique des alertes', 'Règles'] },
  { route: '/ml', name: '05-mlops', media: ['charts'], checks: ['MLOps', 'Matrice de confusion', 'Courbes ROC'] },
  { route: '/devices', name: '06-passerelles', media: [], checks: ['Passerelles', 'Diagnostic réseau'] },
  { route: '/simulator', name: '07-simulateur', media: [], checks: ['Console de démonstration', 'Physique interne des moteurs', 'Démo 3 minutes'] },
  { route: '/settings', name: '08-parametres', media: [], checks: ['Paramètres', 'Seuils d\'alerte', 'Notifications'] },
];

/** Erreurs ignorées : bruit réseau propre au bac à sable, pas des défauts d'application. */
const IGNORED_ERRORS = [/favicon/i, /net::ERR_/i, /Failed to load resource/i, /websocket.*closed/i];

const results = [];

function record(route, ok, detail) {
  results.push({ route, ok, detail });
  console.log(`  ${ok ? '✔' : '✘'} ${route.padEnd(14)} ${detail}`);
}

const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1, locale: 'fr-FR' });

if (WITH_SCREENS) {
  await mkdir(SHOTS, { recursive: true });
}

try {
  for (const pageDefinition of PAGES) {
    const page = await context.newPage();
    const errors = [];
    page.on('console', (message) => {
      if (message.type() === 'error' && !IGNORED_ERRORS.some((pattern) => pattern.test(message.text()))) {
        errors.push(message.text().slice(0, 160));
      }
    });
    page.on('pageerror', (error) => errors.push(`exception : ${error.message.slice(0, 160)}`));

    const url = `${BASE_URL}${pageDefinition.route}`;
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45_000 });
    const status = response?.status() ?? 0;

    // Laisse le temps aux requêtes TanStack Query et au WebSocket de peupler l'écran
    // (les squelettes de chargement sont volontairement visibles au premier rendu).
    await page.waitForTimeout(3500);

    const missing = [];
    for (const needle of pageDefinition.checks) {
      const found = await page.locator(`text=${needle}`).first().isVisible().catch(() => false);
      if (!found) missing.push(needle);
    }

    // Preuve que le temps réel fonctionne : Connectée au WebSocket, l'interface
    // affiche le badge LIVE dans l'en-tête (il reste OFFLINE si l'hydratation a échoué).
    const liveProbe = await page
      .waitForSelector('header >> text=LIVE', { timeout: 25_000 })
      .then(() => true)
      .catch(() => false);

    // Preuve que les données sont réellement rendues : graphiques Recharts et, sur la
    // salle de contrôle, le canevas WebGL du jumeau numérique.
    const media = [];
    if (pageDefinition.media?.includes('charts')) {
      media.push(['graphiques', await page.locator('svg.recharts-surface').count()]);
    }
    if (pageDefinition.media?.includes('canvas')) {
      media.push(['canevas 3D', await page.locator('canvas').count()]);
    }

    if (WITH_SCREENS) {
      await page.screenshot({
        path: path.join(SHOTS, `${pageDefinition.name}.png`),
        fullPage: true,
      });
    }

    const problems = [];
    if (status !== 200) problems.push(`HTTP ${status}`);

    // Parcours d'onglets / sous-vues éventuels
    for (const step of pageDefinition.steps ?? []) {
      const trigger = page.locator(`button:has-text("${step.click}"), [role="tab"]:has-text("${step.click}")`).first();
      if (!(await trigger.isVisible().catch(() => false))) {
        problems.push(`onglet « ${step.click} » introuvable`);
        continue;
      }
      await trigger.click();
      await page.waitForTimeout(2000);
      const stepMissing = [];
      for (const needle of step.expect) {
        const found = await page.locator(`text=${needle}`).first().isVisible().catch(() => false);
        if (!found) stepMissing.push(needle);
      }
      if (stepMissing.length) problems.push(`onglet « ${step.click} » : ${stepMissing.join(', ')} non rendus`);
      media.push([`onglet ${step.click}`, await page.locator('canvas, svg').count() > 0 ? 1 : 0]);
    }
    if (missing.length) problems.push(`repères absents : ${missing.join(', ')}`);
    if (errors.length) problems.push(`erreurs console : ${errors.join(' | ')}`);
    if (!liveProbe) problems.push('badge LIVE absent (WebSocket non connecté ?)');
    const mediaSummary = media.map(([label, count]) => `${label} : ${count}`);
    for (const [label, count] of media) {
      if (count === 0) problems.push(`${label} non rendus`);
    }

    record(pageDefinition.route, problems.length === 0,
      problems.length ? problems.join(' · ') : `OK (${mediaSummary.join(' · ') || 'page statique'})`);
    await page.close();
  }
} finally {
  await browser.close();
}

const failures = results.filter((result) => !result.ok).length;
console.log(`\n${results.length - failures}/${results.length} pages conformes`);
if (WITH_SCREENS) console.log(`captures : ${SHOTS}`);
process.exit(failures === 0 ? 0 : 1);
