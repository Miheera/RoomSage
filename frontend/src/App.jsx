import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, ImagePlus, LogOut, Plus, Upload } from 'lucide-react';
import { api, fetchAuthenticatedImage, storeToken } from './services/api';

const palettes = ['Clean Neutrals', 'Warm Earth', 'Misty Greys', 'Botanical Green', 'Deep Ocean', 'Cream & Wood'];
const CORE_ROOM_TYPES = [
  { type: 'living_room', name: 'Living Room' },
  { type: 'bedroom', name: 'Bedroom' },
  { type: 'dining_room', name: 'Dining Room' },
];
const inr = value => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(value || 0);
const routeNow = () => window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
const go = path => { window.location.hash = `/${path.replace(/^\//, '')}`; };

export default function App() {
  const [route, setRoute] = useState(routeNow);
  const [booting, setBooting] = useState(true);
  const [account, setAccount] = useState(null);
  const [authError, setAuthError] = useState('');
  const [authBusy, setAuthBusy] = useState(false);
  const [authMode, setAuthMode] = useState('register');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [project, setProject] = useState(null);
  const [projectLoading, setProjectLoading] = useState(false);
  const [projectError, setProjectError] = useState('');
  const [roomTypes, setRoomTypes] = useState([]);
  const [roomTypesError, setRoomTypesError] = useState('');
  const [styles, setStyles] = useState([]);
  const [stylesLoading, setStylesLoading] = useState(false);
  const [stylesError, setStylesError] = useState('');
  const [roomName, setRoomName] = useState('');
  const [roomType, setRoomType] = useState('');
  const [room, setRoom] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [budgetRange, setBudgetRange] = useState(null);
  const [budget, setBudget] = useState(0);
  const [style, setStyle] = useState('');
  const [palette, setPalette] = useState(palettes[0]);
  const [stepBusy, setStepBusy] = useState(false);
  const [stepError, setStepError] = useState('');
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoError, setPhotoError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [design, setDesign] = useState(null);
  const [designImageUrl, setDesignImageUrl] = useState(null);
  const [designError, setDesignError] = useState('');
  const [alternatives, setAlternatives] = useState([]);
  const [alternativesItem, setAlternativesItem] = useState(null);
  const [resultTab, setResultTab] = useState('Items');
  const [itemBusy, setItemBusy] = useState(false);

  useEffect(() => {
    const onHash = () => setRoute(routeNow());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    let alive = true;
    const token = localStorage.getItem('roomsage-access-token');
    if (!token) { go('login'); setBooting(false); return () => { alive = false; }; }
    api.me().then(async me => {
      if (!alive) return;
      setAccount(me);
      if (routeNow()[0] === 'login') {
        const projects = await api.getProjects();
        if (projects.length) go(`projects/${projects[0].id}`);
        else { const created = await api.createProject('My Flat'); go(`projects/${created.id}`); }
      }
    }).catch(() => { storeToken(null); go('login'); })
      .finally(() => { if (alive) setBooting(false); });
    return () => { alive = false; };
  }, []);

  const projectId = route[0] === 'projects' ? route[1] : null;
  const isNewRoom = route[2] === 'rooms' && route[3] === 'new';
  const isSetup = route[2] === 'rooms' && (isNewRoom || route[4] === 'setup');
  const setupRoomId = isNewRoom ? null : (isSetup ? route[3] : null);
  const setupStep = isNewRoom ? 1 : (isSetup ? Number(route[5] || 2) : 0);
  const isWorkspace = route[2] === 'rooms' && Boolean(route[3]) && !isSetup;
  const workspaceRoomId = isWorkspace ? route[3] : null;
  const designId = isWorkspace && route[4] === 'designs' ? route[5] : null;

  useEffect(() => {
    if (!account || !projectId) return;
    let alive = true;
    setProjectLoading(true); setProjectError('');
    api.getProject(projectId).then(data => { if (alive) setProject(data); })
      .catch(error => { if (alive) setProjectError(error.message); })
      .finally(() => { if (alive) setProjectLoading(false); });
    return () => { alive = false; };
  }, [account, projectId, refresh]);

  useEffect(() => {
    if (!account || projectId || route[0] === 'login') return;
    let alive = true;
    api.getProjects().then(async projects => {
      if (!alive) return;
      if (projects.length) go(`projects/${projects[0].id}`);
      else {
        const created = await api.createProject('My Flat');
        if (alive) go(`projects/${created.id}`);
      }
    }).catch(error => { if (alive) setProjectError(error.message); });
    return () => { alive = false; };
  }, [account, projectId, route]);

  useEffect(() => {
    if (!account || !(isNewRoom || isSetup)) return;
    let alive = true;
    setRoomTypesError('');
    api.getRoomTypes().then(types => {
      if (!alive) return;
      setRoomTypes(types);
      setRoomTypesError('');
      if (!roomType && types.length) setRoomType(types[0].type);
    }).catch(error => {
      if (alive) {
        setRoomTypes([]);
        setRoomTypesError(error.message);
      }
    });
    if (setupRoomId) {
      api.getRoom(setupRoomId).then(value => { if (alive) { setRoom(value); setRoomType(value.type); } }).catch(error => { if (alive) setStepError(error.message); });
      api.getCoverage(setupRoomId).then(value => { if (alive) setCoverage(value); }).catch(error => { if (alive) setStepError(error.message); });
    }
    return () => { alive = false; };
  }, [account, isNewRoom, isSetup, setupRoomId]);

  useEffect(() => {
    if (!account || !roomType) return;
    let alive = true;
    setStylesLoading(true); setStylesError('');
    api.getRoomStyles(roomType).then(values => {
      if (!alive) return;
      setStyles(values);
      setStyle(current => values.some(value => value.style === current) ? current : (values[0]?.style || ''));
    }).catch(error => { if (alive) setStylesError(error.message); })
      .finally(() => { if (alive) setStylesLoading(false); });
    return () => { alive = false; };
  }, [account, roomType]);

  useEffect(() => {
    if (!account || !setupRoomId || setupStep !== 5 || !style) return;
    let alive = true;
    setBudgetRange(null);
    api.getBudgetRange(setupRoomId, style).then(value => {
      if (alive) {
        setBudgetRange(value);
        setBudget(current => current
          ? Math.min(value.max, Math.max(value.min, current))
          : Math.round((value.min + value.max) / 2));
      }
    }).catch(error => { if (alive) setStepError(error.message); });
    return () => { alive = false; };
  }, [account, setupRoomId, setupStep, style]);

  useEffect(() => {
    if (!account || !workspaceRoomId) return;
    let alive = true;
    let timer;
    const load = async () => {
      try {
        const [roomData, coverageData] = await Promise.all([api.getRoom(workspaceRoomId), api.getCoverage(workspaceRoomId)]);
        if (alive) { setRoom(roomData); setCoverage(coverageData); }
        if (alive && roomData.designs?.some(item => item.status === 'QUEUED' || item.status === 'PROCESSING')) timer = setTimeout(load, 2500);
      } catch (error) { if (alive) setProjectError(error.message); }
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [account, workspaceRoomId, refresh]);

  useEffect(() => {
    if (!account || !designId) { setDesign(null); setDesignImageUrl(null); setDesignError(''); return; }
    let alive = true;
    let timer;
    const load = async () => {
      try {
        const value = await api.getDesign(designId);
        if (!alive) return;
        setDesign(value); setDesignError('');
        if (value.image_url) {
          try { const blobUrl = await fetchAuthenticatedImage(value.image_url); if (alive) setDesignImageUrl(old => { if (old) URL.revokeObjectURL(old); return blobUrl; }); }
          catch (error) { if (alive) setDesignImageUrl(null); }
        } else if (alive) setDesignImageUrl(null);
        if (value.status === 'queued' || value.status === 'running') timer = setTimeout(load, 2000);
      } catch (error) { if (alive) setDesignError(error.message); }
    };
    load();
    return () => { alive = false; clearTimeout(timer); };
  }, [account, designId]);

  const finishAuth = useCallback(async result => {
    storeToken(result.access_token);
    setAccount({ user_id: result.user_id, email: result.email });
    const projects = await api.getProjects();
    if (projects.length) go(`projects/${projects[0].id}`);
    else {
      const created = await api.createProject('My Flat');
      go(`projects/${created.id}`);
    }
  }, []);

  const submitAuth = async event => {
    event.preventDefault(); setAuthBusy(true); setAuthError('');
    try { await finishAuth(await api[authMode](email, password)); }
    catch (error) { setAuthError(error.message || 'Could not sign in.'); }
    finally { setAuthBusy(false); }
  };

  const startNewRoom = () => { setRoom(null); setCoverage({ photo_count: 0, '2d_ready': false, '3d_ready': false, photos_needed: 3 }); setRoomName(''); setRoomType(roomTypes[0]?.type || ''); setStepError(''); setBudgetRange(null); setBudget(0); setStyle(''); setPalette(palettes[0]); go(`projects/${projectId}/rooms/new/1`); };

  const createRoom = async () => {
    setStepBusy(true); setStepError('');
    try {
      const created = await api.createRoom(projectId, roomType, roomName || roomTypes.find(type => type.type === roomType)?.name || 'New room');
      setRoom(created); setCoverage({ photo_count: 0, '2d_ready': false, '3d_ready': false, photos_needed: 3 });
      go(`projects/${projectId}/rooms/${created.id}/setup/2`);
    } catch (error) { setStepError(error.message); }
    finally { setStepBusy(false); }
  };

  const uploadFiles = async (files, labels = []) => {
    if (!setupRoomId && !workspaceRoomId) return;
    const roomId = setupRoomId || workspaceRoomId;
    if (!files.length) return;
    setPhotoBusy(true); setPhotoError('');
    try {
      const result = await api.uploadRoomPhotos(roomId, files, labels);
      setCoverage(result.coverage); setRefresh(value => value + 1);
    } catch (error) { setPhotoError(error.message); }
    finally { setPhotoBusy(false); }
  };

  const saveBudgetAndFinish = async () => {
    if (!setupRoomId) return;
    setStepBusy(true); setStepError('');
    try {
      const updated = await api.setBudget(setupRoomId, Number(budget), style || undefined);
      setRoom(updated);
      const currentCoverage = coverage || await api.getCoverage(setupRoomId);
      setCoverage(currentCoverage);
      if (currentCoverage['2d_ready']) {
        const created = await api.createDesign(setupRoomId, style, palette);
        go(`projects/${projectId}/rooms/${setupRoomId}/designs/${created.design_id}`);
      } else {
        go(`projects/${projectId}/rooms/${setupRoomId}`);
      }
    } catch (error) { setStepError(error.message); }
    finally { setStepBusy(false); }
  };

  const editRoomBudget = item => {
    setRoom(item); setRoomName(item.name); setRoomType(item.type); setBudget(Number(item.budget || 0));
    setStyle(item.designs?.[0]?.style || '');
    setBudgetRange(null); setStepError('');
    go(`projects/${projectId}/rooms/${item.id}/setup/5`);
  };

  const openAlternatives = async item => {
    if (!design) return;
    setItemBusy(true); setDesignError('');
    try { setAlternatives(await api.getAlternatives(design.id, item.id)); setAlternativesItem(item); }
    catch (error) { setDesignError(error.message); }
    finally { setItemBusy(false); }
  };

  const swapItem = async catalogItemId => {
    if (!design || !alternativesItem) return;
    setItemBusy(true); setDesignError('');
    try { setDesign(await api.swapDesignItem(design.id, alternativesItem.id, catalogItemId)); setAlternativesItem(null); }
    catch (error) { setDesignError(error.message); }
    finally { setItemBusy(false); }
  };

  const removeItem = async item => {
    if (!design) return;
    setItemBusy(true); setDesignError('');
    try { setDesign(await api.removeDesignItem(design.id, item.id)); }
    catch (error) { setDesignError(error.message); }
    finally { setItemBusy(false); }
  };

  const rerenderDesign = async () => {
    if (!design) return;
    setItemBusy(true); setDesignError('');
    try {
      const child = await api.renderDesign(design.id);
      go(`projects/${projectId}/rooms/${workspaceRoomId}/designs/${child.design_id}`);
    } catch (error) { setDesignError(error.message); }
    finally { setItemBusy(false); }
  };

  if (booting) return <main className="rs-app state-screen"><div className="loading-indicator"/><p>Checking your account…</p></main>;

  if (route[0] === 'login' || !account) return <main className="rs-app auth-screen"><section className="auth-card"><p className="eyebrow">ROOMSAGE</p><h1>{authMode === 'register' ? 'Create your account' : 'Welcome back'}</h1><p className="muted">Your rooms and designs stay private to your account.</p><form onSubmit={submitAuth}><label>Email<input type="email" required autoComplete="email" value={email} onChange={event => setEmail(event.target.value)}/></label><label>Password<input type="password" required minLength={8} autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} value={password} onChange={event => setPassword(event.target.value)}/></label>{authError && <p className="error-banner">{authError}</p>}<button className="primary-button" disabled={authBusy}>{authBusy ? 'Please wait…' : authMode === 'register' ? 'Create account' : 'Sign in'}</button></form><button className="auth-switch" onClick={() => { setAuthMode(authMode === 'register' ? 'login' : 'register'); setAuthError(''); }}>{authMode === 'register' ? 'Already have an account? Sign in' : 'New to RoomSage? Create an account'}</button></section></main>;

  if (route[0] === 'projects' && !projectId) return <main className="rs-app state-screen"><p className="error-banner">Choose a project to continue.</p></main>;

  if (isNewRoom || isSetup) return <main className="rs-app"><header className="flow-header"><button className="icon-button" onClick={() => isNewRoom ? go(`projects/${projectId}`) : setupStep > 2 ? go(`projects/${projectId}/rooms/${setupRoomId}/setup/${setupStep - 1}`) : go(`projects/${projectId}`)}><ArrowLeft/></button><b>{project?.name || 'My Flat'} · {room?.name || roomName || 'New room'}</b></header><section className="setup-content">
    {setupStep === 1 && <><h1>Set up your room</h1><p className="muted">Choose a room type supported by the RoomSage catalog.</p><label className="field-label">Room type<select value={roomType} onChange={event => { setRoomType(event.target.value); if (!roomName) setRoomName(roomTypes.find(type => type.type === event.target.value)?.name || ''); }}><option value="" disabled>Select room type</option>{roomTypes.map(type => <option value={type.type} key={type.type}>{type.name}</option>)}</select></label><label className="field-label">Room name<input value={roomName} onChange={event => setRoomName(event.target.value)} placeholder="e.g. Main bedroom"/></label>{roomTypesError && <p className="error-banner">{roomTypesError}</p>}</>}
    {setupStep === 2 && <><h1>Add photos of your {room?.name || 'room'}</h1><p className="muted">One photo is enough for a 2D redesign. Three photos provide coverage for a 3D room preview.</p><div className="photo-layout"><div className="photo-slots">{['Corner A', 'Opposite corner', 'Window wall', 'Optional extra'].map((label, index) => <label className="photo-slot" key={label}><ImagePlus size={22}/><span>{index < (coverage?.photo_count || 0) ? label : `+ ${label}`}</span><input type="file" accept="image/jpeg,image/png,image/webp" disabled={photoBusy} onChange={event => { const file = event.target.files?.[0]; if (file) uploadFiles([file], [label]); event.target.value = ''; }}/>{index < (coverage?.photo_count || 0) && <span className="photo-count-mark"><Check size={14}/> Uploaded</span>}</label>)}</div><aside className="coverage-card"><b>Coverage</b><p>2D redesign <span className={coverage?.['2d_ready'] ? 'good' : ''}>{coverage?.['2d_ready'] ? 'Ready' : 'Needs 1 photo'}</span></p><p>3D model <span>{coverage?.['3d_ready'] ? 'Ready' : `${coverage?.photo_count || 0} of 3 photos`}</span></p><div className="meter"><i style={{ width: `${Math.min(100, ((coverage?.photo_count || 0) / 3) * 100)}%` }}/></div><p className="muted">Stand in a corner and shoot toward the opposite one, keeping floor and ceiling in frame.</p><button className="quiet-button template-photo" onClick={() => document.getElementById('template-photo-input')?.click()}>Use a template photo</button><input id="template-photo-input" className="visually-hidden" type="file" accept="image/*" onChange={event => { const file = event.target.files?.[0]; if (file) uploadFiles([file], ['Template']); }}/></aside></div>{photoError && <p className="error-banner">{photoError}</p>}</>}
    {setupStep === 3 && <><h1>Choose a style</h1><p className="muted">Only styles with a complete essential catalog set are shown for this room.</p>{stylesLoading ? <p className="muted">Checking catalog coverage…</p> : stylesError ? <p className="error-banner">{stylesError}</p> : styles.length ? <div className="choice-grid">{styles.map(value => <button className={style === value.style ? 'selected' : ''} onClick={() => setStyle(value.style)} key={value.style}>{value.name}<small>{Object.entries(value.items_per_role).map(([role, count]) => `${role}: ${count}`).join(' · ')} · minimum budget {inr(value.minimum_budget)}</small></button>)}</div> : <p className="error-banner">No catalog style can cover every essential role for this room yet.</p>}</>}
    {setupStep === 4 && <><h1>Pick a palette</h1><p className="muted">Choose the colors that feel right.</p><div className="choice-grid">{palettes.map((value, index) => <button className={palette === value ? 'selected' : ''} onClick={() => setPalette(value)} key={value}><span className={`swatch swatch-${index}`}/>{value}</button>)}</div></>}
    {setupStep === 5 && <><h1>Set a budget for {room?.name || 'your room'}</h1>{budgetRange ? <><p className="muted">Feasible {style} catalog spend: {inr(budgetRange.min)} – {inr(budgetRange.max)}</p><label className="field-label">Room budget <b>{inr(budget)}</b><input className="budget-slider" type="range" min={budgetRange.min} max={budgetRange.max} step={Math.max(1, Math.round((budgetRange.max - budgetRange.min) / 100))} value={Math.min(budgetRange.max, Math.max(budgetRange.min, budget || budgetRange.min))} onChange={event => setBudget(Number(event.target.value))}/><input type="number" min={budgetRange.min} max={budgetRange.max} value={budget || budgetRange.min} onChange={event => setBudget(Number(event.target.value))}/></label><div className="budget-presets">{[budgetRange.min, Math.round((budgetRange.min + budgetRange.max) / 2), budgetRange.max].map(value => <button className={Number(budget) === value ? 'selected' : ''} onClick={() => setBudget(value)} key={value}>{inr(value)}</button>)}</div></> : <p className="muted">Loading catalog budget range…</p>}</>}
    {stepError && <p className="error-banner">{stepError}</p>}
  </section><footer className="flow-footer">{setupStep === 2 ? <button className="quiet-button" onClick={() => go(`projects/${projectId}/rooms/${setupRoomId}/setup/3`)}>Skip photos for now</button> : <button className="quiet-button" onClick={() => setupRoomId ? go(`projects/${projectId}/rooms/${setupRoomId}`) : go(`projects/${projectId}`)}>Cancel</button>}<button className="primary-button" disabled={stepBusy || photoBusy || roomTypes.length === 0 || (setupStep === 3 && (!style || stylesLoading || styles.length === 0))} onClick={() => setupStep === 1 ? createRoom() : setupStep < 5 ? go(`projects/${projectId}/rooms/${setupRoomId}/setup/${setupStep + 1}`) : saveBudgetAndFinish()}>{stepBusy ? 'Saving…' : setupStep === 5 ? (coverage?.['2d_ready'] ? 'Generate design' : 'Save room') : 'Continue'}<ArrowRight size={17}/></button></footer></main>;

  if (designId) return <main className="rs-app">
    <header className="result-header">
      <button className="icon-button" onClick={() => go(`projects/${projectId}/rooms/${workspaceRoomId}`)}><ArrowLeft/></button>
      <div><b>{design?.style || 'Room design'} · {design?.palette || ''}</b><span className="muted">{design?.status || 'loading'}</span></div>
      <button className="quiet-button" disabled={itemBusy} onClick={rerenderDesign}>Regenerate</button>
    </header>
    {designError && <p className="error-banner">{designError}</p>}
    {!design ? <section className="state-screen"><div className="loading-indicator"/><p>Loading design…</p></section> : <>
      {design.image_stale && <aside className="stale-banner"><span>Items changed. The current image is out of date.</span><button className="primary-button" disabled={itemBusy} onClick={rerenderDesign}>Re-render with these changes</button></aside>}
      {design.render_metadata?.render_quality === 'composite_fallback' && <aside className="stale-banner"><span>Remote image rendering was unavailable. Showing the catalog composite instead.</span></aside>}
      {design.is_mock_preview && <aside className="stale-banner"><span>Mock preview: this is the original room photo, not an AI redesign. Item visibility and hotspots are not verified.</span></aside>}
      {design.status === 'queued' || design.status === 'running' ? <section className="generation-card"><div className="loading-indicator"/><div><b>{design.status === 'queued' ? 'Design queued' : 'Rendering catalog items'}</b><p className="muted">{design.progress}% · You can leave this page and return.</p><div className="meter"><i style={{width:`${design.progress}%`}}/></div></div></section> : null}
      {design.status === 'failed' && <section className="error-banner"><b>Design failed.</b> {design.error || 'The renderer could not finish this catalog-constrained design.'}<button className="quiet-button" disabled={itemBusy} onClick={rerenderDesign}>Retry as a new version</button></section>}
      <section className="result-layout catalog-result-layout">
        <div className="result-visual catalog-result-visual">
          <div className="catalog-result-canvas">
            {designImageUrl ? <img className="catalog-result-image" src={designImageUrl} alt={`${design.style} ${design.palette} room redesign`}/> : <div className="design-placeholder">{design.status === 'failed' ? 'No verified image was published.' : 'The room redesign will appear here.'}</div>}
            {design.items.filter(item => item.kind === 'object' && item.visible && item.x !== null && item.y !== null).map((item,index) => <button key={item.id} className="hotspot catalog-hotspot" style={{left:`${item.x*100}%`,top:`${item.y*100}%`}} title={item.title} onClick={() => document.getElementById(`design-item-${item.id}`)?.scrollIntoView({behavior:'smooth',block:'center'})}>{index+1}</button>)}
          </div>
        </div>
        <aside className="result-panel panel-card catalog-result-panel">
          <nav className="result-tabs">{['Items', 'Edit', 'Versions'].map(tab => <button key={tab} className={resultTab === tab ? 'active' : ''} onClick={() => setResultTab(tab)}>{tab === 'Items' ? `Items ${design.items.length}` : tab}</button>)}</nav>
          <div className="catalog-item-list">{design.items.map(item => <article className="catalog-item-row" id={`design-item-${item.id}`} key={item.id}>
            <span className="item-num">{item.kind === 'surface' ? (item.display_label === 'Wall' ? 'W' : 'F') : item.visible ? design.items.filter(entry => entry.kind === 'object' && entry.visible && entry.x !== null && entry.y !== null).findIndex(entry => entry.id === item.id)+1 || '•' : '•'}</span>
            <div><b>{item.display_label || item.title}</b><small>{inr(item.price)} × {item.qty} · {inr(item.line_total)}{item.kind === 'object' && !item.visible ? ' · Not visible in image' : ''}</small>{item.kind === 'object' && !item.visible && item.visibility_reason && <small>{item.visibility_reason}</small>}{item.kind === 'surface' && <small>{item.role === 'paint' ? 'Wall color' : 'Floor material'} · catalog item</small>}</div>
            <button className="quiet-button" disabled={itemBusy || design.status === 'queued' || design.status === 'running'} onClick={() => openAlternatives(item)}>Swap</button>
            <button className="quiet-button" aria-label={`Remove ${item.display_label || item.title}`} disabled={itemBusy || design.status === 'queued' || design.status === 'running'} onClick={() => removeItem(item)}>Remove</button>
          </article>)}</div>
          <section className="budget-summary"><div><b>Budget</b><span>{inr(design.total_cost)} of {inr(design.budget)}</span></div><div className="meter"><i style={{width:`${design.budget ? Math.min(100,design.total_cost/design.budget*100) : 0}%`}}/></div><small>{inr(design.total_cost)} catalog total · every line is a catalog row</small></section>
        </aside>
      </section>
      {alternativesItem && <div className="modal-backdrop" onClick={() => setAlternativesItem(null)}><section className="dialog panel-card alternatives-dialog" onClick={event => event.stopPropagation()}><button className="close-button" onClick={() => setAlternativesItem(null)}>Close</button><h2>Swap {alternativesItem.display_label || alternativesItem.title}</h2>{alternatives.length ? alternatives.map(item => <button className="alternative-row" key={item.catalog_item_id} disabled={itemBusy} onClick={() => swapItem(item.catalog_item_id)}><span><b>{item.title}</b><small>{inr(item.price)} · {item.price_delta < 0 ? 'saves ' : 'adds '}{inr(Math.abs(item.price_delta))}</small></span>{item.image_url && <img src={item.image_url} alt=""/>}</button>) : <p className="muted">No other selectable catalog items share this role and fit.</p>}</section></div>}
    </>}
  </main>;

  if (isWorkspace) return <main className="rs-app"><header className="result-header"><button className="icon-button" onClick={() => go(`projects/${projectId}`)}><ArrowLeft/></button><div><b>{room?.name || 'Room'}</b><span className="muted"> · {roomTypes.find(type => type.type === room?.type)?.name || room?.type}</span></div><button className="quiet-button" onClick={() => { storeToken(null); setAccount(null); go('login'); }}><LogOut size={16}/> Sign out</button></header><nav className="workspace-tabs"><button onClick={() => document.getElementById("workspace-designs")?.scrollIntoView({behavior:"smooth"})}>Designs</button><button className="active">3D</button><button onClick={() => document.getElementById("workspace-designs")?.scrollIntoView({behavior:"smooth"})}>Shopping list</button><button onClick={() => go(`projects/${projectId}/rooms/${workspaceRoomId}/setup/2`)}>Photos</button></nav><section className="workspace-summary"><div><span className="eyebrow">ROOM WORKSPACE</span><h1>{room?.name || 'Loading room…'}</h1><p className="muted">{room?.budget ? `Budget ${inr(room.budget)}` : 'Budget not set'} · {coverage?.photo_count || 0} photos</p></div><button className="primary-button" onClick={() => go(`projects/${projectId}/rooms/${workspaceRoomId}/setup/2`)}>Add photos</button></section>{coverage && <section className="coverage-card workspace-coverage"><b>Photo coverage</b><p>2D redesign <span className={coverage['2d_ready'] ? 'good' : ''}>{coverage['2d_ready'] ? 'Ready' : 'Add 1 photo'}</span></p><p>3D room preview <span>{coverage['3d_ready'] ? 'Photo coverage ready' : `Needs ${coverage.photos_needed} more`}</span></p><div className="meter"><i style={{ width: `${Math.min(100, coverage.photo_count / 3 * 100)}%` }}/></div></section>}{photoError && <p className="error-banner">{photoError}</p>}{projectError && <p className="error-banner">{projectError}</p>}<section className="workspace-designs" id="workspace-designs"><h2>Designs</h2>{!room ? <p className="muted">Loading room…</p> : room.designs?.length ? room.designs.map(value => <button className="workspace-design-card" key={value.id} onClick={() => go(`projects/${projectId}/rooms/${workspaceRoomId}/designs/${value.id}`)}>{value.image_url ? <img src={value.image_url} alt={`${value.style} design`}/> : <div className="design-placeholder"><span className={value.status === 'QUEUED' || value.status === 'PROCESSING' ? 'loading-indicator' : ''}/><b>{value.status === 'FAILED' ? 'Design failed' : `Design ${value.status.toLowerCase()}`}</b></div>}<span><b>{value.style} · {value.palette}</b><small className="muted">{value.status}</small></span></button>) : <div className="empty-state"><ImagePlus/><h3>No designs yet</h3><p className="muted">Add a room photo and complete the room setup to generate a design.</p><button className="quiet-button" onClick={() => go(`projects/${projectId}/rooms/${workspaceRoomId}/setup/2`)}>Add a photo</button></div>}</section></main>;

  if (!projectId) return <main className="rs-app state-screen"><div className="loading-indicator"/><p>Opening your project…</p></main>;
  if (projectLoading) return <main className="rs-app state-screen"><div className="loading-indicator"/><p>Loading rooms…</p></main>;
  if (projectError) return <main className="rs-app state-screen"><p className="error-banner">{projectError}</p><button className="quiet-button" onClick={() => setRefresh(value => value + 1)}>Retry</button></main>;
  if (!project) return <main className="rs-app state-screen"><p className="muted">No project selected.</p></main>;

  return <main className="rs-app"><header className="rs-header"><div><p className="eyebrow">YOUR PROJECT</p><h1>{project.name}</h1><span className="muted">{project.rooms.length} rooms · {account.email}</span></div><button className="quiet-button" onClick={() => { storeToken(null); setAccount(null); go('login'); }}><LogOut size={16}/> Sign out</button><button className="primary-button" onClick={startNewRoom}><Plus size={17}/> Add room</button></header><section className="project-budget"><div><b>Total room budgets</b><span>{inr(project.total_budget)}</span></div></section>{project.rooms.length === 0 ? <section className="empty-state project-empty"><ImagePlus/><h2>No rooms yet</h2><p className="muted">Add a room to get started. You can create a room and add photos later.</p><button className="primary-button" onClick={startNewRoom}><Plus size={16}/> Add your first room</button></section> : <section className="room-grid-cards">{project.rooms.map(item => <article className="room-card" key={item.id}><button className="room-image room-empty-image" onClick={() => go(`projects/${projectId}/rooms/${item.id}`)}>{item.designs?.find(design => design.image_url)?.image_url ? <img src={item.designs.find(design => design.image_url).image_url} alt={`${item.name} design`}/> : <span><ImagePlus/> {item.photo_count ? 'Photos ready' : 'No photos yet'}</span>}<b>{item.has_2d ? 'Latest design' : 'Room workspace'}</b></button><div className="room-card-copy"><h2>{item.name}</h2><div className="chips"><span>{item.photo_count} photo{item.photo_count === 1 ? '' : 's'}</span>{item.has_2d && <span className="good">2D ready</span>}{item.can_3d ? <span className="good">3D photo coverage</span> : item.photo_count > 0 ? <span>Add {Math.max(0, 3 - item.photo_count)} angles</span> : null}</div><p>{item.budget ? inr(item.budget) : 'Budget not set'}</p><div className="room-card-actions"><button className="quiet-button" onClick={() => editRoomBudget(item)}>{item.budget ? 'Modify budget' : 'Add budget'}</button><button className="quiet-button" onClick={() => go(`projects/${projectId}/rooms/${item.id}`)}>Open room</button></div></div></article>)}<button className="add-room-card" onClick={startNewRoom}><span>＋</span><b>Add another room</b><small>Room, photos, style, palette, budget</small></button></section>}</main>;
}


