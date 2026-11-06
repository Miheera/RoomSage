import React, { useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowLeft, ArrowRight, Check, ChevronRight, CircleHelp, ImagePlus, Sparkles, X } from 'lucide-react';
import './styles.css';

const templates = [
  ['Spa bathroom','https://images.unsplash.com/photo-1620626011761-996317b8d101?auto=format&fit=crop&w=600&q=80'],
  ['Coastal bedroom','https://images.unsplash.com/photo-1616486338812-3dadae4b4ace?auto=format&fit=crop&w=600&q=80'],
  ['Modern kitchen','https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&w=600&q=80'],
  ['Warm dining','https://images.unsplash.com/photo-1600210492486-724fe5c67fb0?auto=format&fit=crop&w=600&q=80'],
];
const rooms = ['Bathroom','Bedroom','Coffee Shop','Dining Room','Dorm','Gaming','Garage','Hall','Home Office','Kitchen','Laundry','Living Room','Nursery','Office','Other','Restaurant','Study','Toilet'];
const styles = [
  ['Keep style',''],['Custom',''],['Modern','https://images.unsplash.com/photo-1616486338812-3dadae4b4ace?auto=format&fit=crop&w=500&q=80'],
  ['Halloween','https://images.unsplash.com/photo-1509557965875-b88c97052f0e?auto=format&fit=crop&w=500&q=80'],['Minimalist','https://images.unsplash.com/photo-1600210492486-724fe5c67fb0?auto=format&fit=crop&w=500&q=80'],['Luxury','https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&w=500&q=80'],
  ['Rustic','https://images.unsplash.com/photo-1600607687920-4e2a09cf159d?auto=format&fit=crop&w=500&q=80'],['Tropical','https://images.unsplash.com/photo-1616486029423-aaa4789e8c9a?auto=format&fit=crop&w=500&q=80'],['Zen','https://images.unsplash.com/photo-1618221195710-dd6b41faaea6?auto=format&fit=crop&w=500&q=80'],
  ['Bohemian','https://images.unsplash.com/photo-1616486029423-aaa4789e8c9a?auto=format&fit=crop&w=500&q=80'],['Industrial','https://images.unsplash.com/photo-1600607687939-ce8a6c25118c?auto=format&fit=crop&w=500&q=80'],['Farmhouse','https://images.unsplash.com/photo-1600607687920-4e2a09cf159d?auto=format&fit=crop&w=500&q=80'],
];
const palettes = [
  ['Original palette',['#17171b','#24242a','#66666f','#e9e8e4']],['Surprise me',['#fc1728','#06c5ee','#8a10fb','#0bdf49']],['Clean neutrals',['#a09e93','#d2ccc3','#e7e4de','#f8f7f4']],
  ['Misty greys',['#d7d7d7','#bdbdbd','#999','#737373']],['Light taupe',['#b6a393','#eee8dd','#d6a384','#96766c','#dfcfc8']],['Cozy neutral',['#b6977c','#e5d4c5','#c99f83','#eee7db']],
  ['Deep ocean',['#002431','#003951','#005876','#007d91','#00a6a5']],['Botanical green',['#ede9df','#858c72','#a4a887','#917c72','#dacbc3']],['Cream & wood',['#884229','#706846','#b86535','#d3b99e','#e6d0b2']],
  ['Earthy warm',['#938277','#a7988f','#c9a184','#795e51','#a0664c']],['Velvet dusk',['#193934','#2c5953','#c26165','#f28c9e','#ffd4d6']],['Calming oasis',['#45413d','#89796f','#ac9a90','#c3b2ac','#dfd0c8']],
];
function App(){
  const [step,setStep]=useState(0),[photo,setPhoto]=useState(''),[room,setRoom]=useState('Living Room'),[style,setStyle]=useState(''),[palette,setPalette]=useState(''),[template,setTemplate]=useState('');
  const [notice,setNotice]=useState(''); const input=useRef(null);
  const selectPhoto=e=>{const f=e.target.files?.[0]; if(f){setPhoto(URL.createObjectURL(f));setTemplate('');}};
  const next=()=>{if(step===0){setStep(1);return} if(step===1&&!photo&&!template){setNotice('Add a room photo or choose a template to continue.');return} if(step===1){setNotice('');setStep(2);return} if(step===2){setNotice('');setStep(3);return} if(step===3){if(!style){setNotice('Choose a design style to continue.');return}setNotice('');setStep(4);return} if(!palette){setNotice('Choose a color palette to generate your design.');return}setNotice('Your design brief is ready. Connect the redesign API to generate a preview.');};
  const back=()=>{setNotice('');setStep(Math.max(0,step-1))};
  const restart=()=>{setStep(0);setPhoto('');setRoom('Living Room');setStyle('');setPalette('');setTemplate('');setNotice('')};
  const selectedPhoto=photo||template;
  return <div className="app-shell">
    {step===0?<main className="welcome"><div className="welcome-photo"/><div className="welcome-content"><div className="brand"><span className="brand-mark">R</span> RoomSage</div><p className="eyebrow">YOUR SPACE, REIMAGINED</p><h1>Design any room<br/>in your style.</h1><p className="welcome-copy">A fresh perspective for the place you call home. Start with a photo and explore what’s possible.</p><div className="dots"><i/><i className="active"/><i/></div><button className="primary" onClick={next}>Get started <ArrowRight size={20}/></button><p className="fine-print">Thoughtful spaces begin with a single idea.</p></div></main>:
    <><header className="topbar"><button className="icon-button back" onClick={back} aria-label="Go back"><ArrowLeft/></button><div className="brand"><span className="brand-mark">R</span> RoomSage</div><button className="icon-button" onClick={restart} aria-label="Close flow"><X/></button></header>
      <main className="flow"><div className="step-meta"><span>YOUR REDESIGN</span><span>0{step} <b>/ 04</b></span></div><div className="progress"><span style={{width:`${step*25}%`}}/></div>
        {step===1&&<><div className="intro"><p className="eyebrow">01 — THE SPACE</p><h1>Start with your room</h1><p>Upload a photo of the space you’d like to reimagine.</p></div><button className={`upload ${selectedPhoto?'has-photo':''}`} onClick={()=>input.current?.click()}>{selectedPhoto?<><img src={selectedPhoto}/><span className="change-photo"><ImagePlus size={16}/> Change photo</span></>:<div className="upload-prompt"><span className="upload-icon"><ImagePlus/></span><h2>Bring your space in</h2><p>Choose a photo from your device</p><span className="secondary-button">Browse photos <ChevronRight size={17}/></span><small>JPG, PNG or WEBP · up to 15 MB</small></div>}</button><input ref={input} className="hidden" type="file" accept="image/*" capture="environment" onChange={selectPhoto}/><div className="section-heading"><div><h2>Or explore a room</h2><p>Try one of our starting points</p></div><button className="text-action" onClick={()=>{setPhoto('');setTemplate('')}}>Clear</button></div><div className="template-row">{templates.map(([name,img])=><button className={`template ${template===img?'selected':''}`} onClick={()=>{setTemplate(img);setPhoto('')}} key={name}><img src={img}/><span>{name}</span></button>)}</div></>}
        {step===2&&<><div className="intro"><p className="eyebrow">02 — ROOM TYPE</p><h1>Which room is it?</h1><p>We’ll tailor the design to the way you use your space.</p></div><div className="room-grid">{rooms.map((r,i)=><button key={r} className={`room-option ${room===r?'selected':''}`} onClick={()=>setRoom(r)}><span className="room-symbol">{['⌂','▱','☕','◉','▤','✧','▣','▯','⌘','◫','◉','⌂','♧','▤','◇','♙','▥','○'][i]}</span>{r}{room===r&&<Check size={16} className="check"/>}</button>)}</div></>}
        {step===3&&<><div className="intro"><p className="eyebrow">03 — THE FEELING</p><h1>Choose your style</h1><p>Pick a direction. You can refine the details later.</p></div><div className="choice-grid style-grid">{styles.map(([name,img])=><button className={`choice-card ${style===name?'selected':''} ${!img?'text-card':''}`} onClick={()=>setStyle(name)} key={name}>{img?<img src={img}/>:<div className="blank-art"><Sparkles size={29}/></div>}<span>{name}{style===name&&<Check size={16}/>}</span></button>)}</div></>}
        {step===4&&<><div className="intro"><p className="eyebrow">04 — THE PALETTE</p><h1>Set the palette</h1><p>Colors shape the mood. Choose a palette to guide your redesign.</p></div><div className="choice-grid palette-grid">{palettes.map(([name,colors])=><button className={`choice-card palette-card ${palette===name?'selected':''}`} onClick={()=>setPalette(name)} key={name}><div className="swatches">{colors.map((c,i)=><i key={i} style={{background:c}}/>)}</div><span>{name}{palette===name&&<Check size={16}/>}</span></button>)}</div></>}
        {notice&&<p className="notice" role="status">{notice}</p>}
      </main><footer className="footer"><button className="primary" onClick={next}>{step===4?'Create my design':'Continue'} <ArrowRight size={19}/></button><p>Step {step} of 4 <span>·</span> Your choices can be changed later</p></footer>
    </>}
  </div>
}
if('serviceWorker' in navigator && import.meta.env.PROD) window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js'));
createRoot(document.getElementById('root')).render(<App/>);
