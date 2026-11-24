import os

from seguro.tools.registry import registry, _parse_list
from seguro.safety.gate import safety_gate


@registry.register(
    name="pptx_create",
    description=("Creates a PowerPoint (.pptx). slides is a list of objects {title, text, image}; text and image "
                 "are optional (image = path to a PNG/JPG). Bare filenames go to the Desktop."),
    parameters={
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Output .pptx name or path"},
            "slides": {"type": "array", "items": {"type": "object"}, "description": "[{title, text, image}]"}
        },
        "required": ["filename", "slides"]
    }
)
def pptx_create(filename: str, slides: list) -> dict:
    import json
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from seguro.tools.files import normalize_user_path

    slides = [json.loads(s) if isinstance(s, str) else s for s in _parse_list(slides)]
    if not slides or not all(isinstance(s, dict) and s.get("title") for s in slides):
        return {"ok": False, "error": "slides must be a non-empty list of objects with at least a 'title'"}
    images = {}
    for i, s in enumerate(slides):
        if s.get("image"):
            p = normalize_user_path(str(s["image"]))
            if not os.path.isfile(p):
                return {"ok": False, "error": f"slide {i + 1}: image not found: {p}"}
            images[i] = p

    name = filename if filename.lower().endswith(".pptx") else filename + ".pptx"
    out_path = normalize_user_path(name)
    ok, reason = safety_gate.check_path(out_path)
    if not ok:
        return {"ok": False, "error": reason}

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    for i, s in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[5])  # title only
        slide.shapes.title.text = str(s["title"])
        top = Inches(1.5)
        if s.get("text"):
            box = slide.shapes.add_textbox(Inches(0.6), top, Inches(5.6 if i in images else 12), Inches(5))
            box.text_frame.word_wrap = True
            box.text_frame.text = str(s["text"])
            for para in box.text_frame.paragraphs:
                para.font.size = Pt(18)
        if i in images:
            left = Inches(6.5) if s.get("text") else Inches(1.5)
            slide.shapes.add_picture(images[i], left, top, height=Inches(5.3))
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        prs.save(out_path)
    except PermissionError:
        return {"ok": False, "error": f"'{out_path}' is locked, probably open in another program."}
    return {"ok": True, "path": out_path, "slides": len(slides), "images": len(images)}
