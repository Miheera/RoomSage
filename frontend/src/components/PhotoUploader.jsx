import React, { useRef, useState } from 'react';

export default function PhotoUploader({ projectId, onSceneReady, onSubmit, onFilesSelected, submitLabel = 'Create project and analyze', hideSubmit = false }) {
  const input = useRef(null);
  const [files, setFiles] = useState([]);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState('Drop a room photo here, or browse.');
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const select = async selected => {
    const chosen = [...(selected || [])];
    if(chosen.length < 5 || chosen.length > 15){setMessage('Select between 5 and 15 room photos.');return;}
    if(chosen.some(file => !file.type.startsWith('image/'))){setMessage('Choose JPG, PNG, or WebP images only.');return;}
    setFiles(chosen);
    onFilesSelected?.(chosen);
    const file=chosen[0]; const url = URL.createObjectURL(file);
    const image = new Image(); image.src = url;
    await new Promise(resolve => { image.onload = resolve; image.onerror = resolve; });
    const warnings = [];
    if (image.naturalWidth < 900 || image.naturalHeight < 600) warnings.push('Low resolution may reduce detail.');
    try {
      const canvas = document.createElement('canvas'); canvas.width = 32; canvas.height = 32;
      const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0, 32, 32);
      const pixels = ctx.getImageData(0, 0, 32, 32).data;
      let luminance = 0; for (let i = 0; i < pixels.length; i += 4) luminance += .2126*pixels[i] + .7152*pixels[i+1] + .0722*pixels[i+2];
      if (luminance / (pixels.length / 4) < 55) warnings.push('This photo looks quite dark.');
      const sharpCanvas = document.createElement('canvas'); sharpCanvas.width = 64; sharpCanvas.height = 64;
      const sharpCtx = sharpCanvas.getContext('2d', { willReadFrequently: true });
      sharpCtx.drawImage(image, 0, 0, 64, 64);
      const rgba = sharpCtx.getImageData(0, 0, 64, 64).data;
      const gray = new Float32Array(64 * 64);
      for (let p = 0, i = 0; p < gray.length; p++, i += 4) gray[p] = .2126*rgba[i] + .7152*rgba[i+1] + .0722*rgba[i+2];
      let sum = 0, sumSq = 0, count = 0;
      for (let y = 1; y < 63; y++) for (let x = 1; x < 63; x++) {
        const p = y * 64 + x;
        const lap = gray[p-64] + gray[p+64] + gray[p-1] + gray[p+1] - 4 * gray[p];
        sum += lap; sumSq += lap * lap; count++;
      }
      const variance = sumSq / count - (sum / count) ** 2;
      if (variance < 35) warnings.push('This photo may be blurry; sharper photos help preserve room details.');
    } catch { /* Browser canvas may be unavailable for some image formats. */ }
    setMessage(`${chosen.length} photos selected. ${warnings.join(' ')}`);
  };
  const submit=async()=>{if(!files.length){setMessage('Select 5–15 room photos first.');return;}setBusy(true);try{await onSubmit(files,setProgress);setMessage('Photos uploaded. Analyzing room…');}catch(error){setMessage(error.message||'Upload failed.');}finally{setBusy(false);}};
  return <section className="phase-card">
    <input ref={input} hidden multiple type="file" accept="image/jpeg,image/png,image/webp" onChange={e => select(e.target.files)}/>
    <div className={`drop-zone ${dragging ? 'is-dragging' : ''}`} onDragOver={e => {e.preventDefault(); setDragging(true);}} onDragLeave={() => setDragging(false)} onDrop={e => {e.preventDefault(); setDragging(false); select(e.dataTransfer.files);}}>
      {files[0] && <img className="photo-preview" src={URL.createObjectURL(files[0])} alt="Room photo preview"/>}
      <div className="drop-copy"><strong>{message}</strong><button disabled={busy} className="phase-button" onClick={() => input.current?.click()}>Choose 5–15 photos</button>{!hideSubmit&&files.length>0&&<button disabled={busy} className="phase-button" onClick={submit}>{busy?`Uploading ${progress}%…`:submitLabel}</button>}</div>
    </div>
    {(busy || progress > 0) && <progress className="phase-progress" max="100" value={progress}/>}
  </section>;
}
