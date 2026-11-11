# RoomSage remote image-render notebook

The notebook server accepts the original room photo, the local catalog composite, style, palette, seed, item names, reference cutouts, and the placed-item masks. It returns a PNG at the original photo's dimensions. The backend blends only the dilated, feathered item-mask regions from that output onto the catalog composite; pixels outside those regions remain byte-for-byte the composite's pixels. A remote failure is surfaced in the design metadata as `render_quality: "composite_fallback"`.

## Model choice and licenses

Model cards do not publish peak T4 VRAM measurements. The VRAM values below are inference estimates based on FP16/quantized weights and activation overhead at the listed working resolution; actual peaks depend on Diffusers versions and image dimensions.

| Candidate | License on model card | Approx. T4 VRAM | Notes |
|---|---|---:|---|
| [`stable-diffusion-v1-5/stable-diffusion-inpainting`](https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting) | CreativeML OpenRAIL-M | 6–8 GB, fp16, 512 px | Default masked inpainting model; fits a 16 GB T4 with CPU offload. |
| [`timbrooks/instruct-pix2pix`](https://huggingface.co/timbrooks/instruct-pix2pix) | MIT on the model card | 8–12 GB, fp16, 512 px | Bake-off baseline; the card notes that Stable Diffusion-derived components may carry additional terms. |
| [`Qwen/Qwen-Image-Edit`](https://huggingface.co/Qwen/Qwen-Image-Edit) | Apache-2.0 | About 12–16 GB, 4-bit transformer, CPU offload, 512 px | 20B parameter instruction editor; quantized bake-off candidate, may be close to T4 memory limits. |
| [`diffusers/stable-diffusion-xl-1.0-inpainting-0.1`](https://huggingface.co/diffusers/stable-diffusion-xl-1.0-inpainting-0.1) | CreativeML OpenRAIL++-M | About 14–20 GB, fp16, 1024 px | Larger inpainting alternative; not the selected default because it can exceed a T4's VRAM. |

The selected default is the SD 1.5 inpainting model. In `render_server.py`, edit `MODEL_ID`, `MODEL_LICENSE`, and `MODE` (`inpaint` or `edit`) together when changing candidates. For Qwen Image Edit, set `MODE="edit"`; its transformer is loaded with 4-bit quantization and CPU offload. The default `MODE="inpaint"` unions the submitted placed-item masks as the inpainting mask. In edit mode, the selected editor uses the composite as its image condition; the backend still applies the same item-region-only output blend.

## Kaggle setup

1. Create a Kaggle notebook, enable Internet in notebook settings, and select a GPU accelerator. A T4 (16 GB) is supported by the default FP16 model.
2. Add the dataset containing `render_server.py` with Kaggle's **Add Input** button. Kaggle mounts datasets read-only under `/kaggle/input/<dataset-slug>/`; the slug may differ from the dataset's displayed name, so the launch cell below discovers the file instead of guessing its path.
3. Install the server and model packages:

   ```python
   %pip install -q "fastapi>=0.115" "uvicorn[standard]" python-multipart diffusers transformers accelerate safetensors bitsandbytes
   ```

4. Set a unique long bearer secret using Kaggle Secrets (recommended) or a hidden prompt. Do not print it:

   ```python
   import os, getpass
   os.environ["REMOTE_RENDER_TOKEN"] = getpass.getpass("RoomSage bearer token: ")
   ```

5. Start the model server from a Python cell (do not prefix these lines with `!`):

   ```python
   from pathlib import Path
   import os, subprocess, sys, time, urllib.request
   matches = list(Path("/kaggle/input").rglob("render_server.py"))
   if not matches:
       raise FileNotFoundError("Add the dataset containing render_server.py with Kaggle's Add Input button")
   script = matches[0]
   print("Using server file:", script)
   server_log = open("/tmp/roomsage-server.log", "w")
   server = subprocess.Popen(
       [sys.executable, str(script)],
       stdout=server_log,
       stderr=subprocess.STDOUT,
       start_new_session=True,
   )
   for _ in range(60):
       try:
           urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
           print("RoomSage server is ready")
           break
       except Exception:
           if server.poll() is not None:
               raise RuntimeError(open("/tmp/roomsage-server.log").read())
           time.sleep(1)
   else:
       raise TimeoutError("RoomSage server did not become ready; inspect /tmp/roomsage-server.log")
   ```

6. Download and start a Cloudflare quick tunnel from a Python cell. This avoids notebook `!` shell magics and `nohup`, which Kaggle's IPython shell may reject. The cell waits for and prints the tunnel URL:

   ```python
   import os, re, subprocess, time, urllib.request
   cloudflared = os.path.abspath("./cloudflared")
   urllib.request.urlretrieve(
       "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
       cloudflared,
   )
   os.chmod(cloudflared, 0o755)
   tunnel_log = open("/tmp/cloudflared.log", "w")
   tunnel = subprocess.Popen(
       [cloudflared, "tunnel", "--url", "http://127.0.0.1:8000"],
       stdout=tunnel_log,
       stderr=subprocess.STDOUT,
       start_new_session=True,
   )
   tunnel_url = None
   for _ in range(60):
       tunnel_log.flush()
       try:
           log_text = open("/tmp/cloudflared.log", encoding="utf-8", errors="replace").read()
       except FileNotFoundError:
           log_text = ""
       match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log_text)
       if match:
           tunnel_url = match.group(0)
           break
       if tunnel.poll() is not None:
           raise RuntimeError("cloudflared exited; log:\n" + log_text)
       time.sleep(1)
   if not tunnel_url:
       raise TimeoutError("No trycloudflare URL appeared; inspect /tmp/cloudflared.log")
   print("Tunnel URL:", tunnel_url)
   ```

7. Check `tunnel_url + "/health"`. Set RoomSage's `REMOTE_RENDER_URL` to `tunnel_url + "/render"` and `REMOTE_RENDER_TOKEN` to the exact same secret.

## Colab setup

1. Open a Colab notebook, choose **Runtime → Change runtime type → T4 GPU**, and upload `render_server.py`.
2. Install dependencies:

   ```python
   %pip install -q "fastapi>=0.115" "uvicorn[standard]" python-multipart diffusers transformers accelerate safetensors bitsandbytes pyngrok
   ```

3. Set a unique bearer secret and start the server (use a Python cell, without `!` prefixes):

   ```python
   import getpass, os, subprocess, sys, time, urllib.request
   os.environ["REMOTE_RENDER_TOKEN"] = getpass.getpass("RoomSage bearer token: ")
   server_log = open("/tmp/roomsage-server.log", "w")
   server = subprocess.Popen([sys.executable, "render_server.py"], stdout=server_log, stderr=subprocess.STDOUT, start_new_session=True)
   for _ in range(60):
       try:
           urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read()
           print("RoomSage server is ready")
           break
       except Exception:
           if server.poll() is not None:
               raise RuntimeError(open("/tmp/roomsage-server.log").read())
           time.sleep(1)
   else:
       raise TimeoutError("RoomSage server did not become ready; inspect /tmp/roomsage-server.log")
   ```

4. Add your ngrok auth token using Colab Secrets or a hidden prompt, then expose port 8000:

   ```python
   from pyngrok import ngrok
   ngrok.set_auth_token(getpass.getpass("ngrok auth token: "))
   public_url = ngrok.connect(8000, "http").public_url
   print(public_url)
   ```

5. Health-check `public_url + "/health"`. Set RoomSage's `REMOTE_RENDER_URL` to `public_url + "/render"` and `REMOTE_RENDER_TOKEN` to the same bearer secret used in Colab.

## RoomSage configuration

Set these in `backend/.env` or the render-worker environment:

```dotenv
IMAGE_PROVIDER=remote
REMOTE_RENDER_URL=https://your-tunnel-host/render
REMOTE_RENDER_TOKEN=the-same-secret-entered-in-the-notebook
REMOTE_RENDER_TIMEOUT_SECONDS=180
REMOTE_BLEND_MARGIN_PIXELS=12
REMOTE_RENDER_MODEL_ID=stable-diffusion-v1-5/stable-diffusion-inpainting
```

The backend first runs its existing local catalog compositor. It posts the original photo, that composite, the selected item metadata, each available reference image, and each placed-item mask. Remote HTTP/network/timeout/invalid-image/size errors return the local composite and persist `render_quality="composite_fallback"`. A near-identical response to the original photo fails the job instead of being accepted. The UI displays the composite-fallback label from `Design.render_metadata`.

The bearer token is sent only from the backend to the notebook server. Treat a public notebook tunnel as temporary; stop it after testing and rotate the token. The tunnel URL may change when the notebook restarts.

## Bake-off

Run the local contact-sheet path without loading model weights:

```bash
python notebooks/bakeoff.py --stub
```

This creates `out/bakeoff.png` and `out/bakeoff_metrics.json` for photos 02, 05, and 06. Run without `--stub` on a GPU notebook to load the three candidates. Each candidate receives the same composite, item-mask union, prompt, and deterministic per-photo seed; only the model changes. The candidate output is blended through the same dilated, feathered item regions before it is saved or added to the sheet.
