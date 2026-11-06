import React, { useState } from 'react';
export default function ChatRefinementDrawer({ open, onClose, previewUrl, messages = [], onSend }) {
  const [text,setText]=useState('');
  if(!open)return null;
  const submit=e=>{e.preventDefault();if(!text.trim())return;onSend?.(text.trim());setText('');};
  return <div className="drawer-backdrop" onMouseDown={e=>e.target===e.currentTarget&&onClose?.()}><aside className="refine-drawer"><header><div><small>DESIGN STUDIO</small><h2>Refine your room</h2></div><button onClick={onClose} aria-label="Close">×</button></header><div className="preview-frame">{previewUrl?<img src={previewUrl} alt="Redesign preview"/>:<div>Your redesign preview will appear here</div>}</div><div className="chat-messages">{messages.map((m,i)=><p className={`chat-bubble ${m.role==='user'?'from-user':''}`} key={i}>{m.content}</p>)}</div><form className="chat-form" onSubmit={submit}><input value={text} onChange={e=>setText(e.target.value)} placeholder="Try ‘sage green walls’…"/><button type="submit">Send</button></form></aside></div>;
}
