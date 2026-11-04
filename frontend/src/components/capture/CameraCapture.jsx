import { useEffect, useRef, useState } from "react";
import Icon from "../Icon";
import { MAX_PHOTOS } from "../../lib/config";

/**
 * Full-screen camera with a shutter. Each shot goes through `onCapture(file)` (which
 * resizes and scores it) and the result comes back so we can warn about blur/darkness
 * immediately, while the person is still standing in the room.
 */
export default function CameraCapture({ count, onCapture, onUndo, onClose }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [error, setError] = useState(null);
  const [ready, setReady] = useState(false);
  const [last, setLast] = useState(null);
  const [flash, setFlash] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError(
          window.isSecureContext
            ? "This browser has no camera access. Add photos from your gallery instead."
            : "Phones only allow the camera on secure (https) pages. Run `npm run dev:https`, or add photos from your gallery."
        );
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 }, height: { ideal: 1440 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setReady(true);
      } catch (e) {
        setError(
          e.name === "NotAllowedError"
            ? "Camera permission was denied. Allow it in your browser settings, or add photos from your gallery."
            : "Couldn't start the camera. Add photos from your gallery instead."
        );
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const shoot = async () => {
    const v = videoRef.current;
    if (!v || busy || count >= MAX_PHOTOS) return;
    setBusy(true);
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d").drawImage(v, 0, 0);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.95));
    setFlash(true);
    setTimeout(() => setFlash(false), 120);
    navigator.vibrate?.(20);
    try {
      const photo = await onCapture(new File([blob], "capture.jpg", { type: "image/jpeg" }));
      setLast(photo);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cam" role="dialog" aria-label="Camera">
      <video ref={videoRef} className="cam__video" playsInline muted />
      {flash && <div className="cam__flash" />}

      <div className="cam__top">
        <p>
          {count}/{MAX_PHOTOS} photos
        </p>
        <button className="btn btn--solid-light" onClick={onClose}>
          Done
        </button>
      </div>

      {error ? (
        <div className="cam__error" role="alert">
          <p>{error}</p>
          <button className="btn btn--primary" onClick={onClose}>
            Back
          </button>
        </div>
      ) : (
        <>
          <div className="cam__tip">
            {last?.warnings?.length ? (
              <div className="cam__warn" role="alert">
                {last.warnings[0]}
                <button onClick={() => { onUndo(last.id); setLast(null); }}>Retake</button>
              </div>
            ) : (
              <p>Step a little to the side after each shot. Each photo should overlap the last by about 60%.</p>
            )}
          </div>
          <div className="cam__bottom">
            <div className="cam__thumb">{last && <img src={last.url} alt="Last photo" />}</div>
            <button className="cam__shutter" onClick={shoot} disabled={!ready || busy || count >= MAX_PHOTOS} aria-label="Take photo" />
            <div className="cam__thumb cam__thumb--empty" aria-hidden="true">
              <Icon name="camera" />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
