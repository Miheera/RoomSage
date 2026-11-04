/**
 * The plan shape the frontend expects from Person B's planner
 * (GET /api/v1/projects/{id}/plan). Also used as demo data.
 *
 * price.kind === "catalog"  -> real price from the product catalog (reliable)
 * price.kind === "estimate" -> LLM range, no catalog match (shown differently)
 */
export const samplePlan = {
  summary: "A warm, uncluttered living room: oat and sage tones, one statement sofa, softer lighting.",
  palette: [
    { name: "Oat milk", hex: "#E4D8C3", role: "Walls" },
    { name: "Sage", hex: "#8FA68F", role: "Accent wall and cushions" },
    { name: "Walnut", hex: "#6B4A34", role: "Wood furniture" },
    { name: "Warm white", hex: "#F7F1E5", role: "Curtains and ceiling" },
  ],
  steps: [
    { id: "s1", title: "Clear the floor and move the sofa off the window wall", detail: "Gives the room a clear walking path from the door to the window." },
    { id: "s2", title: "Repaint the walls in Oat milk", detail: "Two coats. Keep one wall (behind the sofa) in Sage." },
    { id: "s3", title: "Hang warm-white curtains at ceiling height", detail: "Mount the rod 10 cm above the window frame to make the room feel taller." },
    { id: "s4", title: "Place the new sofa and coffee table", detail: "Leave about 45 cm between the sofa and the table." },
    { id: "s5", title: "Add the rug and the floor lamp", detail: "The rug should sit under the front legs of the sofa." },
  ],
  shopping: [
    { id: "i1", name: "Oat linen 3-seater sofa", category: "Seating", qty: 1, price: { kind: "catalog", amount: 24999 }, url: null, catalog_image_key: "catalog/sample_chair.png" },
    { id: "i2", name: "Sheesham coffee table", category: "Tables", qty: 1, price: { kind: "catalog", amount: 6499 }, url: null, catalog_image_key: null },
    { id: "i3", name: "Warm-white floor lamp", category: "Lighting", qty: 1, price: { kind: "catalog", amount: 2199 }, url: null, catalog_image_key: null },
    { id: "i4", name: "Linen-blend curtains (pair)", category: "Soft furnishings", qty: 1, price: { kind: "catalog", amount: 1599 }, url: null, catalog_image_key: null },
    { id: "i5", name: "Jute rug, 5 x 7 ft", category: "Rugs", qty: 1, price: { kind: "estimate", min: 3000, max: 4500 }, url: null, catalog_image_key: null },
    { id: "i6", name: "Wall paint, Oat milk, 4 L", category: "Paint", qty: 2, price: { kind: "estimate", min: 1800, max: 2400 }, url: null, catalog_image_key: null },
  ],
  budget: { cap: 50000, currency: "INR" },
};
