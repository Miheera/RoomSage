import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { MAX_PHOTOS, MIN_PHOTOS, ROOM_TYPES } from "../lib/config";
import { makeCover, processPhoto } from "../lib/image";
import { projectsStore } from "../lib/storage";
import CameraCapture from "../components/capture/CameraCapture";
import CoverageDial from "../components/capture/CoverageDial";
import PhotoGrid from "../components/capture/PhotoGrid";
import Icon from "../components/Icon";

const PHASE_LABEL = {
  creating: "Creating your project",
  uploading: "Uploading photos",
  starting: "Starting the 3D build",
};

export default function CapturePage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [roomType, setRoomType] = useState("living_room");
  const [photos, setPhotos] = useState([]);
  const [camera, setCamera] = useState(false);
  const [notice, setNotice] = useState(null);
  const [phase, setPhase] = useState(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [error, setError] = useState(null);
  const photosRef = useRef(photos);
  photosRef.current = photos;
  const resume = useRef({ projectId: null, uploaded: false }); // so a retry never duplicates work

  useEffect(() => () => photosRef.current.forEach((p) => URL.revokeObjectURL(p.url)), []);

  const addFiles = async (fileList) => {
    const room = MAX_PHOTOS - photosRef.current.length;
    const files = [...fileList].slice(0, Math.max(0, room));
    if (fileList.length > room) setNotice(`Only ${MAX_PHOTOS} photos are used. Extra photos were skipped.`);
    const added = [];
    for (const f of files) {
      try {
        added.push(await processPhoto(f));
      } catch (e) {
        setNotice(e.message);
      }
    }
    setPhotos((p) => [...p, ...added]);
    return added;
  };

  const capture = async (file) => {
    const [photo] = await addFiles([file]);
    return photo;
  };

  const remove = (id) =>
    setPhotos((list) => {
      const gone = list.find((p) => p.id === id);
      if (gone) URL.revokeObjectURL(gone.url);
      return list.filter((p) => p.id !== id);
    });

  const canSubmit = photos.length >= MIN_PHOTOS && name.trim().length > 0 && !phase;

  const submit = async () => {
    setError(null);
    try {
      const r = resume.current;
      if (!r.projectId) {
        setPhase("creating");
        const proj = await api.createProject({ name: name.trim(), roomType });
        r.projectId = proj.id;
      }
      if (!r.uploaded) {
        setPhase("uploading");
        setUploadPct(0);
        await api.uploadPhotos(r.projectId, photos.map((p) => p.file), setUploadPct);
        r.uploaded = true;
      }
      setPhase("starting");
      const job = await api.startReconstruction(r.projectId);
      const cover = await makeCover(photos[0].file).catch(() => null);
      projectsStore.add({
        id: r.projectId,
        name: name.trim(),
        roomType,
        createdAt: new Date().toISOString(),
        photoCount: photos.length,
        cover,
        beforeJobId: job.jobId,
        edits: [],
        activeEditId: null,
        checkedSteps: {},
        budgetCap: null,
      });
      navigate(`/project/${r.projectId}`);
    } catch (e) {
      setError(e.message);
      setPhase(null);
    }
  };

  return (
    <main className="page page--narrow">
      <h1>Scan a room</h1>
      <p className="lede">
        Walk around the room and take {MIN_PHOTOS} to {MAX_PHOTOS} photos. RoomSage builds a 3D model of your actual walls, windows and furniture from them.
      </p>

      <section className="panel">
        <div className="fields">
          <label>
            Room name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Living room, flat 4B" maxLength={160} />
          </label>
          <label>
            Room type
            <select value={roomType} onChange={(e) => setRoomType(e.target.value)}>
              {ROOM_TYPES.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="panel capture">
        <CoverageDial count={photos.length} />
        <div className="capture__actions">
          <button className="btn btn--primary" onClick={() => setCamera(true)} disabled={photos.length >= MAX_PHOTOS || !!phase}>
            <Icon name="camera" /> Open camera
          </button>
          <label className={`btn btn--ghost ${photos.length >= MAX_PHOTOS || phase ? "is-disabled" : ""}`}>
            <Icon name="plus" /> Add from gallery
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              hidden
              disabled={photos.length >= MAX_PHOTOS || !!phase}
              onChange={(e) => {
                addFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
        </div>
        <ul className="tips">
          <li>Stand in a corner and turn slowly, then step sideways and repeat. Don't just pivot on the spot.</li>
          <li>Keep each photo overlapping the last by about 60%.</li>
          <li>Include furniture and textured surfaces in every shot. Bare white walls are hard to match.</li>
          <li>Turn the lights on and avoid shooting straight into a window.</li>
        </ul>
      </section>

      {notice && (
        <p className="notice" role="status">
          {notice} <button onClick={() => setNotice(null)}>Dismiss</button>
        </p>
      )}

      <PhotoGrid photos={photos} onRemove={remove} />

      {error && (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      )}

      <div className="submit">
        {phase ? (
          <div className="job" role="status">
            <h3>{PHASE_LABEL[phase]}</h3>
            <div className="bar">
              <div className="bar__fill" style={{ width: `${phase === "uploading" ? Math.max(4, uploadPct * 100) : 100}%` }} />
            </div>
          </div>
        ) : (
          <button className="btn btn--primary btn--lg" disabled={!canSubmit} onClick={submit}>
            Build my 3D room
          </button>
        )}
        {!phase && photos.length < MIN_PHOTOS && (
          <p className="muted">Add {MIN_PHOTOS - photos.length} more photo{MIN_PHOTOS - photos.length === 1 ? "" : "s"} to continue.</p>
        )}
        {!phase && photos.length >= MIN_PHOTOS && !name.trim() && <p className="muted">Give the room a name to continue.</p>}
      </div>

      {camera && <CameraCapture count={photos.length} onCapture={capture} onUndo={remove} onClose={() => setCamera(false)} />}
    </main>
  );
}
