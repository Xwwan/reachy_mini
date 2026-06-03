import * as THREE from 'three';

// Cyber-Mini branding: swap white head parts to cyan, dark base parts to magenta.
// Walks robot.group, finds MeshStandardMaterials whose current color matches a
// "white" or "dark base" tint, replaces with #00FFFF or #FF00FF respectively.

const CYAN    = new THREE.Color(0x00FFFF);
const MAGENTA = new THREE.Color(0xFF00FF);
const BLACK   = new THREE.Color(0x000000);

const WHITE_HSL_L_MIN  = 0.85;
const DARK_HSL_L_MAX   = 0.15;

const _hsl = { h: 0, s: 0, l: 0 };

export function applyCyberBranding(robot) {
    if (!robot || !robot.group) return;
    robot.group.traverse((child) => {
        if (!child.isMesh || !child.material) return;
        const mats = Array.isArray(child.material) ? child.material : [child.material];
        for (const m of mats) {
            if (!m || !m.isMeshStandardMaterial || !m.color) continue;
            m.color.getHSL(_hsl);
            if (_hsl.l >= WHITE_HSL_L_MIN) {
                m.color.copy(CYAN);
                if (m.emissive) m.emissive.copy(CYAN);
                m.emissiveIntensity = Math.max(m.emissiveIntensity ?? 0, 0.30);
                m.needsUpdate = true;
            } else if (_hsl.l <= DARK_HSL_L_MAX) {
                const name = (child.name || '').toLowerCase();
                if (name.includes('antenna_holder') || name.includes('body_foot')) {
                    m.color.copy(MAGENTA);
                    if (m.emissive) m.emissive.copy(MAGENTA);
                    m.emissiveIntensity = Math.max(m.emissiveIntensity ?? 0, 0.20);
                } else {
                    m.color.copy(BLACK);
                    if (m.emissive) m.emissive.copy(BLACK);
                    m.emissiveIntensity = 0;
                }
                m.needsUpdate = true;
            }
        }
    });
}
