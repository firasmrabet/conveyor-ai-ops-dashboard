/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || 'http://127.0.0.1:8000';

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Le frontend appelle toujours des URL relatives (/api, /ws) : Next fait le proxy
  // vers le backend. Le navigateur n'a donc jamais besoin de connaître l'IP du backend
  // (indispensable pour un accès via proxy/preview ou depuis un autre poste).
  async rewrites() {
    return [
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
      { source: '/openapi.json', destination: `${backend}/openapi.json` },
      // WebSocket temps réel : proxifié par le serveur Next (même origine pour le
      // navigateur → aucun problème de CORS ni d'IP backend côté client).
      { source: '/ws/:path*', destination: `${backend}/ws/:path*` },
    ];
  },
};
export default nextConfig;
