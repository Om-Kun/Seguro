import os
import time
from typing import List, Dict, Any, Optional
from seguro.tools.registry import registry
from seguro.safety.gate import safety_gate

@registry.register(
    name="skill_fastapi_generator",
    description="Generates a complete, functional FastAPI microservice with CRUD endpoints, Pydantic schemas, and in-memory or SQLite storage.",
    parameters={
        "type": "object",
        "properties": {
            "entity_name": {"type": "string", "description": "Name of entity model (e.g. 'Book', 'Student', 'Device', 'Invoice')"},
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of field definitions in format 'name:type' (e.g. ['title:str', 'price:float', 'in_stock:bool'])"
            },
            "project_name": {"type": "string", "description": "Optional directory name in ~/SeguroProjects/"},
            "port": {"type": "integer", "description": "Port to run service on (default: 8000)"}
        },
        "required": ["entity_name"]
    }
)
def skill_fastapi_generator(
    entity_name: str,
    fields: Optional[List[str]] = None,
    project_name: Optional[str] = None,
    port: int = 8000
) -> dict:
    fields = fields or ["name:str", "description:str", "status:str"]
    p_name = project_name or f"api_{entity_name.lower()}_{int(time.time())}"
    workdir = os.path.join(os.path.expanduser(r"~\SeguroProjects"), p_name)
    os.makedirs(workdir, exist_ok=True)

    # Generate Pydantic fields
    py_fields = []
    sample_item = {}
    for f in fields:
        parts = f.split(":")
        fname = parts[0].strip()
        ftype = parts[1].strip() if len(parts) > 1 else "str"
        py_fields.append(f"    {fname}: {ftype}")
        sample_item[fname] = 100 if ftype in ("int", "float") else f"sample_{fname}"

    fields_code = "\n".join(py_fields)

    app_code = f'''import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="{entity_name} Management API", version="1.0.0")

class {entity_name}Create(BaseModel):
{fields_code}

class {entity_name}({entity_name}Create):
    id: int

# In-memory storage table
db: List[{entity_name}] = []
counter = 1

@app.get("/")
def read_root():
    return {{"status": "online", "entity": "{entity_name}", "items_count": len(db)}}

@app.get("/{entity_name.lower()}s", response_model=List[{entity_name}])
def get_all():
    return db

@app.get("/{entity_name.lower()}s/{{item_id}}", response_model={entity_name})
def get_one(item_id: int):
    for item in db:
        if item.id == item_id:
            return item
    raise HTTPException(status_code=404, detail="{entity_name} not found")

@app.post("/{entity_name.lower()}s", response_model={entity_name})
def create_one(payload: {entity_name}Create):
    global counter
    new_item = {entity_name}(id=counter, **payload.dict())
    counter += 1
    db.append(new_item)
    return new_item

@app.delete("/{entity_name.lower()}s/{{item_id}}")
def delete_one(item_id: int):
    global db
    initial_len = len(db)
    db = [item for item in db if item.id != item_id]
    if len(db) == initial_len:
        raise HTTPException(status_code=404, detail="{entity_name} not found")
    return {{"ok": True, "deleted_id": item_id}}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port={port})
'''
    main_file = os.path.join(workdir, "main.py")
    with open(main_file, "w", encoding="utf-8") as f:
        f.write(app_code)

    readme_file = os.path.join(workdir, "README.md")
    with open(readme_file, "w", encoding="utf-8") as f:
        f.write(f"# {entity_name} API Service\n\nRun with:\n```powershell\npython main.py\n```\nSwagger docs available at http://127.0.0.1:{port}/docs\n")

    return {
        "ok": True,
        "workdir": workdir,
        "entrypoint": main_file,
        "entity": entity_name,
        "port": port,
        "files": ["main.py", "README.md"]
    }
