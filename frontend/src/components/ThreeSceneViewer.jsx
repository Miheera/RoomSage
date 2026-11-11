import React, { Suspense, useMemo } from 'react';
import { Canvas } from '@react-three/fiber';
import { Environment, OrbitControls, useGLTF } from '@react-three/drei';

function Model({ url }) { const gltf = useGLTF(url); return <primitive object={gltf.scene} position={[0, -1, 0]} />; }
function RoomFallback({ color = '#d8d1c5' }) { return <group><mesh position={[0,-1,0]} rotation={[-Math.PI/2,0,0]}><planeGeometry args={[8,8]}/><meshStandardMaterial color="#857d72"/></mesh><mesh position={[0,1, -2]}><boxGeometry args={[6,4,.12]}/><meshStandardMaterial color={color}/></mesh><mesh position={[-2.9,1,0]} rotation={[0,Math.PI/2,0]}><planeGeometry args={[4,4]}/><meshStandardMaterial color="#c8c1b6" side={2}/></mesh><mesh position={[2.9,1,0]} rotation={[0,-Math.PI/2,0]}><planeGeometry args={[4,4]}/><meshStandardMaterial color="#c8c1b6" side={2}/></mesh></group>; }
export default function ThreeSceneViewer({ modelUrl, scene, sceneData }) {
  scene = scene || sceneData;
  const wallColor = useMemo(() => scene?.primary_wall_color || '#d8d1c5', [scene]);
  return <div className="three-viewer"><Canvas camera={{position:[5,3,7],fov:45}} shadows><color attach="background" args={['#151514']}/><ambientLight intensity={.8}/><directionalLight position={[4,7,5]} intensity={2} castShadow/><Suspense fallback={null}>{modelUrl ? <Model url={modelUrl}/> : <RoomFallback color={wallColor}/>}<Environment preset={scene?.lighting_condition?.toLowerCase().includes('natural') ? 'sunset' : 'apartment'}/></Suspense><OrbitControls makeDefault/></Canvas><span className="viewer-hint">Drag to orbit · scroll to zoom</span></div>;
}
