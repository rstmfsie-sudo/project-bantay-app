import { Prim, roundedRect } from "./Iso3D";

/** Benewake TF-Luna, modelled from the module itself: 35 x 21.25 x 13.5 mm rounded black housing, two lenses
 * stacked along the long side in grey recessed rings, mounting ears with holes at both ends (offset diagonally),
 * white label on one side, connector on the other. Upright: the 35 mm side is vertical. Units: metres. */
export function tfLuna(c: [number, number, number], yaw: number, accent?: string): Prim[] {
  const t = (yaw * Math.PI) / 180, nx = Math.cos(t), ny = Math.sin(t), ux = -ny, uy = nx;
  const at = (n: number, u: number, w: number): [number, number, number] => [c[0] + nx * n + ux * u, c[1] + ny * n + uy * u, c[2] + w];
  const DEPTH = 0.0135, W = 0.02125, H = 0.035, face = DEPTH / 2;
  const prims: Prim[] = [
    // mounting ears (thin tabs behind the front face, diagonally offset like the real part) + their holes
    { type: "prism", c: at(-0.004, 0.004, H / 2 + 0.0025), yaw, depth: 0.0028, outline: roundedRect(0.009, 0.008, 0.0035), color: "#2b2b2b" },
    { type: "prism", c: at(-0.004, -0.004, -H / 2 - 0.0025), yaw, depth: 0.0028, outline: roundedRect(0.009, 0.008, 0.0035), color: "#2b2b2b" },
    { type: "lens", c: at(-0.0025, 0.004, H / 2 + 0.003), r: 0.0016, yaw, color: "#3b3b3b" },
    { type: "lens", c: at(-0.0025, -0.004, -H / 2 - 0.003), r: 0.0016, yaw, color: "#3b3b3b" },
    // housing
    { type: "prism", c, yaw, depth: DEPTH, outline: roundedRect(W, H, 0.0045), color: "#303030" },
    // white label on one side, connector on the other
    { type: "prism", c: at(0, W / 2 + 0.0002, 0), yaw: yaw + 90, depth: 0.0004, outline: roundedRect(0.0095, 0.024, 0.001), color: "#bdbdb8" },
    { type: "prism", c: at(-0.001, -W / 2 - 0.0012, 0), yaw: yaw + 90, depth: 0.0024, outline: roundedRect(0.0055, 0.013, 0.0008), color: "#f1efe6" },
    // two lenses stacked vertically: grey recessed ring + dark glass
    ...[0.0085, -0.0085].flatMap((w): Prim[] => [
      { type: "lens", c: at(face + 0.0001, 0, w), r: 0.0062, yaw, color: "#5a5a5a" },
      { type: "lens", c: at(face + 0.0003, 0, w), r: 0.0044, yaw, color: "#0b0e12" },
    ]),
  ];
  // colour-coded strip across the top (matches the beam colour on the scan)
  if (accent) {
    const [x, y, z] = at(0, 0, H / 2);
    prims.push({ type: "box", x: [x - DEPTH * 0.35, x + DEPTH * 0.35], y: [y - W * 0.3, y + W * 0.3], z: [z, z + 0.0009], yaw, color: accent });
  }
  return prims;
}
