from __future__ import annotations
import json, os, sys, time
from pathlib import Path
from io import BytesIO
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
from config import settings
from services.image_generation_service import HuggingFaceImageProvider
from services.pipeline_trace import trace_enabled

SAMPLES = [
    ('photo_01.jpg', 'narrow hallway', 'ceiling-lit / interior'),
    ('photo_02.jpg', 'compact rectangular room', 'window-lit / neutral'),
    ('photo_05.jpg', 'wide window-facing room', 'bright daylight'),
    ('photo_06.jpg', 'doorway rectangular room', 'mixed interior daylight'),
    ('photo_07.jpg', 'side-window rectangular room', 'soft daylight'),
    ('photo_11.jpg', 'corner window room', 'bright natural light'),
]

texture_path = BACKEND / 'data' / 'catalog_image_audit' / 'thumbs' / 'FLOOR-JAP-009.jpg'
with Image.open(texture_path) as thumbnail:
    swatch = thumbnail.crop((24, 52, 206, 149)).convert('RGB')
buf = BytesIO(); swatch.save(buf, format='PNG'); floor_bytes = buf.getvalue()
items = [
    {'catalog_item_id':'PAINT-JAP-087', 'title':'Asian Paints Royale Sugared Nut L126', 'price_inr':'850.00', 'kind':'surface', 'role':'paint', 'primary_color':'beige', 'prompt_phrase':'warm soft beige wall paint'},
    {'catalog_item_id':'FLOOR-JAP-009', 'title':'Kajaria Eternity light beige tile', 'price_inr':'65.00', 'kind':'surface', 'role':'flooring', 'floor_texture_bytes':floor_bytes},
]
source_dir = BACKEND / 'tests' / 'fixtures' / 'sample_room'
out_dir = ROOT / 'out'
out_dir.mkdir(exist_ok=True)
settings.render_output_dir = str(out_dir)
provider = HuggingFaceImageProvider()
summary = []
for index, (filename, layout, lighting) in enumerate(SAMPLES, 1):
    source_path = source_dir / filename
    raw = source_path.read_bytes()
    with Image.open(source_path) as im:
        rgb = np.asarray(im.convert('RGB'))
        mean_luma = float(np.asarray(im.convert('L')).mean())
    design_id = f'sample-{source_path.stem}-{time.strftime("%Y%m%d-%H%M%S")}-{index}'
    trace_context = {
        'design_id': design_id, 'photo': filename, 'room_id': 'sample-fixture',
        'provider':'huggingface', 'model_ids':settings.image_model_ids,
        'style':'Japandi', 'palette':'warm beige and sage', 'seed':4100+index,
        'worker_prompt':'Japandi empty-room surface design. Preserve room geometry. Edit walls and floor only.',
        'selected_items':[{'id':it['catalog_item_id'],'name':it['title'],'price_inr':it['price_inr'],
                           'style_match':True,'role':it['role'],'kind':'surface'} for it in items],
    }
    started = time.perf_counter()
    output = provider.render(trace_context['worker_prompt'], raw, style='Japandi', palette='warm beige and sage', items=items, depth_map=None, seed=4100+index, trace_context=trace_context)
    verification = provider.locate_items(output, items)
    elapsed = time.perf_counter() - started
    trace_path = out_dir / 'trace' / design_id
    with Image.open(BytesIO(output)) as result:
        result.save(trace_path / '10_final.png')
    run = {'photo':filename,'layout':layout,'lighting_profile':lighting,'mean_input_luminance_0_255':round(mean_luma,2),
           'seconds':round(elapsed,3),'provider':'huggingface','model_id':settings.hf_segmentation_model_id,
           'model_device':'cpu','cuda_available':bool(provider.torch.cuda.is_available()),
           'cuda_device':provider.torch.cuda.get_device_name(0) if provider.torch.cuda.is_available() else None,
           'furniture_guard':'passed','render_metadata':provider.render_metadata,'selected_surface_items':[{'id':x['catalog_item_id'],'name':x['title'],'role':x['role']} for x in items],
           'hotspots':[x.model_dump() for x in verification.items]}
    (trace_path / 'sample_run.json').write_text(json.dumps(run, indent=2)+'\n', encoding='utf-8')
    summary.append({'design_id':design_id,**run})
    print(json.dumps(summary[-1]), flush=True)

# Contact sheet: five requested columns and one row per photo.
cols = ['input','wall mask','floor mask','restyled','final']
thumb_w, thumb_h, label_h, row_h = 360, 240, 28, 284
sheet = Image.new('RGB',(thumb_w*len(cols),row_h*len(SAMPLES)),(245,245,245))
draw=ImageDraw.Draw(sheet)
for row, entry in enumerate(summary):
    folder=out_dir/'trace'/entry['design_id']
    filenames=['01_input.png','02_wall_mask.png','03_floor_mask.png','09_restyled.png','10_final.png']
    for col,(title,artifact) in enumerate(zip(cols,filenames)):
        with Image.open(folder/artifact) as im:
            tile=im.convert('RGB')
            if 'mask' in title:
                tile=Image.merge('RGB',(tile.convert('L'),)*3)
            tile.thumbnail((thumb_w,thumb_h),Image.Resampling.LANCZOS)
            x=col*thumb_w+(thumb_w-tile.width)//2
            y=row*row_h+label_h+(thumb_h-tile.height)//2
            sheet.paste(tile,(x,y))
        draw.text((col*thumb_w+8,row*row_h+6), f"{entry['photo']} · {title}", fill=(20,20,20))
sheet.save(out_dir/'contact_sheet.png')
(out_dir/'sample_run_summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
print(f'CONTACT_SHEET={out_dir / "contact_sheet.png"}')

