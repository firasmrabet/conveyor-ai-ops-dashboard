'use client';

/**
 * Jumeau numérique 3D du convoyeur (react-three-fiber + drei).
 *
 * Représente : le châssis, la bande qui défile à la vitesse réelle, les deux moteurs
 * pas à pas (cylindres colorés selon l'indice de santé), les poulies, la charge
 * transportée (boîtes qui avancent de A vers B), un halo pulsant en cas d'anomalie
 * et des hotspots cliquables pour ouvrir la fiche du moteur.
 *
 * Les animations sont purement locales (useFrame) : aucun re-render React par trame.
 */
import { Html, OrbitControls, RoundedBox, Text } from '@react-three/drei';
import { Canvas, useFrame } from '@react-three/fiber';
import * as React from 'react';
import * as THREE from 'three';
import { healthColor } from '@/lib/format';
import type { MotorId } from '@/lib/types';

interface MotorVisual {
  id: MotorId;
  health: number;
  faultClass: string;
  anomaly: number;
  rpm: number;
  running: boolean;
  label: string;
}

interface ConveyorSceneProps {
  motors: MotorVisual[];
  beltMmPerSecond: number;
  running: boolean;
  loadKg: number;
  onSelect?: (id: MotorId) => void;
  height?: number;
}

// ---------------------------------------------------------------- Bande
function Belt({ speed, running, length = 7.2, width = 1.9 }: { speed: number; running: boolean; length?: number; width?: number }) {
  const texture = React.useMemo(() => {
    // Texture procédurale : rayures qui défilent (pas d'asset externe requis)
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    if (ctx) {
      ctx.fillStyle = '#171B26';
      ctx.fillRect(0, 0, 128, 128);
      ctx.strokeStyle = 'rgba(255,255,255,0.10)';
      ctx.lineWidth = 3;
      for (let i = 0; i < 128; i += 16) {
        ctx.beginPath();
        ctx.moveTo(i, 0);
        ctx.lineTo(i - 22, 128);
        ctx.stroke();
      }
      ctx.strokeStyle = 'rgba(0,229,255,0.10)';
      ctx.lineWidth = 1;
      ctx.strokeRect(0, 1, 128, 126);
    }
    const created = new THREE.CanvasTexture(canvas);
    created.wrapS = THREE.RepeatWrapping;
    created.wrapT = THREE.RepeatWrapping;
    created.repeat.set(6, 1);
    return created;
  }, []);

  useFrame((_, delta) => {
    if (!running) return;
    // Vitesse réelle : mm/s → décalage de texture
    texture.offset.x -= (speed / 1000) * delta * 0.45;
  });

  return (
    <group position={[0, 0.42, 0]}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[length, 0.08, width]} />
        <meshStandardMaterial map={texture} color="#2A3040" roughness={0.85} metalness={0.15} />
      </mesh>
      {/* Châssis latéral */}
      {[-1, 1].map((side) => (
        <mesh key={side} position={[0, -0.11, side * (width / 2 + 0.05)]} castShadow>
          <boxGeometry args={[length + 0.1, 0.14, 0.08]} />
          <meshStandardMaterial color="#3A4152" metalness={0.7} roughness={0.35} />
        </mesh>
      ))}
      {/* Rouleaux d'extrémité */}
      {[-1, 1].map((end) => (
        <mesh key={end} position={[(length / 2 - 0.15) * end, -0.06, 0]} rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.17, 0.17, width + 0.24, 24]} />
          <meshStandardMaterial color="#8A93A6" metalness={0.85} roughness={0.25} />
        </mesh>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------- Moteur
function Motor({
  motor, position, onSelect, side,
}: { motor: MotorVisual; position: [number, number, number]; onSelect?: (id: MotorId) => void; side: 'LEFT' | 'RIGHT' }) {
  const group = React.useRef<THREE.Group>(null);
  const shaft = React.useRef<THREE.Mesh>(null);
  const halo = React.useRef<THREE.Mesh>(null);
  const [hovered, setHovered] = React.useState(false);
  const color = healthColor(motor.health);
  const isCritical = motor.anomaly > 0.6 || motor.health < 45;

  useFrame((state, delta) => {
    if (shaft.current && motor.running) shaft.current.rotation.y -= (motor.rpm / 60) * delta * 2;
    if (halo.current) {
      const pulse = isCritical ? 0.6 + Math.sin(state.clock.elapsedTime * 6) * 0.35 : 0.16;
      (halo.current.material as THREE.MeshBasicMaterial).opacity = pulse;
      halo.current.scale.setScalar(isCritical ? 1.06 + Math.sin(state.clock.elapsedTime * 6) * 0.04 : 1.02);
    }
  });

  return (
    <group position={position}>
      {/* Support */}
      <mesh position={[0, -0.34, 0]} receiveShadow castShadow>
        <boxGeometry args={[0.5, 0.36, 0.62]} />
        <meshStandardMaterial color="#333A4B" metalness={0.6} roughness={0.5} />
      </mesh>

      <group
        ref={group}
        onPointerOver={(event) => { event.stopPropagation(); setHovered(true); }}
        onPointerOut={() => setHovered(false)}
        onClick={(event) => { event.stopPropagation(); onSelect?.(motor.id); }}
      >
        {/* Corps du moteur (cylindre NEMA17) */}
        <mesh rotation={[Math.PI / 2, 0, 0]} castShadow>
          <cylinderGeometry args={[0.17, 0.17, 0.52, 32]} />
          <meshStandardMaterial
            color={color}
            emissive={color}
            emissiveIntensity={isCritical ? 0.55 : 0.16}
            metalness={0.75}
            roughness={0.28}
          />
        </mesh>
        {/* Flasque moteur */}
        <mesh position={[0, 0, 0.27]} rotation={[Math.PI / 2, 0, 0]}>
          <boxGeometry args={[0.34, 0.34, 0.03]} />
          <meshStandardMaterial color="#2B3242" metalness={0.8} roughness={0.3} />
        </mesh>
        {/* Arbre */}
        <mesh ref={shaft} position={[0, 0, 0.36]} rotation={[Math.PI / 2, 0, 0]}>
          <cylinderGeometry args={[0.035, 0.035, 0.16, 16]} />
          <meshStandardMaterial color="#C6CEDA" metalness={0.95} roughness={0.15} />
        </mesh>
        {/* Poulie */}
        <mesh position={[0, 0, 0.46]} rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.11, 0.028, 12, 28]} />
          <meshStandardMaterial color="#9AA3B4" metalness={0.9} roughness={0.2} />
        </mesh>
      </group>

      {/* Halo d'alerte */}
      <mesh ref={halo} rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.24, 0.012, 8, 40]} />
        <meshBasicMaterial color={isCritical ? '#FF4D4F' : color} transparent opacity={0.2} />
      </mesh>

      {/* Étiquette 3D */}
      <Text position={[0, 0.42, 0]} fontSize={0.13} color="#E8ECF5" anchorX="center" font={undefined}>
        {motor.id}
      </Text>

      {/* Hotspot cliquable */}
      <Html position={[0, 0.62, 0]} center distanceFactor={7} zIndexRange={[10, 0]}>
        <button
          type="button"
          onClick={() => onSelect?.(motor.id)}
          className="pointer-events-auto flex items-center gap-1.5 whitespace-nowrap rounded-full border border-white/15 bg-[rgba(10,14,26,0.86)] px-2.5 py-1 text-[10.5px] font-medium backdrop-blur transition hover:border-[rgba(0,229,255,0.5)]"
          style={{ color }}
          aria-label={`Ouvrir la fiche du moteur ${motor.id} (${side})`}
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
          {motor.label}
          <span className="num opacity-80">{motor.health.toFixed(0)}</span>
        </button>
      </Html>
    </group>
  );
}

