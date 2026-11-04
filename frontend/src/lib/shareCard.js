const W = 1080;
const H = 1350;

const load = (src) =>
  new Promise((res, rej) => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = rej;
    img.src = src;
  });

function round(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function cover(ctx, img, x, y, w, h, r) {
  ctx.save();
  round(ctx, x, y, w, h, r);
  ctx.clip();
  const s = Math.max(w / img.width, h / img.height);
  ctx.drawImage(img, x + (w - img.width * s) / 2, y + (h - img.height * s) / 2, img.width * s, img.height * s);
  ctx.restore();
}

function pill(ctx, text, x, y) {
  ctx.font = '600 26px "Figtree Variable", system-ui, sans-serif';
  const w = ctx.measureText(text).width + 36;
  ctx.fillStyle = "rgba(23,48,43,0.88)";
  round(ctx, x, y, w, 46, 23);
  ctx.fill();
  ctx.fillStyle = "#F2F4F0";
  ctx.textBaseline = "middle";
  ctx.fillText(text, x + 18, y + 24);
}

/** 1080x1350 (4:5) before/after card for Instagram / WhatsApp. Returns a PNG Blob. */
export async function renderShareCard({ before, after, title, palette = [], costLine }) {
  await Promise.all([
    document.fonts.load('700 56px "Bricolage Grotesque Variable"'),
    document.fonts.load('600 26px "Figtree Variable"'),
  ]).catch(() => {});
  const [b, a] = await Promise.all([load(before), load(after)]);

  const c = document.createElement("canvas");
  c.width = W;
  c.height = H;
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#F2F4F0";
  ctx.fillRect(0, 0, W, H);

  ctx.fillStyle = "#17302B";
  ctx.textBaseline = "alphabetic";
  ctx.font = '700 58px "Bricolage Grotesque Variable", system-ui, sans-serif';
  ctx.fillText(title.length > 28 ? `${title.slice(0, 27)}…` : title, 48, 96);

  const imgW = W - 96;
  const imgH = 520;
  cover(ctx, b, 48, 130, imgW, imgH, 28);
  pill(ctx, "Before", 72, 154);
  cover(ctx, a, 48, 130 + imgH + 24, imgW, imgH, 28);
  pill(ctx, "After", 72, 154 + imgH + 24);

  const y = 130 + imgH * 2 + 24 + 48;
  palette.slice(0, 5).forEach((p, i) => {
    ctx.fillStyle = p.hex;
    round(ctx, 48 + i * 64, y, 52, 52, 14);
    ctx.fill();
    ctx.strokeStyle = "rgba(23,48,43,0.18)";
    ctx.lineWidth = 2;
    ctx.stroke();
  });
  ctx.fillStyle = "#17302B";
  ctx.font = '600 30px "Figtree Variable", system-ui, sans-serif';
  ctx.textBaseline = "middle";
  if (costLine) ctx.fillText(costLine, 48 + Math.min(palette.length, 5) * 64 + 24, y + 26);

  ctx.textAlign = "right";
  ctx.font = '700 34px "Bricolage Grotesque Variable", system-ui, sans-serif';
  ctx.fillText("RoomSage", W - 48, H - 52);

  return new Promise((res) => c.toBlob(res, "image/png"));
}