// ---------------------------------------------------------------- Charge
function Parcels({ count, speed, running, span = 6.4 }: { count: number; speed: number; running: boolean; span?: number }) {
  const group = React.useRef<THREE.Group>(null);
  const progress = React.useRef<number[]>([]);

  if (progress.current.length !== count) {
    progress.current = Array.from({ length: count }, (_, index) => index / count);
  }

  useFrame((_, delta) => {
    if (!group.current) return;
    const children = group.current.children;
    const advance = ((speed / 1000) * delta) / span * 3.4;
    progress.current = progress.current.map((value, index) => {
      const next = running ? (value + advance) % 1 : value;
      const child = children[index];
      if (child) {
        child.position.x = -span / 2 + next * span;
        child.visible = true;
      }
      return next;
    });
  });

  return (
    <group ref={group}>
      {Array.from({ length: count }).map((_, index) => (
        <group key={index} position={[0, 0.62, 0]}>
          <RoundedBox args={[0.34, 0.24, 0.34]} radius={0.03} smoothness={4} castShadow>
            <meshStandardMaterial color={index % 2 === 0 ? '#B98A4A' : '#8A6B3A'} roughness={0.75} metalness={0.05} />
          </RoundedBox>
        </group>
      ))}
    </group>
  );
}

// ---------------------------------------------------------------- Scène complète
export function ConveyorScene({ motors, beltMmPerSecond, running, loadKg, onSelect, height = 320 }: ConveyorSceneProps) {
  const parcels = Math.max(2, Math.min(6, Math.round(2 + loadKg * 2.5)));

  return (
    <div style={{ height }} className="relative overflow-hidden rounded-2xl border border-white/[0.06] bg-[radial-gradient(circle_at_50%_-20%,rgba(0,229,255,0.10),transparent_55%)]">
      <Canvas
        shadows
        camera={{ position: [5.2, 3.6, 6.4], fov: 42 }}
        dpr={[1, 2]}
        gl={{ antialias: true, powerPreference: 'high-performance' }}
        aria-label="Jumeau numérique 3D du convoyeur à bande"
      >
        <color attach="background" args={['#0A0E1A']} />
        <fog attach="fog" args={['#0A0E1A', 12, 24]} />
        <ambientLight intensity={0.55} />
        <hemisphereLight intensity={0.35} groundColor="#0A0E1A" />
        <directionalLight position={[6, 8, 4]} intensity={1.15} castShadow shadow-mapSize={[1024, 1024]} />
        <pointLight position={[-4, 3, -3]} intensity={0.5} color="#7C5CFF" />
        <pointLight position={[4, 2.4, 3]} intensity={0.45} color="#00E5FF" />

        {/* Sol technique */}
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.63, 0]} receiveShadow>
          <planeGeometry args={[26, 26]} />
          <meshStandardMaterial color="#0C1120" roughness={0.95} metalness={0.05} />
        </mesh>
        <gridHelper args={[26, 26, '#1B2436', '#141B2A']} position={[0, -0.62, 0]} />

        <Belt speed={beltMmPerSecond} running={running} />
        {motors.map((motor) => (
          <Motor
            key={motor.id}
            motor={motor}
            side={motor.id === 'M1' ? 'LEFT' : 'RIGHT'}
            position={[motor.id === 'M1' ? -2.55 : 2.55, 0.1, 1.42]}
            onSelect={onSelect}
          />
        ))}
        <Parcels count={parcels} speed={beltMmPerSecond} running={running} />

        <OrbitControls
          enablePan
          enableZoom
          minDistance={4}
          maxDistance={16}
          minPolarAngle={0.25}
          maxPolarAngle={Math.PI / 2.15}
          target={[0, 0.3, 0]}
          makeDefault
        />
      </Canvas>

      {/* Légende */}
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-center gap-3 rounded-xl border border-white/[0.07] bg-[rgba(10,14,26,0.72)] px-3 py-1.5 text-[10.5px] backdrop-blur">
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-[#00E396]" />Santé ≥ 80</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-[#FFB020]" />60–79</span>
        <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-full bg-[#FF4D4F]" />&lt; 60 / anomalie</span>
        <span className="flex items-center gap-1.5 text-[rgb(var(--muted))]">Molette : zoom · glisser : orbite</span>
      </div>
      <div className="pointer-events-none absolute right-3 top-3 rounded-xl border border-white/[0.07] bg-[rgba(10,14,26,0.72)] px-3 py-1.5 text-[10.5px] backdrop-blur">
        <span className="label">Bande</span>{' '}
        <span className="num text-[#00E5FF]">{beltMmPerSecond.toFixed(0)} mm/s</span>
        <span className="mx-2 text-[rgb(var(--muted))]">·</span>
        <span className="label">Charge</span>{' '}
        <span className="num text-[#B9A6FF]">{loadKg.toFixed(2)} kg</span>
      </div>
    </div>
  );
}
